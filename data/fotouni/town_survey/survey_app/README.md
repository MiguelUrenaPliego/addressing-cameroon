# survey_app

Raw data from the EFYL survey app.

- `exports/efyl_admin_export_YYYYMMDD_HHMMSS.csv` – one admin export per survey day. **Each export is cumulative**: it contains that day's records plus all earlier ones (the later file is a superset by `address_id`; one address can have several rows). Use the latest, de-duplicated.
- `exports/efyl_admin_export_ENRICHI_20260820_162251.csv` – a hand-enriched copy of the 20 Aug export.
- `EFYL_Questionnaires_Complets_EN.pdf` – the full questionnaires.
- `fotouni.zip` – snapshot (25 Sep 2026) of the whole town-survey folder before reorganisation.
