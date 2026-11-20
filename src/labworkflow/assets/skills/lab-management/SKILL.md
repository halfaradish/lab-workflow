---
name: lab-management
description: 管理 {{workspace_root}} 实验工作台的全生命周期：初始化实验、记录目标与成功标准、控制实验过程、总结结果、归档、晋升为正式项目。当用户想快速验证/试验/原型/benchmark 一个想法，或要开始、继续、总结、归档、清理 {{workspace_root}} 中的实验，或要把实验 promote 到 ~/workspace 时使用；即使用户没有说 "lab" 或 "实验"，只要是在做一次性技术验证也应触发。特别强调用最小实现避免过度设计。
---

# lab-management：实验全生命周期

本 skill 把「一个想法」变成可检索的实验记录：

```text
初始化 → 目标 → 过程 → 结论 → 归档 / 晋升
CREATED  RUNNING       SUCCESS  ARCHIVED / PROMOTED
```

工作台数据在 `{{workspace_root}}`，权威记录是
`{{workspace_root}}/registry/experiments.jsonl`；工具命令是 `{{lab_cmd}}`
（不在 PATH 中）。

> 本文示例使用 POSIX shell 的 `LAB="..."` 写法。Windows 上 `{{lab_cmd}}` 会渲染为
> `bin\lab-workflow.cmd`，可直接用 PowerShell/cmd 调用完整路径；路径含空格时加引号。

---

## 第一原则：先证伪，再工程化

实验代码是**消耗品**，产出是洞察。为了快速验证一个想法而引入复杂架构，通常会把
一次 30 分钟的实验拖成一天，并且让结论更难解释。

默认最小实现：

| 要验证的东西 | 默认写法 | 不要做 |
| --- | --- | --- |
| 某个 API / 库能否用 | 单文件脚本 + 标准库 | Web 框架、分层、依赖注入 |
| 两种方案谁更快/更稳 | 一个脚本里两个函数或两种参数 | 微服务、消息队列、K8s |
| 一次性数据处理 | 内存或本地文件 | 数据库、ORM、迁移、后台服务 |
| CLI / 业务逻辑 | `main.py` 或 `main.go` | 插件系统、事件总线、多包拆分 |
| 前端交互想法 | 单页 HTML 或模板自带入口 | 完整构建链、状态管理、组件库 |
| 并发/性能假设 | 最小复现 + 计时 | 压测平台、监控栈、部署流水线 |

每当你准备加一层结构，先回答：

1. 它帮助验证哪一条**成功标准**？
2. 去掉它，实验还能不能得出结论？
3. 如果假设被证伪，这段代码会被丢掉吗？会——那就越简单越好。

以下东西默认禁止，除非**假设本身**就是验证它们：

- 微服务、容器编排、服务发现、消息队列；
- 数据库/ORM/迁移，只为了存一次性的实验数据；
- 认证、多租户、权限、后台管理；
- 插件体系、依赖注入、事件总线、抽象工厂；
- 为了「以后复用」拆包、分层、写通用框架；
- CI/CD、覆盖率门禁、完整测试金字塔。

如果你确实需要跨过这条线，先写一个**复杂度升级门**并让用户确认：

```text
简单方案：...
失败/不足：...
需要引入：...
它验证的哪条假设：...
时间盒与放弃条件：...
```

时间盒建议 60–120 分钟。到点无论结果如何都停下来记录；不要为了「做完」扩大范围。
一个实验只回答一个问题；范围膨胀时开新实验，而不是重构旧实验。

**先判断意图**：用户只是问概念、要方案建议或让你解释一段代码时，直接回答，不要建
实验；只有用户要实际跑、比较、验证，或明确说「开个实验」时才初始化目录。

---

## 工具与边界

先解析命令路径，再检查工作台：

```bash
LAB="{{lab_cmd}}"
[ -x "$LAB" ] || LAB=$(command -v lab-workflow)
"$LAB" doctor
```

如果命令不存在或 `doctor` 显示工作台未初始化，先处理环境，不要伪造实验目录。

硬性边界：

- 只在**当前实验目录**内写文件；不要改其他实验、registry 或工作台共享文件。
- 元数据只通过 CLI 改；不要手编 `experiments.jsonl`。
- 依赖只装进实验内 `.venv/` 或 `node_modules/`；禁止全局安装。
- 不把 `.env`、token、私有配置写进提交。
- 不移动、不删除实验目录；归档/删除必须走 CLI。
- 归档后的实验只读；不要绕过状态机。
- `discard`、`clean --apply`、任何 `--force` 都是破坏性操作，先向用户说明影响并取得同意。

命令细节见 `references/commands.md`。写 README 时见
`references/experiment-record.md`。准备加依赖或结构时，先读
`references/minimal-architecture.md`。

---

## 流程总览

| 阶段 | 关键动作 | 状态 | 产物 |
| --- | --- | --- | --- |
| 0. 准备 | `init` / `doctor` / `search` | — | 可用工作台 |
| 1. 初始化 | `new` 或 `adopt` | CREATED | 目录、README、环境、Git、registry |
| 2. 目标 | 写目标契约，`status running` | RUNNING | 假设、成功标准、非目标、时间盒 |
| 3. 过程 | 运行、记录、控制范围 | RUNNING | 可复现命令、观察记录 |
| 4. 总结 | 写结果，`meta` + `status success` | SUCCESS | 结论、证据、局限 |
| 5. 归档 | `archive` | ARCHIVED | `archive/YYYY/MM/` |
| 6. 晋升 | `promote` | PROMOTED | `~/workspace/<name>` |

---

## 0. 准备

- 工作台未初始化或骨架缺失：`"$LAB" init`。
- 只有在工具升级后、且用户接受覆盖时，才用 `"$LAB" init --refresh`；它会重写
  `AGENTS.md`、`README.md`、`bin/README.md` 和 `templates/`。
- 新建前先查重：`"$LAB" list`、`"$LAB" search <关键词>`。已有类似实验时，优先继续
  旧实验，而不是再开一个。
- 用 `"$LAB" doctor` 确认 registry、Python/Git/npm 和未登记目录状态。`doctor`
  报出的 orphan（`active/` 下未登记目录）可以用 `adopt` 纳入，不要手动改 registry。

---

## 1. 实验初始化

### 新建

```bash
"$LAB" new websocket-latency \
  --template python \
  --description "验证方案 B 的 p95 延迟是否低于方案 A" \
  --tag network,perf \
  --category networking
```

- 模板：`python`（实验内 `.venv`）、`node`（实验内 `node_modules`）、`general`
  （笔记骨架，适合 Rust/Go/shell/文档等）。
- 默认会初始化 Git；`--no-git` 跳过。Python/Node 默认会创建隔离环境；`--no-env`
  跳过，仅当环境不可用或实验不需要运行时使用，并在 README 里记录原因。
- 目录形如 `active/YYYYMMDD-NNN-slug`。记下输出里的实验 ID 和路径。
- 不要手动 `mkdir`、复制模板或额外搭脚手架。

### 登记已有目录

如果目录是 try-rs 或手工创建的：

```bash
"$LAB" adopt "{{workspace_root}}/active/2026-11-19-probe" --template python --tag try-rs
```

`adopt` 只接受 `{{workspace_root}}/active` 下的目录，原地登记，不移动、不覆盖已有 README；
它不会创建环境，只检测已有环境。不要在 `active/` 外建实验再指望 `adopt` 搬进来。

### 初始化完成的判定

- `"$LAB" show <ref>` 能看到 ID、路径、状态、模板、Git 和环境；
- Python 实验的 `{{venv_python}}` 可用，Node 实验的依赖在实验目录内；
- README 存在，且 `## 假设`、`## 方法` 等章节可编辑。

---

## 2. 实验目标记录

**写代码前**先把目标写成可证伪的契约，放在实验 README 的 `## 假设` 和
`## 方法`（这两个区块不受工具管理，不会被 `meta` 覆盖）。

```markdown
## 假设

在相同数据集和相同机器上，方案 B 的 p95 延迟比方案 A 低至少 20%。

### 成功标准

- 通过：两种方案各跑 5 轮，方案 B 的 p95 中位数 ≤ 80% 方案 A；
- 失败：差距 < 10%，或结果在 5 轮内不稳定；
- 数据量：10 万条请求，单机，本地回环。

### 非目标

- 不做生产部署、不做 UI、不做认证、不做持久化；
- 不比较开发效率，只比较延迟。

## 方法

### 最小实现

- 单文件 `bench.py`，标准库 + 一个 HTTP 客户端依赖；
- 两种方案各一个函数，共用同一份输入和计时逻辑。

### 时间盒

- 90 分钟；到点无论结果如何都记录。
```

然后：

```bash
"$LAB" status <ref> running
```

需要时补充可检索元数据：

```bash
"$LAB" meta <ref> \
  --description "验证方案 B 的 p95 延迟是否低于方案 A" \
  --tags network,perf \
  --category networking \
  --technologies python,asyncio
```

注意：`meta --tags` 是**替换**整个标签列表，不是追加；要保留旧标签就一起传。
`--summary` / `--conclusion` 留到有结果时再写。

如果用户的目标很模糊，先提出一个可证伪的假设和成功标准让用户确认；不要先写代码。

---

## 3. 实验过程管理

进入实验目录：

```bash
cd "$("$LAB" open <ref>)"
```

过程中遵守：

- 用模板给的环境：`{{venv_python}}`、`{{venv_pip}}`、实验内 `npm`；不装全局依赖。
- 每次有意义的运行后，在 README 的 `## 结果与观察` 追加一条记录：

  ```markdown
  | 运行 | 命令/输入 | 结果 | 解释 |
  | --- | --- | --- | --- |
  | 1 | `{{venv_python}} bench.py --n 100000` | p95 A=142ms, B=118ms | 差距约 17%，未达 20% |
  ```

- 保留原始数字和命令，不要只写「B 更快」。
- 小步提交 Git；不要提交密钥。工具更新 README 元数据块不算未提交更改，但你自己改
  README 或代码会被算作 dirty；**收尾前先提交**，否则 `archive` / `promote` 会拒绝。
- 如果实验内 `git commit` 报 `Author identity unknown`，只在实验仓库里设置一次
  `user.name` / `user.email`（沿用工具的 `Lab <lab@localhost>` 约定，或问用户），
  不要改全局 Git 配置。
- 范围守卫：如果冒出一个新问题，记到 `## 下一步`，另开实验；不要在同一个实验里
  加功能、加抽象、加部署。
- 如果 30 分钟没有任何可观察进展，停下来写「卡在哪里、还缺什么」，不要用增加架构
  来假装推进。
- 不要用 try-rs 的删除键处理已登记实验；不要手动移动目录；不要改 registry。

`status` 只在真正开始时置 `running`；不要为了「看起来有进展」反复改状态。

---

## 4. 实验结果总结

先写证据，再写结论。README 的 `## 结果与观察` 放详细数据；结论通过 `meta` 同步到
受管区块，避免下次 `meta` 覆盖你的手写内容。

建议的结论结构：

```text
结论：方案 B 的 p95 比方案 A 低 17%，未达到 20% 的成功阈值。
证据：5 轮中位数 A=141ms / B=117ms；完整数据见「结果与观察」。
置信度与局限：单机、回环、固定数据；未测长连接和错误率。
对假设的判断：部分支持，但不足以支撑替换。
下一步：若继续，先测长连接；否则归档。
```

然后：

```bash
"$LAB" meta <ref> \
  --summary "方案 B p95 低 17%，未达 20% 阈值" \
  --conclusion "部分支持假设，但不足以替换方案 A" \
  --tags network,perf,negative-result

"$LAB" status <ref> success
"$LAB" show <ref>
```

关键语义：

- `SUCCESS` 表示「**有结论、值得保留**」，不表示「假设成立」。失败或不确定的结论也
  应该记录为 success，然后归档或丢弃。
- 不要写没有证据的结论；不要声称跑过但没跑的命令。
- `meta` 后再 `show` 一次，确认 registry 和 README 都已同步。

---

## 5. 实验归档

适用：结论已记录、近期不再继续、希望永久保留。

```bash
"$LAB" archive <ref>
```

- 会把目录移到 `archive/YYYY/MM/`，状态变为 `ARCHIVED`，之后**只读**：
  `status` / `meta` 都会被拒绝。
- 有真实未提交更改时会拒绝；先提交或确认后加 `--force`。`--force` 必须先获得用户同意。
- 没有 unarchive。以后要继续，只能 promote 或另开新实验。
- 归档不是删除；要删除用 `discard`（会保留 registry 墓碑）。
- 归档后 `"$LAB" show <ref>` 确认路径和状态。

---

## 6. 正式项目晋升

晋升意味着「这段代码值得长期维护」，不是「实验做完了」。

前提：

- 状态是 `SUCCESS` 或 `ARCHIVED`；
- 用户**明确要求** promote；
- README 有可复现的运行方式和结论；
- 目标目录不存在或为空，且不在 `{{workspace_root}}` 内；
- 代码不是一次性脚本堆；如果还需要大量重构，先和用户确认。

```bash
"$LAB" promote <ref> --to ~/workspace/<project>
# 或使用默认目标 ~/workspace/<slug>（Windows: %USERPROFILE%\workspace\<slug>）
```

它会：

- 过滤复制实验文件，排除 `.git`、`.venv`、`node_modules`、缓存、`.lab-meta.json`
  和 `AGENTS.md` 链接；
- **不**自动 `git init`，也**不**替你重构代码；
- 源实验：SUCCESS 会移到 archive 并标记 `PROMOTED`；ARCHIVED 保持归档并标记
  `PROMOTED`。

晋升后：

1. 检查目标目录内容，确认没有缺文件或多文件；
2. promote 输出会提醒 README 里可能仍带着实验 ID/路径等元数据；按正式项目的需要
   清理（保留结论和运行说明）；
3. 在目标目录实际跑一次构建/测试/最小命令；
4. 需要 Git 仓库时，先问用户，再手动 `git init`；
5. 不要声称「已可生产使用」，只报告实际验证过的内容。

有未提交更改时默认拒绝；`--force` 必须先获得用户同意。

---

## 7. 破坏性操作与失败处理

| 操作 | 默认 | 需要 |
| --- | --- | --- |
| `archive` | 有 dirty 拒绝 | `--force` 需用户同意 |
| `promote` | 有 dirty 拒绝 | `--force` 需用户同意 |
| `discard` | 删除目录、保留墓碑 | `--yes`；dirty 时 `--force` 需同意 |
| `clean` | 只演练 | `--apply` 需用户同意；`--force` 更需 |

- 任何 `--force`、`discard`、`clean --apply` 之前，先说明会删除/移动什么。
- 命令返回 4（状态错误）时，读错误信息并按状态机处理；不要直接改 registry 绕过。
- 命令返回 2（用法错误）时，检查参数；不要猜着重复执行破坏性命令。
- 失败时如实报告原始错误和已产生的影响，不要假装成功。

---

## 8. 完成检查清单

一个实验收尾前逐项确认：

- [ ] 目标契约已写：假设、成功标准、非目标、最小实现、时间盒；
- [ ] 没有引入与假设无关的架构、依赖或部署；
- [ ] README 有可复现的运行命令；
- [ ] `## 结果与观察` 有原始数据和观察；
- [ ] `## 结论` / `meta --conclusion` 已写，且没有超出证据；
- [ ] `status` 与真实阶段一致（通常是 `success`）；
- [ ] 归档/晋升/丢弃已按用户决定执行；
- [ ] 最终汇报实际运行的命令、结果和未验证项。

---

## 参考文件

- `references/commands.md`：命令、参数、退出码、常见错误。
- `references/experiment-record.md`：目标契约、运行日志、结论模板和示例。
- `references/minimal-architecture.md`：复杂度预算、红牌清单和升级门。
