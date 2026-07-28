"""End-to-end orchestration: load a drive -> extract vertical acceleration
-> window/aggregate -> speed-compensate & classify -> build map-able
segments -> render folium map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import folium
import pandas as pd

from imu_road_quality.classification import ClassificationConfig, classify_drive, compute_score_bands
from imu_road_quality.io import DriveData, load_drive
from imu_road_quality.mapping import build_map
from imu_road_quality.segments import Segment, build_segments
from imu_road_quality.signal_processing import compute_vertical_acceleration, window_features


@dataclass
class PipelineConfig:
    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    min_speed_for_scoring: float = 1.0  # m/s; drop near-stationary points before classification


@dataclass
class PipelineResult:
    drive: DriveData
    vert_accel: pd.DataFrame
    windowed: pd.DataFrame
    classified: pd.DataFrame
    thresholds: list[float]
    segments: list[Segment]
    fmap: folium.Map


class RoadQualityPipeline:
    """Runs the full roughness-classification pipeline for one drive.

    Every drive uses the same fixed, corpus-calibrated
    :class:`ClassificationConfig` (see
    ``imu_road_quality.classification``): the roughness score is
    normalized onto a fixed absolute scale and cut at fixed thresholds
    (0.25 / 0.5 / 0.75), so no per-drive tuning is required even though
    phone mount, vehicle, and road mix differ between recordings.
    """

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or PipelineConfig()

    def run(self, drive_path: str | Path, drive_name: str | None = None) -> PipelineResult:
        drive = load_drive(drive_path, name=drive_name)
        return self.run_on_drive(drive)

    def run_on_drive(self, drive: DriveData) -> PipelineResult:
        vert_accel = compute_vertical_acceleration(drive)
        windowed = window_features(vert_accel, drive.location)

        scoring_input = windowed[windowed["speed"] >= self.config.min_speed_for_scoring].copy()
        scoring_input = scoring_input.dropna(subset=["accel_std", "speed"])

        classified, thresholds = classify_drive(scoring_input, self.config.classification)
        segments = build_segments(classified)
        score_bands = compute_score_bands(classified)
        fmap = build_map(segments, score_bands, drive_name=drive.name)

        return PipelineResult(
            drive=drive,
            vert_accel=vert_accel,
            windowed=windowed,
            classified=classified,
            thresholds=thresholds,
            segments=segments,
            fmap=fmap,
        )
