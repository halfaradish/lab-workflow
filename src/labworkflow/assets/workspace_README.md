# 我的实验工作台

这是 `lab-workflow` 管理的个人实验工作台，数据根目录为 `{{workspace_root}}`。

`lab-workflow` 是**registry 与生命周期层**：创建/登记实验、记录结论、归档、
promote、按策略清理。它**不提供 TUI**——目录浏览、预览、模糊搜索和跳转交给
[try-rs](https://github.com/tassiovirginio/try-rs)。

`lab-workflow` **不安装到全局 PATH**，直接在工具仓库内使用：

```text
{{lab_cmd}} list
```

本工作台内的命令路径为 `{{lab_cmd}}`（Windows 下是 `bin\lab-workflow.cmd`）。

## 与 try-rs 的分工

| 场景 | 命令 |
| --- | --- |
| 创建带模板/环境/registry 的实验 | `{{lab_cmd}} new ...` |
| 浏览、预览、搜索、跳转 | `try-rs`（`tries_path = "{{workspace_root}}/active"`） |
| 登记 try-rs 或手工创建的目录 | `{{lab_cmd}} adopt <目录>` |
| 改状态、写结论、归档、promote、清理 | `{{lab_cmd}} ...` |

注意：

- try-rs 的 `tries_path` 只指向 `active/`，不要指向工作台根目录。
- 已登记的实验不要用 try-rs 的 `Ctrl+D` 删除：try-rs 不知道 registry。
  删除请用 `{{lab_cmd}} discard`。
- try-rs 创建的目录先用 `adopt` 登记，之后才能用 `status`/`meta`/`archive`。

## 快速开始

```bash
# 在 lab-workflow 仓库内执行
{{lab_cmd}} init
{{lab_cmd}} new my-first-idea --template python --tag learning,cli
{{lab_cmd}} list
{{lab_cmd}} show my-first-idea
cd "$({{lab_cmd}} open my-first-idea)"

# Windows: 先运行 {{lab_cmd}} open my-first-idea，再 cd 到输出路径

# 用 try-rs 浏览/跳转（先配置 tries_path）
try-rs

# 结束实验
{{lab_cmd}} meta my-first-idea --summary "结论摘要" --conclusion "值得继续"
{{lab_cmd}} archive my-first-idea
```

## 目录结构

```
{{workspace_root}}/
├── AGENTS.md              # 所有实验共享的 Agent 规则
├── README.md              # 本文件
├── active/                # 活跃实验（YYYYMMDD-NNN-kebab-name）
├── archive/YYYY/MM/       # 已归档实验
├── templates/             # python / node / general 模板
├── registry/
│   └── experiments.jsonl  # 元数据，registry 是权威来源
├── bin/                   # 预留；第一版为空
├── .agent/skills/lab-management/   # 权威 skill 副本
└── .agents/skills/lab-management/  # 通用 Agent 运行时发现路径的兼容副本
```

每个实验目录包含：

- `README.md`：结构化实验记录（假设、方法、结果、结论）
- `.lab-meta.json`：registry 的本地镜像（已被 git 忽略）
- `AGENTS.md`：指向工作台 `AGENTS.md` 的符号链接（不支持符号链接时退化为受管副本）
- 按模板生成的代码骨架和隔离环境

## 实验生命周期

| 状态 | 含义 | 清理策略 |
| --- | --- | --- |
| CREATED | 刚创建 | 超过 3 天未推进，`clean` 可清理 |
| RUNNING | 进行中 | 永不自动清理 |
| SUCCESS | 有结论 | 超过 7 天，`clean` 可清理 |
| ARCHIVED | 已归档 | 永久保留，且不再接受 `status`/`meta` |
| PROMOTED | 已提炼进正式项目 | 永久保留 |
| DISCARDED | 已丢弃 | 目录删除，registry 保留墓碑 |

`clean` 默认是 dry-run；`--apply` 才删除。非 git 仓库或有未提交更改的实验
默认跳过，必须加 `--force`。lab-workflow 自己更新 README 元数据块不算未提交更改。

## 常用命令

```bash
{{lab_cmd}} new <名称> [--template python|node|general] [--tag a,b] [--no-git] [--no-env]
{{lab_cmd}} adopt <active 下的已有目录> [--name <名称>] [--template ...]
{{lab_cmd}} list [--all] [--status running] [--tag cli]
{{lab_cmd}} show <id|名称> [--json]
{{lab_cmd}} open <id|名称>
{{lab_cmd}} status <id|名称> running|success
{{lab_cmd}} meta <id|名称> --summary "..." --conclusion "..." --category cli
{{lab_cmd}} search "关键词" [--tag cli] [--status success]
{{lab_cmd}} archive <id|名称>
{{lab_cmd}} promote <id|名称> [--to ~/workspace/my-project]
{{lab_cmd}} discard <id|名称> --yes
{{lab_cmd}} clean [--apply] [--force]
{{lab_cmd}} stats
{{lab_cmd}} doctor
```

`open` 只打印绝对路径，适合脚本；交互式跳转请用 try-rs。

## 环境隔离

- Python：实验内 `.venv/`。如果宿主机缺少 `ensurepip`，`lab-workflow` 会自动下载
  `get-pip.py` 到 `~/.cache/lab-workflow/get-pip.py` 并只在 venv 内安装 pip。
  不会修改系统 Python 或全局 site-packages。
- Node：实验内 `node_modules/`，从不使用 `npm -g`。
- 通用模板不创建语言环境。
- `--no-env` 可跳过环境初始化；之后可在实验目录手动创建。
- `adopt` 不创建环境，只检测并记录已有的 `.venv/` 或 `node_modules/`。

## 元数据字段

`registry/experiments.jsonl` 中的每个实验包含：`id`、`name`、`slug`、`path`、
`status`、`template`、`created_at`、`updated_at`、`status_changed_at`、
`description`、`tags`、`category`、`summary`、`conclusion`、`technologies`、
`related_projects`、`git`、`env`，以及归档/promote/丢弃时间等生命周期字段。
通过 `adopt` 登记的记录额外带 `source: "adopt"` 和 `adopted_at`。

不要手工编辑 registry；使用 `meta`、`status`、`adopt` 等命令。需要备份时直接
复制 `registry/experiments.jsonl`。

## 设计边界（第一版）

- 不提供 TUI、文件预览、模糊搜索界面或 shell 跳转——这些是 try-rs 的职责。
- 不提供 Web 面板、数据库、后台服务。
- 不安装全局命令，不软链到 `~/.local/bin`。
- 没有 unarchive 命令；归档后不能再改状态/元数据，如需继续先评估 promote。
- `promote` 只复制文件，不自动 `git init`，也不替用户决定仓库结构。

本文档最近同步时间：`{{generated_at}}`（`init --refresh` 会更新）。
