# Cortex · 通用项目知识库文档系统

> 让一个对目标项目一无所知的新 Agent，在几分钟内获得**结构化、可检索、可追溯**的知识库。
> 系统**不耦合任何具体项目**——项目结构、语言、甄别规则、治理策略全部由 `config.yaml` 驱动，代码零硬编码。

- pip 包名：`doc-system` ｜ 安装后命令：`cortex`（兼容别名 `doc-system`）
- **本仓库 = Cortex 唯一主仓**（开发即发布）；发布口径见 [`docs/RELEASING.md`](docs/RELEASING.md)，仓库关系见 [`ARCHIVED.md`](ARCHIVED.md)
- 实现范围：设计稿《面向 Agent 接手的项目知识库：文档系统设计方案》Phase 1~6 全部落地 → 完整设计稿见 [`docs/design.md`](docs/design.md)

---

## §0 一页速查（回来看这一处就够）

| 我想… | 用这个 |
|---|---|
| 5 分钟跑通全链路、无 key | 下面「5 分钟 Quickstart」+ `config.quickstart.yaml`（offline） |
| 生产部署、真语义检索 | `config.example.yaml`（siliconflow / BAAI/bge-m3）+ `SILICONFLOW_API_KEY` |
| 大项目加速向量检索 | `--ann-provider hnsw` 或配置 `ann_provider: hnsw`（需 `pip install ".[ann]"`） |
| 确认改动没坏 | `pytest -q`（41 passed，无 key 也全绿）；push 后 GitHub Actions 自动跑 |
| 查全部命令 | `cortex --help` ｜ 下方「完整 CLI 参考」 |
| 发布前安全核查 | `docs/RELEASING.md`（密钥 / 发布源 / 依赖 / 验证 / 发版） |

**三个关键概念（后面会展开）：**
- **两档 embedder 梯子** = `offline`（确定性哈希兜底）/ 通用 OpenAI 兼容接口客户端（`siliconflow` 云端 或 本地模型服务）。决定**语料向量怎么算**。
- **ANN 后端** = `flat`（暴力余弦）/ `hnsw`（hnswlib 近似最近邻）。决定**查询时怎么找最近邻**，与 embedder 正交、可自由组合。
- **三通道并列** = 检索同时返回 `annotation_hits`（`@domain` 显式键）/ `annotation_text_hits`（docstring 词项）/ `vector_hits`（向量近邻），**不预融合**，交 Agent 裁决。

---

## 5 分钟 Quickstart（零配置 · 无 API key · 不联网）

全程使用 `offline` 确定性哈希向量，不需要任何密钥、不访问网络、结果可复现。仓库自带合成示例项目 `fixtures/sample_project/`，可直接跑。

```bash
# 1) 安装（Python >= 3.10）
pip install -e .                 # 最小：只装 pyyaml，够跑 offline 全链路
# pip install -e ".[ann]"        # 可选：加 hnswlib + numpy，启用 hnsw 后端
# pip install -e ".[ann,remote]" # 全量（remote=requests，真语义路径用）

# 2) 在任意工作目录准备产物目录（配置用绝对路径引用仓库自带的 quickstart 配置）
CFG=/path/to/cortex/config.quickstart.yaml
mkdir -p demo && cd demo

# 3) 建档：扫描 → 甄别 → 提取 → 归因 → catalog 快照
cortex ingest --config "$CFG" --out catalog.json
#    [ingest] 项目=quickstart-sample 扫描=6 有效=5 符号=18

# 4) git 式 catalog 存储：初始化 → 首次重算提交 → CI 发布（门禁过才原子切 active）
cortex store-init --config "$CFG" --store .docstore
cortex recompute  --config "$CFG" --store .docstore --reason "init catalog v1"
cortex ci-publish --config "$CFG" --store .docstore --reason "release v1"

# 5) 建索引（三件套 + 向量近邻）
cortex index --config "$CFG" --store .docstore --out .doc_system/index/index.json
#    [index] 条目=18 provider=OfflineEmbedder ann=flat

# 6) 检索：三通道并列返回
cortex retrieve --config "$CFG" --store .docstore --query "user login" --k 3
```

想验证 `hnsw` 后端（需装 `.[ann]`）：

```bash
cortex index --config "$CFG" --store .docstore --ann-provider hnsw \
             --out .doc_system/index/hnsw.json
# 额外落盘 hnsw.hnsw + hnsw.meta.json，下次可零 rebuild 直接加载
```

> **`config.quickstart.yaml` 与 `config.example.yaml` 的关系**：前者是「零配置冒烟版」，把
> `embedding.provider` 和 `attribution.provider` 都设为 `offline`；后者展示**生产默认**
> （`siliconflow` / BAAI/bge-m3 真语义，需 `SILICONFLOW_API_KEY`）。跑通 Quickstart 后，
> 复制 `config.example.yaml` 为 `config.yaml`、按需改 `roots`，并把 key 放进环境变量或项目根
> `.env`（已被 `.gitignore` 排除，绝不入库）即可升级为真语义。

---

## 两档 embedder 梯子：语料向量怎么算

`index.get_embedder(cfg)` 按 `config.yaml` 的 `embedding.provider` 选择；CLI 也可用 `--provider` 覆盖。

| 档 | provider | 实现 | 维度 | 依赖 | 语义 | 适用 |
|---|---|---|---|---|---|---|
| Tier0 | `offline`（默认） | `OfflineEmbedder` 确定性哈希 | 256 | 零（纯标准库） | 无（词项哈希） | 离线 / CI / 无 key / 可复现兜底 |
| Tier1 | `siliconflow`（可指本地） | `SiliconFlowEmbedder` · BAAI/bge-m3 | 1024 | HTTP 客户端 | 真语义、跨语言同义 | 生产建档：corpus 全量真语义 |

**本地与云端同构**：本地模型服务（Ollama / llama.cpp server / LM Studio / vLLM）与云端（硅基流动）都是同一个 OpenAI 兼容接口 —— `POST /v1/embeddings`，请求体 `{model, input, encoding_format}`，响应 `{data:[{index, embedding}]}`。**所以不需要单独实现本地 embedder**，改 `config.api.yaml` 的 `api_base` 即可切换：

```yaml
# 本地端点
embedding:
  provider: siliconflow
  api_base: http://localhost:11434/v1/embeddings   # Ollama；llama.cpp 用 http://localhost:8080/v1/embeddings
  model: bge-m3                                     # 建议本地也用 bge-m3，保持 1024 维
  api_key_env: SILICONFLOW_API_KEY                  # 本地不校验 token，放占位值即可
```

完整可跑配置见 [`config.api.yaml`](config.api.yaml)（注释里给出切本地/切云端各改哪几行）。**注意本地也用 bge-m3 保持 1024 维** —— 否则 `SiliconFlowEmbedder.dim=1024` 与实际向量维度不符，hnsw build 会失败。

**为什么这样分层**：`offline` 保证任何环境（CI、断网、刚 clone）都能零成本跑通全链路，是"永远能退化到的地板"；真语义档提供语义质量（实测跨语言同义对余弦约 0.81~0.87，无关对约 0.44；离线哈希恒为 0、无法区分）。当前硅基流动免费不限速，适合把整个 corpus 一次性嵌入成索引。

**批量 + 并发**：`embed_many(texts)` 按 `batch_size` 分组请求，多 batch 用 `ThreadPoolExecutor` 并发（`max_concurrency`，默认 8），结果按 `data[].index` 与 batch 下标双重归位，保证输出顺序与输入一致；失败抛清晰 `RuntimeError`。长文本截断到 `MAX_EMBED_CHARS=6000`，避免超出 bge-m3 上下文上限被 API 拒（catalog 仍存全文，只截断嵌入输入）。

**API key 不入库**：从环境变量 `SILICONFLOW_API_KEY` 或项目根 `.env` 读取（`Config._load_local_env`）；`config.yaml` 只放 `model / api_base / api_key_env / batch_size / max_concurrency`，不放明文 key。

**查询时重排（选项 C）**：若 corpus 用 `offline` 建索引、但查询想要真语义，可设 `retrieval.rerank: siliconflow`：先召回 top-N 候选，再用 bge-m3 对候选重排挑前 k，按次计费、零额外离线成本。若 corpus 本身就是 bge-m3 真语义索引，查询也用 bge-m3 直接比对，排序即真语义 → 重排冗余，应置 `null`。

---

## ANN 后端：查询时怎么找最近邻

与 embedder **正交**：embedder 决定向量是什么，ANN 后端决定如何从 N 条向量里找 top-k。由 `ann_provider` 配置 / `--ann-provider` 覆盖。

| 后端 | 实现 | 复杂度 | 依赖 | 特点 |
|---|---|---|---|---|
| `flat`（默认） | `FlatBackend` 暴力余弦全扫 | O(n) | 零 | 精确、确定可复现、零依赖兜底 |
| `hnsw` | `HnswBackend` · hnswlib cosine 空间 | 近似对数级 | `hnswlib>=0.8.0` + `numpy` | 大项目检索加速；缺失时给清晰安装提示，不污染 flat 路径 |

- 抽象为 `VectorBackend` 基类：`build(vectors)` / `query(vec, k) -> [(score, idx)]` / `save_index` / `load_index`。`retrieve()` 的**对外签名与返回结构完全不变**，只换底层最近邻计算。
- `hnsw` 支持**跨进程持久化**：`save()` 额外落盘 `<stem>.hnsw` + `<stem>.meta.json`（记录 `dim` / `n`），下次 `KnowledgeIndex.load(ann_provider="hnsw")` 直接 `load_index`、零 rebuild、零 API 消耗。
- 已有 `index.json` 想切 hnsw 不必重算向量：`python scripts/rebuild_hnsw.py` 复用现有 vectors 重建即可。
- 小数据集下 `hnsw` 与 `flat` top-k 集合完全重合（`ef=n` 召回充分），由 `tests/test_phases.py::ANNBackendTest` 锁住。

---

## 设计原则（实现遵循）

- **通用 / 解耦**：不与任何项目绑定；`fixtures/sample_project/` 只是合成示例。
- **确定性优先**：内置甄别 + 内置提取为主路径；AI 提取仅作兜底升级（§13.1）。
- **归因双轨（§12）**：`@domain` 显式键 ‖ 向量隐式轨（软归属）平等裁决。**domain 永不因缺手写标注而全空**——真实项目无 `@domain` 时，用目录树 scope（§13.4 Q5）作稳定归属 + 向量隐式轨产出软分布，结果随 catalog 快照存留（§12.7，避免"代码没动、文档却变"）。
- **三件套 + 三通道（§13.4）**：`向量 + 注释(@domain) + 注释文本(docstring)` 成对并列；检索返回 `annotation_hits` / `annotation_text_hits` / `vector_hits` 三通道并列、互不为主、不预融合，交 Agent 裁决。向量方向与文本注释方向地位平等（§12.1）；冲突原样并列并打 `conflict_flag`，不拦截。
- **git 式 catalog**：每次重算 = 一次提交（强制 `change_reason`、可回滚、`active`=已发布版），并兜住「代码改了但 catalog 没跟上」的漂移窗口（§13.1 / §13.8 Q-3）。
- **读 = Skill（零写）/ 写 = MCP（git 式提交）**：双通道物理分离（§13.7）。
- **零人治理**：写侧质量门禁（声明级溯源）、触发分权（git hook/CI/Agent）、外部探针自动判 + 异构 jury + 行为金丝雀、宪法 AI 自举（§13.6 / §13.8 / §14）。

## 模块映射（Phase → 代码）

| Phase | 主线 | 模块 |
|---|---|---|
| 1 | 输入与源真相 | `config`, `triage`, `extract/*`, `pipeline` |
| 2 | 归因与分类 | `attribution`（§12 双轨裁决 + 软分布 + §12.7 快照：domain 永不为空） |
| 2 | catalog 版本化 | `store`（增量重算 + git 式提交 + 漂移兜底 + 回滚 + TTL 巡检） |
| 3 | 渲染 | `render`（MkDocs 源 + llms.txt 按域分片 + 可选产物 config 开关） |
| 4 | 索引 | `index`（三件套 + 向量近邻 + 三通道并列检索 + 可插拔 ANN 后端 + 近邻候选池近似） |
| 5 | 接入协议 | `access/skill`, `access/write_channel`(MCP), `access/agents_md`, `access/onboarding` |
| 6 | 治理 | `governance/gate`, `governance/trigger`, `governance/observability`, `governance/bootstrap` |

## 完整 CLI 参考（生产配置 `config.yaml`）

```bash
# 1) 扫描 → 甄别 → 提取 → catalog 快照
cortex ingest --config config.yaml --out catalog.snapshot.json

# 2) 初始化 git 式 catalog 存储 + 首次重算提交
cortex store-init --config config.yaml
cortex recompute  --config config.yaml --reason "init catalog v1"

# 3) CI 发布（门禁过则原子切 active；唯一发布权）
cortex ci-publish --config config.yaml --reason "release v1"

# 4) 渲染（MkDocs 源 + llms.txt 分片）+ 索引
cortex render --config config.yaml --out .doc_system
cortex index  --config config.yaml --out .doc_system/index/index.json
cortex index  --config config.yaml --ann-provider hnsw --out .doc_system/index/index.json
cortex retrieve --config config.yaml --query "用户账户与登录" --k 3
cortex retrieve --config config.yaml --query "user login" --rerank siliconflow --k 3

# 5) 读侧产物（零写）
cortex gen-skill     --out .doc_system
cortex gen-agents-md --out .doc_system
cortex onboard       --out .doc_system

# 6) 治理
cortex gate
cortex probe  --config config.yaml
cortex jury   --question "is the KB helpful?"
cortex bootstrap --agents-md path/to/AGENTS.md --out .doc_system/constitution.json

# 漂移兜底
cortex drift     --config config.yaml
cortex reconcile --config config.yaml          # 构建候选版本（不发布）
cortex rollback  --version v1                 # 回滚 active
```

## 测试与 CI

```bash
pytest -q                 # 41 passed，无需 API key
python -m unittest discover -s tests   # 等价（unittest 风格亦可跑）
```

- **无 key 全绿机制**：`tests/conftest.py` 在未设置 `SILICONFLOW_API_KEY` 时，于导入期把 `index` / `attribution` 两处 `get_embedder` 强制为 `OfflineEmbedder`；设置了 key 则保留真实 provider，顺带验证真语义路径。放在导入期（而非函数级 fixture）是为了覆盖 `unittest.TestCase.setUpClass`。
- `tests/test_integration_e2e.py`：offline 全链路（建档 → flat/hnsw 双索引 → 检索），断言向量/注释文本双通道命中与 flat-hnsw top-5 重合 ≥2。
- **GitHub Actions**（`.github/workflows/ci.yml`）：push 到 `main` + PR 触发，Python 3.10 / 3.11 / 3.12 / 3.13 矩阵，`pip install .[ann]` 后 `pytest tests -q`。如需验证真语义路径，在仓库配 `SILICONFLOW_API_KEY` secret 并取消 ci.yml 里的 `env` 注释。
- `pyproject.toml` 的 `testpaths = ["tests"]` 防止把 `fixtures/sample_project/` 里的样本 `.py` 误当测试收集。

## roots 扫描范围（配置驱动）

- root 两种写法：`- path: <dir>` 递归扫描（默认）；`- path: <dir>` + `files_only: true` **只扫该目录顶层文件、不递归**。
  后者用于纳入**项目根级核心代码**（`cli.py`、`hermes_state.py`、`run_agent.py` 这类大而关键的入口文件），
  又不会把同目录下的巨型子目录（如 114M 的 context_system/）一并拖入扫描。
- 大文件性能：Python 提取器对 `ast.get_source_segment` 做了整篇只切一次的 O(行数) 优化 +
  一次建父映射（判 method/function 从 O(n²) 降到 O(n)）。实测 846K 的 `cli.py` 提取 **2.58s → 0.17s**（约 15×），符号数不变。
- 多 root 时 `rel` 自动加 root 名前缀，避免不同 root 下同名文件（`__init__.py`）碰撞导致增量重算不幂等 / 漂移误报。
- 近邻候选池 `index.neighbor_pool`：符号量很大时把 O(n²) 精确近邻降为 O(n·pool) 确定性采样近似；`0` = 精确全量。

## 已知边界（诚实声明）

- 真实语义向量已接入且实测可用（硅基流动 BAAI/bge-m3）；代码默认仍 `offline`（零依赖、可复现），启用语义向量需联网 + 有效 key。向量只影响向量通道近邻质量，三通道并列返回结构不变。
- **Tier1 `local` 本地模型尚未实现**——接口已留（`Embedder` 基类），是 P2 路线图项。
- 归因隐式轨默认 `offline` 确定性哈希向量（软分布语义较弱，但零成本、可复现）；`attribution.provider=siliconflow` 可升级为真语义软归属。`attribution.domain_from` 默认 `structural`（目录树 scope，稳定可复现），可切 `vector`（向量 top 域，语义强但随模型漂移）。二者都是并列信号，不预融合。
- 域骨架取目录树 scope（§13.4 Q5），是**结构域**而非纯语义能力域；跨域语义重叠靠 `domain_distribution` 软分布揭示，交 Agent 判断。关系抽取层、corpus 快照缓存、ANN 量化均在 P2 路线图。
- MCP 写通道提供确定性的 `WriteChannel` 核心 + MCP 工具 schema；真实 `mcp` 包环境下的 stdio 服务由 `build_mcp_server()` 启用，离线时 CLI/CI 直接调用核心。
- TypeScript 提取器为占位（`typescript_typedoc`），新语言按 `extract/` 适配器契约扩展即可。
- MkDocs / PageFind / TypeDoc 站等附加产物走 config 开关（默认关），离线不构建、由 CI/人工启用。

完整设计稿：[`docs/design.md`](docs/design.md)（999 行，含 §0 一页速查 · 八条主线 · §12 归因双轨 · §13 生命周期 · §14 宪法自举）；一页速查另见 [`docs/quickref.md`](docs/quickref.md)。
