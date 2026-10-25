# Addressing Cameroon

Research workspace for the street-addressing / census-data project piloted in Fotouni (West Region) and Bonamoussadi (Douala), Cameroon.

| Folder | What it holds |
|---|---|
| [`code/`](code/README.md) | One folder per git repo: the Python packages (street numbering, IMU road quality, proximity) and the non-package tools (pilot-survey builders, Fotouni town-survey QC, GPS/RTK tooling). |
| [`data/`](data/README.md) | All collected data, organised by place (`douala`, `fotouni`) and by campaign (`pilot_survey`, `town_survey`), plus `imu` (phone IMU road tests). |
| [`reports/`](reports/README.md) | Papers, project reports, presentations, the public website (`project_web`) and trip material. |
| [`bibliography/`](bibliography/README.md) | Master `references.bib`, paper PDFs and reading notes shared by every report. |

The three project strands:

1. **Pilot study** (Douala + Fotouni): building survey with RTK GNSS and phones. Data in `data/*/pilot_survey`.
2. **IMU road-quality tests** with a mobile phone in a car. Data in `data/imu`.
3. **Fotouni town survey**: complete survey of the town using only the phone app. Data in `data/fotouni/town_survey`.

## Repos

This repo (website in `reports/project_web`, built pilot-survey outputs in `data/`) tracks `code/OSMAddressSystem` as a submodule. The other `code/*` folders are separate git repos that must each stay self-contained (they carry copies of the data they need). They are git-ignored here until they have a remote and are added with `git submodule add`. Maps shown on the website are copied into `reports/project_web/maps/` by `reports/project_web/sync_maps.py`.

Utility scripts at the root: `check_large_files.py`, `compress_files.py`, `uncompress_files.py` (keep large files out of git).
