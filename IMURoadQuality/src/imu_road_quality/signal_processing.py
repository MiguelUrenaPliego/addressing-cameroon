"""Signal processing: orientation-invariant vertical acceleration extraction
and alignment of high-rate IMU data onto low-rate GPS location fixes.

Method: the phone's mounting orientation inside the car is arbitrary and can
drift during a drive, so raw accelerometer x/y/z axes are not directly
usable as "vertical" acceleration. Instead we project the (gravity-removed)
acceleration vector onto the instantaneous gravity vector reported by the
Android "Gravity" sensor (a low-pass-filtered estimate of the gravity
direction in device coordinates). This is the standard trick used in the
smartphone pavement-roughness literature to obtain a device-orientation
independent estimate of vertical (quarter-car "z-axis") acceleration, see
``reports/references.md`` items on Z-axis-accelerometer IRI estimation.

    a_vertical = (a . g) / |g|

where ``a`` is raw accelerometer output (m/s^2, includes gravity) and ``g``
is the Gravity sensor vector. This is equivalent to the scalar projection
used in bumpiness_example.py, kept here as the vetted starting point.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from imu_road_quality.io import DriveData


def compute_vertical_acceleration(drive: DriveData) -> pd.DataFrame:
    """Merge Accelerometer + Gravity streams and project onto gravity.

    Returns a dataframe indexed by ``seconds_elapsed`` with columns
    ``vert_accel`` (m/s^2, gravity-inclusive component along "down") and
    ``lateral_accel_mag`` (magnitude of the acceleration component
    orthogonal to gravity, useful as an auxiliary roughness/cornering cue).
    """
    accel = drive.accelerometer.sort_values("seconds_elapsed")
    gravity = drive.gravity.sort_values("seconds_elapsed")

    imu = pd.merge_asof(
        accel,
        gravity,
        on="seconds_elapsed",
        suffixes=("", "_g"),
        direction="nearest",
        tolerance=0.1,
    ).dropna(subset=["x_g", "y_g", "z_g"])

    dot = imu["x"] * imu["x_g"] + imu["y"] * imu["y_g"] + imu["z"] * imu["z_g"]
    grav_mag = np.sqrt(imu["x_g"] ** 2 + imu["y_g"] ** 2 + imu["z_g"] ** 2)
    imu["vert_accel"] = dot / grav_mag

    # Component of acceleration orthogonal to gravity (in-plane / lateral+longitudinal)
    accel_mag_sq = imu["x"] ** 2 + imu["y"] ** 2 + imu["z"] ** 2
    imu["lateral_accel_mag"] = np.sqrt(np.clip(accel_mag_sq - imu["vert_accel"] ** 2, 0, None))

    # vert_accel includes the ~9.81 m/s^2 gravity offset along "down"; remove
    # it so the signal oscillates around zero and its magnitude reflects
    # dynamic (bump-induced) motion only.
    imu["vert_accel_dynamic"] = imu["vert_accel"] - grav_mag

    return imu[["seconds_elapsed", "vert_accel", "vert_accel_dynamic", "lateral_accel_mag"]]


def window_features(
    vert_accel_df: pd.DataFrame,
    location: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate the high-rate vertical-acceleration signal into windows
    aligned with each Location fix (1 window per GPS sample, matching the
    ~1-2 Hz GPS rate of the recordings).

    For each window we compute the standard deviation and the RMS of the
    dynamic vertical acceleration (two common vibration-energy proxies used
    in pavement roughness studies) plus the peak-to-peak amplitude.
    """
    location = location.sort_values("seconds_elapsed").reset_index(drop=True)
    loc_times = location["seconds_elapsed"].to_numpy()

    df = vert_accel_df.copy()
    df["time_bin"] = pd.cut(df["seconds_elapsed"], bins=loc_times, labels=loc_times[:-1], include_lowest=True)

    def _agg(group: pd.DataFrame) -> pd.Series:
        v = group["vert_accel_dynamic"].to_numpy()
        lat = group["lateral_accel_mag"].to_numpy()
        if len(v) == 0:
            return pd.Series(
                {
                    "accel_std": np.nan,
                    "accel_rms": np.nan,
                    "accel_ptp": np.nan,
                    "lateral_accel_mean": np.nan,
                    "n_samples": 0,
                }
            )
        return pd.Series(
            {
                "accel_std": float(np.std(v)),
                "accel_rms": float(np.sqrt(np.mean(v**2))),
                "accel_ptp": float(np.ptp(v)),
                "lateral_accel_mean": float(np.mean(lat)),
                "n_samples": len(v),
            }
        )

    stats = df.groupby("time_bin", observed=False).apply(_agg, include_groups=False)
    stats.index = stats.index.astype(float)
    stats.index.name = "seconds_elapsed"

    out = location.merge(stats, on="seconds_elapsed", how="left")
    return out
