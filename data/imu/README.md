# data/imu

Phone IMU + GPS recordings made from a car, 21-26 March 2026 ("Sensor Logger" CSV exports: `Location`, `Accelerometer`, `Gravity`, `Gyroscope`, `Orientation`, ...). Each drive folder is named `<where>_<date>/<start-time>`.

- `taxi_sat_21_03_2026/`, `Douala_Fotouni_23_03_2026/`, `Fotouni_24_03_2026/` – early tests (mostly excluded as unusable).
- `Fotouni_25_03_2026/`, `Fotouni_Douala_26_03_2026/` – the drives used for the road-quality analysis.
- `context.txt` – notes on the file format.

Photos/videos (`DCIM`, `camera`) are included in some folders. A parquet copy of the 3 streams the analysis needs is stored in `code/IMURoadQuality/experiments/Cameroon/data`.
