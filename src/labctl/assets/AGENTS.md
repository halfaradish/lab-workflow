# AGENTS.md — 实验工作台规则

本目录是个人实验工作台，根目录为 `{{workspace_root}}`。
你是被允许在这个工作台里工作的编码 Agent。请遵守以下规则。

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

第一版 `lab` 只在 lab 仓库内可用：

```bash
cd /path/to/lab-cli
./bin/lab list
./bin/lab new my-idea --template python
```

本文档中的命令路径为 `{{lab_cmd}}`。不要假设 `lab` 已在全局 PATH 中。

常用命令：`init`、`new`、`list`、`show`、`open`、`status`、`meta`、
`archive`、`promote`、`discard`、`clean`、`stats`、`search`。

## 硬性规则

1. **只在当前实验目录内写文件。** 不要修改其他实验、`registry/experiments.jsonl`
   或工作台根目录下的共享文件；需要变更元数据时使用 `lab meta` / `lab status`。
2. **不要删除、移动实验目录。** 归档用 `lab archive`，推广用 `lab promote`，
   丢弃用 `lab discard`。这些操作会影响 registry，不能绕过 CLI。
3. **依赖必须隔离。** Python 使用实验内的 `.venv/`，Node 使用实验内的
   `node_modules/`。禁止 `pip install --user`、`npm install -g`、修改系统 Python，
   也不要为了省事复用其他实验的环境。
4. **不要提交或写入密钥。** `.env`、token、私有配置留在本地；确认 `.gitignore`
   和 `.git/info/exclude` 不会把它们带进提交。
5. **记录结论。** 实验结束前更新 README 的「结果与观察」「结论」，并用
   `lab meta <id> --conclusion "..."` 同步知识库字段。
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
| ARCHIVED | 已归档，永久保留 | 不自动清理 |
| PROMOTED | 已提炼进正式项目 | 不自动清理 |
| DISCARDED | 已删除 | registry 保留墓碑用于检索 |

`clean` 默认只演练；只有 `--apply` 才删除。非 git 仓库或有未提交更改的实验默认跳过，
需要 `--force` 才会删除。

## 完成任务时

- 说明你运行了哪些命令、结果如何。
- 更新实验 README 和元数据。
- 不要声称已完成未验证的事情。
