from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ContextIssue:
    issue_type: str
    path: Path
    line: int
    message: str
    recommended_next_command: str

    def to_payload(self, repo_root: Path | None = None) -> dict[str, object]:
        display_path = self.path
        if repo_root is not None:
            try:
                display_path = self.path.relative_to(repo_root)
            except ValueError:
                display_path = self.path
        return {
            "issue_type": self.issue_type,
            "path": display_path.as_posix(),
            "line": self.line,
            "message": self.message,
            "recommended_next_command": self.recommended_next_command,
        }


class ContextBuildError(RuntimeError):
    def __init__(self, issue: ContextIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue
