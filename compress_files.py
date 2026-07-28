#!/usr/bin/env python3

import os
import re
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent

MB = 1024 * 1024
CONVERT_THRESHOLD = 50 * MB


def _glob_to_regex(pattern: str) -> str:
    i, n = 0, len(pattern)
    out = []

    while i < n:
        c = pattern[i]

        if c == "*":
            if pattern[i:i+3] == "**/":
                out.append("(?:.*/)?")
                i += 3
            elif pattern[i:i+2] == "**":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1

        elif c == "?":
            out.append("[^/]")
            i += 1

        else:
            out.append(re.escape(c))
            i += 1

    return "".join(out)


def _parse_gitignore(gitignore_path: Path):
    patterns = []

    try:
        lines = gitignore_path.read_text(
            encoding="utf-8",
            errors="ignore"
        ).splitlines()
    except OSError:
        return patterns

    for raw in lines:
        line = raw.rstrip()

        if not line or line.startswith("#"):
            continue

        negate = line.startswith("!")
        if negate:
            line = line[1:]

        dir_only = line.endswith("/")
        if dir_only:
            line = line[:-1]

        anchored = line.startswith("/") or "/" in line
        line = line.lstrip("/")

        prefix = "" if anchored else "(?:.*/)?"

        regex = re.compile(
            "^" + prefix + _glob_to_regex(line) + "$"
        )

        patterns.append((regex, negate, dir_only))

    return patterns


def convert_csv(path: Path):

    output = path.with_suffix(".parquet")

    if output.exists():
        print(f"SKIP existing: {output}")
        return

    print(f"Converting: {path}")

    try:
        df = pd.read_csv(path)

        df.to_parquet(
            output,
            engine="pyarrow",
            compression="zstd",
            compression_level=22,
            index=False
        )

        original = path.stat().st_size / MB
        converted = output.stat().st_size / MB

        print(
            f"  {original:.1f}MB -> {converted:.1f}MB"
        )

    except Exception as e:
        print(f"ERROR {path}: {e}")


def main():

    stack = [
        (ROOT, _parse_gitignore(ROOT / ".gitignore"))
    ]

    def is_ignored(path: Path, is_dir: bool):

        ignored = False

        for base, patterns in stack:
            rel = path.relative_to(base).as_posix()

            for regex, negate, dir_only in patterns:
                if dir_only and not is_dir:
                    continue

                if regex.match(rel):
                    ignored = not negate

        return ignored


    for dirpath, dirnames, filenames in os.walk(ROOT):

        dirpath = Path(dirpath)

        dirnames[:] = sorted(
            d for d in dirnames if d != ".git"
        )

        while (
            stack[-1][0] != ROOT
            and stack[-1][0] != dirpath
            and stack[-1][0] not in dirpath.parents
        ):
            stack.pop()

        own_gitignore = dirpath / ".gitignore"

        if dirpath != ROOT and own_gitignore.is_file():
            stack.append(
                (dirpath, _parse_gitignore(own_gitignore))
            )

        dirnames[:] = [
            d for d in dirnames
            if not is_ignored(dirpath / d, True)
        ]

        for name in filenames:

            path = dirpath / name

            if is_ignored(path, False):
                continue

            if path.suffix.lower() != ".csv":
                continue

            try:
                size = path.stat().st_size
            except OSError:
                continue

            if size >= CONVERT_THRESHOLD:
                convert_csv(path)


if __name__ == "__main__":
    main()