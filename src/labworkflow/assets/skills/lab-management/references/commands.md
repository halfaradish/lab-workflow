# lab-workflow 命令参考

## 解析命令路径

```bash
LAB="{{lab_cmd}}"
[ -x "$LAB" ] || LAB=$(command -v lab-workflow)
```

`{{workspace_root}}/bin` 故意为空，不要在那里找命令，也不要假设 `lab-workflow`
在 PATH 中。Windows 下命令是 `{{lab_cmd}}`（`.cmd`），可直接用 PowerShell/cmd
调用；本文示例使用 POSIX shell。

## 指定工作台根目录

```bash
{{lab_cmd}} init /data/lab              # 把任意目录初始化为工作台
{{lab_cmd}} --root /data/lab list       # 显式指定根目录
cd /data/lab && {{lab_cmd}} list        # 从工作台内部自动发现
LAB_WORKFLOW_ROOT=/data/lab {{lab_cmd}} list   # 环境变量
```

优先级：`--root` > `init PATH` > `LAB_WORKFLOW_ROOT` > 当前目录向上发现 > `~/lab`。
`--root` 是全局参数，必须放在子命令前。

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 一般运行错误 |
| 2 | 参数/用法错误 |
| 3 | 找不到实验 |
| 4 | 当前状态不允许该操作 |

## 命令总表

| 命令 | 作用 |
| --- | --- |
| `init [--refresh]` | 初始化/补齐工作台；`--refresh` 重写工具管理的文件 |
| `new <name>` | 创建实验（模板、环境、Git、registry） |
| `adopt <path>` | 登记 `active/` 下已有目录 |
| `list` | 列出实验（默认只显示活跃） |
| `show <ref>` | 显示详情；`--json` 输出结构化数据 |
| `open <ref>` | 打印实验路径；`--shell` 启动子 shell |
| `status <ref> <state>` | `created` / `running` / `success`（别名 `start` / `done`） |
| `meta <ref>` | 更新描述、摘要、结论、标签、分类、技术栈 |
| `archive <ref>` | 归档，移到 `archive/YYYY/MM/` |
| `promote <ref>` | 复制到正式项目目录，源实验标记 PROMOTED |
| `discard <ref>` | 删除目录，保留 registry 墓碑 |
| `clean` | 按保留策略清理（默认演练） |
| `stats` | 工作台统计 |
| `search [query...]` | 在元数据和 README 中检索 |
| `doctor` | 检查工作台、依赖和 try-rs 集成 |

`<ref>` 可以是完整 ID（如 `20261120-001`）、唯一名称或 slug。不要把 `001` 当 ID 简写。

## 初始化

```bash
"$LAB" init
"$LAB" init --refresh
"$LAB" doctor
```

`--refresh` 会覆盖 `AGENTS.md`、`README.md`、`bin/README.md` 和 `templates/` 中由工具
生成的内容。只在工具升级后、且用户接受覆盖时使用；不要拿它修实验目录。

## 创建实验

```bash
"$LAB" new <name> \
  [-t python|node|general] \
  [-d "一句话描述"] \
  [--tag a,b] [--category networking] \
  [--technology python] [--related-project other] \
  [--no-git] [--no-env] [--json]
```

- 名称会规范化为 kebab-case；同名 slug 已存在会拒绝。
- 目录：`active/YYYYMMDD-NNN-slug`。
- 默认初始化 Git；Python 创建 `.venv`，Node 执行实验内 `npm install`。
- `--no-env` 跳过环境；`--no-git` 跳过 Git。
- `--json` 便于脚本读取 ID 和路径。

## 登记已有目录

```bash
"$LAB" adopt <path> \
  [--name <name>] [-t <template>] [-d "描述"] \
  [--tag a,b] [--category x] \
  [--technology t] [--related-project p] \
  [--no-git] [--json]
```

- 只接受 `{{workspace_root}}/active` 下的目录（绝对路径，或相对当前目录/active 的路径）。
- 原地登记：不移动目录，不覆盖已有 README；已有 README 没有受管标记块时会警告。
- 模板默认按内容检测（`package.json` → node，`requirements.txt`/`pyproject.toml`/
  `setup.py`/`Pipfile` → python，否则 general）。
- 不创建环境，只检测已有 `.venv` / `node_modules`。
- 已有 Git 仓库会沿用；没有则初始化（`--no-git` 可跳过）。

## 查询

```bash
"$LAB" list [-a] [-s created] [--tag perf] [--template python] [--limit 20] [--json]
"$LAB" show <ref> [--json]
"$LAB" search websocket p95 [--tag perf] [--status success] [--template python] \
  [--category networking] [--technology python] [--limit 20] [--json]
"$LAB" stats [--json]
"$LAB" doctor [--json]
```

- `list` 默认只显示 `CREATED`/`RUNNING`/`SUCCESS`；`-a/--all` 包含归档、promoted
  和 discarded 墓碑。
- `search` 多个关键词是 AND；检索元数据和 README。
- `stats` 适合收尾时汇报总数、状态分布、标签等。

## 状态与元数据

```bash
"$LAB" status <ref> running
"$LAB" status <ref> success

"$LAB" meta <ref> \
  [--description "..."] [--summary "..."] [--conclusion "..."] \
  [--category networking] \
  [--tags a,b] [--technologies python,asyncio] \
  [--related-projects other-project]
```

- `status` 只接受 `created` / `running` / `success`；归档、promote、丢弃是独立命令。
- `meta --tags` / `--technologies` / `--related-projects` 是**替换**整个列表，不是追加。
- `meta` 会同步 README 的受管区块；`summary` 和 `conclusion` 应基于已记录的证据。
- `ARCHIVED` / `PROMOTED` / `DISCARDED` 为只读，`status`/`meta` 会返回 4。

## 归档

```bash
"$LAB" archive <ref> [--force] [--json]
```

- 允许 `CREATED` / `RUNNING` / `SUCCESS`；通常先写结论再归档。
- 有真实未提交更改时拒绝；`--force` 需用户明确同意。
- 移动到 `archive/YYYY/MM/`，状态变 `ARCHIVED`，此后只读。
- 没有 unarchive；要继续只能 promote 或新建实验。

## 晋升

```bash
"$LAB" promote <ref> [--to <dir>] [--force] [--json]
```

- 只允许 `SUCCESS` 或 `ARCHIVED`。
- 默认目标 `~/workspace/<slug>`（Windows: `%USERPROFILE%\workspace\<slug>`）；
  目标必须不存在或为空，且不能在 `{{workspace_root}}` 内。
- 过滤复制：排除 `.git`、`.venv`、`node_modules`、缓存、`.lab-meta.json`、
  `AGENTS.md` 链接；不会 `git init`。
- SUCCESS 源实验会移到 archive 并标记 `PROMOTED`；ARCHIVED 源实验留在 archive。
- 有未提交更改时拒绝；`--force` 需用户明确同意。

## 删除与清理

```bash
"$LAB" discard <ref> --yes [--force] [--json]
"$LAB" clean [--apply] [--dry-run] [--force] \
  [--created-days 3] [--success-days 7] [--json]
```

- `discard` 删除目录并保留 `DISCARDED` 墓碑；非交互必须 `--yes`；dirty 时 `--force`。
- 已 promote 的实验不能 discard。
- `clean` 只处理 `active/` 下 `CREATED` / `SUCCESS`；`RUNNING`、归档、promoted 永不自动清理。
- `clean` 默认演练；`--apply` 才删除；非 Git 或有未提交更改默认跳过，`--force` 才删。
- `clean` 对 registry 有记录但目录缺失、以及未登记目录只警告，不修改记录。

## Git 提交身份

实验仓库的初始提交由工具用 `-c user.name=Lab -c user.email=lab@localhost` 完成。如果
后续 `git commit` 报 `Author identity unknown`，在**实验仓库内**配置一次：

```bash
git -C "$EXP" config user.name "Lab"
git -C "$EXP" config user.email "lab@localhost"
```

不要改全局 Git 配置；如果用户有常用身份，先问用户是否使用。

## 常见错误与处理

| 错误信息 | 含义 | 处理 |
| --- | --- | --- |
| 工作台未初始化 | `{{workspace_root}}` 骨架或 registry 缺失 | `"$LAB" init` |
| 实验目录不存在 | registry 有记录但目录缺失 | `doctor` / `list -a`；不要手改 registry |
| 实验存在未提交更改 | Git 有真实改动 | 提交，或经用户同意后 `--force` |
| 只有 SUCCESS 或 ARCHIVED 的实验可以 promote | 状态不对 | 先 `status <ref> success` |
| 实验已结束（ARCHIVED/PROMOTED/DISCARDED） | 只读 | 不再 `status`/`meta`；promote 或新建实验 |
| promote 目标目录非空 | 目标已有文件 | 换空目录或不存在路径 |
| adopt 只接受 {{workspace_root}}/active 下的目录 | 路径越界 | 在 active 下创建/移动（移动前问用户） |
| 未知模板 | 模板不存在 | 用 `python` / `node` / `general` |

命令失败时先读 stderr；状态类错误不要通过手改 registry 绕过。
