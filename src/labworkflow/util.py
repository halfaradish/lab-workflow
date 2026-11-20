"""Shared helpers: timestamps, formatting, tables and safe file writes."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import compat

STATUS_LABELS = {
    "created": "CREATED",
    "running": "RUNNING",
    "success": "SUCCESS",
    "archived": "ARCHIVED",
    "promoted": "PROMOTED",
    "discarded": "DISCARDED",
}

ALL_STATUSES = tuple(STATUS_LABELS)
ACTIVE_STATUSES = ("created", "running", "success")

TEMPLATE_TECHNOLOGIES = {
    "python": ["Python"],
    "node": ["Node.js"],
    "general": [],
}

MANAGED_BLOCK_RE = re.compile(
    r"<!--\s*lab:(?P<name>[A-Za-z0-9_-]+):start\s*-->"
    r".*?"
    r"<!--\s*lab:(?P=name):end\s*-->",
    re.DOTALL,
)


def strip_managed_blocks(text: str) -> str:
    """Drop README sections owned by lab-workflow.

    Used to tell apart tool-generated metadata churn from real user edits
    when checking a repository for uncommitted work.
    """

    return MANAGED_BLOCK_RE.sub("", text)


def now_local() -> datetime:
    """Current local time, truncated to whole seconds."""

    return datetime.now().astimezone().replace(microsecond=0)


def now_iso() -> str:
    return now_local().isoformat()


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO timestamp from the registry.

    Naive timestamps are interpreted in the local timezone. Unparseable
    values return ``None`` instead of raising, so a hand-edited registry
    cannot crash read-only commands.
    """

    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now_local().tzinfo)
    return dt


def format_date(value: Any) -> str:
    dt = parse_timestamp(value)
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M")


def format_age(value: Any, now: datetime | None = None) -> str:
    """Human readable age such as ``3 天前``."""

    dt = parse_timestamp(value)
    if dt is None:
        return "-"
    now = now or now_local()
    seconds = max(0.0, (now - dt).total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def format_age_short(value: Any, now: datetime | None = None) -> str:
    """Compact ASCII age such as ``3d``, for table columns."""

    dt = parse_timestamp(value)
    if dt is None:
        return "-"
    now = now or now_local()
    seconds = max(0.0, (now - dt).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def age_days(value: Any, now: datetime | None = None) -> float | None:
    dt = parse_timestamp(value)
    if dt is None:
        return None
    now = now or now_local()
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def compact_number(value: float) -> str:
    """Format a retention duration without a useless trailing ``.0``."""

    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status.upper())


def normalize_status(value: str) -> str:
    status = value.strip().lower()
    if status not in STATUS_LABELS:
        allowed = ", ".join(ALL_STATUSES)
        raise ValueError(f"未知状态 '{value}'；可用状态: {allowed}")
    return status


def parse_status_list(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            result.append(normalize_status(part))
    return unique(result)


def slugify(raw: str) -> str:
    """Normalize an experiment name to kebab-case.

    Spaces and underscores become dashes; uppercase letters are lowered.
    Everything else outside ``[a-z0-9-]`` is rejected.
    """

    if raw is None or not str(raw).strip():
        raise ValueError("实验名称不能为空")
    slug = str(raw).strip().lower()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ValueError("实验名称不能为空")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise ValueError(
            "实验名称必须是 kebab-case（小写字母、数字、连字符），例如 my-idea"
        )
    if len(slug) > 48:
        raise ValueError("实验名称过长（最多 48 个字符）")
    compat.validate_portable_slug(slug)
    return slug


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def split_csv(values: Sequence[str] | None) -> list[str]:
    """Flatten repeated and comma-separated command line values."""

    result: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part:
                result.append(part)
    return unique(result)


def display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def truncate(text: Any, width: int) -> str:
    """Collapse whitespace and truncate by terminal display width."""

    value = " ".join(str(text or "").split())
    if width <= 0:
        return ""
    if display_width(value) <= width:
        return value
    out: list[str] = []
    used = 0
    for char in value:
        char_width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + char_width > width - 1:
            break
        out.append(char)
        used += char_width
    return "".join(out).rstrip() + "…"


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    max_widths: Sequence[int] | None = None,
) -> str:
    """Render a simple left-aligned text table."""

    string_rows = [[str(cell) for cell in row] for row in rows]
    widths = [display_width(header) for header in headers]
    for row in string_rows:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], display_width(cell))
    if max_widths:
        for index, cap in enumerate(max_widths):
            if index < len(widths):
                widths[index] = min(widths[index], cap)

    def clipped(value: str, index: int) -> str:
        cap = widths[index]
        if max_widths and index < len(max_widths):
            cap = min(cap, max_widths[index])
        return truncate(value, cap)

    header_line = "  ".join(
        _pad(clipped(header, index), widths[index]) for index, header in enumerate(headers)
    )
    separator = "  ".join("-" * widths[index] for index in range(len(headers)))
    body = [
        "  ".join(
            _pad(clipped(cell, index), widths[index])
            for index, cell in enumerate(row[: len(headers)])
        )
        for row in string_rows
    ]
    return "\n".join([header_line, separator, *body])


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        compat.atomic_replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        compat.atomic_replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def atomic_write_json(path: Path, value: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=indent) + "\n")
