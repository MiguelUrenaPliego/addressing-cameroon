"""Run the IMU road-quality pipeline on every drive under
``data/car_cam`` and write one folium HTML map + one CSV of classified
points per drive into ``experiments/Cameroon/outputs/<drive_name>/``.

Every drive uses the same fixed, corpus-calibrated `DEFAULT_CONFIG`
(see `imu_road_quality.classification`): road-class boundaries are now
fixed absolute cut points (0.25 / 0.5 / 0.75) on a normalized [0, 1]
score, not fit per drive, so no per-drive override table is needed
anymore. A drive that never gets rough simply ends up with zero
"bad"/"very_bad" points, which is the correct, expected behaviour.

Usage:
    uv run experiments/Cameroon/run_all.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from imu_road_quality.classification import ClassificationConfig, RoadClass
from imu_road_quality.io import find_drive_folders
from imu_road_quality.pipeline import PipelineConfig, RoadQualityPipeline

DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "car_cam"
OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs"

DEFAULT_CONFIG = ClassificationConfig()

# Drives to exclude entirely, keyed by the drive folder's relative path
# under data/car_cam. Reasons, from manual inspection of each drive's map:
SKIP_DRIVES: dict[str, str] = {
    # Data-quality failures (pipeline already raises "No segments to plot"
    # for these; listed explicitly so the reason is documented):
    "Douala_Fotouni_23_03_2026/2026-03-23_09-30-35": "accelerometer sampled at ~1.5 Hz, almost no usable IMU data",
    "Fotouni_24_03_2026/2026-03-24_09-37-42": "car essentially stationary for the whole recording",
    # Confirmed by inspection: too short / erratic GPS shape to be a
    # meaningful road-quality trace.
    "Douala_Fotouni_23_03_2026/2026-03-23_13-09-34": "very short recording, weird/erratic GPS shape",
    "Fotouni_24_03_2026/2026-03-24_12-22-29": "very short recording, weird/erratic GPS shape",
    "taxi_sat_21_03_2026/IMU": "very short recording, weird/erratic GPS shape",
    # Not a car drive.
    "Fotouni_25_03_2026/2026-03-25_15-28-24": "this is a walking trip, not a car drive",
    "Fotouni_25_03_2026/2026-03-25_15-33-02": "this is a walking trip, not a car drive",
}


def drive_key(folder: Path) -> str:
    return str(folder.relative_to(DATA_ROOT))


def main() -> None:
    drive_folders = find_drive_folders(DATA_ROOT)
    print(f"Found {len(drive_folders)} drives under {DATA_ROOT}")

    summary_rows = []
    for folder in drive_folders:
        key = drive_key(folder)
        if key in SKIP_DRIVES:
            print(f"\n=== {key} ===\n  SKIPPED: {SKIP_DRIVES[key]}")
            continue
        name = key.replace("/", "__")
        pipeline = RoadQualityPipeline(PipelineConfig(classification=DEFAULT_CONFIG))

        print(f"\n=== {key} ===")
        try:
            result = pipeline.run(folder, drive_name=key)
        except Exception as exc:  # noqa: BLE001 - report and continue with other drives
            print(f"  FAILED: {exc}")
            continue

        out_dir = OUTPUT_ROOT / name
        out_dir.mkdir(parents=True, exist_ok=True)

        map_path = out_dir / "road_quality_map.html"
        result.fmap.save(str(map_path))

        csv_path = out_dir / "classified_points.csv"
        result.classified.to_csv(csv_path, index=False)

        counts = result.classified["road_class"].value_counts()
        for rc in RoadClass:
            summary_rows.append(
                {
                    "drive": key,
                    "road_class": rc.value,
                    "n_points": int(counts.get(rc, 0)),
                }
            )

        print(f"  points classified: {len(result.classified)}")
        print(f"  thresholds: {[round(t, 3) for t in result.thresholds]}")
        print(f"  class counts:\n{counts.to_string()}")
        print(f"  map saved to {map_path}")

    import pandas as pd

    summary_df = pd.DataFrame(summary_rows)
    summary_path = OUTPUT_ROOT / "summary.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSummary written to {summary_path}")

    sys.path.insert(0, str(OUTPUT_ROOT.parents[1]))
    from build_index import main as build_index_main  # noqa: E402

    build_index_main()


if __name__ == "__main__":
    main()
