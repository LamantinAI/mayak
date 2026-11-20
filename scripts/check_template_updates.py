# FILE: scripts/check_template_updates.py
# SUMMARY: Report known template upgrades against the project's local files.

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    root = parser.parse_args().root
    context = json.loads((root / "docs/project_context.json").read_text(encoding="utf-8"))
    template = context.get("template")
    if template:
        print(f"Template {template['version']}, source tree {template['source_tree']}")
    else:
        print("Template provenance is absent. Use the file checks in docs/upgrades.md.")
    locked = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    certifi = next(
        (package["version"] for package in locked["package"] if package["name"] == "certifi"), None
    )
    if certifi and tuple(map(int, certifi.split("."))) < (2026, 10, 18):
        print("ACTION: run uv lock --upgrade-package certifi, make audit-deps and rebuild images.")
    else:
        print("certifi lock: no update required for PYSEC-2026-4320.")
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    if "\nverify:" not in makefile:
        print("ACTION: port the onboarding and verification changes described in docs/upgrades.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
