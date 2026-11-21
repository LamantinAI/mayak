# FILE: scripts/smoke_local.py
# SUMMARY: Start a disposable loopback runtime and verify its readiness over HTTP.

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    env = os.environ | {
        "SERVER_HOST": "127.0.0.1",
        "SERVER_PORT": str(port),
        "SERVER_WORKERS": "1",
        "ENABLE_FULL_TRACE": "false",
    }
    url = f"http://127.0.0.1:{port}/health/ready"
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        with subprocess.Popen(
            (sys.executable, "-m", "project.launcher.main"),
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        ) as process:
            try:
                deadline = time.monotonic() + 20
                error = "process stopped before it became ready"
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with urllib.request.urlopen(url, timeout=2) as response:
                            if response.status == 200:
                                print("Local readiness: PASS (/health/ready returned 200)")
                                return 0
                    except OSError as exc:
                        error = str(exc)
                    time.sleep(0.2)
                print(f"Local readiness: FAIL ({error})", file=sys.stderr)
                output.seek(0)
                for line in output.readlines()[-20:]:
                    print(line.rstrip(), file=sys.stderr)
                return 1
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
