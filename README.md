# Lab — Personal Experiment Workspace CLI

一个面向“快速验证想法”的个人实验工作台。它把临时实验放进统一的 `~/lab`，
用一条命令创建目录、模板、README、Git 和隔离依赖，并通过 registry 记录生命周期，
避免想法散落成无法检索的代码垃圾场。

核心理念：**快速创建 → 验证 → 归档 / 提炼 / 丢弃**。实验代码默认只是临时资产，
真正有价值的成果才进入 `~/workspace`。

## 版本边界

第一版是**仓库内工具**：

- 只通过仓库里的 `./bin/lab` 运行，不安装到全局 `PATH`，也不创建 `~/.local/bin` 软链。
- 工作台根目录固定为 `~/lab`；不提供 Web 面板、数据库或后台服务。
- 不自动安装 systemd 服务；`contrib/systemd/` 只有可选的清理定时器样例。
- 不自动 promote，也不自动删除非空实验：破坏性操作需要显式参数或交互确认。

## 快速开始

要求：Python 3.10+、Git；Python/Node 实验还需要对应运行时。整个工具只依赖
Python 标准库。

```bash
cd ~/project/lab-cli
./bin/lab init

# 创建一个 Python 实验（默认初始化 Git 和实验内 .venv）
./bin/lab new websocket-test --template python --tag network,perf --description "对比两种 WebSocket 实现"

# 查看并进入实验
./bin/lab list
./bin/lab show websocket-test
cd "$(./bin/lab open websocket-test)"

# 记录结论、推进状态
cd ~/project/lab-cli
./bin/lab status websocket-test running
./bin/lab meta websocket-test --summary "方案 B 延迟更低" --conclusion "值得继续" --category networking
./bin/lab status websocket-test success

# 归档，或提炼到正式项目（目标必须不存在或为空）
./bin/lab archive websocket-test
# ./bin/lab promote websocket-test --to ~/workspace/websocket-test
```

`new` 默认会：

1. 分配 `YYYYMMDD-NNN` 全局唯一 ID；
2. 在 `active/` 下创建 `YYYYMMDD-NNN-kebab-name/`；
3. 渲染模板，生成结构化 `README.md` 和 `.lab-meta.json`；
4. 创建指向 `~/lab/AGENTS.md` 的 `AGENTS.md` 符号链接；
5. 初始化 Git 并提交初始快照；
6. 按模板创建实验内 Python `.venv` 或 Node `node_modules`；
7. 写入 `registry/experiments.jsonl`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `lab init` | 初始化或补齐 `~/lab` 骨架；不覆盖已有文件 |
| `lab new <name>` | 创建实验，支持 `--template`、`--tag`、`--description`、`--no-git`、`--no-env` |
| `lab list` | 列出活跃实验；`--all` 包含归档/promote/丢弃墓碑；支持状态、标签、模板过滤 |
| `lab show <ref>` | 显示详情和路径；`--json` 输出结构化数据 |
| `lab open <ref>` | 只打印绝对路径，便于 `cd $(...)`；`--shell` 直接进入子 shell |
| `lab status <ref> <state>` | 更新 `created` / `running` / `success`（别名 `start` / `done`） |
| `lab meta <ref> ...` | 更新摘要、结论、分类、标签、技术栈、相关项目 |
| `lab archive <ref>` | 移动到 `archive/YYYY/MM/`，状态变为 `ARCHIVED`；有未提交更改需 `--force` |
| `lab promote <ref>` | 只允许 `SUCCESS`/`ARCHIVED`，复制到 `~/workspace/<name>` 或 `--to`；源实验转为归档并标记 `PROMOTED` |
| `lab discard <ref>` | 删除目录并保留 registry 墓碑；非交互必须 `--yes`，有未提交更改还需 `--force` |
| `lab clean` | 按保留策略清理；默认演练，`--apply` 才删除 |
| `lab stats` | 状态、模板、标签、最近新建、目录缺失/脏工作区统计 |
| `lab search [query]` | 在元数据和 README 中检索；支持标签、状态、模板、分类、技术过滤 |

`<ref>` 可以是完整 ID（如 `20261120-001`）、唯一名称或 slug。为免歧义，
不支持把 `001` 当作 ID 简写。

### 元数据

每个实验在 registry 中是一条 JSON 记录，主要字段：

```text
id, name, slug, path, status, template, created_at, updated_at,
status_changed_at, description, summary, conclusion, category,
tags, technologies, related_projects, git, env,
archived_at, archived_from_status, promoted_at, promoted_to,
discarded_at, discarded_reason, discarded_from_status
```

`registry/experiments.jsonl` 是唯一权威来源；每次变更原子重写整个文件。
实验目录里的 `.lab-meta.json` 只是方便本地查看的镜像，且被 Git 忽略。
丢弃和 promote 的记录会保留为墓碑，供以后搜索“以前是否做过”。

已结束的实验禁止再用 `status` / `meta` 修改。不要手工编辑 registry。

## 生命周期与清理

| 状态 | 含义 | 存放位置 | 自动清理 |
| --- | --- | --- | --- |
| CREATED | 刚创建，尚未认真开始 | `active/` | 超过 3 天 |
| RUNNING | 正在验证 | `active/` | 永不 |
| SUCCESS | 有结论，值得保留 | `active/` | 超过 7 天 |
| ARCHIVED | 已归档，永久保留 | `archive/YYYY/MM/` | 永不 |
| PROMOTED | 已提炼进正式项目 | 源目录归档 | 永不 |
| DISCARDED | 已删除 | 无目录，registry 墓碑 | 立即 |

清理默认只做演练：

```bash
./bin/lab clean
./bin/lab clean --apply
./bin/lab clean --created-days 5 --success-days 14
```

`clean` 的默认安全行为：

- 只处理 `active/` 下、状态为 `CREATED` 或 `SUCCESS` 的实验；
- `RUNNING`、归档、promote 过的实验永不自动清理；
- 非 Git 仓库或有未提交更改的实验默认跳过并警告，只有 `--force` 才删除；
- registry 中有记录但目录缺失时只警告，不修改记录；
- `active/` 下未登记的目录只警告，不删除；
- 删除后保留 `DISCARDED` 墓碑。

## 环境隔离

- **Python**：在实验目录创建 `.venv/`，依赖永远不装进系统 Python。
  如果宿主机缺少 `ensurepip`，会先尝试 `--without-pip`，再从
  `https://bootstrap.pypa.io/get-pip.py` 下载到 `~/.cache/lab/get-pip.py`
  并只在实验 venv 内安装 pip。网络失败只产生警告，不阻止实验创建。
- **Node**：在实验目录执行 `npm install`，依赖进入实验内 `node_modules/`；
  不使用 `npm -g`。
- **general**：不创建语言环境，只生成笔记骨架。
- `--no-env` 可跳过环境创建；之后可在实验目录自行处理。
- `--no-git` 可跳过 Git 初始化。默认 `.git/info/exclude` 会忽略 `.venv/`、
  `node_modules/`、`.lab-meta.json` 和 `AGENTS.md` 等实验环境文件。

## 工作台结构

```text
~/lab/
├── AGENTS.md               # 所有实验共享的 Agent 规范
├── README.md               # 工作台说明
├── active/
│   └── YYYYMMDD-NNN-name/
├── archive/YYYY/MM/
├── templates/              # python / node / general
├── registry/experiments.jsonl
└── bin/                    # 预留，不生成全局命令
```

实验 README 使用受管理的标记块（`<!-- lab:metadata:start -->` 等）同步元数据、
描述和结论；用户自己写的章节不会被覆盖。如果手工删除标记，`lab` 不会再改
那一块。

## 代码结构

```text
bin/lab                        入口（把仓库 src/ 加入 sys.path）
src/labctl/cli.py              参数解析、输出、错误处理
src/labctl/workspace.py        工作台骨架、模板、文件锁
src/labctl/registry.py         JSONL registry 与状态机
src/labctl/experiments.py      创建、查询、归档、promote、discard
src/labctl/clean.py            保留策略评估与清理
src/labctl/environment.py      venv / npm 隔离环境
src/labctl/gitutil.py          Git 初始化、dirty 检测
src/labctl/stats.py            统计
src/labctl/search.py           检索
src/labctl/assets/             AGENTS.md、README 模板、三类实验模板
tests/test_lab.py              端到端测试（隔离 HOME，不触碰真实 ~/lab）
contrib/systemd/               可选的清理定时器样例（不自动安装）
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试为每个用例创建临时 `HOME`，通过子进程调用真实的 `./bin/lab`；默认
`--no-env`，不依赖网络。涉及 Python/Node 环境初始化的路径已在手工冒烟测试中
验证，但自动化测试不覆盖下载 `get-pip.py` 这类网络路径。

## 已知限制

- 只支持单机、单用户、本地文件系统；`fcntl` 文件锁和符号链接是 Linux 假设。
- 没有 unarchive 命令；归档后要继续实验，需手工调整目录和 registry（不推荐）。
- `promote` 只做过滤复制（排除 `.git`、`.venv`、`node_modules`、缓存、
  `.lab-meta.json` 和 `AGENTS.md` 链接），不会替你重构代码、补充 README 或
  `git init`；目标目录必须不存在或为空。
- 第一版不安装全局命令。要长期使用，可把仓库放在固定位置并用绝对路径调用。
- 本仓库的 `docs/original.md` 是本地设计稿，已在 `.gitignore` 中排除，不随
  代码提交。
