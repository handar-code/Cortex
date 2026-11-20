# Cortex

*通用项目知识库文档系统 · 将代码库编译为结构化、可检索、可追溯的知识库*

[English](README.en.md) · [简体中文](README.md)

[![CI](https://img.shields.io/github/actions/workflow/status/handar-code/Cortex/ci.yml?branch=main&label=CI&logo=github)](https://github.com/handar-code/Cortex/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Cortex 把代码库编译成结构化知识库：符号目录、归因、版本快照和向量索引，供 Agent 检索时直接取用证据链。项目结构、语言、甄别规则与治理策略均由 `config.yaml` 驱动，不含特定项目硬编码。

从源码安装后提供 `cortex` 命令。

---

## 简介

Agent 接手陌生代码库通常依赖通读源码，成本高且难以追溯。Cortex 将代码库编译为结构化知识库，检索时返回符号、片段与溯源信息。

```
代码库 ──ingest──▶ 符号目录(catalog) ──▶ 归因(@domain) ──▶ 向量索引
                     │                     │                  │
                     └── 增量重算+版本快照    └── 双轨裁决        └── 三通道并列
                                    ▼
                          Agent 检索（结构化证据链）
```

**核心特性**：

1. **归因双轨** —— `@domain` 显式标注 ‖ 向量隐式软归属，两轨平等并行。domain 不依赖手写标注；缺少标注时由目录树作用域与向量隐式轨提供归属。
2. **三通道并列** —— 检索返回 `annotation_hits`（显式键）/ `annotation_text_hits`（docstring 词项）/ `vector_hits`（向量近邻），**不预融合**，由 Agent 裁决。
3. **git 式版本化** —— 每次重算 = 一次提交，强制 `change_reason`、可回滚、`active` 标记已发布版，并检测代码与 catalog 之间的漂移。
4. **零配置可运行** —— 全链路离线、零密钥、零人工介入即可跑通。

**核心指标**（实测）：近万文件的大型项目 → 401 文件 / **10512 符号** / 10 域分片 / ~3.3s / 溯源覆盖率 1.0。

## 核心能力一览

| 能力 | 说明 |
|---|---|
| **多语言符号抽取** | Python AST 提取；Markdown 全文 + 注释（`#` / `//` / `/* */` / `<!-- -->`）独立成符号；TypeScript 提取器为占位，可按 `extract/` 适配器契约扩展 |
| **确定性优先** | 内置甄别 + 内置提取为主路径，AI 提取仅作兜底升级 |
| **两档向量** | `offline` 确定性哈希（零依赖，CI/断网可用）/ OpenAI 兼容接口客户端（本地模型服务与云端**同一套代码**，改 `api_base` 切换） |
| **ANN 后端** | `flat` 暴力余弦（零依赖默认）/ `hnsw` 近似最近邻（`pip install ".[ann]"`），与 embedder 正交可自由组合 |
| **查询时重排** | corpus 用离线哈希建索引、查询用 bge-m3 重排（`retrieval.rerank: siliconflow`），按次计费、零额外离线成本 |
| **渲染产物** | MkDocs 主站（人读）+ Markdown/llms.txt 分片（Agent 读）；可选产物默认关 |
| **读写分离** | 读 = Skill 读指南（零写）/ 写 = MCP git 式提交，双通道物理分离 |
| **隐式契约缺陷检测** | `cortex defects` 静态检出契约断裂；catalog 记录调用关系，defects 报告契约违规 |

## 依赖

**核心依赖**：

```
pyyaml>=6.0
```

全链路（ingest → 归因 → 渲染 → 索引 → 检索）**零第三方依赖即可跑通**，embedding 走内置 `offline` 确定性哈希，不需要联网、不需要任何密钥。

**可选依赖**：

| 组 | 依赖 | 用途 |
|---|---|---|
| `ann` | `hnswlib>=0.8.0`、`numpy>=1.23` | 大规模向量检索的近似最近邻后端 |
| — | 无 | 真语义向量（本地模型服务 / 云端）：标准库 `urllib` 实现 OpenAI 兼容 `POST /v1/embeddings`，**无需额外依赖** |

```bash
pip install -e .                 # 最小：核心功能，够跑 offline 全链路
pip install -e ".[ann]"          # 可选：加 hnswlib + numpy，启用 hnsw 后端
```

- **Python**：>= 3.10（CI 覆盖 3.10 / 3.11 / 3.12 / 3.13）
- **系统要求**：无。纯 Python，无需编译工具链（除非装 `ann` 组，hnswlib 需要 wheel 或编译环境）
- **外部服务**：默认**不需要**。用 `offline` 时完全离线；用真语义时需要一个 OpenAI 兼容 `/v1/embeddings` 端点（本地 Ollama / llama.cpp / LM Studio / vLLM，或云端硅基流动）

---

## 快速索引

| 场景 | 命令或配置 |
|---|---|
| 本地跑通全链路（无需 key） | 「5 分钟 Quickstart」+ `config.quickstart.yaml`（offline） |
| 生产部署、真语义检索 | `config.example.yaml`（siliconflow / BAAI/bge-m3）+ `SILICONFLOW_API_KEY` |
| 大项目加速向量检索 | `--ann-provider hnsw` 或配置 `ann_provider: hnsw`（需 `pip install ".[ann]"`） |
| 确认改动没坏 | `pip install -e ".[ann,dev]" && pytest -q`（60 passed，无 key 也全绿）；push 后 GitHub Actions 自动跑 |
| 查看全部命令 | `cortex --help` ｜ 「完整 CLI 参考」 |
| 报告安全问题 | [`SECURITY.md`](SECURITY.md)（私有漏洞报告入口，请勿开公开 Issue） |

**核心机制：**
- **Embedder 档位** = `offline`（确定性哈希）/ OpenAI 兼容接口客户端（`siliconflow` 云端或本地模型服务）。决定语料向量的计算方式。
- **ANN 后端** = `flat`（暴力余弦）/ `hnsw`（hnswlib 近似最近邻）。决定查询时的最近邻检索方式，与 embedder 正交。
- **三通道并列** = 检索返回 `annotation_hits`（`@domain` 显式键）/ `annotation_text_hits`（docstring 词项）/ `vector_hits`（向量近邻），不做预融合。

---

## 5 分钟 Quickstart（零配置 · 无 API key · 不联网）

本流程使用 `offline` 确定性哈希向量，无需密钥、不访问网络、结果可复现。仓库提供合成示例项目 `fixtures/sample_project/`。

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

**本地与云端接口同构**：本地模型服务（Ollama / llama.cpp server / LM Studio / vLLM）与云端（硅基流动）都是同一个 OpenAI 兼容接口 —— `POST /v1/embeddings`，请求体 `{model, input, encoding_format}`，响应 `{data:[{index, embedding}]}`。无需单独实现本地 embedder；修改 `config.api.yaml` 的 `api_base` 即可切换：

```yaml
# 本地端点
embedding:
  provider: siliconflow
  api_base: http://localhost:11434/v1/embeddings   # Ollama；llama.cpp 用 http://localhost:8080/v1/embeddings
  model: bge-m3                                     # 建议本地也用 bge-m3，保持 1024 维
  api_key_env: SILICONFLOW_API_KEY                  # 本地不校验 token，放占位值即可
```

完整可跑配置见 [`config.api.yaml`](config.api.yaml)（注释里给出切本地/切云端各改哪几行）。**注意本地也用 bge-m3 保持 1024 维** —— 否则 `SiliconFlowEmbedder.dim=1024` 与实际向量维度不符，hnsw build 会失败。

**分层依据**：`offline` 保证任何环境（CI、断网、刚 clone）都能零成本跑通全链路，是"永远能退化到的地板"；真语义档提供语义质量（实测跨语言同义对余弦约 0.81~0.87，无关对约 0.44；离线哈希恒为 0、无法区分）。当前硅基流动免费不限速，适合把整个 corpus 一次性嵌入成索引。

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
- 已有 `index.json` 切换到 hnsw 时无需重算向量：运行 `python scripts/rebuild_hnsw.py` 复用现有向量重建。
- 小数据集下 `hnsw` 与 `flat` top-k 集合完全重合（`ef=n` 召回充分），由 `tests/test_phases.py::ANNBackendTest` 锁住。

---

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
pytest -q                 # 60 passed，无需 API key
python -m unittest discover -s tests   # 等价（unittest 风格亦可跑）
```

- **无 key 运行机制**：`tests/conftest.py` 在未设置 `SILICONFLOW_API_KEY` 时，于导入期把 `index` / `attribution` 两处 `get_embedder` 强制为 `OfflineEmbedder`；设置了 key 则保留真实 provider，顺带验证真语义路径。放在导入期（而非函数级 fixture）是为了覆盖 `unittest.TestCase.setUpClass`。
- `tests/test_integration_e2e.py`：offline 全链路（建档 → flat/hnsw 双索引 → 检索），断言向量/注释文本双通道命中与 flat-hnsw top-5 重合 ≥2。
- **GitHub Actions**（`.github/workflows/ci.yml`）：push 到 `main` + PR 触发，Python 3.10 / 3.11 / 3.12 / 3.13 矩阵，`pip install .[ann]` 后 `pytest tests -q`。如需验证真语义路径，在仓库配 `SILICONFLOW_API_KEY` secret 并取消 ci.yml 里的 `env` 注释。
- `pyproject.toml` 的 `testpaths = ["tests"]` 防止把 `fixtures/sample_project/` 里的样本 `.py` 误当测试收集。

安全政策：[`SECURITY.md`](SECURITY.md)（漏洞报告入口）。
