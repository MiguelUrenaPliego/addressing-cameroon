# Bibliography

Shared knowledge for every report in `../reports/`.

- `references.bib` – the **master** BibTeX file. Add/fix entries here first.
- `papers/` – downloaded paper PDFs and notes on papers (`paywalled_papers.md`: cited but not yet read).
- `journals/` – notes on target journals and APC/open-access agreements.
- `references_fuas_notes.md` – per-key summary of what each reference justifies in the FUAS project report.
- `sync_references.py` – rewrites each `reports/*/references.bib` as the subset of the master that report cites
  (`python3 bibliography/sync_references.py`, or `--check` to only report drift).

Rule: a report's local `references.bib` is always a partial duplicate of the master; never edit it directly.
Any other markdown needed to write a report (reading notes, journal research) lives here, not in the report folder.
