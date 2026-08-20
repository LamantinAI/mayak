# FILE: project/core/logging/file_manager.py
# SUMMARY: File-based log management for local development: per-run log paths and retention rotation.

import os
from datetime import datetime, timezone
from pathlib import Path


# FUNCTION: create_run_log_path
# SUMMARY: Create a unique NDJSON log file path for the current application run.
# OUTPUT: (Path): Absolute path to the new log file (directory created if missing).
def create_run_log_path(log_dir: str) -> Path:
    """Create a unique NDJSON log file path for the current application run.

    Format: ``<log_dir>/<ISO-timestamp>_pid<PID>.ndjson``

    The timestamp uses a filesystem-safe format (hyphens instead of colons).
    Sorting files by name yields chronological order.
    """
    dir_path = Path(log_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    pid = os.getpid()
    filename = f"{timestamp}_pid{pid}.ndjson"

    return dir_path / filename


# FUNCTION: rotate_log_files
# SUMMARY: Remove oldest NDJSON log files keeping only the most recent ones.
# INPUT: max_files (int): Maximum number of files to retain (0 disables rotation).
def rotate_log_files(log_dir: str, max_files: int) -> int:
    """Delete oldest ``.ndjson`` log files, keeping *max_files* most recent.

    Files are sorted by name (which embeds an ISO timestamp) so the newest
    entries survive.  Called once at application startup — not at runtime.

    Returns the number of deleted files.
    """
    if max_files <= 0:
        return 0

    dir_path = Path(log_dir)
    if not dir_path.is_dir():
        return 0

    log_files = sorted(dir_path.glob("*.ndjson"))
    excess = len(log_files) - max_files

    if excess <= 0:
        return 0

    deleted = 0
    for old_file in log_files[:excess]:
        try:
            old_file.unlink()
            deleted += 1
        except OSError:
            pass

    return deleted
