"""Build map-able road segments (edges between consecutive GPS fixes) from
classified location points, keeping the per-point roughness score sequence
so the map can plot exactly the metric that decided each edge's class.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

EARTH_RADIUS_M = 6_371_000.0


@dataclass
class Segment:
    coords: list[tuple[float, float]]  # [(lat, lon), ...], full path of the segment
    start_time: float
    end_time: float
    road_class: str
    roughness_score: float  # mean roughness score over the segment's points
    speed: float  # mean speed (m/s) over the segment's points
    accel_std: float
    n_samples: int
    point_times: list[float] = field(default_factory=list)  # seconds_elapsed of each point in the run
    point_scores: list[float] = field(default_factory=list)  # roughness_score of each point in the run (same metric used for classification)
    point_distances: list[float] = field(default_factory=list)  # cumulative distance (m) along the run, aligned with point_scores


def _class_value(c) -> str:
    return c.value if hasattr(c, "value") else str(c)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return float(2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a)))


def _cumulative_distances(lats: list[float], lons: list[float]) -> list[float]:
    if not lats:
        return []
    dist = [0.0]
    for i in range(1, len(lats)):
        dist.append(dist[-1] + _haversine_m(lats[i - 1], lons[i - 1], lats[i], lons[i]))
    return dist


def build_segments(classified_points: pd.DataFrame, merge_consecutive: bool = True) -> list[Segment]:
    """Turn consecutive classified location points into line segments.

    Each edge inherits the roughness class/score of its *end* point (the
    class measured while traversing that stretch of road). When
    ``merge_consecutive`` is True (default), runs of consecutive points
    sharing the same class are merged into a single multi-point segment,
    which keeps the map fast to render and easier to read (one popup per
    contiguous stretch of road instead of one per GPS fix).
    """
    pts = classified_points.dropna(subset=["latitude", "longitude", "road_class"]).reset_index(drop=True)
    if len(pts) < 2:
        return []

    if not merge_consecutive:
        segments: list[Segment] = []
        for i in range(1, len(pts)):
            a, b = pts.iloc[i - 1], pts.iloc[i]
            segments.append(_pair_to_segment(a, b, b))
        return segments

    segments = []
    run_start_idx = 0
    classes = pts["road_class"].map(_class_value).to_numpy()
    for i in range(1, len(pts) + 1):
        end_of_run = i == len(pts) or classes[i] != classes[run_start_idx]
        if end_of_run:
            run = pts.iloc[run_start_idx:i]
            if len(run) >= 2:
                segments.append(_run_to_segment(run))
            run_start_idx = i
    return segments


def _pair_to_segment(a: pd.Series, b: pd.Series, label_row: pd.Series) -> Segment:
    dist = _haversine_m(float(a["latitude"]), float(a["longitude"]), float(b["latitude"]), float(b["longitude"]))
    has_score = pd.notna(label_row["roughness_score"])
    return Segment(
        coords=[(float(a["latitude"]), float(a["longitude"])), (float(b["latitude"]), float(b["longitude"]))],
        start_time=float(a["seconds_elapsed"]),
        end_time=float(b["seconds_elapsed"]),
        road_class=_class_value(label_row["road_class"]),
        roughness_score=float(label_row["roughness_score"]) if has_score else float("nan"),
        speed=float(label_row["speed"]) if pd.notna(label_row["speed"]) else float("nan"),
        accel_std=float(label_row.get("accel_std", np.nan)) if pd.notna(label_row.get("accel_std", np.nan)) else float("nan"),
        n_samples=int(label_row.get("n_samples", 0) or 0),
        point_times=[float(a["seconds_elapsed"]), float(b["seconds_elapsed"])],
        point_scores=[float(label_row["roughness_score"])] * 2 if has_score else [],
        point_distances=[0.0, dist] if has_score else [],
    )


def _run_to_segment(run: pd.DataFrame) -> Segment:
    a, b = run.iloc[0], run.iloc[-1]
    avg = run.mean(numeric_only=True)
    coords = list(zip(run["latitude"].astype(float), run["longitude"].astype(float)))
    scores = run["roughness_score"].astype(float)
    valid = scores.notna()
    valid_lats = run.loc[valid, "latitude"].astype(float).tolist()
    valid_lons = run.loc[valid, "longitude"].astype(float).tolist()
    return Segment(
        coords=coords,
        start_time=float(a["seconds_elapsed"]),
        end_time=float(b["seconds_elapsed"]),
        road_class=_class_value(a["road_class"]),
        roughness_score=float(avg.get("roughness_score", float("nan"))),
        speed=float(avg.get("speed", float("nan"))),
        accel_std=float(avg.get("accel_std", float("nan"))),
        n_samples=int(run["n_samples"].sum()) if "n_samples" in run else 0,
        point_times=run.loc[valid, "seconds_elapsed"].astype(float).tolist(),
        point_scores=scores[valid].tolist(),
        point_distances=_cumulative_distances(valid_lats, valid_lons),
    )
