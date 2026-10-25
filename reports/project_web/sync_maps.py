#!/usr/bin/env python3
"""Copy the interactive maps published on the website into reports/project_web/maps/.

The website (this repo) must work from a fresh clone, but the maps are
produced by separate code repos (code/IMURoadQuality, code/proximity) that are
not submodules of this repo. This script copies the finished, self-contained
HTML maps here so the pages can embed them with plain relative links.

Not copied: the OSMAddressSystem maps (code/OSMAddressSystem is a submodule of
this repo) and the building-survey maps (tracked under data/*/pilot_survey/
and indexed by data/survey_maps_index.html).

Usage:
    python3 reports/project_web/sync_maps.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = Path(__file__).resolve().parent / "maps"

IMU = ROOT / "code" / "IMURoadQuality" / "experiments"
PROX = ROOT / "code" / "proximity"


def copy(src: Path, dst: Path) -> None:
    """Copy one file, creating parent folders.

    Args:
        src: File to copy.
        dst: Destination path.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"{src.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")


def main() -> None:
    """Refresh the copied IMU road-quality and proximity maps."""
    copy(IMU / "index.html", DEST / "imu" / "index.html")
    for m in sorted(IMU.glob("Cameroon/outputs/*/road_quality_map.html")):
        copy(m, DEST / "imu" / m.relative_to(IMU))
    copy(PROX / "index.html", DEST / "proximity" / "index.html")
    for m in sorted(PROX.glob("*/map.html")):
        copy(m, DEST / "proximity" / m.relative_to(PROX))


if __name__ == "__main__":
    main()
