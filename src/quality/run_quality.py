"""Runs the Great Expectations suites and writes a data quality report.

The report (Markdown + JSON) goes to reports/ and is also stored in the lake under
quality/reports/. It includes quarantine counts per rule, so rows set aside by the
Silver quality rules are visible in every run.

Usage: python -m src.quality.run_quality
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# Quiet, offline GX: no progress bars, no usage statistics.
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("GX_ANALYTICS_ENABLED", "False")

import duckdb  # noqa: E402
import great_expectations as gx  # noqa: E402
import pandas as pd  # noqa: E402

from src.common.config import PROJECT_ROOT, load_settings  # noqa: E402
from src.common.logging import get_logger  # noqa: E402
from src.common.run_metrics import tracked_run  # noqa: E402
from src.common.storage import Lake  # noqa: E402
from src.quality.expectations import SUITES, build_suite  # noqa: E402
from src.transform.dbt_runner import warehouse_path  # noqa: E402

log = get_logger("quality")
logging.getLogger("great_expectations").setLevel(logging.WARNING)


@dataclass
class ExpectationOutcome:
    expectation: str
    column: str | None
    success: bool
    unexpected_count: int | None = None
    unexpected_percent: float | None = None
    observed_value: Any = None


@dataclass
class SuiteOutcome:
    table: str
    rows: int
    success: bool
    expectations: list[ExpectationOutcome] = field(default_factory=list)


def validate_dataframe(name: str, df: pd.DataFrame, expectations: list) -> SuiteOutcome:
    context = gx.get_context(mode="ephemeral")
    source = context.data_sources.add_pandas(name="solarpulse")
    asset = source.add_dataframe_asset(name=name)
    batch_definition = asset.add_batch_definition_whole_dataframe("full")
    suite = context.suites.add(build_suite(name, expectations))
    validation = context.validation_definitions.add(
        gx.ValidationDefinition(name=f"{name}_validation", data=batch_definition, suite=suite)
    )
    result = validation.run(batch_parameters={"dataframe": df})

    outcomes = []
    for r in result.results:
        cfg = r.expectation_config
        res = r.result or {}
        observed = res.get("observed_value")
        outcomes.append(
            ExpectationOutcome(
                expectation=cfg.type,
                column=cfg.kwargs.get("column")
                or ",".join(cfg.kwargs.get("column_list", []))
                or (f"{cfg.kwargs['column_A']}>={cfg.kwargs['column_B']}"
                    if "column_A" in cfg.kwargs else None),
                success=bool(r.success),
                unexpected_count=res.get("unexpected_count"),
                unexpected_percent=res.get("unexpected_percent"),
                observed_value=round(observed, 4) if isinstance(observed, float) else observed,
            )
        )
    return SuiteOutcome(
        table=name, rows=len(df), success=bool(result.success), expectations=outcomes
    )


def load_frame(con: duckdb.DuckDBPyConnection, relation: str) -> pd.DataFrame:
    df = con.execute(f"select * from {relation}").df()
    for col in df.columns:
        if df[col].dtype == bool:
            df[col] = df[col].astype(int)  # lets GX compute means of boolean flags
    return df


def quarantine_summary(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        select rule, count(*) as rows
        from silver.quarantine_hourly_weather, unnest(failed_rules) as t(rule)
        group by rule order by rows desc
        """
    ).fetchall()
    return [{"rule": rule, "rows": n} for rule, n in rows]


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Data quality report",
        "",
        f"Run at {report['run_at']} · overall: **{'PASS' if report['success'] else 'FAIL'}**",
        "",
        "## Silver quarantine",
        "",
    ]
    if report["quarantine"]:
        lines += ["| Rule | Rows |", "| --- | --- |"]
        lines += [f"| {q['rule']} | {q['rows']:,} |" for q in report["quarantine"]]
    else:
        lines.append("No rows were quarantined.")
    for suite in report["suites"]:
        status = "PASS" if suite["success"] else "FAIL"
        lines += [
            "",
            f"## {suite['table']} ({suite['rows']:,} rows) · {status}",
            "",
            "| Expectation | Column | Result | Unexpected | Observed |",
            "| --- | --- | --- | --- | --- |",
        ]
        for e in suite["expectations"]:
            unexpected = "" if e["unexpected_count"] is None else f"{e['unexpected_count']:,}"
            observed = "" if e["observed_value"] is None else str(e["observed_value"])
            lines.append(
                f"| {e['expectation']} | {e['column'] or ''} | "
                f"{'pass' if e['success'] else 'FAIL'} | {unexpected} | {observed} |"
            )
    return "\n".join(lines) + "\n"


def run_quality(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    con = duckdb.connect(str(warehouse_path(settings)), read_only=True)
    try:
        suites = []
        for relation, factory in SUITES.items():
            outcome = validate_dataframe(relation, load_frame(con, relation), factory())
            suites.append(outcome)
            log.info(
                "suite_validated",
                extra={"table": relation, "rows": outcome.rows, "success": outcome.success},
            )
        quarantine = quarantine_summary(con)
    finally:
        con.close()

    run_at = datetime.now(timezone.utc)
    report = {
        "run_at": run_at.isoformat(timespec="seconds"),
        "success": all(s.success for s in suites),
        "quarantine": quarantine,
        "suites": [asdict(s) for s in suites],
    }
    write_report(report, settings, run_at)
    return report


def write_report(report: dict[str, Any], settings: dict[str, Any], run_at: datetime) -> None:
    report_dir = PROJECT_ROOT / settings.get("reports", {}).get("dir", "reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    body_json = json.dumps(report, indent=2, default=str)
    (report_dir / "data_quality_report.json").write_text(body_json, encoding="utf-8")
    (report_dir / "data_quality_report.md").write_text(to_markdown(report), encoding="utf-8")

    # Keep the history in the lake: one JSON per run.
    lake = Lake.from_settings(settings)
    stamp = run_at.strftime("%Y%m%dT%H%M%SZ")
    path = lake.write_text(f"quality/reports/{stamp}.json", body_json)
    log.info("report_written", extra={"local": str(report_dir), "lake": path})


def main() -> int:
    with tracked_run("quality") as run:
        report = run_quality()
        run.update(
            success=report["success"],
            failed_expectations=sum(
                not e["success"] for s in report["suites"] for e in s["expectations"]
            ),
            rows_quarantined_by_rule={q["rule"]: q["rows"] for q in report["quarantine"]},
            _failed=not report["success"],
        )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

