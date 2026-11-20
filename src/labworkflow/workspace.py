"""Workspace layout, initialization and locking."""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import compat
from .errors import LabError, StateError
from .util import atomic_write_text, now_local, now_iso

ASSET_ROOT = Path(__file__).resolve().parent / "assets"
TEMPLATE_ROOT = ASSET_ROOT / "templates"
SKILL_ROOT = ASSET_ROOT / "skills"
SKILL_NAME = "lab-management"
SKILL_PARENTS = (".agent/skills", ".agents/skills")
ROOT_ENV = "LAB_WORKFLOW_ROOT"
AGENTS_MARKER = "<!-- lab:workspace-agents -->"
MANAGED_ASSETS = {
    "AGENTS.md": ASSET_ROOT / "AGENTS.md",
    "README.md": ASSET_ROOT / "workspace_README.md",
    "bin/README.md": ASSET_ROOT / "bin_README.md",
}
SKELETON_DIRS = ("active", "archive", "templates", "registry", "bin")
IGNORED_TREE_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    ".git",
}


def default_root() -> Path:
    """The default workspace root: ``~/lab``."""

    return Path.home() / "lab"


def discover_root(start: Path | None = None) -> Path | None:
    """Find the nearest parent that already looks like a lab workspace."""

    try:
        current = (start or Path.cwd()).expanduser().resolve()
    except OSError:
        return None
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "active").is_dir()
            and (candidate / "registry" / "experiments.jsonl").is_file()
        ):
            return candidate
    return None


def resolve_root(explicit: str | Path | None = None, *, start: Path | None = None) -> Path:
    """Resolve the workspace root without touching the filesystem.

    Precedence: explicit CLI value, ``LAB_WORKFLOW_ROOT``, a workspace
    discovered from ``start``/cwd, then the legacy default ``~/lab``.
    """

    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()

    env_root = os.environ.get(ROOT_ENV)
    if env_root and env_root.strip():
        return Path(env_root).expanduser().resolve()

    discovered = discover_root(start)
    if discovered is not None:
        return discovered
    return default_root().expanduser().resolve()


class Workspace:
    """Paths and lifecycle for one lab workspace."""

    def __init__(self, root: Path | None = None):
        self.root = (root or default_root()).expanduser().resolve()
        self.active = self.root / "active"
        self.archive = self.root / "archive"
        self.templates = self.root / "templates"
        self.registry_dir = self.root / "registry"
        self.registry_path = self.registry_dir / "experiments.jsonl"
        self.lock_path = self.registry_dir / ".lock"
        self.bin_dir = self.root / "bin"

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    def __str__(self) -> str:
        return str(self.root)

    def ensure(self) -> list[str]:
        """Create missing workspace structure; never overwrite user files.

        Returns a list of created paths relative to the workspace root.
        """

        if self.root.exists() and not self.root.is_dir():
            raise LabError(f"工作台路径不是目录: {self.root}")
        try:
            home = Path.home().resolve()
        except OSError:
            home = None
        if home is not None and self.root == home:
            raise LabError(
                "不要把用户主目录直接初始化为工作台；请指定子目录，例如 ~/lab"
            )
        if self.root.parent == self.root:
            raise LabError("不要把文件系统根目录初始化为工作台")

        if not TEMPLATE_ROOT.is_dir():
            raise LabError("lab 安装不完整：找不到内置模板目录")

        created: list[str] = []
        if not self.root.exists():
            self.root.mkdir(parents=True)
            created.append(".")

        for name in SKELETON_DIRS:
            path = self.root / name
            if not path.exists():
                path.mkdir(parents=True)
                created.append(name + "/")
            elif not path.is_dir():
                raise LabError(f"工作台结构冲突：{path} 不是目录")

        mapping = self._mapping()
        for relative, source in MANAGED_ASSETS.items():
            destination = self.root / relative
            if destination.exists():
                continue
            if not source.is_file():
                raise LabError(f"lab 安装不完整：缺少 {source}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            text = source.read_text(encoding="utf-8")
            atomic_write_text(destination, render_template(text, mapping))
            created.append(relative)

        self._copy_missing_tree(TEMPLATE_ROOT, self.templates, created, prefix="templates/")

        if not self.registry_path.exists():
            atomic_write_text(self.registry_path, "")
            created.append("registry/experiments.jsonl")

        created.extend(self.install_skills(refresh=False))
        return created

    # ------------------------------------------------------------------
    # Commands / generated assets
    # ------------------------------------------------------------------
    @property
    def is_default_root(self) -> bool:
        try:
            return self.root == default_root().expanduser().resolve()
        except OSError:
            return False

    @property
    def cli_path(self) -> str:
        """Best known path of the ``lab-workflow`` executable for documentation."""

        return compat.cli_executable(ASSET_ROOT.parents[2])

    def command_hint(self, tail: str = "") -> str:
        """A copy-pasteable command that targets this workspace explicitly."""

        command = self.cli_path
        if " " in command:
            command = f'"{command}"'
        if not self.is_default_root:
            command += f' --root "{self.root}"'
        return f"{command} {tail}".strip()

    def _mapping(self) -> dict[str, object]:
        return {
            "workspace_root": str(self.root),
            "lab_cmd": self.cli_path,
            "date": now_local().strftime("%Y-%m-%d"),
            "generated_at": now_iso(),
            "venv_python": compat.venv_python_rel(),
            "venv_pip": compat.venv_pip_rel(),
            "venv_activate": compat.venv_activate_rel(),
        }

    def install_skills(self, *, refresh: bool) -> list[str]:
        """Install the bundled project-level skills into this workspace.

        Both ``.agent`` (canonical per workspace convention) and ``.agents``
        (the universal/ZCode discovery path) receive a real copy. Copies keep
        Windows free of symlink-privilege requirements and can be refreshed
        together with the other managed assets.
        """

        source = SKILL_ROOT / SKILL_NAME
        if not source.is_dir():
            raise LabError(f"lab-workflow 安装不完整：找不到内置 skill {SKILL_NAME}")

        changed: list[str] = []
        mapping = self._mapping()
        for parent in SKILL_PARENTS:
            destination = self.root / parent / SKILL_NAME
            self._sync_rendered_tree(
                source,
                destination,
                mapping,
                refresh=refresh,
                changed=changed,
                prefix=f"{parent}/{SKILL_NAME}/",
            )
        return changed

    def _sync_rendered_tree(
        self,
        source: Path,
        destination: Path,
        mapping: dict[str, object],
        *,
        refresh: bool,
        changed: list[str],
        prefix: str,
    ) -> None:
        if destination.is_symlink() and (refresh or not destination.exists()):
            destination.unlink()
        if destination.exists() and not destination.is_dir():
            raise LabError(f"skill 路径冲突：{destination} 不是目录")
        destination.mkdir(parents=True, exist_ok=True)

        text_suffixes = {".md", ".txt", ".json", ".yaml", ".yml", ".toml"}
        for item in _template_entries(source):
            target = destination / item.name
            relative = prefix + item.name
            if item.is_dir():
                if target.exists() and not target.is_dir():
                    raise LabError(f"skill 结构冲突：{target} 不是目录")
                if not target.exists():
                    target.mkdir(parents=True)
                    changed.append(relative + "/")
                self._sync_rendered_tree(
                    item,
                    target,
                    mapping,
                    refresh=refresh,
                    changed=changed,
                    prefix=relative + "/",
                )
                continue
            if target.exists() and not refresh:
                continue
            if item.suffix.lower() in text_suffixes:
                text = render_template(item.read_text(encoding="utf-8"), mapping)
                try:
                    current = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    current = None
                if current != text:
                    atomic_write_text(target, text)
                    changed.append(relative)
            else:
                try:
                    unchanged = target.is_file() and target.read_bytes() == item.read_bytes()
                except OSError:
                    unchanged = False
                if not unchanged:
                    shutil.copy2(item, target)
                    changed.append(relative)

    def refresh(self) -> list[str]:
        """Rewrite workspace files generated by lab-workflow.

        This is explicit (``init --refresh``) because templates and the
        workspace README may contain local edits. User experiments and the
        registry are never touched.
        """

        if not TEMPLATE_ROOT.is_dir():
            raise LabError("lab-workflow 安装不完整：找不到内置模板目录")

        changed: list[str] = []
        mapping = self._mapping()
        for relative, source in MANAGED_ASSETS.items():
            if not source.is_file():
                raise LabError(f"lab-workflow 安装不完整：缺少 {source}")
            text = render_template(source.read_text(encoding="utf-8"), mapping)
            destination = self.root / relative
            try:
                current = destination.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                current = None
            if current != text:
                atomic_write_text(destination, text)
                changed.append(relative)

        self._overwrite_tree(TEMPLATE_ROOT, self.templates, changed, prefix="templates/")
        changed.extend(self.install_skills(refresh=True))
        return changed

    def _overwrite_tree(
        self,
        source: Path,
        destination: Path,
        changed: list[str],
        *,
        prefix: str = "",
    ) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for item in _template_entries(source):
            target = destination / item.name
            relative = prefix + item.name
            if item.is_dir():
                if target.exists() and not target.is_dir():
                    raise LabError(f"模板结构冲突：{target} 不是目录")
                target.mkdir(exist_ok=True)
                self._overwrite_tree(item, target, changed, prefix=relative + "/")
            else:
                try:
                    unchanged = target.is_file() and target.read_bytes() == item.read_bytes()
                except OSError:
                    unchanged = False
                if not unchanged:
                    shutil.copy2(item, target)
                    changed.append(relative)

    def _copy_missing_tree(
        self,
        source: Path,
        destination: Path,
        created: list[str],
        *,
        prefix: str = "",
    ) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for item in _template_entries(source):
            target = destination / item.name
            relative = prefix + item.name
            if item.is_dir():
                if target.exists() and not target.is_dir():
                    raise LabError(f"模板结构冲突：{target} 不是目录")
                if not target.exists():
                    target.mkdir()
                    created.append(relative + "/")
                self._copy_missing_tree(item, target, created, prefix=relative + "/")
            else:
                if target.exists():
                    continue
                shutil.copy2(item, target)
                created.append(relative)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    def experiment_path(self, record: dict) -> Path | None:
        """Absolute path stored in a record, guarded against escaping root."""

        relative = record.get("path")
        if not relative:
            return None
        path = (self.root / str(relative)).resolve()
        if path != self.root and not path.is_relative_to(self.root):
            raise StateError(f"registry 中的实验路径越界: {relative}")
        return path

    def relative(self, path: Path) -> str:
        resolved = path.expanduser().resolve()
        if resolved == self.root or not resolved.is_relative_to(self.root):
            raise StateError(f"路径不在工作台内: {path}")
        return resolved.relative_to(self.root).as_posix()

    def archived_path(self, slug: str, when=None):
        when = when or now_local()
        return self.archive / when.strftime("%Y") / when.strftime("%m") / slug

    def available_templates(self) -> list[str]:
        if not self.templates.is_dir():
            return []
        return sorted(
            item.name
            for item in self.templates.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        )

    def template_path(self, name: str) -> Path:
        return self.templates / name

    # ------------------------------------------------------------------
    # Locking
    # ------------------------------------------------------------------
    @contextmanager
    def lock(self, *, exclusive: bool = True) -> Iterator[None]:
        """Serialize registry mutations with an advisory file lock."""

        self.registry_dir.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+b") as handle:
            compat.lock_handle(handle, exclusive=exclusive)
            try:
                yield
            finally:
                compat.unlock_handle(handle)


def _template_entries(source: Path) -> list[Path]:
    """Template entries worth copying; skip caches and environments."""

    return [
        item
        for item in sorted(source.iterdir(), key=lambda entry: entry.name)
        if item.name not in IGNORED_TREE_NAMES and item.suffix != ".pyc"
    ]


def render_template(text: str, mapping: dict[str, object]) -> str:
    """Replace ``{{key}}`` placeholders in one pass."""

    import re

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in mapping:
            return match.group(0)
        return str(mapping[key])

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", replace, text)


def copy_tree_rendered(source: Path, destination: Path, mapping: dict[str, object]) -> None:
    """Copy a template tree, rendering text files through the mapping."""

    destination.mkdir(parents=True, exist_ok=False)
    text_suffixes = {
        ".md",
        ".txt",
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".sh",
        ".cfg",
        ".ini",
        ".gitignore",
    }
    for item in _template_entries(source):
        target = destination / item.name
        if item.is_dir():
            copy_tree_rendered(item, target, mapping)
        else:
            if item.name == ".gitignore" or item.suffix.lower() in text_suffixes:
                try:
                    text = item.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    shutil.copy2(item, target)
                else:
                    atomic_write_text(target, render_template(text, mapping))
                    shutil.copymode(item, target)
            else:
                shutil.copy2(item, target)


def ensure_agents_link(workspace: Workspace, experiment_dir: Path) -> str:
    """Expose the workspace AGENTS.md inside every experiment directory.

    Agent runtimes usually read AGENTS.md from the current directory. A
    symlink keeps the rules current without copying them into every
    experiment. On Windows (or filesystems without symlink support) fall
    back to a copy and report that fact to the caller.

    Returns ``"symlink"``, ``"copy"``, or ``"existing"``.
    """

    source = workspace.root / "AGENTS.md"
    if not source.is_file():
        return "missing"
    link = experiment_dir / "AGENTS.md"
    if link.exists() or link.is_symlink():
        return "existing"
    try:
        os.symlink(source, link)
        return "symlink"
    except OSError:
        shutil.copy2(source, link)
        return "copy"
