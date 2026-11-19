"""Workspace statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from . import gitutil
from .registry import load_for_read
from .util import ACTIVE_STATUSES, now_local, parse_timestamp
from .workspace import Workspace


@dataclass
class StatsReport:
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_template: dict[str, int] = field(default_factory=dict)
    top_tags: list[tuple[str, int]] = field(default_factory=list)
    active_missing: list[str] = field(default_factory=list)
    active_dirty: list[str] = field(default_factory=list)
    recent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_status": self.by_status,
            "by_template": self.by_template,
            "top_tags": [{"tag": tag, "count": count} for tag, count in self.top_tags],
            "active_missing": self.active_missing,
            "active_dirty": self.active_dirty,
            "created_last_7_days": self.recent,
        }


def build_stats(workspace: Workspace, *, check_dirty: bool = True) -> StatsReport:
    records = load_for_read(workspace)
    report = StatsReport(total=len(records))

    for record in records:
        status = str(record.get("status", "unknown"))
        report.by_status[status] = report.by_status.get(status, 0) + 1
        template = str(record.get("template", "unknown"))
        report.by_template[template] = report.by_template.get(template, 0) + 1

    tag_counts: dict[str, int] = {}
    tag_display: dict[str, str] = {}
    for record in records:
        for tag in record.get("tags", []) or []:
            key = str(tag).casefold()
            tag_counts[key] = tag_counts.get(key, 0) + 1
            tag_display.setdefault(key, str(tag))
    report.top_tags = sorted(
        ((tag_display[key], count) for key, count in tag_counts.items()),
        key=lambda item: (-item[1], item[0].casefold()),
    )[:8]

    cutoff = now_local() - timedelta(days=7)
    for record in records:
        created = parse_timestamp(record.get("created_at"))
        if created and created >= cutoff:
            report.recent += 1

    for record in records:
        if record.get("status") not in ACTIVE_STATUSES:
            continue
        try:
            path = workspace.experiment_path(record)
        except Exception:
            path = None
        if path is None or not path.is_dir():
            report.active_missing.append(str(record.get("id")))
            continue
        if check_dirty and record.get("git") and gitutil.is_repo(path):
            if gitutil.dirty(path):
                report.active_dirty.append(str(record.get("id")))
    return report
