# fotouni/pilot_survey

Pilot study in Fotouni (Bandja area). Surveyors walked with RTK receivers and phones running SW Maps.

- `raw/` – one folder per surveyor/device (`.swm2` project, `.xlsx`, `Media/`) plus `fotouni_rtk/` (RTK unit) and `Fotouni phone.xlsx`. Not tracked in git.
- `fotouni.csv` – merged dataset built by `code/building_survey/build_fotouni.py`.
- `images/` – survey photos referenced by the CSV.
- `streets.gpkg`, `survey_map.html` – street network and interactive map.

Methodology: `../../building_datasets.md`.
