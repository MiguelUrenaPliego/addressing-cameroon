# IMU Road Quality

Classify road bumpiness from smartphone IMU + GPS recordings ("Sensor
Logger"-style exports: `Accelerometer.csv`, `Gravity.csv`, `Location.csv`,
etc.) into 4 classes: **paved**, **unpaved**, **bad**, **very_bad**.

## Roughness score and class boundaries

Every drive is scored onto the **same fixed scale**: a `roughness_score`
normalized to `[0, 1]`, cut into 4 classes at **fixed, identical
thresholds on every drive**:

| Class | Roughness score |
|---|---|
| paved | 0.00 – 0.25 |
| unpaved | 0.25 – 0.50 |
| bad | 0.50 – 0.75 |
| very_bad | 0.75 – 1.00 |

Phone model, mount, and vehicle differ between recordings, so the score
is normalized against **fixed calibration constants**
(`ClassificationConfig.score_min` / `score_max`) fit once on the pooled
distribution across the project's dataset, rather than against each
drive's own distribution. This means the classes above always mean the
same severity, and a drive that never gets rough simply ends up with
zero (or very few) points in the higher classes — nothing is forced or
declared absent. See `reports/report.md` (sections 2.5-2.6 and 3.1) for
the full derivation and its limitations.

## Package layout

```
src/imu_road_quality/
    io.py                load a drive's raw CSV streams
    signal_processing.py orientation-invariant vertical acceleration + windowing
    classification.py    speed-compensated roughness score on a fixed [0,1]
                          scale + fixed-threshold classification
    segments.py          build map-able polyline segments from classified points
    mapping.py            folium map with per-class colors and click-to-chart popups
    pipeline.py           orchestrates the steps above end-to-end
experiments/Cameroon/
    run_all.py            runs the pipeline on every drive in data/car_cam
    outputs/               generated folium maps + classified CSVs (git-ignored)
notebooks/
    fotouni_douala_example.ipynb  worked example on one full drive (all 4 classes)
reports/
    report.md              methodology write-up, hyperparameters, references
```

## Usage

```bash
uv run experiments/Cameroon/run_all.py
```

or programmatically:

```python
from imu_road_quality.pipeline import RoadQualityPipeline

result = RoadQualityPipeline().run("data/car_cam/.../2026-03-25_09-32-13")
result.fmap.save("road_quality_map.html")
print(result.classified["road_class"].value_counts())
```

Every drive uses the same `ClassificationConfig` defaults — no per-drive
calibration is required. See `notebooks/fotouni_douala_example.ipynb` for
a fully worked example, and `reports/report.md` for the method and
hyperparameter reference.
