#!/usr/bin/env python3
# FILE: run_mutations.py
# SUMMARY: Break the reference vertical one defect at a time and record which test suite, and which test, notices.
# NOTE: This is the template's measuring stick for its own tests, not a gate. A catalogue in
# docs/mutations/ names each defect as an exact text replacement in the code plus the test expected
# to catch it; this script applies one, runs every tier, restores the file, and moves on. The
# numbers that matter are per defect — caught by the fast tier, caught only by the Docker tier, or
# missed — because a change to the tests is judged by what it gains and loses against the recorded
# baseline, not by a total. The catalogue belongs to the reference vertical: a project that removes
# the vertical removes the catalogue with it.
#
#   uv run python scripts/run_mutations.py                 # all defects, all tiers, compare to baseline
#   uv run python scripts/run_mutations.py --only ID ...   # a subset
#   uv run python scripts/run_mutations.py --record        # also write the result as the new baseline
#
# A full run of the reference catalogue took 25-30 minutes on a laptop (about 50 s per defect: the
# fast tier ~22 s, the Docker tier ~26 s with a warm image cache). Reports land in logs/mutations/.

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Repository root, derived from this file's location in scripts/.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: DEFAULT_CATALOGUE (Path)
# SUMMARY: The reference vertical's defect catalogue.
DEFAULT_CATALOGUE = ROOT_DIR / "docs" / "mutations" / "reference_task.json"

# ATTRIBUTE: REPORT_DIR (Path)
# SUMMARY: Where each run's full report is written; logs/ is ignored by git.
REPORT_DIR = ROOT_DIR / "logs" / "mutations"

# ATTRIBUTE: BACKUP_SUFFIX (str)
# SUMMARY: Suffix of the copy a mutated file is restored from if the run dies before its `finally`.
BACKUP_SUFFIX = ".mutation-backup"

# ATTRIBUTE: TIER_TIMEOUT_SECONDS (int)
# SUMMARY: Ceiling for one tier on one defect; a hang is recorded as a timeout, never as a catch.
TIER_TIMEOUT_SECONDS = 900

# ATTRIBUTE: _FAILED_LINE (re.Pattern[str])
# SUMMARY: A pytest summary line naming a failed or errored test.
# NOTE: The Docker tier prints through Compose, which prefixes each line with `tests-1  | ` and
# colours it, so the prefix and the escape codes are both allowed before FAILED/ERROR.
_FAILED_LINE = re.compile(r"^(?:\S+\s+\|\s+)?(FAILED|ERROR) (\S+?)(?: - (.*))?$", re.M)

# ATTRIBUTE: _ANSI (re.Pattern[str])
# SUMMARY: Terminal colour sequences Compose adds to its output.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# DATACLASS: run_mutations.TierResult
# SUMMARY: What one tier did with one version of the code.
@dataclass
class TierResult:
    # ATTRIBUTE: state (str)
    # SUMMARY: "green", "red" or "timeout".
    state: str

    # ATTRIBUTE: seconds (int)
    # SUMMARY: Wall-clock duration of the tier.
    seconds: int

    # ATTRIBUTE: failed (list[str])
    # SUMMARY: Test ids pytest reported as FAILED or ERROR, in report order.
    failed: list[str] = field(default_factory=list)

    # ATTRIBUTE: first_error (str)
    # SUMMARY: The reason printed next to the first failed test, or the output tail when none was named.
    first_error: str = ""


# FUNCTION: parse_failed_tests
# SUMMARY: Pull the failed test ids and the first failure reason out of a tier's output.
# INPUT: output (str): Combined stdout and stderr of the tier.
# OUTPUT: (tuple[list[str], str]): Unique test ids in report order, and the first reason given.
def parse_failed_tests(output: str) -> tuple[list[str], str]:
    clean = _ANSI.sub("", output)
    failed: list[str] = []
    first_error = ""
    for match in _FAILED_LINE.finditer(clean):
        test_id = match.group(2)
        if test_id not in failed:
            failed.append(test_id)
        if not first_error and match.group(3):
            first_error = match.group(3).strip()[:240]
    return failed, first_error


# FUNCTION: run_tier
# SUMMARY: Run one tier's command from the repository root and classify the outcome.
# INPUT: command (Sequence[str]): The tier's argv, e.g. ["make", "test"].
# OUTPUT: (TierResult): Its state, duration and the tests it named.
# NOTE: A red tier that names no test is kept red with the output tail as the reason. That is how
# a coverage floor, a collection error or an application that never started shows up, and the
# baseline has to say so rather than credit a test that did not run.
def run_tier(command: Sequence[str]) -> TierResult:
    started = time.monotonic()
    try:
        # **LOGIC_STEP**: A wide terminal, because pytest drops the failure reason from a summary
        # line that does not fit its width, and a test id alone cannot tell a catch for the
        # right reason from a crash elsewhere.
        done = subprocess.run(
            list(command),
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=TIER_TIMEOUT_SECONDS,
            env=os.environ | {"COLUMNS": "300"},
        )
    except subprocess.TimeoutExpired:
        return TierResult("timeout", TIER_TIMEOUT_SECONDS)
    seconds = int(time.monotonic() - started)
    output = done.stdout + done.stderr
    if done.returncode == 0:
        return TierResult("green", seconds)
    failed, first_error = parse_failed_tests(output)
    if not first_error:
        first_error = " ".join(_ANSI.sub("", output)[-400:].split())
    return TierResult("red", seconds, failed, first_error)


# FUNCTION: restore_leftover_backups
# SUMMARY: Put back any file a previous run left mutated because it was killed mid-defect.
# INPUT: files (set[Path]): Every file the catalogue mutates.
def restore_leftover_backups(files: set[Path]) -> None:
    for target in sorted(files):
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        if backup.exists():
            backup.replace(target)
            print(f"restored {target.relative_to(ROOT_DIR)} from a previous interrupted run")


# FUNCTION: check_catalogue
# SUMMARY: Refuse to measure when any target file has local edits or any anchor no longer matches once.
# INPUT: mutations (list[dict[str, Any]]): Catalogue entries selected for this run.
# OUTPUT: (list[str]): Problems found; empty when the run may start.
def check_catalogue(mutations: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for relative in sorted({entry["file"] for entry in mutations}):
        dirty = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT_DIR
        ).returncode
        if dirty:
            problems.append(f"{relative} differs from HEAD; commit or revert it before measuring")
    for entry in mutations:
        text = (ROOT_DIR / entry["file"]).read_text(encoding="utf-8")
        count = text.count(entry["find"])
        if count != 1:
            problems.append(f"{entry['id']}: anchor found {count} times in {entry['file']}")
        if entry["find"] == entry["replace"]:
            problems.append(f"{entry['id']}: the replacement changes nothing")
    return problems


# FUNCTION: measure_one
# SUMMARY: Apply one defect, run every tier against it, and restore the file whatever happens.
# INPUT: entry (dict[str, Any]): Catalogue entry with file, find and replace.
# INPUT: tiers (dict[str, list[str]]): Tier name to command.
# OUTPUT: (dict[str, TierResult]): Result per tier.
def measure_one(entry: dict[str, Any], tiers: dict[str, list[str]]) -> dict[str, TierResult]:
    target = ROOT_DIR / entry["file"]
    original = target.read_text(encoding="utf-8")
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    backup.write_text(original, encoding="utf-8")
    try:
        target.write_text(original.replace(entry["find"], entry["replace"], 1), encoding="utf-8")
        return {name: run_tier(command) for name, command in tiers.items()}
    finally:
        target.write_text(original, encoding="utf-8")
        backup.unlink()


# FUNCTION: verdict
# SUMMARY: Name the cheapest tier that caught the defect.
# INPUT: results (dict[str, TierResult]): Result per tier, in tier order.
# OUTPUT: (str): "caught:<tier>", "missed" or "timeout".
def verdict(results: dict[str, TierResult]) -> str:
    for name, result in results.items():
        if result.state == "red":
            return f"caught:{name}"
    if any(result.state == "timeout" for result in results.values()):
        return "timeout"
    return "missed"


# FUNCTION: _short
# SUMMARY: Reduce a pytest id to its test function name for the printed table.
# INPUT: test_id (str): Full pytest id.
# OUTPUT: (str): The last `::` component without parametrisation.
def _short(test_id: str) -> str:
    return test_id.split("::")[-1].split("[")[0]


# FUNCTION: _raise_interrupt
# SUMMARY: Turn SIGTERM into KeyboardInterrupt so the `finally` that restores the file runs.
def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


# FUNCTION: main
# SUMMARY: Measure the selected defects and report each against the catalogue's baseline.
# INPUT: argv (Sequence[str] | None): CLI arguments; None means none.
# OUTPUT: (int): 0 when measured, 2 when the catalogue or the clean code is not fit to measure.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument("--only", nargs="+", metavar="ID", help="measure only these defects")
    parser.add_argument(
        "--record", action="store_true", help="write the result into the catalogue as its baseline"
    )
    args = parser.parse_args([] if argv is None else argv)
    signal.signal(signal.SIGTERM, _raise_interrupt)

    catalogue = json.loads(args.catalogue.read_text(encoding="utf-8"))
    tiers: dict[str, list[str]] = catalogue["tiers"]
    selected = [
        entry for entry in catalogue["mutations"] if not args.only or entry["id"] in args.only
    ]
    unknown = set(args.only or ()) - {entry["id"] for entry in selected}
    if unknown:
        print(f"unknown defect ids: {', '.join(sorted(unknown))}")
        return 2

    restore_leftover_backups({ROOT_DIR / entry["file"] for entry in selected})
    problems = check_catalogue(selected)
    if problems:
        print("not measuring:\n  " + "\n  ".join(problems))
        return 2

    # **LOGIC_STEP**: Every tier green on the clean code first. A tier that is already red would
    # credit every defect with a catch it did not make.
    control = {name: run_tier(command) for name, command in tiers.items()}
    for name, result in control.items():
        print(f"control {name}: {result.state} in {result.seconds} s")
    if any(result.state != "green" for result in control.values()):
        return 2

    baseline: dict[str, Any] = catalogue.get("baseline", {}).get("results", {})
    observed: dict[str, dict[str, Any]] = {}
    for entry in selected:
        results = measure_one(entry, tiers)
        outcome = verdict(results)
        observed[entry["id"]] = {
            "verdict": outcome,
            "tiers": {
                name: {
                    "state": result.state,
                    "seconds": result.seconds,
                    "failed": result.failed,
                    "first_error": result.first_error,
                }
                for name, result in results.items()
            },
        }
        before = baseline.get(entry["id"], {}).get("verdict", "-")
        change = "" if before in ("-", outcome) else f"   (baseline: {before})"
        names = [_short(test) for result in results.values() for test in result.failed]
        print(f"{entry['id']:38s} {outcome:15s} {', '.join(dict.fromkeys(names))[:110]}{change}")

    # **LOGIC_STEP**: The clean code once more after the last restore — proof that the run left
    # the tree as it found it and that no tier went red for a reason of its own midway.
    after = {name: run_tier(command) for name, command in tiers.items()}
    for name, result in after.items():
        print(f"control after {name}: {result.state} in {result.seconds} s")

    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT_DIR, capture_output=True, text=True
    ).stdout.strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / f"{catalogue['subject']}-{stamp}.json"
    report.write_text(
        json.dumps({"commit": commit, "results": observed}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    counts = {"caught:" + name: 0 for name in tiers} | {"missed": 0, "timeout": 0}
    for item in observed.values():
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    print("total: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    print(f"report: {report.relative_to(ROOT_DIR)}")

    if args.record:
        # **LOGIC_STEP**: The baseline keeps what a later run is compared on — the verdict and the
        # tests each tier named. Durations and error text stay in the report file: they change
        # from run to run and would turn every re-record into noise in the diff.
        compact = {
            defect: {
                "verdict": item["verdict"],
                "caught_by": {
                    name: sorted({_short(test) for test in tier["failed"]})
                    or ["(red, no test named: " + tier["first_error"][:120] + ")"]
                    for name, tier in item["tiers"].items()
                    if tier["state"] == "red"
                },
            }
            for defect, item in observed.items()
        }
        merged = dict(baseline) | compact
        catalogue["baseline"] = {"commit": commit, "results": merged}
        args.catalogue.write_text(
            json.dumps(catalogue, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"baseline written to {args.catalogue.relative_to(ROOT_DIR)}")
    return 0 if all(result.state == "green" for result in after.values()) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
