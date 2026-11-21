"""Command line interface for lab-workflow.

There is deliberately no TUI here: directory browsing, previewing and
jumping belong to try-rs. This CLI owns the registry and lifecycle.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

from . import __version__, clean, compat, doctor, experiments, gitutil, search, stats
from .errors import LabError, StateError, UsageError
from .registry import active
from .util import (
    ALL_STATUSES,
    format_age_short,
    parse_status_list,
    split_csv,
    status_label,
    table,
    unique,
)
from .workspace import Workspace, resolve_root


class LabParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        raise UsageError(message)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _json_print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _warn(messages: list[str]) -> None:
    for message in unique(messages):
        print(f"警告: {message}", file=sys.stderr)


def _require_workspace(workspace: Workspace) -> None:
    if not workspace.root.is_dir():
        raise LabError(
            f"工作台不存在: {workspace.root}\n先运行: {workspace.command_hint('init')}"
        )
    if not (workspace.active.is_dir() and workspace.registry_dir.is_dir()):
        raise LabError(
            f"工作台结构不完整: {workspace.root}\n"
            f"运行 {workspace.command_hint('init')} 补齐骨架"
        )
    if not workspace.registry_path.is_file():
        raise LabError(
            f"registry 文件缺失: {workspace.registry_path}\n"
            f"运行 {workspace.command_hint('init')} 重新创建"
        )


def _statuses(values: list[str] | None) -> list[str]:
    try:
        return parse_status_list(values)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def _record_json(workspace: Workspace, record: dict, *, with_git: bool = False) -> dict:
    data = dict(record)
    path = experiments.record_path(workspace, record)
    exists = bool(path and path.is_dir())
    data["absolute_path"] = str(path) if path else None
    data["exists"] = exists
    if with_git:
        data["dirty"] = bool(
            exists
            and path
            and record.get("git")
            and gitutil.is_repo(path)
            and gitutil.dirty(path)
        )
        data["head"] = gitutil.head_commit(path) if exists and path else None
    return data


def _format_age_days(value: float) -> str:
    return f"{value:.1f} 天"


# ----------------------------------------------------------------------
# init
# ----------------------------------------------------------------------
def cmd_init(args, workspace: Workspace) -> int:
    created = workspace.ensure()
    refreshed = workspace.refresh() if args.refresh else []
    print(f"工作台: {workspace.root}")
    if created:
        print("已创建/补齐:")
        for item in created:
            print(f"  + {item if item != '.' else '（根目录）'}")
    else:
        print("工作台已就绪，无需变更。")
    if refreshed:
        print("已刷新（覆盖工具管理的文件）:")
        for item in refreshed:
            print(f"  ~ {item}")
    templates = workspace.available_templates()
    print(f"模板: {', '.join(templates) if templates else '无'}")
    print("")
    print("下一步:")
    print(f"  {workspace.command_hint('new my-first-idea --template python')}")
    print(f"  目录浏览与跳转交给 try-rs（把 tries_path 指向 {workspace.active}），")
    print("  lab-workflow 只负责 registry、生命周期与清理。")
    return 0


# ----------------------------------------------------------------------
# new
# ----------------------------------------------------------------------
def cmd_new(args, workspace: Workspace) -> int:
    tags = split_csv(args.tag)
    technologies = split_csv(args.technologies)
    related = split_csv(args.related_projects)
    try:
        result = experiments.create_experiment(
            workspace,
            args.name,
            template=args.template,
            description=args.description or "",
            tags=tags,
            category=args.category or "",
            technologies=technologies,
            related_projects=related,
            no_git=args.no_git,
            no_env=args.no_env,
        )
    except ValueError as exc:
        raise UsageError(str(exc)) from exc

    record = result.record
    _warn(result.warnings)
    if args.json:
        _json_print(_record_json(workspace, record))
        return 0

    print(f"实验已创建: {record.get('id')}")
    print(f"名称: {record.get('name')}")
    print(f"路径: {result.path}")
    print(f"模板: {record.get('template')}")
    if args.no_env:
        print("环境: 已跳过（--no-env）")
    else:
        print(f"环境: {experiments.environment.environment_summary(result.env.record)}")
        for message in result.env.messages:
            print(f"  {message}")
    print(f"Git: {result.git_message}")
    print("")
    print("下一步:")
    print(f"  cd {shlex.quote(str(result.path))}")
    print(f"  {workspace.command_hint('show ' + str(record.get('id')))}")
    return 0


# ----------------------------------------------------------------------
# adopt
# ----------------------------------------------------------------------
def cmd_adopt(args, workspace: Workspace) -> int:
    try:
        result = experiments.adopt_experiment(
            workspace,
            args.path,
            name=args.name,
            template=args.template,
            description=args.description or "",
            tags=split_csv(args.tag),
            category=args.category or "",
            technologies=split_csv(args.technologies),
            related_projects=split_csv(args.related_projects),
            no_git=args.no_git,
        )
    except ValueError as exc:
        raise UsageError(str(exc)) from exc

    record = result.record
    _warn(result.warnings)
    if args.json:
        _json_print(_record_json(workspace, record))
        return 0

    print(f"已登记实验: {record.get('id')}")
    print(f"名称: {record.get('name')}")
    print(f"路径: {result.path}")
    print(f"模板: {result.template}（自动检测，可用 --template 覆盖）")
    print(f"Git: {result.git_message}")
    print("")
    print("下一步:")
    print(f"  {workspace.command_hint('show ' + str(record.get('id')))}")
    print(f"  用 try-rs 浏览/跳转（tries_path 指向 {workspace.active}）")
    return 0


# ----------------------------------------------------------------------
# list
# ----------------------------------------------------------------------
def cmd_list(args, workspace: Workspace) -> int:
    if args.limit is not None and args.limit < 1:
        raise UsageError("--limit 必须是正整数")
    statuses = _statuses(args.status)
    records = experiments.collect_records(
        workspace,
        statuses=statuses,
        include_all=args.all and not statuses,
        tags=split_csv(args.tag),
        template=args.template,
    )
    total = len(records)
    if args.limit:
        records = records[: args.limit]

    if args.json:
        _json_print([_record_json(workspace, record) for record in records])
        return 0

    if not records:
        if total == 0:
            print("没有匹配的实验。")
            if not args.all and not statuses and not args.tag:
                print(f"新建一个: {workspace.command_hint('new my-first-idea --template python')}")
        else:
            print("没有匹配的实验。")
        return 0

    rows = [
        [
            record.get("id", ""),
            status_label(record.get("status", "")),
            record.get("name", ""),
            ", ".join(record.get("tags", []) or []),
            format_age_short(record.get("updated_at")),
        ]
        for record in records
    ]
    print(
        table(
            ["ID", "状态", "名称", "标签", "更新"],
            rows,
            max_widths=[13, 10, 36, 24, 8],
        )
    )
    counts = Counter(str(record.get("status")) for record in records)
    summary = " · ".join(
        f"{status_label(status)} {counts[status]}" for status in ALL_STATUSES if counts[status]
    )
    print("")
    print(f"共 {total} 个实验" + (f"（{summary}）" if summary else ""))
    if args.all:
        print("提示: 包含归档、promote 和 discarded 墓碑；搜索仍可用 lab search。")
    elif not statuses:
        print("提示: 默认只显示 CREATED/RUNNING/SUCCESS；用 --all 查看归档与墓碑。")

    missing = [
        record.get("id")
        for record in records
        if active(record) and not experiments.record_exists(workspace, record)
    ]
    if missing:
        print(
            f"警告: {len(missing)} 个活跃实验目录缺失: {', '.join(str(item) for item in missing)}",
            file=sys.stderr,
        )
    return 0


# ----------------------------------------------------------------------
# show
# ----------------------------------------------------------------------
def cmd_show(args, workspace: Workspace) -> int:
    record = experiments.find_experiment(workspace, args.ref)
    if args.json:
        _json_print(_record_json(workspace, record, with_git=True))
        return 0
    print(experiments.show_text(workspace, record))
    return 0


# ----------------------------------------------------------------------
# open
# ----------------------------------------------------------------------
def cmd_open(args, workspace: Workspace) -> int:
    record = experiments.find_experiment(workspace, args.ref)
    path = experiments.record_path(workspace, record)
    if path is None or not path.is_dir():
        raise StateError(f"实验目录不存在（状态 {status_label(record.get('status', ''))}）")
    print(path)
    if args.shell:
        command = compat.interactive_shell()
        if not compat.is_windows() and not Path(command[0]).exists():
            raise LabError(f"找不到 shell: {command[0]}")
        try:
            return subprocess.call(command, cwd=str(path))
        except OSError as exc:
            raise LabError(f"启动 shell 失败: {exc}") from exc
    return 0


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------
def cmd_status(args, workspace: Workspace) -> int:
    result = experiments.update_status(workspace, args.ref, args.status)
    _warn(result.warnings)
    if args.json:
        _json_print(_record_json(workspace, result.record))
        return 0
    if result.previous_status == result.record.get("status"):
        print(f"实验 {result.record.get('id')} 状态未变化: {status_label(result.record.get('status'))}")
    else:
        print(
            f"实验 {result.record.get('id')}: "
            f"{status_label(result.previous_status)} → {status_label(result.record.get('status'))}"
        )
    return 0


# ----------------------------------------------------------------------
# meta
# ----------------------------------------------------------------------
def cmd_meta(args, workspace: Workspace) -> int:
    values = [args.description, args.summary, args.conclusion, args.category]
    values.extend([args.tags, args.technologies, args.related_projects])
    if all(value is None for value in values):
        raise UsageError(
            "至少提供一个字段: --description/--summary/--conclusion/--category/"
            "--tags/--technologies/--related-projects"
        )
    result = experiments.update_meta(
        workspace,
        args.ref,
        description=args.description,
        summary=args.summary,
        conclusion=args.conclusion,
        category=args.category,
        tags=split_csv(args.tags) if args.tags is not None else None,
        technologies=split_csv(args.technologies) if args.technologies is not None else None,
        related_projects=split_csv(args.related_projects)
        if args.related_projects is not None
        else None,
    )
    _warn(result.warnings)
    if args.json:
        _json_print(_record_json(workspace, result.record))
        return 0
    if not result.changed:
        print(f"实验 {result.record.get('id')} 元数据无变化")
        return 0
    fields = []
    for label, value in (
        ("描述", args.description),
        ("摘要", args.summary),
        ("结论", args.conclusion),
        ("分类", args.category),
        ("标签", args.tags),
        ("技术", args.technologies),
        ("相关知识", args.related_projects),
    ):
        if value is not None:
            fields.append(label)
    print(f"已更新实验 {result.record.get('id')}: {', '.join(fields)}")
    return 0


# ----------------------------------------------------------------------
# archive
# ----------------------------------------------------------------------
def cmd_archive(args, workspace: Workspace) -> int:
    result = experiments.archive_experiment(
        workspace, args.ref, force=args.force
    )
    _warn(result.warnings)
    if args.json:
        _json_print(_record_json(workspace, result.record))
        return 0
    print(f"实验 {result.record.get('id')} 已归档")
    print(f"  从: {result.record.get('archived_from_status', '?')}")
    print(f"  到: {result.path}")
    if result.dirty:
        print(
            f"  注意: 归档时存在 {len(result.dirty)} 项未提交更改，已随目录移动。",
            file=sys.stderr,
        )
    print("归档内容永久保留；需要继续开发时请先评估是否 promote。")
    return 0


# ----------------------------------------------------------------------
# promote
# ----------------------------------------------------------------------
def cmd_promote(args, workspace: Workspace) -> int:
    target = Path(args.to).expanduser() if args.to else None
    result = experiments.promote_experiment(
        workspace, args.ref, target=target, force=args.force
    )
    _warn(result.warnings)
    if args.json:
        data = _record_json(workspace, result.record)
        data["promoted_target"] = str(result.target)
        _json_print(data)
        return 0
    print(f"实验 {result.record.get('id')} 已 promote")
    print(f"  目标: {result.target}")
    print(f"  源实验（已归档保留）: {result.source_path}")
    print("")
    print("检查清单:")
    print("  - 已排除 .git/、.venv/、node_modules/ 和 .lab-meta.json")
    print("  - 检查 README 中的实验 ID/路径等元数据是否需要清理")
    print("  - 检查 .env、密钥、本地配置是否不应带入正式项目")
    print("  - 如需版本控制，请在目标目录自行 git init")
    print("  - 按目标项目的规范补充依赖锁定和运行说明")
    return 0


# ----------------------------------------------------------------------
# discard
# ----------------------------------------------------------------------
def cmd_discard(args, workspace: Workspace) -> int:
    record = experiments.find_experiment(workspace, args.ref)
    path = experiments.record_path(workspace, record)
    dirty: list[str] = []
    if path and path.is_dir() and record.get("git"):
        dirty = gitutil.status_lines(path) or []

    if not args.yes:
        if not sys.stdin.isatty():
            raise UsageError(
                "discard 会删除实验目录；非交互模式请显式加 --yes"
            )
        print(f"实验: {record.get('id')} / {record.get('name')}")
        print(f"路径: {record.get('path')}")
        if dirty:
            print(f"未提交更改: {len(dirty)} 项", file=sys.stderr)
        try:
            answer = input(f"输入实验 ID {record.get('id')} 确认删除: ").strip()
        except EOFError as exc:
            raise LabError("已取消") from exc
        if answer != record.get("id"):
            raise LabError("确认不匹配，已取消")

    if dirty and not args.force:
        if not sys.stdin.isatty():
            raise StateError(
                f"实验有 {len(dirty)} 项未提交更改；确认后加 --force 再删除"
            )
        try:
            answer = input(
                f"存在 {len(dirty)} 项未提交更改，仍要删除？输入 yes 继续: "
            ).strip().lower()
        except EOFError as exc:
            raise LabError("已取消") from exc
        if answer not in {"yes", "y"}:
            raise LabError("已取消")

    result = experiments.discard_experiment(workspace, record.get("id"))
    _warn(result.warnings)
    if args.json:
        _json_print(_record_json(workspace, result.record))
        return 0
    print(f"实验 {result.record.get('id')} 已删除")
    if result.old_path:
        print(f"  原路径: {result.old_path}")
    print("registry 保留墓碑；可用 lab search 检索结论、标签和历史。")
    return 0


# ----------------------------------------------------------------------
# clean
# ----------------------------------------------------------------------
def cmd_clean(args, workspace: Workspace) -> int:
    if args.dry_run and args.apply:
        raise UsageError("--dry-run 与 --apply 不能同时使用")
    if args.created_days < 0 or args.success_days < 0:
        raise UsageError("保留天数不能为负数")
    report = clean.evaluate(
        workspace,
        created_days=args.created_days,
        success_days=args.success_days,
        force=args.force,
    )
    if args.apply:
        clean.apply_clean(workspace, report)
    _warn(report.warnings)

    if args.json:
        _json_print(report.to_dict())
        return 0

    mode = "执行删除" if args.apply else "演练（不会删除文件）"
    print(f"清理模式: {mode}")
    print(
        f"保留策略: CREATED 超过 {clean.format_days(args.created_days)} 天、"
        f"SUCCESS 超过 {clean.format_days(args.success_days)} 天可清理；RUNNING 永不清理"
    )
    if args.force:
        print("已启用 --force：非 git 仓库和有未提交更改的实验也会被删除。")
    print("")

    rows = report.candidates + report.skipped
    rows.sort(key=lambda item: item.age, reverse=True)
    if rows:
        table_rows = []
        for candidate in rows:
            if candidate.action == "deleted":
                action = "已删除"
            elif candidate.action == "skip":
                action = "跳过"
            elif args.apply:
                action = "未删除"
            else:
                action = "将删除"
            reason = candidate.reason or "超过保留期"
            table_rows.append(
                [
                    candidate.record.get("id", ""),
                    status_label(candidate.record.get("status", "")),
                    _format_age_days(candidate.age),
                    f"{clean.format_days(candidate.limit)} 天",
                    action,
                    reason,
                ]
            )
        print(
            table(
                ["ID", "状态", "已存在", "阈值", "结果", "说明"],
                table_rows,
                max_widths=[13, 10, 9, 7, 7, 44],
            )
        )
    else:
        print("没有超过保留期的实验。")

    if report.kept:
        print(f"保留期内: {report.kept} 个")
    for orphan in report.orphans[:5]:
        print(f"注意: active/ 下有未登记目录（未处理）: {orphan}", file=sys.stderr)
    if len(report.orphans) > 5:
        print(f"注意: 另有 {len(report.orphans) - 5} 个未登记目录未列出", file=sys.stderr)
    if report.missing:
        print(
            f"注意: {len(report.missing)} 个活跃 registry 记录目录缺失（未处理）: "
            + ", ".join(str(record.get("id")) for record in report.missing),
            file=sys.stderr,
        )
    if args.apply:
        print("")
        print(f"已删除 {len(report.deleted)} 个实验；registry 保留墓碑。")
    elif report.candidates:
        print("")
        print("这是演练；确认后加 --apply 执行删除。")
    return 0


# ----------------------------------------------------------------------
# stats
# ----------------------------------------------------------------------
def cmd_stats(args, workspace: Workspace) -> int:
    report = stats.build_stats(workspace)
    if args.json:
        _json_print(report.to_dict())
        return 0
    print(f"实验总数: {report.total}")
    if report.total == 0:
        print(f"还没有实验。新建一个: {workspace.command_hint('new my-first-idea')}")
        return 0
    active_count = sum(report.by_status.get(status, 0) for status in ("created", "running", "success"))
    print(f"活跃实验: {active_count}")
    print(f"最近 7 天新建: {report.recent}")
    print("")
    status_rows = [
        [status_label(status), report.by_status.get(status, 0)] for status in ALL_STATUSES
    ]
    print(table(["状态", "数量"], status_rows))
    print("")
    print("按模板:")
    print(
        table(
            ["模板", "数量"],
            [[name, count] for name, count in sorted(report.by_template.items())],
        )
    )
    if report.top_tags:
        print("")
        print("标签 Top:")
        for tag, count in report.top_tags:
            print(f"  {tag}: {count}")
    if report.active_missing:
        print("")
        print(
            f"警告: {len(report.active_missing)} 个活跃实验目录缺失: "
            + ", ".join(report.active_missing),
            file=sys.stderr,
        )
    if report.active_dirty:
        print("")
        print(
            f"注意: {len(report.active_dirty)} 个活跃 git 实验有未提交更改: "
            + ", ".join(report.active_dirty),
            file=sys.stderr,
        )
    return 0


def cmd_doctor(args, workspace: Workspace) -> int:
    report = doctor.build_report(workspace)
    if args.json:
        _json_print(report.to_dict())
        return 0

    state = "已初始化" if report.initialized else "未初始化（运行 init）"
    print(f"工作台: {report.root}  [{state}]")
    registry_state = "正常" if report.registry_ok else "不可用"
    print(f"registry: {registry_state}，共 {report.total} 个实验")
    if report.by_status:
        summary = " · ".join(
            f"{status_label(status)} {count}" for status, count in report.by_status.items()
        )
        print(f"  {summary}")
    print(f"Python: {report.python}")
    print(f"Git: {report.git or '未安装'}")
    print(f"Node/npm: {report.npm or '未安装'}")
    if report.try_rs:
        print(f"try-rs: {report.try_rs}（负责目录浏览/跳转）")
    else:
        print("try-rs: 未安装 — 目录浏览与跳转请安装 try-rs（cargo install try-rs）")
    if report.orphans:
        print(f"未登记目录: {len(report.orphans)} 个（可用 adopt 纳入 registry）")
        for item in report.orphans[:10]:
            print(f"  - {item}")
        if len(report.orphans) > 10:
            print(f"  … 另有 {len(report.orphans) - 10} 个")
    if report.missing:
        print(f"目录缺失的活跃记录: {', '.join(report.missing)}")
    if report.dirty:
        print(f"有未提交更改的活跃实验: {', '.join(report.dirty)}")
    for message in report.warnings:
        print(f"警告: {message}", file=sys.stderr)
    print("")
    print("定位: lab-workflow 管理 registry 与生命周期；TUI、预览与跳转由 try-rs 负责。")
    return 0


# ----------------------------------------------------------------------
# search
# ----------------------------------------------------------------------
def cmd_search(args, workspace: Workspace) -> int:
    query = " ".join(args.query).strip()
    statuses = _statuses(args.status)
    tags = split_csv(args.tag)
    technologies = split_csv(args.technology)
    if not any([query, statuses, tags, args.template, args.category, technologies]):
        raise UsageError("至少提供关键词或过滤条件（--status/--tag/--template/--category/--technology）")
    if args.limit < 0:
        raise UsageError("--limit 不能为负数")
    report = search.search_experiments(
        workspace,
        query,
        statuses=statuses or None,
        tags=tags,
        template=args.template,
        category=args.category,
        technology=technologies,
        limit=args.limit,
    )
    if args.json:
        _json_print(report.to_dict())
        return 0
    if not report.hits:
        print("没有找到匹配的实验。")
        return 0
    rows = [
        [
            hit.record.get("id", ""),
            status_label(hit.record.get("status", "")),
            hit.record.get("name", ""),
            hit.snippet,
        ]
        for hit in report.hits
    ]
    print(
        table(
            ["ID", "状态", "名称", "匹配内容"],
            rows,
            max_widths=[13, 10, 28, 64],
        )
    )
    print("")
    scope = "全部状态" if not statuses else "/".join(status_label(item) for item in statuses)
    if report.total_before_limit > len(report.hits):
        print(
            f"共 {report.total_before_limit} 个匹配，显示前 {len(report.hits)} 个；"
            f"搜索范围: {scope}。用 --limit 调整。"
        )
    else:
        print(f"共 {len(report.hits)} 个匹配；搜索范围: {scope}。")
    return 0


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------
def build_parser() -> LabParser:
    parser = LabParser(
        prog="lab-workflow",
        description="实验工作流层：registry、生命周期与清理（不提供 TUI；目录浏览交给 try-rs）",
        epilog=(
            "工作台根目录默认 ~/lab；也可用 --root 指定，或运行 init <目录>。"
            "进入工作台后命令会自动发现根目录。"
        ),
    )
    parser.add_argument("--version", action="version", version=f"lab-workflow {__version__}")
    parser.add_argument(
        "--root",
        metavar="PATH",
        help="工作台根目录（默认 ~/lab；也可用 LAB_WORKFLOW_ROOT 或从当前目录发现）",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("init", help="初始化或补齐工作台骨架")
    p.add_argument(
        "path",
        nargs="?",
        help="工作台目录（默认 ~/lab 或从当前目录发现；也可用全局 --root）",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="重写工具管理的文件（AGENTS.md/README.md/bin/README.md/templates/、skill，会覆盖手工修改）",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new", help="创建实验")
    p.add_argument("name", help="实验名称（会规范化为 kebab-case）")
    p.add_argument("-t", "--template", default="general", help="模板: python/node/general（默认 general）")
    p.add_argument("-d", "--description", default="", help="一句话描述")
    p.add_argument("--tag", action="append", default=[], help="标签，可重复或逗号分隔")
    p.add_argument("--category", default="", help="分类")
    p.add_argument("--technology", action="append", default=[], dest="technologies", help="技术栈，可重复")
    p.add_argument("--related-project", action="append", default=[], dest="related_projects", help="相关项目，可重复")
    p.add_argument("--no-git", action="store_true", help="不初始化 git 仓库")
    p.add_argument("--no-env", action="store_true", help="不创建 venv / node_modules")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("adopt", help="把已有目录（例如 try-rs 创建的）登记为实验")
    p.add_argument("path", help="工作台 active/ 下的已有目录")
    p.add_argument("--name", help="覆盖从目录名推导的 name/slug")
    p.add_argument("-t", "--template", help="模板（默认按目录内容检测）")
    p.add_argument("-d", "--description", default="", help="一句话描述")
    p.add_argument("--tag", action="append", default=[], help="标签，可重复或逗号分隔")
    p.add_argument("--category", default="", help="分类")
    p.add_argument("--technology", action="append", default=[], dest="technologies", help="技术栈，可重复")
    p.add_argument("--related-project", action="append", default=[], dest="related_projects", help="相关项目，可重复")
    p.add_argument("--no-git", action="store_true", help="不初始化 git（已有仓库保持原样）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_adopt)

    p = sub.add_parser("list", help="列出实验")
    p.add_argument("-a", "--all", action="store_true", help="包含归档、promote 和 discarded 墓碑")
    p.add_argument("-s", "--status", action="append", default=[], help="按状态过滤，可重复或逗号分隔")
    p.add_argument("--tag", action="append", default=[], help="按标签过滤（必须全部匹配）")
    p.add_argument("--template", help="按模板过滤")
    p.add_argument("--limit", type=int, help="最多显示条数")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="显示实验详情")
    p.add_argument("ref", help="实验 ID 或名称")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("open", help="打印实验目录（交互式跳转请用 try-rs）")
    p.add_argument("ref", help="实验 ID 或名称")
    p.add_argument("--shell", action="store_true", help="在实验目录启动子 shell（try-rs 未安装时的降级方案）")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("status", help="更新实验状态（created/running/success）")
    p.add_argument("ref", help="实验 ID 或名称")
    p.add_argument("status", help="新状态: created / running / success（别名 start/done）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("meta", help="更新实验元数据")
    p.add_argument("ref", help="实验 ID 或名称")
    p.add_argument("--description", help="一句话描述")
    p.add_argument("--summary", help="摘要")
    p.add_argument("--conclusion", help="结论")
    p.add_argument("--category", help="分类")
    p.add_argument("--tags", action="append", help="标签列表（替换原值；可重复/逗号分隔）")
    p.add_argument("--technologies", action="append", help="技术栈列表（替换原值）")
    p.add_argument("--related-projects", action="append", dest="related_projects", help="相关项目列表（替换原值）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_meta)

    p = sub.add_parser("archive", help="归档实验并移动到 archive/YYYY/MM")
    p.add_argument("ref", help="实验 ID 或名称")
    p.add_argument("--force", action="store_true", help="存在未提交更改时仍然归档")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("promote", help="把成功实验提炼到正式项目目录")
    p.add_argument("ref", help="实验 ID 或名称")
    p.add_argument("--to", help="目标目录（默认 ~/workspace/<名称>；必须为空或不存在）")
    p.add_argument("--force", action="store_true", help="存在未提交更改时仍然 promote")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("discard", help="删除实验目录，保留 registry 墓碑")
    p.add_argument("ref", help="实验 ID 或名称")
    p.add_argument("--yes", action="store_true", help="非交互确认（仍会检查未提交更改）")
    p.add_argument("--force", action="store_true", help="有未提交更改时仍然删除")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_discard)

    p = sub.add_parser("clean", help="按保留策略清理过期实验（默认演练）")
    p.add_argument("--apply", action="store_true", help="真正执行删除（默认只列出）")
    p.add_argument("--dry-run", action="store_true", help="只列出待清理项（默认行为，显式写法）")
    p.add_argument("--force", action="store_true", help="连非 git / 有未提交更改的实验也删除")
    p.add_argument("--created-days", type=float, default=clean.DEFAULT_CREATED_DAYS, help="CREATED 保留天数（默认 3）")
    p.add_argument("--success-days", type=float, default=clean.DEFAULT_SUCCESS_DAYS, help="SUCCESS 保留天数（默认 7）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("stats", help="工作台统计")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("search", help="搜索实验元数据和 README")
    p.add_argument("query", nargs="*", help="关键词（多个词为 AND）")
    p.add_argument("--status", action="append", default=[], help="按状态过滤，可重复")
    p.add_argument("--tag", action="append", default=[], help="按标签过滤，可重复")
    p.add_argument("--template", help="按模板过滤")
    p.add_argument("--category", help="按分类过滤")
    p.add_argument("--technology", action="append", default=[], dest="technology", help="按技术过滤，可重复")
    p.add_argument("--limit", type=int, default=20, help="最多结果数（默认 20；0 表示不限）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("doctor", help="检查工作台与 try-rs 集成状态")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_doctor)

    return parser


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _cli_root(args) -> str | None:
    root = args.root
    init_path = getattr(args, "path", None) if args.command == "init" else None
    if init_path:
        if root:
            explicit = Path(root).expanduser().resolve()
            target = Path(init_path).expanduser().resolve()
            if explicit != target:
                raise UsageError("--root 与 init PATH 指向不同目录，请只保留一个")
        root = init_path
    return root


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        workspace = Workspace(root=resolve_root(_cli_root(args)))
        if args.command == "init":
            return args.func(args, workspace)
        _require_workspace(workspace)
        return int(args.func(args, workspace) or 0)
    except LabError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return exc.exit_code
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            pass
        return 0
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"系统错误: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive top level
        if os.environ.get("LAB_DEBUG"):
            raise
        print(f"错误: 未预期异常: {exc}", file=sys.stderr)
        print("设置 LAB_DEBUG=1 可查看完整 traceback。", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
