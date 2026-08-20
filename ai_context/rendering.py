# SUMMARY: Stable rendering helpers for generated AI context artifacts.

from __future__ import annotations

import json
from pathlib import Path


def render_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def build_generated_outputs(
    outputs: dict[Path, dict[str, object]],
) -> dict[Path, str]:
    return {output_path: render_json(payload) for output_path, payload in outputs.items()}
