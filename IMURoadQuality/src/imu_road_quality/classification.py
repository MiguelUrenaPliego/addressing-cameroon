"""Speed-compensated roughness scoring and a fixed, cross-drive absolute
roughness scale.

Classes (ordered from smoothest to roughest), matching the user's request:
    paved      - smooth, well maintained road
    unpaved    - dirt/gravel road, mildly rough but regular
    bad        - degraded paved road or rough unpaved road
    very_bad   - severely degraded / heavily potholed road

Phone model, mount, and vehicle change between recordings, so the
roughness score must mean the same thing on every drive: a fixed
normalized value of, say, 0.6 should indicate roughly the same severity
whether it came from a smooth 30-minute highway drive or a 5-minute
off-road stretch. The previous version of this module instead fit
thresholds *per drive*, as quantiles of that drive's own score
distribution — which guarantees every drive gets some points in every
class regardless of how rough it actually was, defeating the purpose of
an absolute scale (a drive that never leaves paved road would still be
told it has a "very_bad" 7th percentile).

This version instead uses:

1. A fixed, corpus-calibrated ``[score_min, score_max]`` normalization
   window (see ``score_min``/``score_max`` below) instead of a per-drive
   quantile fit, so the same raw vibration level always maps to the same
   normalized score on every drive.
2. Fixed class boundaries at 0.25 / 0.5 / 0.75 of that normalized score,
   **always** — a drive that never gets rough simply has zero points
   above 0.5, rather than points being forced into "bad"/"very_bad" to
   fill out the distribution.

See ``reports/report.md`` for the calibration procedure and
``reports/FUASProjektarbeit/references.bib`` for the literature this
design was checked against (robust/absolute normalization for
cross-device sensor comparability, and the World Bank/AASHTO convention
of fixed IRI severity bands, which motivates using fixed rather than
per-recording bands here).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class RoadClass(str, Enum):
    PAVED = "paved"
    UNPAVED = "unpaved"
    BAD = "bad"
    VERY_BAD = "very_bad"


CLASS_ORDER = [RoadClass.PAVED, RoadClass.UNPAVED, RoadClass.BAD, RoadClass.VERY_BAD]

# Green -> yellow -> orange -> red (the conventional "road condition"
# palette). Chosen with clearly different lightness as well as hue
# (green and red are both medium-dark, yellow and orange are both
# light/medium-light) so the sequence stays readable via brightness
# contrast alone, since pure hue is unreliable for red-green color-vision
# deficiency.
CLASS_COLORS = {
    RoadClass.PAVED: "#1a9850",
    RoadClass.UNPAVED: "#fee08b",
    RoadClass.BAD: "#fc8d59",
    RoadClass.VERY_BAD: "#d73027",
}

# Fixed roughness-score cut points, identical for every drive. A
# normalized score of 0.6 means "bad" whether it came from a 2-minute
# recording or a 40-minute one.
FIXED_SCORE_THRESHOLDS: tuple[float, float, float] = (0.25, 0.5, 0.75)


@dataclass
class ClassificationConfig:
    """Hyperparameters governing roughness scoring and classification.

    Unlike the per-drive calibration this module used previously, these
    are now genuinely global constants: the same ``ClassificationConfig``
    (with its defaults) should be used for every drive, since the whole
    point of the fixed scale is that it does not need per-recording
    tuning. The one exception is ``roughness_metric``/`speed_exponent`,
    which are properties of the signal-processing choice, not of a
    specific drive, so they too are shared defaults rather than per-drive
    knobs.
    """

    # --- speed compensation ---
    speed_exponent: float = 1.5
    min_speed_floor: float = 0.5  # m/s, avoids blow-up near a stop
    stationary_speed: float = 0.3  # m/s, points slower than this are dropped
    roughness_metric: str = "accel_std"  # one of accel_std, accel_rms, accel_ptp

    # --- normalization ---
    use_log_transform: bool = True

    # Fixed normalization anchors for the log1p-transformed,
    # speed-compensated score, calibrated ONCE on the pooled distribution
    # across all 5 retained drives in this project's dataset (the 1st and
    # 97th percentile of log1p(accel_std / speed^1.5) over ~24k pooled
    # GPS-rate points, see reports/report.md section "Fixed normalization
    # calibration"). These are corpus-level constants, not fit per drive:
    # a drive with unusually smooth or unusually rough IMU readings should
    # still be compared against the same absolute scale as every other
    # drive, rather than being re-normalized against itself.
    score_min: float = 0.02
    score_max: float = 0.58

    # --- classification ---
    # Fixed score thresholds (3 increasing cut points in [0, 1] normalized
    # score) separating paved|unpaved|bad|very_bad. These are always the
    # same for every drive (see FIXED_SCORE_THRESHOLDS); overriding is
    # supported for experimentation only and is not expected in normal use.
    score_thresholds: tuple[float, float, float] = FIXED_SCORE_THRESHOLDS

    # Rolling-mode smoothing window (in points) applied to the per-point
    # class sequence before segments are built. Real road surface changes
    # are spatially contiguous, but the raw per-point score is noisy near
    # class boundaries; a short majority-vote filter removes single-point
    # flicker without washing out genuine transitions. Set to 1 to disable.
    smoothing_window: int = 5


def compute_roughness_score(df: pd.DataFrame, config: ClassificationConfig) -> pd.DataFrame:
    """Speed-compensate the raw vibration metric and map it onto the fixed
    ``[config.score_min, config.score_max]`` calibration window, clipped to
    ``[0, 1]``.

    Because ``score_min``/``score_max`` are fixed, corpus-level constants
    rather than quantiles fit on this drive's own data, the resulting
    ``roughness_score`` is directly comparable across drives.
    """
    df = df.copy()
    df["effective_speed"] = df["speed"].clip(lower=config.min_speed_floor)
    metric = df[config.roughness_metric]
    df["raw_score"] = metric / np.power(df["effective_speed"], config.speed_exponent)

    if config.use_log_transform:
        df["processed_score"] = np.log1p(df["raw_score"])
    else:
        df["processed_score"] = df["raw_score"]

    v_min, v_max = config.score_min, config.score_max
    clipped = df["processed_score"].replace([np.inf, -np.inf], np.nan).clip(lower=v_min, upper=v_max)
    df["roughness_score"] = ((clipped - v_min) / (v_max - v_min)).clip(0, 1)
    return df


def assign_classes(df: pd.DataFrame, config: ClassificationConfig) -> tuple[pd.DataFrame, list[float]]:
    """Assign a :class:`RoadClass` to every row based on `roughness_score`,
    using the fixed thresholds in ``config.score_thresholds`` (0.25 / 0.5 /
    0.75 by default, identical for every drive).

    Returns the annotated dataframe and the thresholds used. Unlike the
    previous per-drive quantile fit, a class that this drive never
    actually reaches simply ends up with zero points — it is not merged
    away or forced to appear.
    """
    df = df.copy()
    thresholds = list(config.score_thresholds)
    bins = [-np.inf, *thresholds, np.inf]
    codes = pd.cut(df["roughness_score"], bins=bins, labels=False, include_lowest=True)
    df["road_class"] = [CLASS_ORDER[c] if pd.notna(c) else None for c in codes]
    return df, thresholds


def _smooth_classes(df: pd.DataFrame, window: int) -> pd.DataFrame:
    if window <= 1 or len(df) < window:
        return df
    df = df.copy()
    codes = df["road_class"].apply(lambda c: CLASS_ORDER.index(c) if c is not None else np.nan)
    smoothed = (
        codes.rolling(window, center=True, min_periods=1)
        .apply(lambda w: pd.Series(w).mode().iloc[0], raw=False)
    )
    df["road_class"] = smoothed.apply(lambda c: CLASS_ORDER[int(c)] if pd.notna(c) else None)
    return df


def compute_score_bands(classified: pd.DataFrame, low_pctl: float = 10, high_pctl: float = 90) -> dict[str, tuple[float, float]]:
    """Empirical [low_pctl, high_pctl] roughness-score band actually
    observed for each class in the *final* classified data (after
    fixed-threshold cutting and smoothing).

    Using the final data rather than the raw fixed thresholds guarantees
    the displayed band for a class always matches the points that were
    actually assigned to it. A class with no points on this drive simply
    has no band (nothing to draw), rather than a synthetic placeholder.
    """
    bands: dict[str, tuple[float, float]] = {}
    valid = classified.dropna(subset=["road_class", "roughness_score"])
    for road_class, group in valid.groupby("road_class", observed=True):
        scores = group["roughness_score"]
        if len(scores) == 0:
            continue
        key = road_class.value if hasattr(road_class, "value") else str(road_class)
        bands[key] = (float(scores.quantile(low_pctl / 100)), float(scores.quantile(high_pctl / 100)))
    return bands


def classify_drive(df: pd.DataFrame, config: ClassificationConfig) -> tuple[pd.DataFrame, list[float]]:
    """Full scoring + classification pipeline for one drive's location-rate
    feature dataframe (must contain `speed` and the configured metric col).
    """
    scored = compute_roughness_score(df, config)
    classified, thresholds = assign_classes(scored, config)
    classified = _smooth_classes(classified, config.smoothing_window)
    return classified, thresholds
