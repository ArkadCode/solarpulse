"""Data lake access: an S3-compatible object store or local disk, behind the same interface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from src.common.config import PROJECT_ROOT


class Lake:
    """Reads and writes Parquet under a root (an S3 bucket or a local folder)."""

    def __init__(self, fs: pafs.FileSystem, root: str):
        self.fs = fs
        self.root = root.rstrip("/")

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Lake:
        storage = settings["storage"]
        if storage["backend"] == "s3":
            fs = pafs.S3FileSystem(
                access_key=storage["access_key"],
                secret_key=storage["secret_key"],
                endpoint_override=storage["s3_endpoint"],
                scheme=storage.get("s3_scheme", "http"),
                region=storage.get("s3_region", "us-east-1"),
                allow_bucket_creation=True,
            )
            bucket = storage["bucket"]
            if fs.get_file_info(bucket).type == pafs.FileType.NotFound:
                fs.create_dir(bucket)
            return cls(fs, bucket)
        if storage["backend"] == "local":
            root = Path(storage["local_root"])
            if not root.is_absolute():
                root = PROJECT_ROOT / root
            root.mkdir(parents=True, exist_ok=True)
            return cls(pafs.LocalFileSystem(), str(root))
        raise ValueError(f"Unsupported storage backend: {storage['backend']}")

    def path(self, relative: str) -> str:
        return f"{self.root}/{relative.lstrip('/')}"

    def exists(self, relative: str) -> bool:
        return self.fs.get_file_info(self.path(relative)).type == pafs.FileType.File

    def write_parquet(self, relative: str, table: pa.Table) -> str:
        """Atomic write: write a temp file first, then rename it.

        An interrupted run never leaves a half-written file at the final path.
        """
        final = self.path(relative)
        tmp = f"{final}.tmp"
        self.fs.create_dir(final.rsplit("/", 1)[0], recursive=True)
        pq.write_table(table, tmp, filesystem=self.fs, compression="zstd")
        self.fs.move(tmp, final)
        return final

    def write_text(self, relative: str, text: str) -> str:
        path = self.path(relative)
        self.fs.create_dir(path.rsplit("/", 1)[0], recursive=True)
        with self.fs.open_output_stream(path) as out:
            out.write(pa.py_buffer(text.encode("utf-8")))
        return path

    def read_schema_metadata(self, relative: str) -> dict[str, str]:
        """Read only the Parquet footer (the data itself is not downloaded)."""
        schema = pq.read_schema(self.path(relative), filesystem=self.fs)
        meta = schema.metadata or {}
        return {k.decode(): v.decode() for k, v in meta.items()}

    def read_dataset(self, relative: str) -> pa.Table:
        return pq.read_table(self.path(relative), filesystem=self.fs, partitioning="hive")
