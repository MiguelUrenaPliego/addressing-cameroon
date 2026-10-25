# osm

Data downloaded from OpenStreetMap (Overpass API, 2026-10-18) for the **Bandja** commune (OSM relation 3837548, admin_level 8), which contains Fotouni. All 2,127 usable survey buildings lie inside it.

- `bandja_highways.osm` - every `highway` way in the commune with its nodes (input of OSMAddressSystem / osmnx). Query and server in `DOWNLOAD_INFO.txt`.
- `overpass_places.json` - `place=*` objects (Fotouni appears only as a village node, id 2212936539).
- `overpass_bounds.json` - boundaries/chiefdom objects inside Bandja (only the commune itself exists; there are no OSM boundaries for Fotouni or its quarters).

OSM has almost no street names here (16 of ~3,100 highway ways in the surrounding box are named).
