"""Generate a schematic figure explaining the 20 meter house numbering
rule in plain terms: a street is divided into 20 meter steps from its
start, the right side gets odd numbers, the left side gets even numbers,
and more than one building in the same step shares a number with a
letter suffix (3A, 3B, 3C).

Standalone, no dependency on the OSMAddressSystem package. Run with any
Python environment that has matplotlib and numpy:

    python3 reports/project_web/scripts/fig_numbering_scheme.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrow

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = REPO_ROOT / "reports" / "project_web" / "figures" / "fig_numbering_scheme.jpg"

STEP_M = 20.0
STREET_LENGTH_M = 100.0

# (distance_along_street_m, side) for a handful of synthetic buildings.
# side: +1 = right (odd numbers), -1 = left (even numbers)
BUILDINGS = [
    (4, +1), (9, +1), (16, +1),
    (7, -1),
    (24, +1),
    (33, -1), (37, -1),
    (48, +1),
    (63, -1),
    (71, +1), (74, +1),
    (92, -1),
]


def house_number(distance_m: float, side: int) -> tuple[int, int]:
    """Return (base_number, index_within_step) for a building at this
    distance and side, using the same even-right, odd-left convention
    and 20 meter step as numbering.assign_house_numbers."""
    step_index = int(distance_m // STEP_M)
    base = 2 * step_index + (1 if side > 0 else 2)
    return base, step_index


def main() -> None:
    fig, ax = plt.subplots(figsize=(11, 5.2))

    # Street centerline
    ax.plot([0, STREET_LENGTH_M], [0, 0], color="#444444", lw=5, solid_capstyle="round", zorder=2)
    ax.annotate(
        "", xy=(STREET_LENGTH_M + 6, 0), xytext=(-4, 0),
        arrowprops=dict(arrowstyle="-|>", color="#444444", lw=1.8),
    )
    ax.text(-4, 8, "street start\n(an intersection)", fontsize=9, ha="left", color="#444444")

    # 20 m step boundaries, alternating light bands to make the steps legible
    n_steps = int(np.ceil(STREET_LENGTH_M / STEP_M))
    for i in range(n_steps):
        x0 = i * STEP_M
        x1 = min((i + 1) * STEP_M, STREET_LENGTH_M)
        if i % 2 == 0:
            ax.axvspan(x0, x1, color="#f0ede2", zorder=0)
        ax.axvline(x0, color="#c9c2ad", lw=1, ls="--", zorder=1)
        ax.text((x0 + x1) / 2, -34, f"{int(x0)}–{int(x1)} m", ha="center", fontsize=8, color="#7a7460")
    ax.axvline(STREET_LENGTH_M, color="#c9c2ad", lw=1, ls="--", zorder=1)

    ax.text(STREET_LENGTH_M / 2, 30, "RIGHT side: odd numbers (1, 3, 5, ...)", ha="center", fontsize=10, color="#2f6f4f", fontweight="bold")
    ax.text(STREET_LENGTH_M / 2, -46, "LEFT side: even numbers (2, 4, 6, ...)", ha="center", fontsize=10, color="#b5651d", fontweight="bold")

    # Buildings, grouped by (base_number) to assign letter suffixes to
    # buildings sharing the same 20 m step and side.
    counts: dict[tuple[int, int], int] = {}
    for distance_m, side in BUILDINGS:
        base, step_index = house_number(distance_m, side)
        counts[(base, side)] = counts.get((base, side), 0) + 1

    seen: dict[tuple[int, int], int] = {}
    for distance_m, side in BUILDINGS:
        base, step_index = house_number(distance_m, side)
        y = 14 if side > 0 else -14
        color = "#2f6f4f" if side > 0 else "#b5651d"
        ax.plot([distance_m, distance_m], [0, y], color=color, lw=1.2, zorder=3)
        ax.scatter([distance_m], [y], color=color, s=90, zorder=4, edgecolor="white", linewidth=1)
        total_here = counts[(base, side)]
        if total_here > 1:
            seen[(base, side)] = seen.get((base, side), 0) + 1
            letter = chr(ord("A") + seen[(base, side)] - 1)
            label = f"{base}{letter}"
        else:
            label = str(base)
        ax.annotate(label, (distance_m, y), fontsize=9, fontweight="bold",
                    ha="center", va="bottom" if side > 0 else "top",
                    xytext=(0, 6 if side > 0 else -6), textcoords="offset points")

    ax.set_xlim(-10, STREET_LENGTH_M + 10)
    ax.set_ylim(-58, 42)
    ax.axis("off")
    ax.set_title(
        "House numbering along a street: distance from the start is divided into\n"
        f"{int(STEP_M)} meter steps, giving one odd number per step on the right and one even number\n"
        "on the left. Two or more buildings in the same step share the number, told apart by a letter (3A, 3B, 3C).",
        fontsize=10.5,
    )

    fig.tight_layout()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=180, pil_kwargs={"quality": 92})
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
