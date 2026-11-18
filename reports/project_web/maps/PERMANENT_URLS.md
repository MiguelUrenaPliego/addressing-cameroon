# Permanent map URLs — do not move or rename

These addresses are linked from papers, slides, e-mails and other sites. They must keep working.
Base: `https://miguelurenapliego.github.io/addressing-cameroon/`

| Map | Permanent URL (after the base) | Real location in the repo |
|---|---|---|
| IMU road quality | `reports/project_web/maps/imu/index.html` | same |
| Proximity | `reports/project_web/maps/proximity/index.html` | same |
| Building survey | `data/survey_maps_index.html` | same |
| Numbering demo | `reports/project_web/addressing-system/numbering-demo.html` | same |

Old addresses that are kept alive by small redirect files (do not delete them):

| Old URL | Kept by |
|---|---|
| `IMURoadQuality/experiments/index.html` | `IMURoadQuality/experiments/index.html` (redirect) |
| `proximity/index.html` | `proximity/index.html` (redirect) |
| anything under `documents/...` | `404.html` at the repo root (rewrites `/documents/` to `/reports/`) |

## Rules
- Do not rename or move the folders `reports/`, `reports/project_web/`, `reports/project_web/maps/` (`imu`, `proximity`), `data/` or the files in the tables above.
- Do not change what these maps show in a way that breaks links to them: the page must stay at the same address.
- If a map really has to move, leave a redirect file (`<meta http-equiv="refresh" ...>`) at the old address and add it to the tables above.
- `sync_maps.py` copies the maps into `reports/project_web/maps/`; keep its target folders as they are.
- Before a release, check each URL above returns 200 (`curl -sI <url>`).
