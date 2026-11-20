# lab-workflow

**实验的 registry 与生命周期层。** 它负责创建/登记实验、记录结论、归档、promote 和
按策略清理；**不提供 TUI**——目录浏览、预览、模糊搜索和跳转交给
[try-rs](https://github.com/tassiovirginio/try-rs)。

```text
想法
 │  lab-workflow new / adopt
 ▼
实验目录 + 模板 + venv/npm + registry
 │
 ├── try-rs：浏览 / 预览 / 跳转
 │
 ├── lab-workflow status / meta / search / stats
 ▼
archive / promote / clean / discard
```

## 定位与非目标

工作台数据默认放在 `~/lab`，但不再是硬编码：`init <目录>` 可以把任意目录变成
工作台。工具与数据目录解耦；已有 `~/lab` 工作台无需迁移。

```bash
./bin/lab-workflow init /data/lab          # 指定目录初始化
./bin/lab-workflow --root /data/lab list   # 显式指定根目录
cd /data/lab && /path/to/lab-workflow list # 从工作台内部自动发现
LAB_WORKFLOW_ROOT=/data/lab ./bin/lab-workflow list
```

优先级：`--root` > `init PATH` > `LAB_WORKFLOW_ROOT` > 当前目录向上发现 > `~/lab`。
`--root` 是全局参数，必须放在子命令前。

**做：**

- 一条命令创建实验目录、模板、README、Git 和隔离依赖；
- JSONL registry 作为唯一权威来源，记录状态、摘要、结论、标签、技术栈；
- 生命周期：`CREATED → RUNNING → SUCCESS → ARCHIVED / PROMOTED / DISCARDED`；
- 归档、promote 到 `~/workspace`、按保留策略 `clean`（默认演练）；
- 全文检索历史结论，保留丢弃墓碑；
- `adopt`：把 try-rs 或手工创建的目录纳入 registry；
- `doctor`：检查工作台、依赖和 try-rs 集成状态。

**不做（非目标）：**

- 不做 TUI、文件预览、主题、模糊搜索界面、shell 目录跳转；
- 不做 Web 面板、数据库、后台服务；
- 不安装全局命令，不接管 shell；
- 不做 unarchive（归档即只读）。

## 与 try-rs 的分工

[try-rs](https://github.com/tassiovirginio/try-rs) 是临时实验目录的**文件系统视图**：
Rust 单二进制、TUI、模糊过滤、预览、多根目录、编辑器/shell 集成。它没有 registry、
状态机、结论字段，删除也不留墓碑。两者不是替代关系：

| 能力 | try-rs | lab-workflow |
| --- | --- | --- |
| 创建目录 | ✅（可带日期前缀） | ✅（ID/模板/README/venv/npm/Git） |
| 浏览 / 预览 / 模糊搜索 | ✅ TUI | ❌ 交给 try-rs |
| 跳转 / shell cd 集成 | ✅ | ❌（`open` 只输出路径） |
| registry + 墓碑 | ❌ | ✅ JSONL |
| 状态 / 摘要 / 结论 / 标签 | ❌ | ✅ |
| 归档 / promote | ❌（只能手工移动） | ✅ |
| 保留策略清理 | ❌（手工删除） | ✅ dry-run + `--apply` |
| 依赖隔离 | ❌ | ✅ 实验内 `.venv` / `node_modules` |
| Agent 规范 | ❌ | ✅ `AGENTS.md` 注入 |
| 历史检索 | 仅目录名 | ✅ 元数据 + README 全文 |

**推荐配置**：让 try-rs 只管理工作台的 `active/`，不要指向工作台根目录。
如果使用自定义根目录，把下面的路径替换成 `<workspace>/active`。

```toml
# ~/.config/try-rs/config.toml
tries_path = "~/lab/active"
editor = "code"
```

### 组合工作流

1. `lab-workflow new websocket-test --template python --tag network` 创建并登记；
2. `try-rs` 里模糊搜索、预览、回车跳转；
3. 实验过程中用 `lab-workflow status` / `meta` 记录进展和结论；
4. 结束后 `archive`、`promote` 到 `~/workspace`，或交给 `clean` 按策略处理；
5. 半年后用 `lab-workflow search websocket` 找回结论。

### 两边都创建了目录怎么办

try-rs 或手工创建的目录没有 registry 记录，用 `adopt` 原地登记（不移动、不覆盖
现有文件）：

```bash
cd ~/lab/active
try-rs nightly-probe        # try-rs 创建目录
cd ~/project/lab-workflow
./bin/lab-workflow adopt ~/lab/active/nightly-probe --template python
```

`adopt` 会分配 ID、生成缺失的 README、建立 AGENTS 链接、检测模板与已有环境、
初始化 Git（已有仓库则沿用），并写入 registry。名字里的 `20261120-001-` 或
`2026-11-20-` 前缀会被识别。目录里的文件原样保留，`created_at` / `adopted_at`
记录登记时间（不伪造历史创建时间）。如果工作台不是 `~/lab`，把示例里的路径换成
`<workspace>/active`，并给命令加 `--root <workspace>`。

### 两条边界

1. **不要用 try-rs 的 `Ctrl+D` 删除已登记实验**：它只删目录，registry 会留下
   "目录缺失"的记录。删除请用 `lab-workflow discard`。
2. **不要给 lab-workflow 加 TUI**。需要新的浏览能力时，优先给 try-rs 提 issue/PR，
   或另建独立前端；见下方决策记录。

## 快速开始

要求：Python 3.10+、Git；Python/Node 实验需要对应运行时。只依赖 Python 标准库。

```bash
cd ~/project/lab-workflow
./bin/lab-workflow init              # 默认工作台 ~/lab
# 或：./bin/lab-workflow init /data/lab

# 创建 Python 实验（默认初始化 Git 和实验内 .venv）
./bin/lab-workflow new websocket-test --template python --tag network,perf \
  --description "对比两种 WebSocket 实现"

./bin/lab-workflow list
./bin/lab-workflow show websocket-test
cd "$(./bin/lab-workflow open websocket-test)"

# 记录结论、推进状态
./bin/lab-workflow status websocket-test running
./bin/lab-workflow meta websocket-test --summary "方案 B 延迟更低" \
  --conclusion "值得继续" --category networking
./bin/lab-workflow status websocket-test success

# 归档，或提炼到正式项目（目标必须不存在或为空）
./bin/lab-workflow archive websocket-test
# ./bin/lab-workflow promote websocket-test --to ~/workspace/websocket-test
```

`init` 还会把 `lab-management` skill 安装到工作台的 `.agent/skills/` 和
`.agents/skills/`。后者是 ZCode 等运行时的通用发现路径；`init --refresh` 会同时更新
两份。skill 里预留了工作台根目录和 CLI 路径，安装时按当前机器渲染。

### Windows

```bat
cd C:\path\to\lab-workflow
bin\lab-workflow.cmd init
bin\lab-workflow.cmd init D:\lab
bin\lab-workflow.cmd new websocket-test --template python
```

`bin\lab-workflow.cmd` 会优先使用 `py -3`，否则使用 `python`。实验内 Python 环境在
`.venv\Scripts\`；`AGENTS.md` 在不支持符号链接的文件系统上会退化为受管副本。
建议开启 Windows 长路径支持（实验路径 + `.venv` 可能超过 260 字符）。

`new` 默认会：

1. 分配 `YYYYMMDD-NNN` 全局唯一 ID；
2. 在 `active/` 下创建 `YYYYMMDD-NNN-kebab-name/`；
3. 渲染模板，生成结构化 `README.md` 和 `.lab-meta.json`；
4. 创建指向 `<workspace>/AGENTS.md` 的 `AGENTS.md` 符号链接（Windows 上退化为受管副本）；
5. 初始化 Git 并提交初始快照；
6. 按模板创建实验内 Python `.venv` 或 Node `node_modules`；
7. 写入 `registry/experiments.jsonl`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `lab-workflow init [PATH]` | 初始化任意目录为工作台（默认 `~/lab`）；`--refresh` 重写工具管理的文件和 skill |
| `lab-workflow new <name>` | 创建实验，支持 `--template`、`--tag`、`--description`、`--no-git`、`--no-env` |
| `lab-workflow adopt <path>` | 把 `active/` 下已有目录登记为实验（不移动、不覆盖） |
| `lab-workflow list` | 列出活跃实验；`--all` 含归档/promote/丢弃墓碑；支持状态、标签、模板过滤 |
| `lab-workflow show <ref>` | 显示详情和路径；`--json` 输出结构化数据 |
| `lab-workflow open <ref>` | 只打印绝对路径；`--shell` 是无 try-rs 时的降级方案 |
| `lab-workflow status <ref> <state>` | 更新 `created` / `running` / `success`（别名 `start` / `done`） |
| `lab-workflow meta <ref> ...` | 更新摘要、结论、分类、标签、技术栈、相关项目 |
| `lab-workflow archive <ref>` | 移动到 `archive/YYYY/MM/`，状态变为 `ARCHIVED` |
| `lab-workflow promote <ref>` | 只允许 `SUCCESS`/`ARCHIVED`，复制到 `~/workspace/<name>` 或 `--to` |
| `lab-workflow discard <ref>` | 删除目录并保留 registry 墓碑；非交互必须 `--yes` |
| `lab-workflow clean` | 按保留策略清理；默认演练，`--apply` 才删除 |
| `lab-workflow stats` | 状态、模板、标签、最近新建、目录缺失/脏工作区统计 |
| `lab-workflow search [query]` | 在元数据和 README 中检索；支持标签、状态、模板、分类、技术过滤 |
| `lab-workflow doctor` | 检查工作台、Git/Python/npm、try-rs 集成和未登记目录 |

`<ref>` 可以是完整 ID、唯一名称或 slug。不支持把 `001` 当作 ID 简写。

### 未提交更改的判定

`status` / `meta` 会改写实验 README 里的受管区块（`<!-- lab:metadata:start -->` 等）。
这类工具自身产生的差异**不算未提交更改**，因此正常走完 `status → meta → archive /
promote` 不需要 `--force`。用户自己改 README 其他章节、代码或新增文件，仍然会被
`archive` / `promote` / `discard` / `clean` 拦截。

### 元数据

每个实验在 registry 中是一条 JSON 记录，主要字段：

```text
id, name, slug, path, status, template, created_at, updated_at,
status_changed_at, description, summary, conclusion, category,
tags, technologies, related_projects, git, env,
archived_at, archived_from_status, promoted_at, promoted_to,
discarded_at, discarded_reason, discarded_from_status
```

通过 `adopt` 登记的记录额外包含 `source: "adopt"` 和 `adopted_at`。

`registry/experiments.jsonl` 是唯一权威来源；每次变更原子重写整个文件。
实验目录里的 `.lab-meta.json` 只是方便本地查看的镜像，且被 Git 忽略。
丢弃和 promote 的记录会保留为墓碑，供以后搜索“以前是否做过”。

`ARCHIVED`、`PROMOTED`、`DISCARDED` 为只读状态，不能再 `status` / `meta`。
不要手工编辑 registry。

## 生命周期与清理

| 状态 | 含义 | 存放位置 | 自动清理 |
| --- | --- | --- | --- |
| CREATED | 刚创建，尚未认真开始 | `active/` | 超过 3 天 |
| RUNNING | 正在验证 | `active/` | 永不 |
| SUCCESS | 有结论，值得保留 | `active/` | 超过 7 天 |
| ARCHIVED | 已归档，永久保留 | `archive/YYYY/MM/` | 永不 |
| PROMOTED | 已提炼进正式项目 | 源目录归档 | 永不 |
| DISCARDED | 已删除 | 无目录，registry 墓碑 | 立即 |

```bash
./bin/lab-workflow clean              # 演练
./bin/lab-workflow clean --apply
./bin/lab-workflow clean --created-days 5 --success-days 14
```

`clean` 的安全规则：只处理 `active/` 下 `CREATED`/`SUCCESS`；`RUNNING` 和归档
永不自动清理；非 Git 或有未提交更改默认跳过（`--force` 才删）；registry 有记录但
目录缺失只警告；`active/` 下未登记目录只警告（可用 `adopt` 纳入）。

## 环境隔离

- **Python**：实验内 `.venv/`。宿主机缺 `ensurepip` 时先用 `--without-pip`，
  再从 `https://bootstrap.pypa.io/get-pip.py` 下载到缓存目录
  （Linux: `$XDG_CACHE_HOME/lab-workflow` 或 `~/.cache/lab-workflow`；
  Windows: `%LOCALAPPDATA%\lab-workflow`），只在实验 venv 内安装 pip。网络失败只警告。
  Windows 的解释器在 `.venv\Scripts\python.exe`。
- **Node**：实验内 `npm install`，依赖进入 `node_modules/`，不使用 `npm -g`。
- **general**：不创建语言环境。
- `--no-env` / `--no-git` 可跳过；`adopt` 不创建环境，只检测已有环境。

## 工作台结构

```text
<workspace>/                 # 默认 ~/lab，也可由 init <目录> 指定
├── AGENTS.md               # 所有实验共享的 Agent 规范
├── README.md               # 工作台说明
├── active/
│   └── YYYYMMDD-NNN-name/
├── archive/YYYY/MM/
├── templates/              # python / node / general
├── registry/experiments.jsonl
├── bin/                    # 预留，不生成全局命令
├── .agent/skills/lab-management/   # 权威 skill 副本
└── .agents/skills/lab-management/  # 兼容副本（ZCode 等通用发现路径）
```

实验 README 使用受管理的标记块（`<!-- lab:metadata:start -->` 等）同步元数据。
用户自己写的章节不会被覆盖；手工删除标记后，工具不再改那一块。

## 设计边界

**决策：不做 TUI（ADR-0001）。** try-rs 已经把"临时实验目录的浏览/跳转"做得很成熟，
再写一个 TUI 只会更差，并且会分叉数据模型。lab-workflow 只做 try-rs 没有的
registry、生命周期和审计能力。

- `open` 只输出路径，供脚本或 `cd $(...)` 使用；`--shell` 仅作为没有 try-rs 时的
  降级方案，不算产品方向。
- 想加基于 registry 的交互视图时，先评估能否作为 try-rs 的上游功能；不能则单独
  建前端仓库，不往本仓库塞 UI。
- 不引入数据库：个人规模下 JSONL + 原子重写足够，且便于 diff 和备份。
- 不自动提交用户代码：registry 是权威来源，实验目录里的 Git 归用户。

## 代码结构

```text
bin/lab-workflow               入口（把仓库 src/ 加入 sys.path）
bin/lab-workflow.cmd           Windows 入口（优先 py -3，回退 python）
src/labworkflow/cli.py         参数解析、根目录解析、输出、错误处理
src/labworkflow/workspace.py   工作台骨架、模板、skill 安装、刷新、文件锁
src/labworkflow/compat.py      平台兼容：锁、venv 路径、编码、缓存、原子替换
src/labworkflow/registry.py    JSONL registry 与状态机
src/labworkflow/experiments.py 创建、adopt、查询、归档、promote、discard
src/labworkflow/clean.py       保留策略评估与清理
src/labworkflow/environment.py venv / npm 隔离环境
src/labworkflow/gitutil.py     Git 初始化、dirty 检测（忽略受管 README 区块）
src/labworkflow/stats.py       统计
src/labworkflow/search.py      检索
src/labworkflow/doctor.py      工作台与 try-rs 集成诊断
src/labworkflow/assets/        AGENTS.md、README 模板、三类实验模板、skills/
tests/test_lab_workflow.py     端到端测试（隔离 HOME/USERPROFILE，不触碰真实工作台）
contrib/systemd/               可选的清理定时器样例（Linux，不自动安装）
```

## 测试

```bash
python3 -m unittest discover -s tests -v
# Windows: py -3 -m unittest discover -s tests -v
```

测试为每个用例创建临时 `HOME`/`USERPROFILE`/`LOCALAPPDATA`，通过子进程调用真实的
`./bin/lab-workflow`（Windows 为 `bin\lab-workflow.cmd` 同一入口逻辑）；默认
`--no-env`，不依赖网络。

## 版本与迁移

当前版本 `0.3.0`：

- 工作台根目录不再硬编码：支持 `init <目录>`、全局 `--root`、
  `LAB_WORKFLOW_ROOT` 和从当前目录自动发现；默认仍是 `~/lab`；
- `lab-management` skill 进入仓库资产，`init` 自动安装到
  `<workspace>/.agent/skills/` 和 `<workspace>/.agents/skills/`，
  `init --refresh` 更新；
- 新增 Linux/Windows 兼容层：文件锁、venv `Scripts/` 路径、UTF-8 子进程、
  read-only 文件清理、AGENTS.md 符号链接回退、Windows 保留名检查；
- 新增 `bin/lab-workflow.cmd`；
- 工作台数据格式和默认 `~/lab` 保持兼容，已有 registry 无需迁移。

已有工作台升级：

```bash
cd ~/project/lab-workflow
./bin/lab-workflow --root ~/lab init --refresh
```

`--refresh` 会覆盖 `AGENTS.md`、`README.md`、`bin/README.md`、`templates/` 和
`.agent`/`.agents` 下的 skill；实验目录和 registry 不受影响。0.2.0 的
`lab-cli`/`labctl` → `lab-workflow`/`labworkflow` 改名仍然有效。移动工作台目录或
工具仓库后，运行一次 `init --refresh` 更新生成文档和 skill 里的绝对路径。

## 已知限制

- 只支持单机、单用户、本地文件系统；没有 Windows CI，平台相关路径由单元测试覆盖，
  实际 Windows 行为仍需在目标机器上冒烟验证。
- Windows 长路径（`.venv` + 实验目录可能超过 260 字符）需要开启系统长路径支持。
- 没有 unarchive；归档后要继续只能 promote 或手工处理（不推荐）。
- `promote` 只做过滤复制（排除 `.git`、`.venv`、`node_modules`、缓存、
  `.lab-meta.json` 和受管的 `AGENTS.md`），不会替你重构代码或 `git init`；
  目标目录必须不存在或为空。
- `adopt` 只接受当前工作台 `active/` 下的目录，不负责搬移外部目录的代码。
- 不安装全局命令；长期使用可把仓库放在固定位置并用绝对路径调用。
- `contrib/systemd` 仅适用于 Linux；Windows 暂无等价定时任务样例。
- 本仓库的 `docs/original.md` 是本地设计稿，已在 `.gitignore` 中排除。
