"""Retention-based cleanup for active experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gitutil
from .experiments import discard_experiment
from .registry import Registry
from .util import ACTIVE_STATUSES, age_days, compact_number
from .workspace import Workspace

DEFAULT_CREATED_DAYS = 3.0
DEFAULT_SUCCESS_DAYS = 7.0


@dataclass
class CleanCandidate:
    record: dict[str, Any]
    path: Path
    age: float
    limit: float
    is_git: bool
    dirty: list[str] = field(default_factory=list)
    reason: str = ""
    action: str = "delete"


@dataclass
class CleanReport:
    candidates: list[CleanCandidate] = field(default_factory=list)
    deleted: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[CleanCandidate] = field(default_factory=list)
    orphans: list[Path] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    kept: int = 0
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        def candidate_dict(candidate: CleanCandidate) -> dict[str, Any]:
            return {
                "id": candidate.record.get("id"),
                "name": candidate.record.get("name"),
                "status": candidate.record.get("status"),
                "path": candidate.record.get("path"),
                "age_days": round(candidate.age, 2),
                "limit_days": candidate.limit,
                "git": candidate.is_git,
                "dirty_count": len(candidate.dirty),
                "action": candidate.action,
                "reason": candidate.reason,
            }

        return {
            "applied": self.applied,
            "kept": self.kept,
            "candidates": [candidate_dict(item) for item in self.candidates],
            "deleted": [record.get("id") for record in self.deleted],
            "skipped": [candidate_dict(item) for item in self.skipped],
            "orphans": [str(path) for path in self.orphans],
            "missing": [record.get("id") for record in self.missing],
            "warnings": self.warnings,
        }


def _retention(record: dict[str, Any], created_days: float, success_days: float) -> tuple[float, float, str] | None:
    status = record.get("status")
    if status == "created":
        return created_days, created_days, "created_at"
    if status == "success":
        return success_days, success_days, "status_changed_at"
    return None


def evaluate(
    workspace: Workspace,
    *,
    created_days: float = DEFAULT_CREATED_DAYS,
    success_days: float = DEFAULT_SUCCESS_DAYS,
    force: bool = False,
) -> CleanReport:
    records = Registry(workspace).load()
    report = CleanReport()

    active_records = [record for record in records if record.get("status") in ACTIVE_STATUSES]
    known_paths: set[Path] = set()
    for record in active_records:
        path = workspace.experiment_path(record)
        if path is not None:
            known_paths.add(path)
        if path is None or not path.is_dir():
            if record.get("status") in {"created", "success"}:
                report.missing.append(record)
            continue
        retention = _retention(record, created_days, success_days)
        if retention is None:
            continue
        age = age_days(record.get(retention[2]) or record.get("created_at"))
        if age is None:
            continue
        if age <= retention[0]:
            report.kept += 1
            continue
        candidate = CleanCandidate(
            record=record,
            path=path,
            age=age,
            limit=retention[1],
            is_git=gitutil.is_repo(path),
        )
        if candidate.is_git:
            candidate.dirty = gitutil.status_lines(path) or []
            if candidate.dirty and not force:
                candidate.action = "skip"
                candidate.reason = "存在未提交更改（加 --force 仍然删除）"
                report.skipped.append(candidate)
                continue
        elif not force:
            candidate.action = "skip"
            candidate.reason = "非 git 仓库，无法确认未提交更改（加 --force 仍然删除）"
            report.skipped.append(candidate)
            continue
        if candidate.dirty:
            report.warnings.append(
                f"{record.get('id')} 有 {len(candidate.dirty)} 项未提交更改，"
                "已按 --force 继续清理"
            )
        report.candidates.append(candidate)

    active_dir = workspace.active
    if active_dir.is_dir():
        for child in sorted(active_dir.iterdir(), key=lambda item: item.name):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.resolve() not in known_paths:
                report.orphans.append(child)
    return report


def apply_clean(workspace: Workspace, report: CleanReport) -> CleanReport:
    report.applied = True
    for candidate in list(report.candidates):
        identifier = str(candidate.record.get("id"))
        try:
            result = discard_experiment(workspace, identifier, reason="clean")
        except Exception as exc:  # keep cleaning the rest
            report.warnings.append(f"清理 {identifier} 失败: {exc}")
            continue
        report.deleted.append(result.record)
        report.warnings.extend(result.warnings)
        candidate.action = "deleted"
    return report


def format_days(value: float) -> str:
    return compact_number(value)
