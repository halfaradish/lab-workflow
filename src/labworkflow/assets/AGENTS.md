<!-- lab:workspace-agents -->
# AGENTS.md — lab-workflow 工作台规则

本目录是个人实验工作台，根目录为 `{{workspace_root}}`。
你是被允许在这个工作台里工作的编码 Agent。请遵守以下规则。

## 定位

lab-workflow 是**实验的 registry 与生命周期层**：登记实验、记录元数据、归档、
promote、清理。它**不提供 TUI**。

目录浏览、预览、模糊搜索和跳转由 [try-rs](https://github.com/tassiovirginio/try-rs)
负责。不要在本仓库里另做一个更差的 TUI。

## 工作台结构

```
{{workspace_root}}/
├── AGENTS.md              # 本文件（所有实验共享）
├── README.md              # 人类使用说明
├── active/                # 进行中的实验
├── archive/YYYY/MM/       # 已归档实验，永久保留
├── templates/             # python / node / general 模板
├── registry/
│   └── experiments.jsonl  # 实验元数据（唯一权威来源）
└── bin/                   # 保留目录，第一版不放入全局命令
```

实验目录命名：`YYYYMMDD-序号-kebab-名称`，例如 `{{workspace_root}}/active/20260101-001-my-idea/`。

## 命令

`lab-workflow` 只在工具仓库内可用，不假设它在全局 PATH 中：

```text
{{lab_cmd}} list
{{lab_cmd}} new my-idea --template python
```

本文档中的命令路径为 `{{lab_cmd}}`（Windows 下是 `bin\lab-workflow.cmd`）。

常用命令：`init`、`new`、`adopt`、`list`、`show`、`open`、`status`、`meta`、
`archive`、`promote`、`discard`、`clean`、`stats`、`search`、`doctor`。

工作台自带 `lab-management` skill（`.agents/skills/lab-management/`）。做实验时优先
按该 skill 的流程执行：先写目标契约，用最小实现验证，再总结和归档。

## 与 try-rs 的分工

| 事情 | 由谁负责 |
| --- | --- |
| 创建带模板/venv/registry 的实验 | `lab-workflow new` |
| 浏览、预览、模糊搜索、跳转目录 | `try-rs` |
| 把 try-rs 创建的目录纳入管理 | `lab-workflow adopt <目录>` |
| 状态、结论、标签、归档、promote、清理 | `lab-workflow` |

规则：

1. try-rs 的 `tries_path` 只指向 `{{workspace_root}}/active`，**不要**指向工作台根目录，
   否则可能误删 registry、模板或已归档内容。
2. 已登记实验不要用 try-rs 的删除键（`Ctrl+D`）。try-rs 只删目录、不会更新
   registry；删除实验必须用 `lab-workflow discard`。
3. 用 try-rs 或手工新建的目录，先用 `lab-workflow adopt` 登记，再开始记录结论。
4. 需要跳转时用 try-rs 或 `cd "$(lab-workflow open <id>)"`；不要要求 lab-workflow
   提供交互界面。

## 硬性规则

1. **只在当前实验目录内写文件。** 不要修改其他实验、`registry/experiments.jsonl`
   或工作台根目录下的共享文件；需要变更元数据时使用 `lab-workflow meta` / `status`。
2. **不要删除、移动实验目录。** 归档用 `archive`，推广用 `promote`，丢弃用
   `discard`。这些操作会影响 registry，不能绕过 CLI。
3. **依赖必须隔离。** Python 使用实验内的 `.venv/`，Node 使用实验内的
   `node_modules/`。禁止 `pip install --user`、`npm install -g`、修改系统 Python，
   也不要为了省事复用其他实验的环境。
4. **不要提交或写入密钥。** `.env`、token、私有配置留在本地；确认 `.gitignore`
   和 `.git/info/exclude` 不会把它们带进提交。
5. **记录结论。** 实验结束前更新 README 的「结果与观察」「结论」，并用
   `lab-workflow meta <id> --conclusion "..."` 同步知识库字段。
6. **promote 需要人工确认。** 只有实验达到 SUCCESS，且用户明确要求时才 promote；
   不要把未验证的代码直接复制到 `~/workspace` 或用户的其他项目。
7. **破坏性操作先确认。** `clean --apply`、`discard`、`--force` 都会删除文件；
   Agent 必须先向用户说明影响并获得同意。
8. **保持可复现。** 在 README 中写清运行命令、依赖安装方式、数据来源和环境假设。

## 生命周期

| 状态 | 含义 | 保留策略 |
| --- | --- | --- |
| CREATED | 刚创建，尚未认真开始 | 超过 3 天可被 `clean` 清理 |
| RUNNING | 正在实验 | 不自动清理 |
| SUCCESS | 有结论，值得保留 | 超过 7 天可被 `clean` 清理 |
| ARCHIVED | 已归档，永久保留 | 不自动清理，且不能再改元数据 |
| PROMOTED | 已提炼进正式项目 | 不自动清理 |
| DISCARDED | 已删除 | registry 保留墓碑用于检索 |

`clean` 默认只演练；只有 `--apply` 才删除。非 git 仓库或有未提交更改的实验默认跳过，
需要 `--force` 才会删除。lab-workflow 自己更新 README 元数据块不算"未提交更改"。

## 完成任务时

- 说明你运行了哪些命令、结果如何。
- 更新实验 README 和元数据。
- 不要声称已完成未验证的事情。
