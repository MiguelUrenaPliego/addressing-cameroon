# code/

Every sub-folder is its own git repo (`OSMAddressSystem` is a submodule of the workspace repo). Each repo is self-contained: data it needs is mirrored inside it, and external libraries are vendored or declared as dependencies.

| Folder | Kind | Purpose |
|---|---|---|
| `OSMAddressSystem/` | Python package | Street identities, path numbering, naming plan, street signs, street editor, experiments for Bandja (Fotouni) and Douala, and the **community address app** (`osmaddresssystem/app`, project `projects/fotouni`). |
| `IMURoadQuality/` | Python package | Classify road roughness (paved/unpaved/bad/very bad) from phone IMU + GPS drives. |
| `proximity/` | Python project | 15-minute-city / walk-accessibility maps for Douala and Fotouni (vendors `UrbanAccessAnalyzer`). |
| `building_survey/` | Scripts (not a package) | Build the pilot building-survey datasets (RTK + phone) and their survey maps. |
| `fotouni_town_qc/` | Scripts (not a package) | Quality control of the Fotouni town phone survey and its report. |
| `gps/` | Tooling | u-blox / ArduSimple RTK base-station scripts, ESP32 firmware, RTK2go NTRIP client. |
