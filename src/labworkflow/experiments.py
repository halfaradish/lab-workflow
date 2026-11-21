"""Experiment lifecycle: create, inspect, archive, promote and discard."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import compat, environment, gitutil
from .environment import EnvOutcome
from .errors import LabError, NotFoundError, StateError, UsageError
from .registry import (
    Registry,
    active,
    check_editable,
    load_for_read,
    normalize_ref,
    set_status,
    touch,
)
from .util import (
    ACTIVE_STATUSES,
    TEMPLATE_TECHNOLOGIES,
    atomic_write_json,
    atomic_write_text,
    format_age,
    format_date,
    now_iso,
    now_local,
    slugify,
    status_label,
    unique,
)
from .workspace import (
    AGENTS_MARKER,
    ASSET_ROOT,
    Workspace,
    copy_tree_rendered,
    ensure_agents_link,
    render_template,
)

README_ASSET = ASSET_ROOT / "experiment_README.md"
META_FILENAME = ".lab-meta.json"
MANAGED_READMES = ("metadata", "description", "conclusion")
DESCRIPTION_PLACEHOLDER = "_（待补充：一句话说明这个实验想验证什么。）_"
CONCLUSION_PLACEHOLDER = "_（待补充：实验结束或 promote 前写下结论。）_"


@dataclass
class CreateResult:
    record: dict[str, Any]
    path: Path
    env: EnvOutcome = field(default_factory=EnvOutcome)
    git_message: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class AdoptResult:
    record: dict[str, Any]
    path: Path
    template: str
    git_message: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class UpdateResult:
    record: dict[str, Any]
    previous_status: str = ""
    warnings: list[str] = field(default_factory=list)
    changed: bool = True
    adopted: list[str] = field(default_factory=list)


@dataclass
class ArchiveResult:
    record: dict[str, Any]
    path: Path
    dirty: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PromoteResult:
    record: dict[str, Any]
    target: Path
    source_path: Path
    dirty: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DiscardResult:
    record: dict[str, Any]
    old_path: Path | None
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# README and metadata mirror
# ----------------------------------------------------------------------
def _md_cell(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "—"
    return text.replace("|", "\\|")


def _md_list(values: Any) -> str:
    if isinstance(values, str):
        values = [values]
    items = [str(item).strip() for item in values or [] if str(item).strip()]
    return ", ".join(items) if items else "—"


def metadata_block(record: dict[str, Any]) -> str:
    rows = [
        ("状态", f"**{status_label(record.get('status', ''))}**"),
        ("实验 ID", f"`{record.get('id', '')}`"),
        ("模板", f"`{record.get('template', '')}`"),
        ("分类", _md_cell(record.get("category"))),
        ("摘要", _md_cell(record.get("summary"))),
        ("标签", _md_list(record.get("tags"))),
        ("技术", _md_list(record.get("technologies"))),
        ("相关知识", _md_list(record.get("related_projects"))),
        ("创建于", format_date(record.get("created_at"))),
        ("更新于", format_date(record.get("updated_at"))),
        ("路径", f"`{record.get('path', '')}`"),
    ]
    lines = ["| 字段 | 内容 |", "| --- | --- |"]
    lines.extend(f"| {key} | {value} |" for key, value in rows)
    return "\n".join(lines)


def description_block(record: dict[str, Any]) -> str:
    text = str(record.get("description") or "").strip()
    return text if text else DESCRIPTION_PLACEHOLDER


def conclusion_block(record: dict[str, Any]) -> str:
    text = str(record.get("conclusion") or "").strip()
    return text if text else CONCLUSION_PLACEHOLDER


def render_readme(workspace: Workspace, record: dict[str, Any]) -> str:
    if not README_ASSET.is_file():
        raise LabError("lab 安装不完整：找不到实验 README 模板")
    mapping = {
        "name": record.get("name", ""),
        "id": record.get("id", ""),
        "slug": record.get("slug", ""),
        "template": record.get("template", ""),
        "created_at_display": format_date(record.get("created_at")),
        "workspace_root": str(workspace.root),
        "lab_cmd": workspace.cli_path,
        "venv_python": compat.venv_python_rel(),
        "venv_pip": compat.venv_pip_rel(),
        "venv_activate": compat.venv_activate_rel(),
        "metadata_block": metadata_block(record),
        "description_block": description_block(record),
        "conclusion_block": conclusion_block(record),
    }
    return render_template(README_ASSET.read_text(encoding="utf-8"), mapping)


def _replace_managed(text: str, name: str, content: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(<!-- lab:{re.escape(name)}:start -->).*?(<!-- lab:{re.escape(name)}:end -->)",
        re.DOTALL,
    )
    if not pattern.search(text):
        return text, False

    def replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}\n{content}\n{match.group(2)}"

    return pattern.sub(replace, text, count=1), True


def read_managed_block(text: str, name: str) -> str | None:
    """Return the content between one managed marker pair, if present."""

    pattern = re.compile(
        rf"<!--\s*lab:{re.escape(name)}:start\s*-->(.*?)"
        rf"<!--\s*lab:{re.escape(name)}:end\s*-->",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1).strip()


def _render_managed_block(name: str, record: dict[str, Any]) -> str:
    if name == "metadata":
        return metadata_block(record)
    if name == "description":
        return description_block(record)
    return conclusion_block(record)


def reconcile_readme(
    workspace: Workspace,
    record: dict[str, Any],
    previous: dict[str, Any],
    *,
    adopt_description: bool = False,
    protected: tuple[str, ...] = (),
    strict: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    """Detect hand edits in managed blocks before the next render.

    ``previous`` is the registry snapshot whose rendered blocks were last
    written to disk. Comparing against it avoids false positives from the
    tool's own changes (status, updated_at). Returns ``(warnings, adopted,
    conflicts)``; ``record`` is updated in place when content is adopted.

    The conclusion and description blocks are plain user prose, so a
    non-empty hand edit can be pulled back into the registry. The metadata
    block is tool-owned and is always regenerated from the registry.
    """

    path = workspace.experiment_path(record)
    if path is None:
        return [], [], []
    readme = path / "README.md"
    if not readme.is_file():
        return [], [], []
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return [], [], []

    blocks: dict[str, str] = {}
    for name in MANAGED_READMES:
        disk = read_managed_block(text, name)
        if disk is None:
            continue
        if disk == _render_managed_block(name, previous).strip():
            continue
        blocks[name] = disk
    if not blocks:
        return [], [], []

    conflicts = list(blocks)
    if strict:
        names = ", ".join(conflicts)
        raise StateError(
            f"README 受管区块存在手工修改: {names}；--strict 已拒绝覆盖。"
            "受支持的内容可用 meta --adopt-readme 采纳，或先把内容写入 registry"
        )

    warnings: list[str] = []
    adopted: list[str] = []
    for name, disk in blocks.items():
        empty = not disk or disk in {DESCRIPTION_PLACEHOLDER, CONCLUSION_PLACEHOLDER}
        if name == "conclusion":
            if name not in protected and not empty:
                record["conclusion"] = disk
                adopted.append("conclusion")
                warnings.append("README 结论区块有手工修改，已采纳到 registry（未覆盖）")
            elif name in protected:
                warnings.append("README 结论区块的手工内容已被 --conclusion 覆盖")
            else:
                warnings.append(
                    "README 结论区块与 registry 不一致，已按 registry 重新渲染"
                )
        elif name == "description":
            if adopt_description and name not in protected and not empty:
                record["description"] = disk
                adopted.append("description")
                warnings.append("README 描述区块有手工修改，已采纳到 registry（未覆盖）")
            elif name in protected:
                warnings.append("README 描述区块的手工内容已被 --description 覆盖")
            else:
                warnings.append(
                    "README 描述区块有手工修改，将按 registry 重新渲染；"
                    "如需保留请用 meta --adopt-readme"
                )
        else:
            warnings.append(
                "README 元数据区块有手工修改，将按 registry 重新渲染；"
                "请用 meta 更新元数据"
            )
    return warnings, adopted, conflicts


def sync_readme(workspace: Workspace, record: dict[str, Any]) -> list[str]:
    """Refresh the managed blocks of an experiment README.

    User-written sections are never touched. A README whose markers were
    removed is left alone deliberately.
    """

    path = workspace.experiment_path(record)
    if path is None:
        return []
    readme = path / "README.md"
    if not path.exists():
        return [f"实验目录不存在，无法更新 README: {path}"]
    if not readme.exists():
        try:
            atomic_write_text(readme, render_readme(workspace, record))
        except OSError as exc:
            return [f"无法生成 README: {exc}"]
        return [f"README 缺失，已重新生成: {readme}"]
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"无法读取 README: {exc}"]
    original = text
    text, _ = _replace_managed(text, "metadata", metadata_block(record))
    text, _ = _replace_managed(text, "description", description_block(record))
    text, _ = _replace_managed(text, "conclusion", conclusion_block(record))
    if text != original:
        try:
            atomic_write_text(readme, text)
        except OSError as exc:
            return [f"无法更新 README: {exc}"]
    return []


def write_meta(workspace: Workspace, record: dict[str, Any]) -> list[str]:
    path = workspace.experiment_path(record)
    if path is None or not path.exists():
        return []
    target = path / META_FILENAME
    try:
        if target.is_file():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing == record:
                return []
    except (OSError, json.JSONDecodeError):
        pass
    try:
        atomic_write_json(target, record)
    except OSError as exc:
        return [f"无法写入 .lab-meta.json: {exc}"]
    return []


def _sync_all(workspace: Workspace, record: dict[str, Any]) -> list[str]:
    warnings = write_meta(workspace, record)
    warnings.extend(sync_readme(workspace, record))
    return warnings


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------
def _normalize_name(raw: str) -> tuple[str, str]:
    slug = slugify(raw)
    display = " ".join(str(raw or "").strip().split()) or slug
    return display, slug


def _unique_slug(records: list[dict[str, Any]], slug: str) -> None:
    for record in records:
        if record.get("slug") == slug:
            raise UsageError(
                f"名称 '{slug}' 已被实验 {record.get('id')} 使用；请换一个名称"
            )


def _default_technologies(template: str, provided: list[str]) -> list[str]:
    values = [*TEMPLATE_TECHNOLOGIES.get(template, []), *provided]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item or item.casefold() in seen:
            continue
        seen.add(item.casefold())
        result.append(item)
    return result


def _template_label(template: str) -> str:
    return {
        "python": "Python",
        "node": "Node.js",
        "general": "通用",
    }.get(template, template)


def create_experiment(
    workspace: Workspace,
    name: str,
    *,
    template: str = "general",
    description: str = "",
    tags: list[str] | None = None,
    category: str = "",
    technologies: list[str] | None = None,
    related_projects: list[str] | None = None,
    no_git: bool = False,
    no_env: bool = False,
) -> CreateResult:
    display_name, slug = _normalize_name(name)
    if workspace.registry_path.parent.is_dir():
        available = [
            item.name
            for item in workspace.templates.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        ] if workspace.templates.is_dir() else []
        if template not in available:
            allowed = ", ".join(sorted(available)) or "无"
            raise UsageError(f"未知模板 '{template}'；可用模板: {allowed}")

    result = CreateResult(record={}, path=Path(), env=EnvOutcome())
    created_dir: Path | None = None
    with workspace.lock():
        records = Registry(workspace).load()
        _unique_slug(records, slug)
        registry = Registry(workspace)
        identifier = registry.next_id(records)
        created_at = now_iso()
        folder = f"{identifier}-{slug}"
        path = workspace.active / folder
        if path.exists():
            raise StateError(f"实验目录已存在但不是 registry 记录（孤立目录）: {path}")
        template_dir = workspace.template_path(template)
        if not template_dir.is_dir():
            raise UsageError(f"模板目录不存在: {template_dir}")

        display_template = _template_label(template)
        mapping = {
            "id": identifier,
            "name": display_name,
            "slug": slug,
            "folder": folder,
            "template": template,
            "date": format_date(created_at),
            "created_at": created_at,
            "workspace_root": str(workspace.root),
            "lab_cmd": workspace.cli_path,
            "venv_python": compat.venv_python_rel(),
            "venv_pip": compat.venv_pip_rel(),
            "venv_activate": compat.venv_activate_rel(),
            "description": description.strip(),
        }
        try:
            copy_tree_rendered(template_dir, path, mapping)
            if ensure_agents_link(workspace, path) == "copy":
                result.warnings.append(
                    "文件系统不支持符号链接，AGENTS.md 已复制（工作台规则更新后需手动同步）"
                )
            created_dir = path

            record: dict[str, Any] = {
                "id": identifier,
                "name": display_name,
                "slug": slug,
                "path": workspace.relative(path),
                "status": "created",
                "template": template,
                "template_label": display_template,
                "created_at": created_at,
                "updated_at": created_at,
                "status_changed_at": created_at,
                "description": description.strip(),
                "tags": unique(str(tag).strip() for tag in (tags or []) if str(tag).strip()),
                "category": category.strip(),
                "summary": "",
                "conclusion": "",
                "technologies": _default_technologies(template, technologies or []),
                "related_projects": unique(
                    str(item).strip() for item in (related_projects or []) if str(item).strip()
                ),
                "git": False,
                "env": None,
            }
            atomic_write_text(path / "README.md", render_readme(workspace, record))

            env_outcome = environment.setup_environment(path, template, enabled=not no_env)
            record["env"] = env_outcome.record
            if no_env and template in {"python", "node"}:
                record["env_skipped"] = True

            if no_git:
                git_message = "已跳版本控制（--no-git）"
            else:
                git_ok, git_message = gitutil.init_repo(path)
                record["git"] = bool(git_ok)
            result.git_message = git_message

            registry.write_record(record)
            result.record = record
            result.path = path
            result.env = env_outcome
            result.warnings.extend(env_outcome.warnings)
            result.warnings.extend(_sync_all(workspace, record))
        except Exception:
            if created_dir is not None and created_dir.exists():
                compat.safe_rmtree(created_dir, ignore_errors=True)
            raise
    return result


# ----------------------------------------------------------------------
# Adopt existing directories (typically created by try-rs)
# ----------------------------------------------------------------------
_EXTERNAL_DATE_PATTERNS = (
    (re.compile(r"^(\d{8})-(\d{1,3})-(.+)$"), True),
    (re.compile(r"^(\d{4}-\d{2}-\d{2})[-_](.+)$"), False),
    (re.compile(r"^(\d{8})[-_](.+)$"), False),
)


def split_external_name(basename: str) -> tuple[str, str | None]:
    """Split a try-rs style folder name into ``(display name, embedded id)``.

    ``20261120-001-api-test`` keeps its id; ``2026-11-20-rust-test`` and
    ``rust-test`` only supply a name. Undated names are used as-is.
    """

    for pattern, embeds_id in _EXTERNAL_DATE_PATTERNS:
        match = pattern.match(basename)
        if not match:
            continue
        if embeds_id:
            return match.group(3), f"{match.group(1)}-{int(match.group(2)):03d}"
        return match.group(2), None
    return basename, None


def detect_template(path: Path) -> str:
    """Best-effort template detection for a directory created elsewhere."""

    if (path / "package.json").is_file():
        return "node"
    python_markers = ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile")
    if any((path / marker).is_file() for marker in python_markers):
        return "python"
    suffixes: set[str] = set()
    try:
        for entry in path.iterdir():
            if entry.is_file() and entry.suffix:
                suffixes.add(entry.suffix.lower())
    except OSError:
        return "general"
    if ".py" in suffixes:
        return "python"
    if suffixes & {".js", ".mjs", ".cjs", ".ts"}:
        return "node"
    return "general"


def _detect_env(path: Path, template: str) -> dict[str, Any] | None:
    venv = path / ".venv"
    if template == "python" and compat.venv_python(venv).exists():
        return {
            "type": "python",
            "path": ".venv",
            "ok": True,
            "pip": compat.venv_pip(venv).exists(),
        }
    if template == "node" and (path / "node_modules").is_dir():
        return {"type": "node", "path": "node_modules", "ok": True}
    return None


def _resolve_adopt_path(workspace: Workspace, raw: str) -> Path:
    candidate = Path(raw).expanduser()
    options = (
        [candidate]
        if candidate.is_absolute()
        else [Path.cwd() / candidate, workspace.active / candidate]
    )
    for option in options:
        if option.is_dir():
            return option.resolve()
    raise NotFoundError(f"要 adopt 的目录不存在: {raw}")


def adopt_experiment(
    workspace: Workspace,
    raw_path: str,
    *,
    name: str | None = None,
    template: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
    category: str = "",
    technologies: list[str] | None = None,
    related_projects: list[str] | None = None,
    no_git: bool = False,
) -> AdoptResult:
    """Register an existing directory without moving or rewriting its code.

    This is the bridge from try-rs: try-rs owns browsing/creation, while
    lab-workflow adds metadata, lifecycle and clean-up. Adoption never
    deletes user files and never overwrites an existing README.
    """

    path = _resolve_adopt_path(workspace, raw_path)
    active_root = workspace.active.resolve()
    if path == active_root or not path.is_relative_to(active_root):
        raise UsageError(
            f"adopt 只接受 {workspace.active} 下的目录: {path}\n"
            "可以先把目录移入 active/，或用 lab-workflow new 重新创建。"
        )

    result = AdoptResult(record={}, path=path, template="")
    with workspace.lock():
        registry = Registry(workspace)
        records = registry.load()
        known = {
            candidate.resolve()
            for candidate in (record_path(workspace, record) for record in records)
            if candidate is not None
        }
        if path in known:
            raise StateError(f"目录已经登记在 registry 中: {path}")

        derived_name, embedded_id = split_external_name(path.name)
        slug = slugify(name or derived_name)
        _unique_slug(records, slug)
        identifier = embedded_id or registry.next_id(records)
        if any(str(record.get("id")) == identifier for record in records):
            raise StateError(f"实验 ID 已存在: {identifier}")

        chosen_template = template or detect_template(path)
        if not workspace.template_path(chosen_template).is_dir():
            available = ", ".join(workspace.available_templates()) or "无"
            raise UsageError(f"未知模板 '{chosen_template}'；可用模板: {available}")

        created_at = now_iso()
        record: dict[str, Any] = {
            "id": identifier,
            "name": name or derived_name,
            "slug": slug,
            "path": workspace.relative(path),
            "status": "created",
            "template": chosen_template,
            "template_label": _template_label(chosen_template),
            "created_at": created_at,
            "updated_at": created_at,
            "status_changed_at": created_at,
            "description": description.strip(),
            "tags": unique(str(tag).strip() for tag in (tags or []) if str(tag).strip()),
            "category": category.strip(),
            "summary": "",
            "conclusion": "",
            "technologies": _default_technologies(chosen_template, technologies or []),
            "related_projects": unique(
                str(item).strip() for item in (related_projects or []) if str(item).strip()
            ),
            "git": False,
            "env": _detect_env(path, chosen_template),
            "source": "adopt",
            "adopted_at": created_at,
        }
        result.template = chosen_template
        before = {entry.name for entry in path.iterdir()}
        try:
            readme = path / "README.md"
            if not readme.exists():
                atomic_write_text(readme, render_readme(workspace, record))
            else:
                try:
                    text = readme.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                if "<!-- lab:metadata:start -->" not in text:
                    result.warnings.append(
                        "README 已存在但没有 lab-workflow 标记块，元数据不会同步进 README"
                    )
                else:
                    snapshot = copy.deepcopy(record)
                    warnings, _, _ = reconcile_readme(workspace, record, snapshot)
                    result.warnings.extend(warnings)
            if ensure_agents_link(workspace, path) == "copy":
                result.warnings.append(
                    "文件系统不支持符号链接，AGENTS.md 已复制（工作台规则更新后需手动同步）"
                )

            if no_git:
                existing = gitutil.is_repo(path)
                record["git"] = existing
                result.git_message = (
                    "沿用已有 git 仓库（--no-git）" if existing else "已跳过版本控制（--no-git）"
                )
            elif gitutil.is_repo(path):
                gitutil.write_excludes(path)
                gitutil.configure_repo(path)
                record["git"] = True
                result.git_message = "沿用已有 git 仓库（未提交）"
                dirty = gitutil.status_lines(path) or []
                if dirty:
                    result.warnings.append(
                        f"目录有 {len(dirty)} 项未提交更改，已原样保留"
                    )
            else:
                ok, message = gitutil.init_repo(path)
                record["git"] = bool(ok)
                result.git_message = message

            registry.write_record(record)
            result.record = record
            result.warnings.extend(_sync_all(workspace, record))
        except Exception:
            for cleanup_name in (META_FILENAME, "README.md", "AGENTS.md"):
                if cleanup_name in before:
                    continue
                try:
                    leftover = path / cleanup_name
                    if leftover.is_symlink() or leftover.is_file():
                        leftover.unlink()
                except OSError:
                    pass
            raise
    return result


# ----------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------
def collect_records(
    workspace: Workspace,
    *,
    statuses: list[str] | None = None,
    include_all: bool = False,
    tags: list[str] | None = None,
    template: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    records = load_for_read(workspace)
    if include_all:
        selected = records
    elif statuses:
        wanted = set(statuses)
        selected = [record for record in records if record.get("status") in wanted]
    else:
        selected = [record for record in records if active(record)]
    if tags:
        wanted_tags = {tag.casefold() for tag in tags}
        selected = [
            record
            for record in selected
            if wanted_tags.issubset({str(tag).casefold() for tag in record.get("tags", [])})
        ]
    if template:
        selected = [
            record
            for record in selected
            if str(record.get("template", "")).casefold() == template.casefold()
        ]
    if category:
        selected = [
            record
            for record in selected
            if str(record.get("category", "")).casefold() == category.casefold()
        ]
    selected.sort(key=lambda record: (str(record.get("created_at", "")), str(record.get("id", ""))), reverse=True)
    return selected


def find_experiment(workspace: Workspace, reference: str, records=None) -> dict[str, Any]:
    registry = Registry(workspace)
    return registry.find(normalize_ref(reference), records)


def record_path(workspace: Workspace, record: dict[str, Any]) -> Path | None:
    try:
        return workspace.experiment_path(record)
    except StateError:
        return None


def record_exists(workspace: Workspace, record: dict[str, Any]) -> bool:
    path = record_path(workspace, record)
    return bool(path and path.is_dir())


def show_text(workspace: Workspace, record: dict[str, Any]) -> str:
    path = record_path(workspace, record)
    exists = bool(path and path.is_dir())
    lines = [
        f"实验: {record.get('name', '')}",
        f"ID: {record.get('id', '')}",
        f"状态: {status_label(record.get('status', ''))}"
        f"（{format_date(record.get('status_changed_at') or record.get('created_at'))}，"
        f"{format_age(record.get('status_changed_at') or record.get('created_at'))}）",
        f"路径: {path if path else record.get('path', '-')}"
        + ("" if exists or record.get("status") in {"discarded", "promoted"} else "  ⚠ 目录缺失"),
        f"创建: {format_date(record.get('created_at'))}",
        f"更新: {format_date(record.get('updated_at'))}",
        f"模板: {record.get('template', '-')}",
        f"分类: {record.get('category') or '未分类'}",
        f"标签: {_md_list(record.get('tags'))}",
        f"技术: {_md_list(record.get('technologies'))}",
        f"相关知识: {_md_list(record.get('related_projects'))}",
    ]
    if record.get("git"):
        dirty = []
        head = None
        if exists and path:
            dirty = gitutil.status_lines(path) or []
            head = gitutil.head_commit(path)
        state = "干净" if not dirty else f"有 {len(dirty)} 项未提交更改"
        lines.append(f"Git: {state}" + (f"（{head}）" if head else ""))
    else:
        lines.append("Git: 未初始化")
    lines.append(
        "环境: 未创建（--no-env）"
        if record.get("env_skipped")
        else f"环境: {environment.environment_summary(record.get('env'))}"
    )
    if record.get("adopted_at"):
        lines.append(f"登记于: {format_date(record.get('adopted_at'))}（adopt）")
    if record.get("archived_at"):
        lines.append(f"归档于: {format_date(record.get('archived_at'))}")
    if record.get("promoted_at"):
        lines.append(f"promote 于: {format_date(record.get('promoted_at'))}")
        lines.append(f"promote 到: {record.get('promoted_to', '-')}")
    if record.get("discarded_at"):
        reason = record.get("discarded_reason")
        suffix = f"（{reason}）" if reason else ""
        lines.append(f"删除于: {format_date(record.get('discarded_at'))}{suffix}")
    if exists and path:
        lines.append(f"README: {path / 'README.md'}")
    lines.append("")
    if record.get("description"):
        lines.append("描述:")
        lines.append(str(record["description"]))
    if record.get("summary"):
        lines.append("摘要:")
        lines.append(str(record["summary"]))
    if record.get("conclusion"):
        lines.append("结论:")
        lines.append(str(record["conclusion"]))
    history = record.get("history")
    if isinstance(history, list):
        recent = [entry for entry in history if isinstance(entry, dict)][-3:]
        if recent:
            lines.append("")
            lines.append("状态历史（最近 3 条）:")
            for entry in recent:
                source = status_label(str(entry.get("from") or "?"))
                target = status_label(str(entry.get("to") or "?"))
                lines.append(f"  {source} → {target}  {format_date(entry.get('at'))}")
    return "\n".join(lines).rstrip()


# ----------------------------------------------------------------------
# Update
# ----------------------------------------------------------------------
def _require_editable(workspace: Workspace, reference: str) -> dict[str, Any]:
    record = find_experiment(workspace, reference)
    check_editable(record)
    return record


def update_status(workspace: Workspace, reference: str, status: str) -> UpdateResult:
    wanted = status.strip().lower()
    aliases = {"start": "running", "done": "success", "ok": "success"}
    wanted = aliases.get(wanted, wanted)
    if wanted not in ACTIVE_STATUSES:
        raise UsageError(
            "status 只接受 created / running / success；归档请用 lab archive"
        )
    with workspace.lock():
        registry = Registry(workspace)
        record = find_experiment(workspace, reference, registry.load())
        check_editable(record)
        previous = str(record.get("status", ""))
        if previous == wanted:
            return UpdateResult(record=record, previous_status=previous, changed=False)
        snapshot = copy.deepcopy(record)
        set_status(record, wanted)
        warnings, adopted, _ = reconcile_readme(workspace, record, snapshot)
        registry.write_record(record)
        warnings.extend(_sync_all(workspace, record))
    return UpdateResult(
        record=record,
        previous_status=previous,
        warnings=warnings,
        adopted=adopted,
    )


def update_meta(
    workspace: Workspace,
    reference: str,
    *,
    description: str | None = None,
    summary: str | None = None,
    conclusion: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    technologies: list[str] | None = None,
    related_projects: list[str] | None = None,
    adopt_readme: bool = False,
    strict: bool = False,
) -> UpdateResult:
    provided = [
        value
        for value in (description, summary, conclusion, category, tags, technologies, related_projects)
        if value is not None
    ]
    if not provided and not adopt_readme:
        raise UsageError("至少提供一个要更新的字段")
    with workspace.lock():
        registry = Registry(workspace)
        record = find_experiment(workspace, reference, registry.load())
        check_editable(record)
        snapshot = copy.deepcopy(record)
        updates: dict[str, Any] = {}
        if description is not None:
            updates["description"] = description.strip()
        if summary is not None:
            updates["summary"] = summary.strip()
        if conclusion is not None:
            updates["conclusion"] = conclusion.strip()
        if category is not None:
            updates["category"] = category.strip()
        if tags is not None:
            updates["tags"] = unique(tag.strip() for tag in tags if tag.strip())
        if technologies is not None:
            updates["technologies"] = unique(item.strip() for item in technologies if item.strip())
        if related_projects is not None:
            updates["related_projects"] = unique(
                item.strip() for item in related_projects if item.strip()
            )

        def unchanged(key: str, value: Any) -> bool:
            current = record.get(key)
            if key in {"tags", "technologies", "related_projects"}:
                return [str(item) for item in (current or [])] == value
            return str(current or "") == value

        protected = tuple(
            name
            for name, value in (("description", description), ("conclusion", conclusion))
            if value is not None
        )
        record.update(updates)
        warnings, adopted, conflicts = reconcile_readme(
            workspace,
            record,
            snapshot,
            adopt_description=adopt_readme,
            protected=protected,
            strict=strict,
        )
        if record == snapshot and not conflicts:
            # No field changed: leave registry, README and mirror untouched
            # instead of bumping updated_at and rewriting identical files.
            return UpdateResult(record=record, changed=False, warnings=warnings)

        touch(record)
        registry.write_record(record)
        warnings.extend(_sync_all(workspace, record))
    return UpdateResult(
        record=record,
        warnings=warnings,
        changed=True,
        adopted=adopted,
    )


# ----------------------------------------------------------------------
# Move helpers
# ----------------------------------------------------------------------
def _dirty_lines(path: Path) -> list[str]:
    return gitutil.status_lines(path) or []


def _dirty_message(lines: list[str], limit: int = 5) -> str:
    shown = "; ".join(line.strip() for line in lines[:limit])
    if len(lines) > limit:
        shown += f"; …（共 {len(lines)} 项）"
    return shown


def _archive_target(workspace: Workspace, record: dict[str, Any]) -> Path:
    return workspace.archived_path(str(record.get("slug")), now_local())


def archive_experiment(workspace: Workspace, reference: str, *, force: bool = False) -> ArchiveResult:
    with workspace.lock():
        registry = Registry(workspace)
        record = find_experiment(workspace, reference, registry.load())
        if record.get("status") == "archived":
            raise StateError(f"实验 {record.get('id')} 已经是 ARCHIVED")
        if record.get("status") not in ACTIVE_STATUSES:
            raise StateError(f"当前状态 {status_label(record.get('status', ''))} 不能归档")
        source = workspace.experiment_path(record)
        if source is None or not source.is_dir():
            raise StateError(f"实验目录不存在: {record.get('path')}")
        dirty = _dirty_lines(source)
        if dirty and not force:
            raise StateError(
                "实验存在未提交更改：" + _dirty_message(dirty) + "。确认后加 --force 再归档"
            )
        target = _archive_target(workspace, record)
        if target.exists():
            raise StateError(f"归档目标已存在: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        previous_status = record.get("status")
        snapshot = copy.deepcopy(record)
        warnings, _, _ = reconcile_readme(workspace, record, snapshot)
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            raise StateError(f"归档移动失败: {exc}") from exc
        try:
            record["path"] = workspace.relative(target)
            set_status(record, "archived")
            record["archived_at"] = now_iso()
            record["archived_from_status"] = previous_status
            registry.write_record(record)
        except Exception:
            try:
                shutil.move(str(target), str(source))
            except OSError:
                pass
            raise
        warnings.extend(_sync_all(workspace, record))
    return ArchiveResult(record=record, path=target, dirty=dirty, warnings=warnings)


# ----------------------------------------------------------------------
# Promote
# ----------------------------------------------------------------------
PROMOTE_IGNORE = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".lab-meta.json",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "$RECYCLE.BIN",
}


def _is_lab_agents(path: Path) -> bool:
    try:
        return AGENTS_MARKER in path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return False


def _copy_project(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        name = entry.name
        if name in PROMOTE_IGNORE:
            continue
        destination = target / name
        if name == "AGENTS.md" and (entry.is_symlink() or _is_lab_agents(entry)):
            # The workspace-level AGENTS.md is a lab-internal link/copy.
            continue
        if entry.is_symlink():
            try:
                os.symlink(
                    os.readlink(entry),
                    destination,
                    target_is_directory=entry.is_dir(),
                )
            except OSError:
                if entry.is_dir():
                    shutil.copytree(entry, destination, symlinks=False)
                else:
                    shutil.copy2(entry, destination)
        elif entry.is_dir():
            _copy_project(entry, destination)
        elif entry.is_file():
            shutil.copy2(entry, destination)


def default_promote_target(record: dict[str, Any]) -> Path:
    return Path.home() / "workspace" / str(record.get("slug", ""))


def promote_experiment(
    workspace: Workspace,
    reference: str,
    *,
    target: Path | None = None,
    force: bool = False,
) -> PromoteResult:
    with workspace.lock():
        registry = Registry(workspace)
        record = find_experiment(workspace, reference, registry.load())
        status = record.get("status")
        if status == "promoted":
            raise StateError(
                f"实验 {record.get('id')} 已经 promote 到 {record.get('promoted_to')}"
            )
        if status == "discarded":
            raise StateError(f"实验 {record.get('id')} 已删除，无法 promote")
        if status not in {"success", "archived"}:
            raise StateError(
                "只有 SUCCESS 或 ARCHIVED 的实验可以 promote；"
                f"当前是 {status_label(status or '')}，可先运行 lab status {record.get('id')} success"
            )
        source = workspace.experiment_path(record)
        if source is None or not source.is_dir():
            raise StateError(f"实验目录不存在: {record.get('path')}")

        dirty = _dirty_lines(source)
        if dirty and not force:
            raise StateError(
                "实验存在未提交更改：" + _dirty_message(dirty) + "。确认后加 --force 再 promote"
            )

        destination = (target or default_promote_target(record)).expanduser()
        if not destination.is_absolute():
            destination = (Path.cwd() / destination).resolve()
        else:
            destination = destination.resolve()
        if destination == workspace.root or destination.is_relative_to(workspace.root):
            raise UsageError(f"promote 目标不能位于工作台 {workspace.root} 内")
        if destination == source or destination.is_relative_to(source):
            raise UsageError("promote 目标不能是实验目录本身")
        if destination.exists():
            if not destination.is_dir():
                raise StateError(f"promote 目标已存在且不是目录: {destination}")
            if any(destination.iterdir()):
                raise StateError(f"promote 目标目录非空，请换一个空目录: {destination}")

        snapshot = copy.deepcopy(record)
        warnings, _, _ = reconcile_readme(workspace, record, snapshot)

        created_target = not destination.exists()
        try:
            _copy_project(source, destination)
        except OSError as exc:
            if created_target and destination.exists():
                compat.safe_rmtree(destination, ignore_errors=True)
            raise StateError(f"复制实验文件失败: {exc}") from exc

        previous_status = status
        archive_path: Path | None = None
        if status == "success":
            candidate = _archive_target(workspace, record)
            if candidate.exists():
                if created_target:
                    compat.safe_rmtree(destination, ignore_errors=True)
                raise StateError(f"归档目标已存在: {candidate}")
            candidate.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(source), str(candidate))
            except OSError as exc:
                if created_target:
                    compat.safe_rmtree(destination, ignore_errors=True)
                raise StateError(f"promote 后归档源实验失败: {exc}") from exc
            archive_path = candidate
            record["path"] = workspace.relative(candidate)
            record["archived_at"] = now_iso()
            record["archived_from_status"] = previous_status

        try:
            set_status(record, "promoted")
            record["promoted_at"] = now_iso()
            record["promoted_to"] = str(destination)
            registry.write_record(record)
        except Exception:
            if archive_path is not None and archive_path.exists():
                try:
                    shutil.move(str(archive_path), str(source))
                except OSError:
                    pass
            if created_target:
                compat.safe_rmtree(destination, ignore_errors=True)
            raise
        warnings.extend(_sync_all(workspace, record))
    return PromoteResult(
        record=record,
        target=destination,
        source_path=archive_path or source,
        dirty=dirty,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Discard / clean deletion
# ----------------------------------------------------------------------
def _trash_root(workspace: Workspace) -> Path:
    root = workspace.root / ".trash"
    root.mkdir(parents=True, exist_ok=True)
    return root


def discard_experiment(
    workspace: Workspace,
    reference: str,
    *,
    reason: str = "manual",
) -> DiscardResult:
    """Delete an experiment directory and retain a registry tombstone.

    Callers are responsible for confirmation; this function is also used
    by ``clean`` after its own safety checks.
    """

    with workspace.lock():
        registry = Registry(workspace)
        record = find_experiment(workspace, reference, registry.load())
        status = record.get("status")
        if status == "discarded":
            raise StateError(f"实验 {record.get('id')} 已经是 DISCARDED")
        if status == "promoted":
            raise StateError(f"实验 {record.get('id')} 已 promote，不能再 discard")
        source = workspace.experiment_path(record)
        old_path = source
        existing = bool(source and source.is_dir())
        trash_parent: Path | None = None
        trashed: Path | None = None
        if source and source.exists():
            trash_parent = Path(tempfile.mkdtemp(prefix="discard-", dir=_trash_root(workspace)))
            trashed = trash_parent / "experiment"
            try:
                shutil.move(str(source), str(trashed))
            except OSError as exc:
                compat.safe_rmtree(trash_parent, ignore_errors=True)
                raise StateError(f"无法移走实验目录: {exc}") from exc

        previous_status = status
        try:
            if trashed is not None:
                set_status(record, "discarded")
                record["discarded_at"] = now_iso()
                record["discarded_reason"] = reason
                record["discarded_from_status"] = previous_status
            else:
                # Missing directory: still close the record.
                set_status(record, "discarded")
                record["discarded_at"] = now_iso()
                record["discarded_reason"] = reason + " (目录已缺失)"
            registry.write_record(record)
        except Exception:
            if trashed is not None and source is not None and trashed.exists():
                try:
                    shutil.move(str(trashed), str(source))
                except OSError:
                    pass
            if trash_parent is not None:
                compat.safe_rmtree(trash_parent, ignore_errors=True)
            raise

        warnings: list[str] = []
        if trash_parent is not None:
            try:
                compat.safe_rmtree(trash_parent)
            except OSError as exc:
                warnings.append(f"临时删除目录清理失败（不影响 registry）: {exc}")
    return DiscardResult(record=record, old_path=old_path if existing else None, warnings=warnings)
