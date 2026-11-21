"""JSONL-backed experiment registry."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .errors import LabError, NotFoundError, StateError, UsageError
from .util import ACTIVE_STATUSES, ALL_STATUSES, now_iso, now_local
from .workspace import Workspace

ID_RE = re.compile(r"^(\d{8})-(\d{1,3})$")
# Also normalize the id prefix of a directory-style reference such as
# ``20261120-1-demo`` so shorthand works with directory names too.
EMBEDDED_ID_RE = re.compile(r"^(\d{8})-(\d{1,3})(?=-)")


def normalize_ref(value: str) -> str:
    """Accept ``20261120-1`` as shorthand for ``20261120-001``."""

    text = value.strip()
    match = ID_RE.match(text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):03d}"
    match = EMBEDDED_ID_RE.match(text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):03d}{text[match.end():]}"
    return text


def _reference_forms(record: dict[str, Any]) -> set[str]:
    """Values a user may type to address one experiment.

    Besides the canonical id and slug, experiments are commonly referenced
    by their directory name (``20261120-001-demo``), which is exactly
    ``<id>-<slug>`` for created experiments and may differ for adopted ones.
    """

    identifier = str(record.get("id") or "")
    slug = str(record.get("slug") or "")
    forms = {identifier, slug}
    if identifier and slug:
        forms.add(f"{identifier}-{slug}")
    path = str(record.get("path") or "").replace("\\", "/").rstrip("/")
    if path:
        forms.add(path.rsplit("/", 1)[-1])
    return {form for form in forms if form}


class Registry:
    """Read and write ``registry/experiments.jsonl``.

    Each line is a full snapshot of one experiment. Rewriting the whole
    file is cheap at personal-workspace scale and keeps the format
    append-friendly for manual inspection and backups.
    """

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.path = workspace.registry_path

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LabError(
                    f"registry 第 {number} 行不是合法 JSON（{exc.msg}），"
                    f"请修复 {self.path} 或从备份恢复"
                ) from exc
            if not isinstance(record, dict) or not record.get("id"):
                raise LabError(f"registry 第 {number} 行缺少实验 id")
            if record["id"] in seen:
                raise LabError(f"registry 中存在重复实验 id: {record['id']}")
            seen.add(record["id"])
            records.append(record)
        return records

    def save(self, records: Iterable[dict[str, Any]]) -> None:
        ordered = sorted(records, key=lambda item: (item.get("id", ""), item.get("created_at", "")))
        lines = [json.dumps(record, ensure_ascii=False, sort_keys=False) for record in ordered]
        from .util import atomic_write_text

        atomic_write_text(self.path, "\n".join(lines) + ("\n" if lines else ""))

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def find(self, reference: str, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        records = self.load() if records is None else records
        reference = reference.strip()
        if not reference:
            raise UsageError("实验标识不能为空")
        normalized = normalize_ref(reference)
        for record in records:
            if record.get("id") == normalized:
                return record
        # Adopted experiments keep their original directory name, which may
        # not follow the <id>-<slug> convention. Accept it as an exact alias.
        for record in records:
            forms = _reference_forms(record)
            if reference in forms:
                return record
        slug_matches = [r for r in records if r.get("slug") == reference]
        if len(slug_matches) == 1:
            return slug_matches[0]
        name_matches = [r for r in records if r.get("name") == reference]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(slug_matches) > 1 or len(name_matches) > 1:
            matches = slug_matches or name_matches
            ids = ", ".join(str(item.get("id")) for item in matches)
            raise LabError(f"标识 '{reference}' 对应多个实验，请使用 id: {ids}")
        prefix_matches = [
            record
            for record in records
            if any(form.startswith(normalized) for form in _reference_forms(record))
        ]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(prefix_matches) > 1:
            ids = ", ".join(str(item.get("id")) for item in prefix_matches)
            raise LabError(
                f"标识 '{reference}' 匹配多个实验，请使用更精确的 id: {ids}"
            )
        raise NotFoundError(f"找不到实验 '{reference}'")

    def next_id(self, records: list[dict[str, Any]] | None = None, *, when=None) -> str:
        records = self.load() if records is None else records
        prefix = (when or now_local()).strftime("%Y%m%d")
        highest = 0
        for record in records:
            identifier = str(record.get("id", ""))
            if identifier.startswith(prefix + "-"):
                try:
                    highest = max(highest, int(identifier.rsplit("-", 1)[1]))
                except ValueError:
                    continue
        if highest >= 999:
            raise StateError(f"{prefix} 当天实验编号已用尽（999）")
        return f"{prefix}-{highest + 1:03d}"

    def write_record(self, record: dict[str, Any]) -> None:
        records = self.load()
        for index, existing in enumerate(records):
            if existing.get("id") == record.get("id"):
                records[index] = record
                break
        else:
            records.append(record)
        self.save(records)

    def all_ids(self) -> set[str]:
        return {str(record.get("id")) for record in self.load()}


# ----------------------------------------------------------------------
# Record mutation helpers
# ----------------------------------------------------------------------
TRANSITIONS: dict[str, set[str]] = {
    "created": {"running", "success", "archived", "discarded"},
    "running": {"success", "archived", "discarded"},
    "success": {"running", "archived", "promoted", "discarded"},
    "archived": {"promoted", "discarded"},
    "promoted": set(),
    "discarded": set(),
}


def touch(record: dict[str, Any], *, when: str | None = None) -> dict[str, Any]:
    stamp = when or now_iso()
    record["updated_at"] = stamp
    return record


def set_status(record: dict[str, Any], status: str, *, when: str | None = None) -> None:
    if status not in ALL_STATUSES:
        raise StateError(f"未知状态: {status}")
    previous = record.get("status")
    if previous == status:
        return
    allowed = TRANSITIONS.get(str(previous), set())
    if status not in allowed:
        raise StateError(f"实验 {record.get('id')} 不能从 {previous} 变更为 {status}")
    stamp = when or now_iso()
    record["status"] = status
    record["status_changed_at"] = stamp
    record["updated_at"] = stamp


def active(record: dict[str, Any]) -> bool:
    return record.get("status") in ACTIVE_STATUSES


def check_editable(record: dict[str, Any]) -> None:
    status = record.get("status")
    if status in {"archived", "promoted", "discarded"}:
        raise StateError(
            f"实验 {record.get('id')} 已结束（{str(status).upper()}），"
            "不能再修改状态或元数据；需要继续开发请先评估是否 promote"
        )


def validate_record(record: dict[str, Any]) -> None:
    required = ("id", "name", "slug", "path", "status", "created_at")
    missing = [field for field in required if not record.get(field)]
    if missing:
        raise StateError(f"registry 记录缺少字段: {', '.join(missing)}")
    if record["status"] not in ALL_STATUSES:
        raise StateError(f"registry 记录含未知状态: {record['status']}")


def load_for_read(workspace: Workspace) -> list[dict[str, Any]]:
    registry = Registry(workspace)
    records = registry.load()
    for record in records:
        validate_record(record)
    return records
