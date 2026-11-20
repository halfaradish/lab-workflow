"""Small, defensive wrappers around the git command line."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from . import compat
from .util import atomic_write_text, strip_managed_blocks

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
    "Thumbs.db",
    "desktop.ini",
    ".env",
)


def git_available() -> bool:
    return shutil.which("git") is not None


def _run(args: Sequence[str], cwd: Path, *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        compat.executable_command(shutil.which("git") or "git", *args),
        cwd=str(cwd),
        capture_output=True,
        timeout=timeout,
        env=env,
        **compat.text_subprocess_kwargs(),
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

    result = _run(["rev-parse", "--git-path", "info/exclude"], path)
    if result.returncode == 0 and result.stdout.strip():
        candidate = Path(result.stdout.strip())
        exclude_path = candidate if candidate.is_absolute() else path / candidate
    else:
        exclude_path = path / ".git" / "info" / "exclude"

    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    if EXCLUDE_MARKER in existing:
        return
    block = [EXCLUDE_MARKER, *EXCLUDE_PATTERNS]
    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    atomic_write_text(exclude_path, text + "\n".join(block) + "\n")


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


def _managed_readme_only(path: Path) -> bool:
    """True when README.md differs from HEAD only inside managed blocks."""

    readme = path / "README.md"
    if not readme.is_file():
        return False
    result = _run(["show", "HEAD:README.md"], path)
    if result.returncode != 0:
        return False
    try:
        working = readme.read_text(encoding="utf-8")
    except OSError:
        return False
    return strip_managed_blocks(result.stdout) == strip_managed_blocks(working)


def _is_managed_readme_line(path: Path, line: str) -> bool:
    if len(line) < 4:
        return False
    if line[:2].strip() != "M":
        return False
    rest = line[3:]
    if " -> " in rest:
        return False
    if rest.strip().strip('"') != "README.md":
        return False
    return _managed_readme_only(path)


def status_lines(path: Path, *, ignore_managed: bool = True) -> list[str] | None:
    """Porcelain status lines, or ``None`` when this is not a git repo.

    By default a ``README.md`` whose only change sits inside the
    lab-workflow managed blocks is ignored: metadata updates must not turn
    every experiment into a dirty repository. Real user edits still count.
    """

    if not is_repo(path):
        return None
    result = _run(["status", "--porcelain=v1", "--untracked-files=normal"], path)
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if ignore_managed:
        lines = [line for line in lines if not _is_managed_readme_line(path, line)]
    return lines


def dirty(path: Path) -> bool:
    lines = status_lines(path)
    return bool(lines)


def head_commit(path: Path) -> str | None:
    if not is_repo(path):
        return None
    result = _run(["rev-parse", "--short", "HEAD"], path)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
