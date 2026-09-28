#!/usr/bin/env python3
# FILE: run_mutations.py
# Break the reference vertical one defect at a time and record which test suite, and which test, notices.
# This is the template's measuring stick for its own tests, not a gate. A catalogue in
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

# Repository root, derived from this file's location in scripts/.
ROOT_DIR = Path(__file__).resolve().parent.parent

# The reference vertical's defect catalogue.
DEFAULT_CATALOGUE = ROOT_DIR / "docs" / "mutations" / "reference_task.json"

# Where each run's full report is written; logs/ is ignored by git.
REPORT_DIR = ROOT_DIR / "logs" / "mutations"

# Suffix of the copy a mutated file is restored from if the run dies before its `finally`.
BACKUP_SUFFIX = ".mutation-backup"

# Ceiling for one tier on one defect; a hang proves nothing either way, so it is inconclusive.
TIER_TIMEOUT_SECONDS = 900

# A pytest summary line naming a failed or errored test. FAILED is a test body that failed —
# the one outcome that is a catch. ERROR is a fixture, a teardown or a module that would not
# import, which a Docker daemon that is down produces as readily as a defect does.
# The Docker tier prints through Compose, which prefixes each line with `tests-1  | ` and
# colours it, so the prefix and the escape codes are both allowed before FAILED/ERROR.
_FAILED_LINE = re.compile(r"^(?:\S+\s+\|\s+)?(FAILED|ERROR) (\S+?)(?: - (.*))?$", re.M)

# Terminal colour sequences Compose adds to its output.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Every tier runs without tests/template. The extraction tests there hold the vertical's files to
# the digests the template ships, and every defect changes a file, so each would count as caught by
# a checksum instead of by a test of what the code does: on 2026-09-24 the manifest test turned up
# in every catch of a run. The template's own tests measure its tools, not the vertical.
# `--deselect`, not `--ignore`: run_all_tests.py names tests/template on the command line, and
# pytest does not ignore a path it was given; deselection drops the collected tests by node id.
_WITHOUT_TEMPLATE_TESTS = "--deselect=tests/template/"


# What one tier did with one version of the code.
@dataclass
class TierResult:
    # "green"; "red" when a test body failed; "inconclusive" when the tier failed without one —
    # only errored tests, or none named at all; "timeout".
    state: str

    # Wall-clock duration of the tier.
    seconds: int

    # Test ids pytest reported as FAILED, in report order.
    failed: list[str] = field(default_factory=list)

    # The reason printed next to the first failed test, or the output tail when none was named.
    first_error: str = ""

    # Test ids pytest reported as ERROR, in report order.
    errored: list[str] = field(default_factory=list)


# Pull the failed and errored test ids and the first reason out of a tier's output.
# output: Combined stdout and stderr of the tier.
# Returns: Unique FAILED ids and unique ERROR ids, each in report order, and the first reason.
def parse_failed_tests(output: str) -> tuple[list[str], list[str], str]:
    clean = _ANSI.sub("", output)
    failed: list[str] = []
    errored: list[str] = []
    first_error = ""
    for match in _FAILED_LINE.finditer(clean):
        named = failed if match.group(1) == "FAILED" else errored
        if match.group(2) not in named:
            named.append(match.group(2))
        if not first_error and match.group(3):
            first_error = match.group(3).strip()[:240]
    return failed, errored, first_error


# Run one tier's command from the repository root and classify the outcome.
# command: The tier's argv, e.g. ["make", "test"].
# Returns: Its state, duration and the tests it named.
# A tier that fails with no FAILED test is inconclusive, with the output tail as the reason. That
# is how a coverage floor, a collection error, a database that never started or a Docker daemon
# that is down shows up; counting it red credited the defect with a catch no test made.
def run_tier(command: Sequence[str]) -> TierResult:
    started = time.monotonic()
    try:
        # A wide terminal, because pytest drops the failure reason from a summary
        # line that does not fit its width, and a test id alone cannot tell a catch for the
        # right reason from a crash elsewhere.
        done = subprocess.run(
            list(command),
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=TIER_TIMEOUT_SECONDS,
            env=os.environ
            | {
                "COLUMNS": "300",
                "PYTEST_ADDOPTS": f"{os.environ.get('PYTEST_ADDOPTS', '')} "
                f"{_WITHOUT_TEMPLATE_TESTS}".strip(),
            },
        )
    except subprocess.TimeoutExpired:
        return TierResult("timeout", TIER_TIMEOUT_SECONDS)
    seconds = int(time.monotonic() - started)
    output = done.stdout + done.stderr
    if done.returncode == 0:
        return TierResult("green", seconds)
    failed, errored, first_error = parse_failed_tests(output)
    if not first_error:
        first_error = " ".join(_ANSI.sub("", output)[-400:].split())
    return TierResult("red" if failed else "inconclusive", seconds, failed, first_error, errored)


# Put back any file a previous run left mutated because it was killed mid-defect.
# files: Every file the catalogue mutates.
def restore_leftover_backups(files: set[Path]) -> None:
    for target in sorted(files):
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        if backup.exists():
            backup.replace(target)
            print(f"restored {target.relative_to(ROOT_DIR)} from a previous interrupted run")


# Refuse to measure when any target file has local edits or any anchor no longer matches once.
# mutations: Catalogue entries selected for this run.
# Returns: Problems found; empty when the run may start.
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


# Apply one defect, run every tier against it, and restore the file whatever happens.
# entry: Catalogue entry with file, find and replace.
# tiers: Tier name to command.
# Returns: Result per tier.
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


# Name the cheapest tier that caught the defect.
# results: Result per tier, in tier order.
# Returns: "caught:<tier>", "missed", or "inconclusive:<tier>" for the first tier that could not
# say — a timeout or a failure without a failed test. Past such a tier a later catch is no longer
# the cheapest one known, so it is not reported as one.
def verdict(results: dict[str, TierResult]) -> str:
    for name, result in results.items():
        if result.state == "red":
            return f"caught:{name}"
        if result.state != "green":
            return f"inconclusive:{name}"
    return "missed"


# Name the defects this run catches later, or not at all, compared with the baseline.
# baseline: Recorded results by defect id.
# observed: This run's results by defect id.
# tiers: Tier names, cheapest first.
# Returns: Defect id to (baseline verdict, this run's verdict).
# Ranked by the cheapest tier that catches: a defect that moves from the fast tier to e2e is
# a loss even though it is still caught. An inconclusive defect is neither a loss nor a hold — the
# run names it separately. A defect without a baseline (newly added to the catalogue) cannot regress.
def regressions(
    baseline: dict[str, Any], observed: dict[str, dict[str, Any]], tiers: list[str]
) -> dict[str, tuple[str, str]]:
    rank = {f"caught:{name}": index for index, name in enumerate(tiers)}

    def _rank(outcome: str) -> int:
        return rank.get(outcome, len(tiers))

    worse: dict[str, tuple[str, str]] = {}
    for defect, item in observed.items():
        before = baseline.get(defect, {}).get("verdict")
        if item["verdict"].startswith("inconclusive"):
            continue
        if before is not None and _rank(item["verdict"]) > _rank(before):
            worse[defect] = (before, item["verdict"])
    return worse


# Reduce a pytest id to its test function name for the printed table.
# test_id: Full pytest id.
# Returns: The last `::` component without parametrisation.
def _short(test_id: str) -> str:
    return test_id.split("::")[-1].split("[")[0]


# Turn SIGTERM into KeyboardInterrupt so the `finally` that restores the file runs.
def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


# Measure the selected defects and report each against the catalogue's baseline.
# argv: CLI arguments; None means none.
# Returns: 0 when measured with no lost catch, 1 when a defect is caught later or not at
# all compared with the baseline, 2 when the catalogue or the clean code is not fit to measure,
# or when any defect's result was inconclusive.
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

    # Every tier green on the clean code first. A tier that is already red would
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
                    "errored": result.errored,
                    "first_error": result.first_error,
                }
                for name, result in results.items()
            },
        }
        before = baseline.get(entry["id"], {}).get("verdict", "-")
        change = "" if before in ("-", outcome) else f"   (baseline: {before})"
        names = [_short(test) for result in results.values() for test in result.failed]
        print(f"{entry['id']:38s} {outcome:15s} {', '.join(dict.fromkeys(names))[:110]}{change}")

    # The clean code once more after the last restore — proof that the run left
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
    counts = {"caught:" + name: 0 for name in tiers} | {"missed": 0}
    for item in observed.values():
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    print("total: " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    print(f"report: {report.relative_to(ROOT_DIR)}")

    # A change to the tests is judged defect by defect, so a run that loses a catch
    # must say so in its exit code — a total that stays the same can hide one defect moving from
    # the fast tier to e2e while another moves the other way. It also refuses to record: writing a
    # worse result as the new baseline would erase the evidence of the loss.
    worse = regressions(baseline, observed, list(tiers))
    for defect, (before, now) in worse.items():
        print(f"REGRESSION {defect}: {before} -> {now}")
    inconclusive = [
        defect for defect, item in observed.items() if item["verdict"].startswith("inconclusive:")
    ]
    for defect in inconclusive:
        print(f"INCONCLUSIVE {defect}: {observed[defect]['verdict']}")
    # A baseline is what the next run is judged against, so it is written only from a run that
    # measured everything: no loss, no defect it could not judge, and the clean code green again
    # at the end — before 2026-09-28 it was written first and the final control read after.
    unfit = [name for name, result in after.items() if result.state != "green"]
    if args.record and not worse and not inconclusive and not unfit:
        # The baseline keeps what a later run is compared on — the verdict and the
        # tests each tier named. Durations and error text stay in the report file: they change
        # from run to run and would turn every re-record into noise in the diff.
        compact = {
            defect: {
                "verdict": item["verdict"],
                "caught_by": {
                    name: sorted({_short(test) for test in tier["failed"]})
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
    elif args.record:
        why = [
            "the run lost catches the baseline has" if worse else "",
            f"{len(inconclusive)} defect(s) inconclusive" if inconclusive else "",
            f"the clean code is no longer green: {', '.join(unfit)}" if unfit else "",
        ]
        print("baseline NOT written: " + "; ".join(reason for reason in why if reason))
    if inconclusive or unfit:
        return 2
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
