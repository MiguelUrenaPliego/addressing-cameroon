"""Generate the road-quality pipeline figure from a real recorded drive.

Runs the actual IMURoadQuality package (signal_processing + classification)
on one real Fotouni-Douala drive and plots four stages of the pipeline:

  (a) raw 3-axis accelerometer signal and the Gravity-sensor vector, showing
      why the phone's own x/y/z axes cannot be read directly as "vertical"
  (b) the resulting orientation-invariant dynamic vertical acceleration
  (c) the windowed, speed-compensated roughness score against vehicle speed
  (d) the per-drive quantile thresholds cutting that score into 4 classes,
      and the resulting classified road segments along the actual GPS track

Run with the IMURoadQuality project's own virtualenv, which already has
pandas/numpy/matplotlib and the imu_road_quality package installed:

    IMURoadQuality/.venv/bin/python \
        reports/FUASProjektarbeit/scripts/fig_road_quality_pipeline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "IMURoadQuality" / "src"))

from imu_road_quality.classification import (  # noqa: E402
    CLASS_COLORS,
    CLASS_ORDER,
    ClassificationConfig,
    classify_drive,
)
from imu_road_quality.io import load_drive  # noqa: E402
from imu_road_quality.signal_processing import compute_vertical_acceleration, window_features  # noqa: E402

DRIVE_PATH = REPO_ROOT / "data" / "car_cam" / "Fotouni_25_03_2026" / "2026-03-25_15-03-36"
OUT_PATH = REPO_ROOT / "reports" / "FUASProjektarbeit" / "figures" / "fig_road_quality_pipeline.jpg"

# Same fixed, corpus-calibrated configuration used everywhere else in the
# pipeline (experiments/Cameroon/run_all.py): no per-drive tuning, since the
# whole point of the fixed [0, 1] scale is that it doesn't need any.
CONFIG = ClassificationConfig()


def main() -> None:
    print(f"Loading drive from {DRIVE_PATH} ...")
    drive = load_drive(DRIVE_PATH)
    print(f"  accelerometer rows: {len(drive.accelerometer):,}, location fixes: {len(drive.location):,}")

    vert_accel = compute_vertical_acceleration(drive)
    windowed = window_features(vert_accel, drive.location)
    scoring_input = windowed[windowed["speed"] >= 1.0].dropna(subset=["accel_std", "speed"]).copy()
    classified, thresholds = classify_drive(scoring_input, CONFIG)
    # `road_class` holds RoadClass members (a str+Enum hybrid). Pandas'
    # dtype inference on a column mixing that hybrid type with None ends up
    # storing str(member), i.e. "RoadClass.UNPAVED" rather than the enum's
    # own str-value "unpaved", which silently breaks direct `== cls`
    # comparisons. Normalize explicitly to the plain value string once here.
    classified["road_class_str"] = classified["road_class"].apply(lambda c: c.value if c is not None else None)
    print(f"  fitted thresholds: {thresholds}")
    print(f"  class counts:\n{classified['road_class_str'].value_counts()}")

    # A short, representative raw-signal window (30 s) for panels (a)/(b),
    # picked from roughly a third of the way into the drive so it is not the
    # stationary warm-up at the very start.
    t0 = float(drive.accelerometer["seconds_elapsed"].iloc[0])
    t_start = t0 + (drive.accelerometer["seconds_elapsed"].iloc[-1] - t0) * 0.35
    t_end = t_start + 30.0

    accel = drive.accelerometer
    gravity = drive.gravity
    win_accel = accel[(accel["seconds_elapsed"] >= t_start) & (accel["seconds_elapsed"] <= t_end)]
    win_gravity = gravity[(gravity["seconds_elapsed"] >= t_start) & (gravity["seconds_elapsed"] <= t_end)]
    win_vert = vert_accel[(vert_accel["seconds_elapsed"] >= t_start) & (vert_accel["seconds_elapsed"] <= t_end)]

    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    ((ax_a, ax_b), (ax_c, ax_d)) = axes

    # --- Panel (a): raw 3-axis accelerometer + gravity vector -------------
    t_rel = win_accel["seconds_elapsed"] - t_start
    ax_a.plot(t_rel, win_accel["x"], lw=0.6, label="$a_x$", color="#4c72b0")
    ax_a.plot(t_rel, win_accel["y"], lw=0.6, label="$a_y$", color="#55a868")
    ax_a.plot(t_rel, win_accel["z"], lw=0.6, label="$a_z$", color="#c44e52")
    g_rel = win_gravity["seconds_elapsed"] - t_start
    ax_a.plot(g_rel, win_gravity["z"], lw=1.4, color="black", ls="--", label="$g_z$ (Gravity sensor)")
    ax_a.set_title("(a) Raw device-frame accelerometer axes\nand the Gravity sensor's own $z$ component")
    ax_a.set_xlabel("time (s)")
    ax_a.set_ylabel(r"acceleration (m s$^{-2}$)")
    ax_a.legend(loc="upper right", fontsize=8, ncol=2)
    ax_a.annotate(
        "none of $a_x,a_y,a_z$ tracks\n\"down\" on its own:\nmount orientation is arbitrary",
        xy=(0.03, 0.04), xycoords="axes fraction", fontsize=7.5, style="italic", color="#555555",
    )

    # --- Panel (b): orientation-invariant dynamic vertical acceleration ---
    t_rel_v = win_vert["seconds_elapsed"] - t_start
    ax_b.plot(t_rel_v, win_vert["vert_accel_dynamic"], lw=0.7, color="#8172b2")
    ax_b.axhline(0, color="black", lw=0.6)
    ax_b.set_title(r"(b) $a_{\mathrm{vertical}} = (\mathbf{a}\cdot\mathbf{g})/|\mathbf{g}| - |\mathbf{g}|$" "\n(gravity-projected, zero-mean)")
    ax_b.set_xlabel("time (s)")
    ax_b.set_ylabel(r"vertical accel. (m s$^{-2}$)")

    # --- Panel (c): windowed, speed-compensated roughness score vs speed --
    w = classified.sort_values("seconds_elapsed")
    t_full = (w["seconds_elapsed"] - t0) / 60.0
    ax_c2 = ax_c.twinx()
    ax_c2.fill_between(t_full, w["speed"] * 3.6, color="#cccccc", alpha=0.5, step="mid", lw=0)
    ax_c2.set_ylabel("speed (km/h)", color="#888888")
    ax_c2.tick_params(axis="y", colors="#888888")
    # twinx() stacks the new axes on top of ax_c, which would otherwise hide
    # the scatter plotted on ax_c below the opaque speed fill.
    ax_c.set_zorder(ax_c2.get_zorder() + 1)
    ax_c.patch.set_visible(False)
    for cls in CLASS_ORDER:
        mask = w["road_class_str"] == cls.value
        ax_c.scatter(t_full[mask], w.loc[mask, "roughness_score"], s=3, color=CLASS_COLORS[cls], label=cls.value)
    for thr in thresholds:
        ax_c.axhline(thr, color="black", lw=0.5, ls=":")
    ax_c.set_title("(c) Speed-compensated roughness score over the full drive\n(color = assigned class, dotted lines = fixed 0.25/0.5/0.75 thresholds)")
    ax_c.set_xlabel("time (min)")
    ax_c.set_ylabel("roughness score [0, 1]")
    ax_c.legend(loc="upper left", fontsize=7, ncol=2, markerscale=2)

    # --- Panel (d): classified track in physical space (x = distance) -----
    lat0, lon0 = w["latitude"].iloc[0], w["longitude"].iloc[0]
    R = 111_320.0
    x_m = (w["longitude"] - lon0) * R * np.cos(np.radians(lat0))
    y_m = (w["latitude"] - lat0) * R
    dist_m = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x_m), np.diff(y_m)))])
    for cls in CLASS_ORDER:
        mask = (w["road_class_str"] == cls.value).to_numpy()
        if mask.sum() == 0:
            continue
        ax_d.scatter(x_m[mask], y_m[mask], s=10, color=CLASS_COLORS[cls], label=cls.value)
    ax_d.set_aspect("equal")
    ax_d.set_title(f"(d) Classified GPS track ({dist_m[-1]/1000:.1f} km driven)")
    ax_d.set_xlabel("east-west (m)")
    ax_d.set_ylabel("north-south (m)")
    if ax_d.get_legend_handles_labels()[0]:
        ax_d.legend(loc="best", fontsize=7, markerscale=1.5)

    fig.suptitle(
        f"Road-quality classification pipeline on a real recorded drive\n"
        f"({DRIVE_PATH.parent.name}/{DRIVE_PATH.name}, {len(drive.accelerometer):,} accelerometer samples, "
        f"{len(classified):,} classified GPS-rate points)",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=180, pil_kwargs={"quality": 92})
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
