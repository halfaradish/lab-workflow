"""Workspace diagnostics with explicit try-rs integration hints."""

from __future__ import annotations

import platform
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import compat, environment, gitutil
from .errors import LabError
from .registry import load_for_read
from .util import ACTIVE_STATUSES, ALL_STATUSES
from .workspace import Workspace


def _first_line(command: str, *args: str, timeout: int = 5) -> str | None:
    executable = shutil.which(command)
    if executable is None:
        return None
    try:
        proc = subprocess.run(
            compat.executable_command(executable, *args),
            capture_output=True,
            timeout=timeout,
            check=False,
            **compat.text_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    output = (proc.stdout or proc.stderr).strip()
    return output.splitlines()[0] if output else None


@dataclass
class DoctorReport:
    root: str
    initialized: bool = False
    registry_ok: bool = False
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    python: str = ""
    git: str | None = None
    npm: str | None = None
    try_rs: str | None = None
    orphans: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    dirty: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_root": self.root,
            "initialized": self.initialized,
            "registry_ok": self.registry_ok,
            "total": self.total,
            "by_status": self.by_status,
            "python": self.python,
            "git": self.git,
            "npm": self.npm,
            "try_rs": self.try_rs,
            "orphans": self.orphans,
            "missing": self.missing,
            "dirty": self.dirty,
            "warnings": self.warnings,
        }


def build_report(workspace: Workspace) -> DoctorReport:
    report = DoctorReport(root=str(workspace.root), python=platform.python_version())
    report.initialized = (
        workspace.root.is_dir()
        and workspace.active.is_dir()
        and workspace.registry_path.is_file()
    )

    records: list[dict[str, Any]] = []
    if report.initialized:
        try:
            records = load_for_read(workspace)
        except LabError as exc:
            report.warnings.append(f"registry 不可用: {exc}")
        else:
            report.registry_ok = True

    report.total = len(records)
    counts = Counter(str(record.get("status", "unknown")) for record in records)
    report.by_status = {status: counts[status] for status in ALL_STATUSES if counts[status]}

    known_paths = set()
    for record in records:
        if record.get("status") not in ACTIVE_STATUSES:
            continue
        try:
            path = workspace.experiment_path(record)
        except LabError:
            path = None
        if path is None or not path.is_dir():
            report.missing.append(str(record.get("id")))
            continue
        known_paths.add(path.resolve())
        if record.get("git") and gitutil.is_repo(path) and gitutil.dirty(path):
            report.dirty.append(str(record.get("id")))

        env = record.get("env") or {}
        if env.get("type") == "python":
            identifier = str(record.get("id"))
            requirements = path / "requirements.txt"
            stored = env.get("requirements_digest")
            if stored and not requirements.is_file():
                report.warnings.append(
                    f"{identifier}: requirements.txt 已被删除，环境与记录不一致"
                )
            elif stored and environment.requirements_digest(requirements) != stored:
                report.warnings.append(
                    f"{identifier}: requirements.txt 在环境初始化后已修改，"
                    "请在实验目录重新安装依赖"
                )
            if env.get("ok") and not compat.venv_python(path / ".venv").exists():
                report.warnings.append(f"{identifier}: record 显示已创建 venv，但 .venv/ 不存在")

    if workspace.active.is_dir():
        for child in sorted(workspace.active.iterdir(), key=lambda item: item.name):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.resolve() not in known_paths:
                report.orphans.append(str(child))

    report.git = _first_line("git", "--version")
    report.npm = _first_line("npm", "--version")
    report.try_rs = _first_line("try-rs", "--version")
    return report
