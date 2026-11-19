# 我的实验工作台

这是 `lab` 管理的个人实验工作台，根目录固定为 `{{workspace_root}}`。

`lab` 的第一版**不安装到全局 PATH**，只在 lab 仓库目录内使用：

```bash
cd /path/to/lab-cli
./bin/lab list
```

本工作台内的命令路径为 `{{lab_cmd}}`。

## 快速开始

```bash
# 在 lab 仓库内执行
./bin/lab init
./bin/lab new my-first-idea --template python --tag learning,cli
./bin/lab list
./bin/lab show my-first-idea
./bin/lab open my-first-idea
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
└── bin/                   # 预留；第一版为空
```

每个实验目录包含：

- `README.md`：结构化实验记录（假设、方法、结果、结论）
- `.lab-meta.json`：registry 的本地镜像（已被 git 忽略）
- `AGENTS.md`：指向工作台 `AGENTS.md` 的符号链接
- 按模板生成的代码骨架和隔离环境

## 实验生命周期

| 状态 | 含义 | 清理策略 |
| --- | --- | --- |
| CREATED | 刚创建 | 超过 3 天未推进，`clean` 可清理 |
| RUNNING | 进行中 | 永不自动清理 |
| SUCCESS | 有结论 | 超过 7 天，`clean` 可清理 |
| ARCHIVED | 已归档 | 永久保留 |
| PROMOTED | 已提炼进正式项目 | 永久保留 |
| DISCARDED | 已丢弃 | 目录删除，registry 保留墓碑 |

`clean` 默认是 dry-run；`--apply` 才删除。非 git 仓库或有未提交更改的实验
默认跳过，必须加 `--force`。删除前请确认没有值得保留的内容。

## 常用命令

```bash
{{lab_cmd}} new <名称> [--template python|node|general] [--tag a,b] [--no-git] [--no-env]
{{lab_cmd}} list [--all] [--status running] [--tag cli]
{{lab_cmd}} show <id|名称> [--json]
{{lab_cmd}} open <id|名称> [--shell]
{{lab_cmd}} status <id|名称> running|success
{{lab_cmd}} meta <id|名称> --summary "..." --conclusion "..." --category cli
{{lab_cmd}} search "关键词" [--tag cli] [--status success]
{{lab_cmd}} archive <id|名称>
{{lab_cmd}} promote <id|名称> [--to ~/workspace/my-project]
{{lab_cmd}} discard <id|名称> --yes
{{lab_cmd}} clean [--apply] [--force]
{{lab_cmd}} stats
```

`open` 只打印绝对路径；`open --shell` 会在实验目录启动一个子 shell。

## 环境隔离

- Python：实验内 `.venv/`。如果宿主机缺少 `ensurepip`，`lab` 会自动下载
  `get-pip.py` 到 `~/.cache/lab/get-pip.py` 并只在 venv 内安装 pip。
  不会修改系统 Python 或全局 site-packages。
- Node：实验内 `node_modules/`，从不使用 `npm -g`。
- 通用模板不创建语言环境。
- `--no-env` 可跳过环境初始化；之后可在实验目录手动创建。

## 元数据字段

`registry/experiments.jsonl` 中的每个实验包含：`id`、`name`、`slug`、`path`、
`status`、`template`、`created_at`、`updated_at`、`status_changed_at`、
`description`、`tags`、`category`、`summary`、`conclusion`、`technologies`、
`related_projects`、`git`、`env`，以及归档/promote/丢弃时间等生命周期字段。

不要手工编辑 registry；使用 `lab meta`、`lab status` 等命令。需要备份时直接
复制 `registry/experiments.jsonl`。

## 设计边界（第一版）

- 不提供 Web 面板、数据库、后台服务。
- 不安装全局命令，不软链到 `~/.local/bin`。
- 没有 unarchive 命令；归档后如需继续实验，先 `lab promote` 或手工移动并
  修改 registry（不推荐）。
- `promote` 只复制文件，不自动 `git init`，也不替用户决定仓库结构。

工作台初始化时间：`{{generated_at}}`。
