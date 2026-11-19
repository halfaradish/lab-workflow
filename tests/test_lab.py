"""End-to-end tests for the repo-local lab CLI.

Every test gets an isolated HOME so the real ~/lab is never touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "bin" / "lab"
PYTHON = sys.executable
ID_RE = re.compile(r"实验已创建: (\d{8}-\d{3})")


class LabCase(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="lab-test-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.lab_root = self.home / "lab"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def run_lab(
        self,
        *args: str,
        input_text: str | None = None,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["XDG_CACHE_HOME"] = str(self.home / ".cache")
        env.pop("LAB_DEBUG", None)
        return subprocess.run(
            [PYTHON, str(LAB), *[str(arg) for arg in args]],
            cwd=str(ROOT),
            env=env,
            text=True,
            input=input_text,
            stdin=subprocess.DEVNULL if input_text is None else None,
            capture_output=True,
            timeout=timeout,
        )

    def init(self) -> None:
        proc = self.run_lab("init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.lab_root.is_dir())

    def create(self, name: str, *args: str) -> str:
        proc = self.run_lab("new", name, "--no-env", *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        match = ID_RE.search(proc.stdout)
        self.assertIsNotNone(match, proc.stdout)
        return match.group(1)

    def records(self) -> list[dict]:
        path = self.lab_root / "registry" / "experiments.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(self, identifier: str) -> dict:
        for record in self.records():
            if record["id"] == identifier:
                return record
        self.fail(f"record not found: {identifier}")

    def exp_path(self, record: dict) -> Path:
        return self.lab_root / record["path"]

    def write_records(self, records: list[dict]) -> None:
        path = self.lab_root / "registry" / "experiments.jsonl"
        path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
            encoding="utf-8",
        )

    def backdate(self, identifier: str, days: float, *, field: str = "created_at") -> None:
        records = self.records()
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        for record in records:
            if record["id"] == identifier:
                record[field] = stamp
                break
        else:
            self.fail(f"record not found: {identifier}")
        self.write_records(records)

    def git_commit_all(self, path: Path) -> None:
        subprocess.run(
            ["git", "-C", str(path), "add", "-A"],
            check=True,
            capture_output=True,
            text=True,
        )
        proc = subprocess.run(
            [
                "git", "-C", str(path),
                "-c", "user.name=Lab Test",
                "-c", "user.email=lab-test@example.com",
                "commit", "-m", "test sync",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def assert_ok(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    # ------------------------------------------------------------------
    # init / new
    # ------------------------------------------------------------------
    def test_init_creates_skeleton_and_is_idempotent(self) -> None:
        proc = self.run_lab("init")
        self.assert_ok(proc)

        for name in ("active", "archive", "templates", "registry", "bin"):
            self.assertTrue((self.lab_root / name).is_dir(), name)
        self.assertTrue((self.lab_root / "registry" / "experiments.jsonl").is_file())
        self.assertTrue((self.lab_root / "AGENTS.md").is_file())
        self.assertTrue((self.lab_root / "README.md").is_file())
        self.assertTrue((self.lab_root / "bin" / "README.md").is_file())
        for template in ("python", "node", "general"):
            self.assertTrue((self.lab_root / "templates" / template).is_dir(), template)

        agents = (self.lab_root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(str(self.lab_root), agents)
        self.assertNotIn("{{", agents)

        second = self.run_lab("init")
        self.assert_ok(second)
        self.assertIn("已就绪", second.stdout)
        self.assertEqual(self.records(), [])

    def test_new_creates_full_experiment(self) -> None:
        self.init()
        identifier = self.create(
            "My First Idea",
            "--template", "python",
            "--description", "验证一个想法",
            "--tag", "cli,alpha",
            "--technology", "rust",
        )
        record = self.record(identifier)
        self.assertRegex(identifier, r"^\d{8}-\d{3}$")
        self.assertEqual(record["slug"], "my-first-idea")
        self.assertEqual(record["name"], "My First Idea")
        self.assertEqual(record["status"], "created")
        self.assertEqual(record["template"], "python")
        self.assertEqual(record["description"], "验证一个想法")
        self.assertEqual(record["tags"], ["cli", "alpha"])
        self.assertIn("rust", record["technologies"])
        self.assertIn("Python", record["technologies"])
        self.assertTrue(record["git"])
        self.assertTrue(record["env_skipped"])
        self.assertIsNone(record["env"])

        path = self.exp_path(record)
        self.assertTrue(path.is_dir())
        self.assertTrue((path / "README.md").is_file())
        self.assertTrue((path / ".lab-meta.json").is_file())
        self.assertTrue((path / "AGENTS.md").is_symlink())
        self.assertEqual(
            (path / "AGENTS.md").resolve(),
            (self.lab_root / "AGENTS.md").resolve(),
        )
        self.assertTrue((path / ".git").is_dir())
        log = subprocess.run(
            ["git", "-C", str(path), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(len(log.stdout.strip().splitlines()), 1)

        readme = (path / "README.md").read_text(encoding="utf-8")
        self.assertIn(identifier, readme)
        self.assertIn("My First Idea", readme)
        self.assertIn("验证一个想法", readme)
        self.assertIn("lab:metadata:start", readme)

        shown = self.run_lab("show", identifier, "--json")
        self.assert_ok(shown)
        data = json.loads(shown.stdout)
        self.assertTrue(data["exists"])
        self.assertFalse(data["dirty"])
        self.assertEqual(data["absolute_path"], str(path))

        opened = self.run_lab("open", identifier)
        self.assert_ok(opened)
        self.assertEqual(opened.stdout.strip(), str(path))

        listed = self.run_lab("list")
        self.assert_ok(listed)
        self.assertIn(identifier, listed.stdout)
        self.assertIn("CREATED", listed.stdout)

        excluded = (path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(".lab-meta.json", excluded)
        self.assertIn("/AGENTS.md", excluded)

    def test_new_no_git_and_validation(self) -> None:
        self.init()
        identifier = self.create("No Git", "--template", "general", "--no-git")
        record = self.record(identifier)
        self.assertFalse(record["git"])
        self.assertFalse((self.exp_path(record) / ".git").exists())

        duplicate = self.run_lab("new", "No Git", "--no-env")
        self.assertEqual(duplicate.returncode, 2, duplicate.stderr)
        self.assertIn("已被实验", duplicate.stderr)

        bad_name = self.run_lab("new", "bad/name", "--no-env")
        self.assertEqual(bad_name.returncode, 2, bad_name.stderr)

        unknown = self.run_lab("new", "Unknown Template", "--template", "ruby", "--no-env")
        self.assertEqual(unknown.returncode, 2, unknown.stderr)
        self.assertIn("未知模板", unknown.stderr)

        too_long = self.run_lab("new", "x" * 80, "--no-env")
        self.assertEqual(too_long.returncode, 2, too_long.stderr)

    def test_workspace_required(self) -> None:
        proc = self.run_lab("list")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("工作台不存在", proc.stderr)

    # ------------------------------------------------------------------
    # status / meta / search
    # ------------------------------------------------------------------
    def test_status_and_meta_sync_readme(self) -> None:
        self.init()
        identifier = self.create("Lifecycle", "--template", "general")
        path = self.exp_path(self.record(identifier))

        running = self.run_lab("status", identifier, "running")
        self.assert_ok(running)
        self.assertIn("CREATED → RUNNING", running.stdout)
        self.assertEqual(self.record(identifier)["status"], "running")

        meta = self.run_lab(
            "meta", identifier,
            "--summary", "初步验证通过",
            "--conclusion", "值得继续",
            "--category", "tools",
            "--tags", "alpha,beta",
            "--technologies", "python",
            "--related-projects", "demo",
        )
        self.assert_ok(meta)
        record = self.record(identifier)
        self.assertEqual(record["summary"], "初步验证通过")
        self.assertEqual(record["conclusion"], "值得继续")
        self.assertEqual(record["category"], "tools")
        self.assertEqual(record["tags"], ["alpha", "beta"])
        self.assertEqual(record["technologies"], ["python"])
        self.assertEqual(record["related_projects"], ["demo"])
        readme = (path / "README.md").read_text(encoding="utf-8")
        self.assertIn("初步验证通过", readme)
        self.assertIn("值得继续", readme)

        done = self.run_lab("status", identifier, "success")
        self.assert_ok(done)
        self.assertIn("RUNNING → SUCCESS", done.stdout)
        self.assertEqual(self.record(identifier)["status"], "success")

        invalid = self.run_lab("status", identifier, "archived")
        self.assertEqual(invalid.returncode, 2, invalid.stderr)
        self.assertIn("status 只接受", invalid.stderr)

        empty_meta = self.run_lab("meta", identifier)
        self.assertEqual(empty_meta.returncode, 2, empty_meta.stderr)

    def test_custom_readme_sections_survive(self) -> None:
        self.init()
        identifier = self.create("Readme Keep", "--template", "general")
        path = self.exp_path(self.record(identifier))
        readme = path / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(text + "\n\n## 我自己的记录\n\n不要覆盖这一段。\n", encoding="utf-8")
        proc = self.run_lab("meta", identifier, "--conclusion", "保留自定义内容")
        self.assert_ok(proc)
        updated = readme.read_text(encoding="utf-8")
        self.assertIn("不要覆盖这一段。", updated)
        self.assertIn("保留自定义内容", updated)

    def test_search_filters_and_json(self) -> None:
        self.init()
        identifier = self.create(
            "Search Target",
            "--template", "general",
            "--description", "A CLI experiment",
            "--tag", "cli,alpha",
        )
        self.run_lab(
            "meta", identifier,
            "--summary", "搜索摘要",
            "--conclusion", "搜索结论",
            "--category", "tooling",
            "--tags", "beta",
            "--technologies", "python",
            "--related-projects", "proj-a",
        )

        text = self.run_lab("search", "搜索摘要")
        self.assert_ok(text)
        self.assertIn(identifier, text.stdout)
        self.assertIn("搜索摘要", text.stdout)

        tagged = self.run_lab("search", "--tag", "beta")
        self.assert_ok(tagged)
        self.assertIn(identifier, tagged.stdout)

        status = self.run_lab("search", "--status", "created")
        self.assert_ok(status)
        self.assertIn(identifier, status.stdout)

        category = self.run_lab("search", "--category", "tooling")
        self.assert_ok(category)
        self.assertIn(identifier, category.stdout)

        technology = self.run_lab("search", "--technology", "python")
        self.assert_ok(technology)
        self.assertIn(identifier, technology.stdout)

        data = self.run_lab("search", "搜索", "--json")
        self.assert_ok(data)
        payload = json.loads(data.stdout)
        self.assertEqual(payload["query"], "搜索")
        self.assertEqual(payload["hits"][0]["id"], identifier)

        missing = self.run_lab("search", "不存在的关键词")
        self.assert_ok(missing)
        self.assertIn("没有找到", missing.stdout)

        no_filter = self.run_lab("search")
        self.assertEqual(no_filter.returncode, 2, no_filter.stderr)

    # ------------------------------------------------------------------
    # archive / promote / discard
    # ------------------------------------------------------------------
    def test_archive_requires_force_when_dirty(self) -> None:
        self.init()
        identifier = self.create("Archive Me", "--template", "general")
        record = self.record(identifier)
        path = self.exp_path(record)
        (path / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

        refused = self.run_lab("archive", identifier)
        self.assertEqual(refused.returncode, 4, refused.stderr)
        self.assertIn("未提交更改", refused.stderr)
        self.assertTrue(path.is_dir())

        archived = self.run_lab("archive", identifier, "--force")
        self.assert_ok(archived)
        record = self.record(identifier)
        self.assertEqual(record["status"], "archived")
        self.assertEqual(record["archived_from_status"], "created")
        self.assertRegex(record["path"], r"^archive/\d{4}/\d{2}/archive-me$")
        self.assertFalse(path.exists())
        self.assertTrue(self.exp_path(record).is_dir())

        again = self.run_lab("archive", identifier)
        self.assertEqual(again.returncode, 4, again.stderr)

        all_list = self.run_lab("list", "--all")
        self.assert_ok(all_list)
        self.assertIn("ARCHIVED", all_list.stdout)

    def test_promote_copies_then_keeps_archive(self) -> None:
        self.init()
        identifier = self.create("Promote Me", "--template", "general")
        self.run_lab("status", identifier, "success")
        record = self.record(identifier)
        source = self.exp_path(record)
        (source / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

        refused = self.run_lab("promote", identifier, "--to", str(self.home / "workspace" / "p1"))
        self.assertEqual(refused.returncode, 4, refused.stderr)

        target = self.home / "workspace" / "promoted"
        promoted = self.run_lab("promote", identifier, "--to", str(target), "--force")
        self.assert_ok(promoted)
        record = self.record(identifier)
        self.assertEqual(record["status"], "promoted")
        self.assertEqual(record["promoted_to"], str(target.resolve()))
        self.assertTrue(target.is_dir())
        self.assertTrue((target / "README.md").is_file())
        self.assertTrue((target / "notes.md").is_file())
        self.assertFalse((target / ".git").exists())
        self.assertFalse((target / ".lab-meta.json").exists())
        self.assertFalse((target / "AGENTS.md").exists())
        self.assertTrue((target / "uncommitted.txt").is_file())  # regular files are copied
        self.assertFalse(source.exists())
        self.assertTrue(self.exp_path(record).is_dir())
        self.assertEqual(record["path"], "archive/" + record["archived_at"][:7].replace("-", "/") + "/promote-me")

        second = self.create("Promote Guard", "--template", "general")
        self.run_lab("status", second, "success")

        non_empty = self.home / "workspace" / "non-empty"
        non_empty.mkdir(parents=True)
        (non_empty / "x").write_text("x", encoding="utf-8")
        refused_target = self.run_lab(
            "promote", second, "--to", str(non_empty), "--force"
        )
        self.assertEqual(refused_target.returncode, 4, refused_target.stderr)

        inside = self.run_lab(
            "promote", second, "--to", str(self.lab_root / "inside"), "--force"
        )
        self.assertEqual(inside.returncode, 2, inside.stderr)

    def test_promote_requires_success_or_archived(self) -> None:
        self.init()
        identifier = self.create("Not Ready", "--template", "general")
        proc = self.run_lab("promote", identifier, "--to", str(self.home / "workspace" / "x"))
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertIn("只有 SUCCESS 或 ARCHIVED", proc.stderr)

    def test_discard_noninteractive_and_dirty(self) -> None:
        self.init()
        identifier = self.create("Discard Me", "--template", "general")
        path = self.exp_path(self.record(identifier))

        refused = self.run_lab("discard", identifier)
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn("--yes", refused.stderr)
        self.assertTrue(path.is_dir())

        (path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        dirty_refused = self.run_lab("discard", identifier, "--yes")
        self.assertEqual(dirty_refused.returncode, 4, dirty_refused.stderr)
        self.assertIn("--force", dirty_refused.stderr)

        discarded = self.run_lab("discard", identifier, "--yes", "--force")
        self.assert_ok(discarded)
        record = self.record(identifier)
        self.assertEqual(record["status"], "discarded")
        self.assertFalse(path.exists())
        self.assertEqual(record["discarded_from_status"], "created")

        shown = self.run_lab("show", identifier)
        self.assert_ok(shown)
        self.assertIn("DISCARDED", shown.stdout)

        found = self.run_lab("search", "Discard Me")
        self.assert_ok(found)
        self.assertIn(identifier, found.stdout)

    def test_promoted_cannot_be_discarded(self) -> None:
        self.init()
        identifier = self.create("Keep Promoted", "--template", "general")
        self.run_lab("status", identifier, "success")
        target = self.home / "workspace" / "keep"
        self.assert_ok(self.run_lab("promote", identifier, "--to", str(target), "--force"))
        proc = self.run_lab("discard", identifier, "--yes", "--force")
        self.assertEqual(proc.returncode, 4, proc.stderr)

    # ------------------------------------------------------------------
    # clean / stats / polish
    # ------------------------------------------------------------------
    def test_clean_retention_skips_and_apply(self) -> None:
        self.init()

        old_created = self.create("Old Created", "--template", "general")
        self.backdate(old_created, 4)

        old_success = self.create("Old Success", "--template", "general")
        self.run_lab("status", old_success, "success")
        self.git_commit_all(self.exp_path(self.record(old_success)))
        self.backdate(old_success, 8, field="status_changed_at")
        self.backdate(old_success, 8, field="created_at")

        old_running = self.create("Old Running", "--template", "general")
        self.run_lab("status", old_running, "running")
        self.backdate(old_running, 30)
        self.backdate(old_running, 30, field="status_changed_at")

        dirty = self.create("Dirty Old", "--template", "general")
        self.backdate(dirty, 4)
        (self.exp_path(self.record(dirty)) / "dirty.txt").write_text("x\n", encoding="utf-8")

        no_git = self.create("No Git Old", "--template", "general", "--no-git")
        self.backdate(no_git, 4)

        orphan = self.lab_root / "active" / "19990101-999-orphan"
        orphan.mkdir()

        dry = self.run_lab("clean")
        self.assert_ok(dry)
        self.assertIn(old_created, dry.stdout)
        self.assertIn(old_success, dry.stdout)
        self.assertIn("演练", dry.stdout)
        self.assertIn("未登记目录", dry.stderr)
        self.assertTrue(self.exp_path(self.record(old_created)).is_dir())

        explicit_dry = self.run_lab("clean", "--dry-run")
        self.assert_ok(explicit_dry)
        self.assertIn("演练", explicit_dry.stdout)
        conflict = self.run_lab("clean", "--dry-run", "--apply")
        self.assertEqual(conflict.returncode, 2, conflict.stderr)

        dry_json = self.run_lab("clean", "--json")
        self.assert_ok(dry_json)
        payload = json.loads(dry_json.stdout)
        self.assertFalse(payload["applied"])
        candidate_ids = {item["id"] for item in payload["candidates"]}
        self.assertEqual(candidate_ids, {old_created, old_success})
        skipped_ids = {item["id"] for item in payload["skipped"]}
        self.assertEqual(skipped_ids, {dirty, no_git})
        self.assertTrue(any(Path(item).name == orphan.name for item in payload["orphans"]))

        applied = self.run_lab("clean", "--apply")
        self.assert_ok(applied)
        self.assertFalse(self.exp_path(self.record(old_created)).exists())
        self.assertFalse(self.exp_path(self.record(old_success)).exists())
        self.assertTrue(self.exp_path(self.record(old_running)).is_dir())
        self.assertTrue(self.exp_path(self.record(dirty)).is_dir())
        self.assertTrue(self.exp_path(self.record(no_git)).is_dir())
        self.assertEqual(self.record(old_created)["status"], "discarded")
        self.assertEqual(self.record(old_success)["status"], "discarded")

        forced = self.run_lab("clean", "--apply", "--force", "--created-days", "0")
        self.assert_ok(forced)
        self.assertFalse(self.exp_path(self.record(dirty)).exists())
        self.assertFalse(self.exp_path(self.record(no_git)).exists())
        self.assertTrue(self.exp_path(self.record(old_running)).is_dir())

    def test_clean_missing_directory_is_reported_not_deleted(self) -> None:
        self.init()
        identifier = self.create("Missing Dir", "--template", "general")
        path = self.exp_path(self.record(identifier))
        shutil.rmtree(path)
        proc = self.run_lab("clean", "--created-days", "0", "--json")
        self.assert_ok(proc)
        payload = json.loads(proc.stdout)
        self.assertIn(identifier, payload["missing"])
        self.assertEqual(self.record(identifier)["status"], "created")

    def test_stats_and_list_all(self) -> None:
        self.init()
        created = self.create("Stats Created", "--template", "general", "--tag", "a")
        success = self.create("Stats Success", "--template", "python")
        self.run_lab("status", success, "success")
        self.run_lab("discard", created, "--yes")

        data = self.run_lab("stats", "--json")
        self.assert_ok(data)
        stats = json.loads(data.stdout)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["by_status"]["success"], 1)
        self.assertEqual(stats["by_status"]["discarded"], 1)
        self.assertEqual(stats["by_template"]["general"], 1)
        self.assertGreaterEqual(stats["created_last_7_days"], 1)

        listed = self.run_lab("list", "--all", "--json")
        self.assert_ok(listed)
        payload = json.loads(listed.stdout)
        self.assertEqual(len(payload), 2)
        self.assertTrue(all("exists" in item for item in payload))

    def test_init_repairs_missing_files(self) -> None:
        self.init()
        (self.lab_root / "AGENTS.md").unlink()
        shutil.rmtree(self.lab_root / "bin")
        proc = self.run_lab("init")
        self.assert_ok(proc)
        self.assertTrue((self.lab_root / "AGENTS.md").is_file())
        self.assertTrue((self.lab_root / "bin").is_dir())


if __name__ == "__main__":
    unittest.main()
