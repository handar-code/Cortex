# 本仓库是 Cortex 的主仓（唯一开发与发布源）

> ✅ **本仓库 = Cortex 唯一主目录。** 之后所有源码修改、提交、CI、发版都在这里进行。
> 早期存在过一个 `<REPO_ROOT>` 副本（2026-11-15 下午克隆），已降级为历史来源，
> **不再维护、不再双写、不作为发布源**。

## 为什么以本仓库为准（实测对比结论）

2026-11-15 做过一次逐文件全量哈希比对（不只是看文件名清单），结论明确：

| | 本仓库（Desktop/cortex） | doc-system 副本 |
|---|---|---|
| 原始设计资产 | **190,222 字节**（`docs/design.md` 999 行完整设计稿、`quickref.md`、`system-readme.md`、`test_report.md`、`deployment.yaml`） | **完全没有**（只剩 `docs/RELEASING.md`） |
| 代码领先项 | — | 3 个文件的 BUG 修复 + `config.api.yaml` |
| 首个 commit | `7e3b633` @13:22「源码 + **设计稿 + 设计过程 + 理念**」 | `8de3653` @14:42「baseline: 原始代码快照」 |

即：本仓库是**原始仓库**，doc-system 是之后克隆时丢掉了设计文档的代码副本。
那 3 个 BUG 修复与 `config.api.yaml` **已合并回本仓库**（本仓库 `src/` 现与 doc-system 字节一致，
43 passed），所以不需要再从任何副本同步。

## 仓库关系

| 仓库 | 角色 | 说明 |
|---|---|---|
| **本仓库（Desktop/cortex）** | **唯一主仓** | 开发即发布；设计文档 + 代码 + CI 全在这里 |
| `doc-system` | 历史来源 | 下午克隆的代码副本，已合并其全部领先项；不再维护 |
| `Desktop/cortex-private` | 本地私有副本 | 含**真实** `SILICONFLOW_API_KEY`；**永不 push**；`cortex-private` 的 `.env` 曾被 git 跟踪，属待清理项 |

## 曾经多仓并行的问题（已解决）

早期为了避免把含 key 的 `cortex-private` 直接发布，把本仓库当作「干净发布镜像」，
由另一个仓同步源码过来后再发。实践中**多仓并行维护导致源码持续漂移**（旧快照缺 ANN 后端、
CI 与集成测试要靠 worker 分别补两遍），维护成本高于收益——而且镜像还**丢掉了设计文档**。

现在收敛为单仓：`.env` / `.docstore/` / 运行时产物均由 `.gitignore` 排除，
git 历史经彻底重写（`--tree-filter` 改内容 + `--env-filter` 统一作者），
详见 [`docs/RELEASING.md`](docs/RELEASING.md) 第 1 条的复核命令。

## 去哪里

| 需求 | 位置 |
|---|---|
| 源码 / 提交 / PR / CI | **本仓库** |
| 5 分钟 Quickstart | 本仓库 `README.md` |
| 发布安全清单 | 本仓库 `docs/RELEASING.md` |
| 完整设计推导 | 本仓库 `docs/design.md`（999 行） |
| 一页速查（防忘锚点） | 本仓库 `docs/quickref.md` |
| 端到端测试报告 | 本仓库 `docs/test_report.md` |
| 操作总纲（9 类） | `~/.workbuddy/skills/cortex-onboard/OPERATIONS.md` |

主目录口径确立日期：2026-11-15
