"""Platform compatibility helpers.

The project targets Python 3.10+ on POSIX and Windows. Keep OS-specific
branches here instead of scattering ``os.name`` checks through the domain
modules. Helpers that need to be tested for the other platform accept an
optional ``windows`` argument.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from pathlib import Path

WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def is_windows(*, windows: bool | None = None) -> bool:
    return os.name == "nt" if windows is None else windows


# ----------------------------------------------------------------------
# Locking
# ----------------------------------------------------------------------
def lock_handle(handle, *, exclusive: bool = True) -> None:
    """Take an advisory lock on an open binary file handle.

    Windows has no shared-lock equivalent in ``msvcrt``; shared requests
    are serialized as exclusive. The lock file is only held for short
    registry operations, so this is acceptable.
    """

    if is_windows():
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + 60
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    import fcntl

    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    fcntl.flock(handle.fileno(), operation)


def unlock_handle(handle) -> None:
    if is_windows():
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ----------------------------------------------------------------------
# Virtualenv layout
# ----------------------------------------------------------------------
def venv_python(venv: Path, *, windows: bool | None = None) -> Path:
    win = is_windows(windows=windows)
    return venv / ("Scripts/python.exe" if win else "bin/python")


def venv_pip(venv: Path, *, windows: bool | None = None) -> Path:
    win = is_windows(windows=windows)
    return venv / ("Scripts/pip.exe" if win else "bin/pip")


def venv_python_rel(venv_name: str = ".venv", *, windows: bool | None = None) -> str:
    return str(venv_python(Path(venv_name), windows=windows))


def venv_pip_rel(venv_name: str = ".venv", *, windows: bool | None = None) -> str:
    return str(venv_pip(Path(venv_name), windows=windows))


def venv_activate_rel(venv_name: str = ".venv", *, windows: bool | None = None) -> str:
    win = is_windows(windows=windows)
    name = "Scripts/activate.bat" if win else "bin/activate"
    return str(Path(venv_name) / name)


# ----------------------------------------------------------------------
# User-level locations
# ----------------------------------------------------------------------
def cache_dir(app: str = "lab-workflow", *, windows: bool | None = None) -> Path:
    win = is_windows(windows=windows)
    if win:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / app
        return Path.home() / "AppData" / "Local" / app
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / app
    return Path.home() / ".cache" / app


def cli_executable(repo_root: Path, *, windows: bool | None = None) -> str:
    """Best known path of the repo-local ``lab-workflow`` command."""

    win = is_windows(windows=windows)
    candidates = [repo_root / "bin" / ("lab-workflow.cmd" if win else "lab-workflow")]
    if win:
        candidates.append(repo_root / "bin" / "lab-workflow")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    found = shutil.which("lab-workflow")
    if found:
        return found
    if win:
        found = shutil.which("lab-workflow.cmd")
        if found:
            return found
    return candidates[0].name


def interactive_shell() -> list[str]:
    if is_windows():
        return [os.environ.get("COMSPEC", "cmd.exe"), "/K"]
    return [os.environ.get("SHELL") or "/bin/sh"]


def executable_command(executable: str, *args: str) -> list[str]:
    """Build an argv that also works for Windows ``.cmd`` shims."""

    if is_windows() and executable.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", executable, *args]
    return [executable, *args]


# ----------------------------------------------------------------------
# Subprocesses and filesystem
# ----------------------------------------------------------------------
def text_subprocess_kwargs() -> dict[str, str]:
    return {"encoding": "utf-8", "errors": "replace"}


def _clear_readonly(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass


def safe_rmtree(path: Path, *, ignore_errors: bool = False) -> None:
    """Remove a tree, clearing Windows read-only bits before retrying."""

    def onerror(func, failed_path, exc_info) -> None:
        _clear_readonly(failed_path)
        try:
            func(failed_path)
        except OSError:
            if not ignore_errors:
                raise

    def onexc(func, failed_path, exc) -> None:
        _clear_readonly(failed_path)
        try:
            func(failed_path)
        except OSError:
            if not ignore_errors:
                raise

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=onexc)
    else:  # pragma: no cover - Python 3.10/3.11
        shutil.rmtree(path, onerror=onerror)


def atomic_replace(source: Path, destination: Path, *, attempts: int = 5) -> None:
    """``os.replace`` with a short retry for transient Windows locks."""

    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            transient = isinstance(exc, PermissionError) or getattr(
                exc, "winerror", None
            ) in {5, 32, 33}
            if not transient:
                raise
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    os.replace(source, destination)


def validate_portable_slug(slug: str) -> None:
    """Reject names that cannot be used as a final path component on Windows."""

    base = slug.split(".", 1)[0].strip().lower()
    if base in WINDOWS_RESERVED_NAMES:
        raise ValueError(
            f"实验名称 '{slug}' 是 Windows 保留设备名，无法作为目录名；请换一个名称"
        )
