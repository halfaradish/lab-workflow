"""Small, defensive wrappers around the git command line."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

EXCLUDE_MARKER = "# lab: experiment environment and metadata"
EXCLUDE_PATTERNS = (
    ".venv/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".lab-meta.json",
    "/AGENTS.md",
    ".DS_Store",
    ".env",
)


def git_available() -> bool:
    return shutil.which("git") is not None


def _run(args: Sequence[str], cwd: Path, *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def is_repo(path: Path) -> bool:
    if not git_available():
        return False
    if not (path / ".git").exists():
        return False
    result = _run(["rev-parse", "--is-inside-work-tree"], path)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _identity_config(path: Path) -> list[str]:
    args: list[str] = []
    if _run(["config", "--get", "user.name"], path).returncode != 0:
        args.extend(["-c", "user.name=Lab"])
    if _run(["config", "--get", "user.email"], path).returncode != 0:
        args.extend(["-c", "user.email=lab@localhost"])
    args.extend(["-c", "commit.gpgsign=false"])
    return args


def write_excludes(path: Path) -> None:
    """Add lab-local ignore rules to ``.git/info/exclude``.

    Using the repository-local exclude file (instead of the experiment's
    tracked ``.gitignore``) keeps generated environments and the shared
    AGENTS.md symlink out of every experiment without polluting the
    template or the user's global git configuration.
    """

    exclude_path = path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    if EXCLUDE_MARKER in existing:
        return
    block = [EXCLUDE_MARKER, *EXCLUDE_PATTERNS]
    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    exclude_path.write_text(text + "\n".join(block) + "\n", encoding="utf-8")


def init_repo(path: Path) -> tuple[bool, str]:
    """Initialize a git repository and make an initial commit.

    Returns ``(ok, message)``. A failed initial commit is reported but is
    not fatal for experiment creation; the user may simply have no git.
    """

    if not git_available():
        return False, "未找到 git，已跳过版本控制初始化"
    result = _run(["init", "-q"], path)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        return False, f"git init 失败：{detail}"
    write_excludes(path)
    result = _run(["add", "-A"], path)
    if result.returncode != 0:
        return True, f"git 仓库已初始化，但 git add 失败：{result.stderr.strip()}"
    commit_args = [*_identity_config(path), "commit", "-q", "-m", "chore: initialize experiment"]
    result = _run(commit_args, path)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        return True, f"git 仓库已初始化，但初始提交失败：{detail}"
    return True, "已初始化 git 仓库并提交初始版本"


def status_lines(path: Path) -> list[str] | None:
    """Porcelain status lines, or ``None`` when this is not a git repo."""

    if not is_repo(path):
        return None
    result = _run(["status", "--porcelain=v1", "--untracked-files=normal"], path)
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def dirty(path: Path) -> bool:
    lines = status_lines(path)
    return bool(lines)


def dirty_summary(path: Path, *, limit: int = 5) -> str:
    lines = status_lines(path) or []
    shown = "; ".join(line.strip() for line in lines[:limit])
    if len(lines) > limit:
        shown += f"; …（共 {len(lines)} 项）"
    return shown


def head_commit(path: Path) -> str | None:
    if not is_repo(path):
        return None
    result = _run(["rev-parse", "--short", "HEAD"], path)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
