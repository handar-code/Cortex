# Cortex

> 让一个对新项目一无所知的新 Agent，快速、可靠地接手。
> 把乱糟糟的源码、注释、Markdown，结构化成**可归因、可检索、可治理**的知识记忆层。

系统定位、设计主线、架构、命令参考见 `docs/design.md`（完整设计稿，Phase 1–6 全部落地）与 `docs/system-readme.md`（技术 README）。本文件讲**理念**与**设计过程**——也就是「为什么是它、怎么走到今天」。

## 一、名字的由来

**Cortex = 大脑皮层**。一个 Agent 接手新项目，缺的往往不是代码，而是「关于这个项目该往哪想」的知识皮层。Cortex 把原始代码 / 文档沉淀成结构化的、可按域检索、可溯源、有治理的记忆层，相当于给 Agent 外接了一块「项目知识脑皮层」。

> 名字由本仓库创建者所起，随时可改；仓库目录即 `cortex`。

## 二、核心理念（Philosophy）

1. **通用 / 解耦**：不与任何项目绑定。所有项目结构、语言、触发方式、治理规则都在 `config.yaml` 里，代码零硬编码。换个项目只改配置。
2. **确定性优先**：内置甄别 + 内置提取（AST / 正则）是主路径；AI 提取只作兜底升级。可复现、零幻觉、断网可跑。
3. **归因双轨（§12）**：`@domain` 显式键 ‖ 向量隐式轨（软归属）平等裁决。**domain 永不因缺手写标注而全空**——真实项目没写 `@domain` 时，用目录树 scope 作稳定归属 + 向量隐式轨产出软分布。
4. **三件套 + 三通道（§13.4）**：`向量 + 注释(@domain) + 注释文本(docstring)` 成对并列；检索返回 `annotation_hits / annotation_text_hits / vector_hits` **三通道并列、互不为主、不预融合**，交 Agent 自己裁决。向量方向与文本注释方向地位平等。
5. **git 式 catalog**：每次重算 = 一次提交（强制 `change_reason`、可回滚、`active`=已发布版），兜住「代码改了但 catalog 没跟上」的漂移窗口。
6. **读 = Skill（零写）/ 写 = MCP（git 式提交）**：双通道物理分离，读侧绝不污染源真相。
7. **零人治理**：写侧质量门禁、触发分权、外部探针自动判 + 异构 jury + 行为金丝雀、宪法 AI 自举。
8. **嵌入可插拔、分三档**：
   - **offline** — 本地确定性哈希（词袋向量），纯标准库、零依赖、零成本、可复现。最低兜底档。
   - **siliconflow** — BAAI/bge-m3 语义向量，经硅基流动 API（当前**免费、不限次、不限速**），1024 维真语义。**当前默认**。
   - **local** — 本地向量模型（sentence-transformers / Ollama / llama.cpp），真语义 + 数据不出本机 + 断网可用；在 10 核 GPU 的 M 系列本机上比云端更快。未来可选档。

## 三、设计过程（How we got here）

这段是真实演进记录，不是凭空设计。

**起点**：先有设计稿（`docs/design.md`），Phase 1–6 全部实现——输入 / 甄别 / 提取 / 归因 / 渲染 / 索引 / 接入 / 治理八条主线落地。但「实现完成」不等于「对」。

**第一轮追问（用户的三个问题）**
- **Q1**：Catalog 有指回原文件的路径吗？→ 确认有：`source_ref = relative_path:line_number`，检索命中精确映射到真实文件行。
- **Q2**：抽取是代码级匹配，会不会漏掉未声明的函数、不建模函数间的协同关系？→ 诚实承认：提取是 AST + 正则的**单符号视角**，只记父 class，**不建模跨函数调用 / 协同关系**。列为待办（关系抽取层）。
- **Q3**：MD 文件只读了前 200 字，但 MD 可能是关于代码的完整叙事，应该**全量搜索 + 加注释**。→ 重写 `plaintext.py`：module / section 的 `content` = 全文，新增 `comment` 符号（fenced 代码注释 + HTML 注释）。hermes 端到端 10502 → 10512 符号；中文语义查询现能命中设计稿 §12 / §13.7 章节。

**第二轮（向量通道的软肋）**：默认 embedder 是离线哈希（词袋向量），对中文 / 语义查询返回噪声——实测「把 session 状态持久化写入数据库」的 top1 是个空 docstring、score 1.0 的符号。给出四个选项（A 两阶段 / B 全量 bge-m3 索引 / C 查询时 bge-m3 重排 / D 维持现状），落地了 **C**：corpus 仍用离线哈希召回 top-20，查询时只对候选用 bge-m3 重排。验证：32/32 单测通过，噪声被压制。

**第三轮（provider 演进的关键转折）**：用户告知 **硅基流动 BAAI/bge-m3 免费、不限次、不限速**。这直接推翻了 C 优于 B 的唯一理由（成本）。于是：
- 重新评估：B 的缺点（建索引烧 API、每次重建重烧）因「免费不限速」而消失，只剩结构性依赖（要 key / 网络）。
- 决策：默认档切 **全量 bge-m3 索引**（`embedding.provider=siliconflow`）+ 隐式轨同步语义化（`attribution.provider=siliconflow`），`rerank` 关闭（corpus 已是真语义，重排冗余）；offline 降级为断网 / 零配置兜底。
- 顺带红利：§12 隐式轨软归因也因真语义变准（「这个函数语义上属于 database 域」而不只是看有没有标注）。
- 验证：单元 27/27 通过（代码默认仍 `offline` 保零配置即跑）；实时联调 `get_embedder(siliconflow)` 真实返回 1024 维向量。

**第四轮（本地模型的彩蛋）**：用户补充——M5 Air 10 核 GPU、本地跑 9B 都不烫、本地向量比云端快。于是把「本地向量模型」定为第三档（Tier1）：真语义 + 数据不出本机 + 断网可用，且在该机器上更快。架构已支持（加个 `LocalEmbedder` provider 即可），本轮先只落地云端档，本地档留作后续。

## 四、架构速览

| Phase | 主线 | 模块 |
|---|---|---|
| 1 | 输入与源真相 | `config`, `triage`, `extract/*`, `pipeline` |
| 2 | 归因与分类 | `attribution`（§12 双轨裁决 + 软分布） |
| 2 | catalog 版本化 | `store`（增量重算 + git 式提交 + 漂移兜底 + 回滚） |
| 3 | 渲染 | `render`（MkDocs 源 + llms.txt 分片） |
| 4 | 索引 | `index`（三件套 + 向量近邻 + 三通道并列检索） |
| 5 | 接入协议 | `access/skill`, `access/write_channel`(MCP), `access/agents_md`, `access/onboarding` |
| 6 | 治理 | `governance/*`（gate / trigger / observability / bootstrap） |

## 五、快速开始

```bash
python -m venv .venv && .venv/bin/pip install pyyaml
export PYTHONPATH=src

# 1) 扫描 → 甄别 → 提取 → catalog 快照
python -m doc_system.cli ingest --config config.example.yaml --out catalog.snapshot.json

# 2) 初始化 git 式 catalog 存储 + 首次重算提交
python -m doc_system.cli store-init --config config.example.yaml --store .docstore

# 3) 建索引（默认 offline；要真语义把 provider 改成 siliconflow，见 docs/deployment.yaml）
python -m doc_system.cli index --config config.example.yaml --store .docstore --out index.json

# 4) 检索（三通道并列返回）
python -m doc_system.cli retrieve --config config.example.yaml --store .docstore --query "把 session 状态持久化写入数据库"
```

## 六、文档索引

- `docs/design.md` — 完整设计稿（八主线 + §12 归因双轨 + §13.4 三通道）
- `docs/system-readme.md` — 技术 README（模块映射、命令参考）
- `docs/quickref.md` — 一页速查（进度 / 术语 / 主路线 / 待拍板）
- `docs/test_report.md` — 端到端测试报告（含向量通道四选项对比、MD 全量抽取复测）
- `docs/deployment.yaml` — 真实部署配置（硅基流动 bge-m3 默认 + rerank 关闭）
- `config.example.yaml` — 配置样例（三档 provider 文档化）

## 七、待推进（设计上已留口、尚未实现）

1. **关系抽取层**：跨函数调用图 / 多函数共作用于一模块（回应 Q2）—— §13.4 之外的新能力，非小修补。
2. **本地模型档（Tier1）**：`LocalEmbedder` provider，sentence-transformers / Ollama / llama.cpp 任选；数据不出本机、断网可用、在 M 系列本机更快。
3. **corpus 全量 bge-m3 快照缓存**：建索引走网络时落快照，减少重建时的重复 API 调用。
