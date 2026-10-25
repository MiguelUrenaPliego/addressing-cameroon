#!/usr/bin/env python3
"""Keep each report's references.bib a partial duplicate of bibliography/references.bib.

bibliography/references.bib is the single master list. Every LaTeX report
folder under reports/ carries its own references.bib containing only the
entries that report cites (LaTeX needs a local file). Edit the master, then:

    python3 bibliography/sync_references.py          # rewrite report bibs
    python3 bibliography/sync_references.py --check  # report drift only

Cited keys are read from \\cite-style commands in the report's .tex files.
A report-local entry missing from the master is reported (never silently
dropped); add it to the master first.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "bibliography" / "references.bib"
CITE = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")


def parse(text):
    entries, i = {}, 0
    while True:
        i = text.find("@", i)
        if i < 0:
            return entries
        m = re.match(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text[i:])
        if not m:
            i += 1
            continue
        depth, j = 0, i + text[i:].index("{")
        for k in range(j, len(text)):
            depth += (text[k] == "{") - (text[k] == "}")
            if depth == 0:
                break
        entries[m.group(2)] = text[i:k + 1]
        i = k + 1


def main():
    check = "--check" in sys.argv
    master = parse(MASTER.read_text())
    status = 0
    for bib in sorted((ROOT / "reports").glob("*/**/references.bib")):
        folder = bib.parent
        cited = set()
        for tex in folder.rglob("*.tex"):
            for grp in CITE.findall(tex.read_text(errors="ignore")):
                cited.update(k.strip() for k in grp.split(","))
        local = parse(bib.read_text())
        missing = sorted(k for k in cited if k not in master)
        stale = sorted(k for k in local if k in master and local[k] != master[k])
        orphan = sorted(k for k in local if k not in master)
        print(f"{bib.relative_to(ROOT)}: {len(cited)} cited, "
              f"missing from master {missing}, differs {stale}, local-only {orphan}")
        if missing or orphan or stale:
            status = 1
        if not check and not missing:
            body = "\n\n".join(master[k] for k in master if k in cited)
            bib.write_text(body + "\n")
    sys.exit(status)


if __name__ == "__main__":
    main()
