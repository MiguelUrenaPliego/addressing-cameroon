"""Loading of raw Sensor Logger (Android) recordings for a single drive.

Each drive is a folder containing exports produced by the "Sensor Logger"
style app referenced in ``data/car_cam/context.txt``.

Supported formats:
- Parquet (preferred if available)
- CSV (fallback)

Expected streams:
Accelerometer, Gravity, Location
and optionally:
AccelerometerUncalibrated, Gyroscope, GyroscopeUncalibrated,
Orientation, TotalAcceleration, Annotation.

All streams share a ``time`` (nanoseconds since epoch) and
``seconds_elapsed`` (seconds since recording start) column, which is used
to align streams sampled at different rates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


REQUIRED_FILES = (
    "Accelerometer.csv",
    "Gravity.csv",
    "Location.csv",
)

OPTIONAL_FILES = (
    "AccelerometerUncalibrated.csv",
    "Gyroscope.csv",
    "GyroscopeUncalibrated.csv",
    "Orientation.csv",
    "TotalAcceleration.csv",
    "Annotation.csv",
)


@dataclass
class DriveData:
    """Container for all raw streams belonging to a single drive/recording."""

    name: str
    path: Path
    accelerometer: pd.DataFrame
    gravity: pd.DataFrame
    location: pd.DataFrame
    gyroscope: pd.DataFrame | None = None
    orientation: pd.DataFrame | None = None
    total_acceleration: pd.DataFrame | None = None
    annotation: pd.DataFrame | None = None
    extra: dict[str, pd.DataFrame] = field(default_factory=dict)


def _read_table(path: Path) -> pd.DataFrame:
    """Read a sensor export.

    Parquet is preferred because it is smaller and faster.
    CSV is used as a fallback for compatibility.
    """

    parquet_path = path.with_suffix(".parquet")

    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)

    elif path.exists():
        df = pd.read_csv(path)

    else:
        raise FileNotFoundError(
            f"Missing both {path.name} and {parquet_path.name} in {path.parent}"
        )

    if "seconds_elapsed" in df.columns:
        df = (
            df.sort_values("seconds_elapsed")
            .reset_index(drop=True)
        )

    return df


def find_drive_folders(root: Path) -> list[Path]:
    """Recursively find every folder that looks like a drive.

    A folder is considered a drive if it contains Location and
    Accelerometer exports (CSV or Parquet).
    """

    root = Path(root)
    drives = []

    for loc_csv in sorted(root.rglob("Location.csv")):
        folder = loc_csv.parent

        if (
            (folder / "Accelerometer.csv").exists()
            or (folder / "Accelerometer.parquet").exists()
        ):
            drives.append(folder)

    # Also detect folders that only contain parquet files
    for loc_parquet in sorted(root.rglob("Location.parquet")):
        folder = loc_parquet.parent

        if (
            (folder / "Accelerometer.csv").exists()
            or (folder / "Accelerometer.parquet").exists()
        ):
            if folder not in drives:
                drives.append(folder)

    return drives


def load_drive(
    path: str | Path,
    name: str | None = None,
    load_optional: bool = False,
) -> DriveData:
    """Load a drive folder.

    Required streams:
        - Accelerometer
        - Gravity
        - Location

    Parquet files are preferred over CSV files.

    Optional streams are not loaded by default because some exports contain
    millions of rows and are not needed by the roughness scoring pipeline.
    """

    path = Path(path)

    missing = [
        f
        for f in REQUIRED_FILES
        if not (
            (path / f).exists()
            or (path / f).with_suffix(".parquet").exists()
        )
    ]

    if missing:
        raise FileNotFoundError(
            f"Drive at {path} is missing required files: {missing}"
        )

    accel = _read_table(path / "Accelerometer.csv")
    gravity = _read_table(path / "Gravity.csv")
    location = _read_table(path / "Location.csv")

    kwargs: dict[str, pd.DataFrame] = {}
    extra: dict[str, pd.DataFrame] = {}

    files_to_load = (
        OPTIONAL_FILES
        if load_optional
        else ("Annotation.csv",)
    )

    for fname in files_to_load:

        fpath = path / fname

        parquet_path = fpath.with_suffix(".parquet")

        if not fpath.exists() and not parquet_path.exists():
            continue

        try:
            df = _read_table(fpath)

        except pd.errors.EmptyDataError:
            continue

        if df.empty:
            continue

        key = fname.replace(".csv", "")

        if key == "Gyroscope":
            kwargs["gyroscope"] = df

        elif key == "Orientation":
            kwargs["orientation"] = df

        elif key == "TotalAcceleration":
            kwargs["total_acceleration"] = df

        elif key == "Annotation":
            kwargs["annotation"] = df

        else:
            extra[key] = df

    return DriveData(
        name=name or path.name,
        path=path,
        accelerometer=accel,
        gravity=gravity,
        location=location,
        extra=extra,
        **kwargs,
    )