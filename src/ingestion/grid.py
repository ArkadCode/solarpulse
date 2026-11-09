"""Regular grid of points over mainland Colombia for the scale phase.

The country outline is a simplified polygon (about 50 vertices, accurate to roughly
20-30 km). It only decides which grid points count as "inside Colombia", which is
precise enough for a grid with ~28 km spacing. San Andrés and other islands are left out.
"""

from __future__ import annotations

from src.common.config import Location

# (longitude, latitude), clockwise from Punta Gallinas.
COLOMBIA_OUTLINE: list[tuple[float, float]] = [
    (-71.67, 12.46), (-71.10, 12.05), (-71.33, 11.62), (-72.25, 11.15), (-72.49, 11.11),
    (-72.95, 10.45), (-73.38, 9.17), (-72.78, 9.08), (-72.33, 8.32), (-72.47, 7.90),
    (-72.16, 7.33), (-71.12, 6.98), (-70.12, 6.97), (-69.43, 6.11), (-67.70, 6.27),
    (-67.34, 5.77), (-67.83, 4.50), (-67.30, 3.41), (-67.82, 2.82), (-67.20, 1.99),
    (-67.08, 1.13), (-67.94, 1.75), (-68.20, 1.97), (-69.82, 1.71), (-69.82, 1.07),
    (-69.20, 0.99), (-69.13, 0.65), (-70.05, 0.54), (-70.06, -0.15), (-69.58, -0.55),
    (-69.40, -1.18), (-69.95, -4.22), (-70.72, -3.78), (-70.05, -2.73), (-70.81, -2.25),
    (-71.41, -2.34), (-72.94, -2.45), (-73.66, -1.26), (-74.78, -0.20), (-75.27, -0.11),
    (-75.80, 0.08), (-76.41, 0.40), (-77.42, 0.82), (-78.86, 1.38), (-78.62, 2.54),
    (-77.93, 2.70), (-77.36, 3.83), (-77.50, 4.70), (-77.31, 5.85), (-77.89, 7.23),
    (-77.36, 7.70), (-77.20, 8.06), (-76.84, 8.64), (-76.24, 9.01), (-75.67, 9.44),
    (-75.54, 10.40), (-75.04, 10.94), (-74.22, 11.30), (-73.28, 11.35), (-72.80, 11.62),
    (-72.23, 11.89),
]

BBOX = {"min_lat": -4.25, "max_lat": 12.5, "min_lon": -79.0, "max_lon": -66.85}


def point_in_polygon(lon: float, lat: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray casting: count how many edges a ray going east from the point crosses."""
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > lat) != (yj > lat):
            x_cross = xi + (lat - yi) * (xj - xi) / (yj - yi)
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def grid_points(resolution_deg: float = 0.25) -> list[tuple[float, float]]:
    """(latitude, longitude) of every grid node inside the outline."""
    steps_lat = round((BBOX["max_lat"] - BBOX["min_lat"]) / resolution_deg)
    steps_lon = round((BBOX["max_lon"] - BBOX["min_lon"]) / resolution_deg)
    points = []
    for i in range(steps_lat + 1):
        lat = round(BBOX["min_lat"] + i * resolution_deg, 4)
        for j in range(steps_lon + 1):
            lon = round(BBOX["min_lon"] + j * resolution_deg, 4)
            if point_in_polygon(lon, lat, COLOMBIA_OUTLINE):
                points.append((lat, lon))
    return points


def grid_location_id(lat: float, lon: float) -> str:
    def fmt(v: float, pos: str, neg: str) -> str:
        return f"{abs(v):.2f}{pos if v >= 0 else neg}".replace(".", "p")

    return f"g_{fmt(lat, 'n', 's')}_{fmt(lon, 'e', 'w')}"


def grid_locations(resolution_deg: float = 0.25) -> list[Location]:
    return [
        Location(
            location_id=grid_location_id(lat, lon),
            name=f"Grid {lat:.2f}, {lon:.2f}",
            department="",
            region="grid",
            latitude=lat,
            longitude=lon,
            elevation_m=0,
        )
        for lat, lon in grid_points(resolution_deg)
    ]
