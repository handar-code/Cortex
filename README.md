# doc-system

**通用的、从零构建的项目知识库文档系统**，专为「让一个对新项目一无所知的新 Agent 快速、可靠地接手」而设计。
系统**不耦合任何具体项目**——所有项目结构、语言、触发方式、治理规则都通过 `config.yaml` 配置，代码本身零硬编码。

本仓库是设计稿《面向 Agent 接手的项目知识库：文档系统设计方案》的**完整实现**（Phase 1~6 全部落地）。

## 设计原则（实现遵循）

- **通用 / 解耦**：不与任何项目绑定；`fixtures/sample_project/` 只是合成示例。
- **确定性优先**：内置甄别 + 内置提取为主路径；AI 提取仅作兜底升级（§13.1）。
- **归因双轨（§12）**：`@domain` 显式键 ‖ 向量隐式轨（软归属）平等裁决。**domain 永不因缺手写标注而全空**——真实项目无 `@domain` 时，用目录树 scope（§13.4 Q5）作稳定归属 + 向量隐式轨产出软分布，结果随 catalog 快照存留。
- **三件套 + 三通道（§13.4）**：`向量 + 注释(@domain) + 注释文本(docstring)` 成对并列；检索返回 `annotation_hits` / `annotation_text_hits` / `vector_hits` **三通道并列、互不为主、不预融合**，交 Agent 裁决。向量方向与文本注释方向地位平等（§12.1）。
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
| 4 | 索引 | `index`（三件套 + 向量近邻 + 三通道并列检索 + 近邻候选池近似） |
| 5 | 接入协议 | `access/skill`, `access/write_channel`(MCP), `access/agents_md`, `access/onboarding` |
| 6 | 治理 | `governance/gate`, `governance/trigger`, `governance/observability`, `governance/bootstrap` |

## 快速开始

```bash
# 准备环境（managed venv + PyYAML）
python -m venv .venv && .venv/bin/pip install pyyaml
export PYTHONPATH=src

# 1) 扫描 → 甄别 → 提取 → catalog 快照
python -m doc_system.cli ingest --config config.example.yaml --out catalog.snapshot.json

# 2) 初始化 git 式 catalog 存储 + 首次重算提交
python -m doc_system.cli store-init --config config.example.yaml
python -m doc_system.cli recompute --config config.example.yaml --reason "init catalog v1"

# 3) CI 发布（门禁过则原子切 active；唯一发布权）
python -m doc_system.cli ci-publish --config config.example.yaml --reason "release v1"

# 4) 渲染（MkDocs 源 + llms.txt 分片）+ 索引
python -m doc_system.cli render --config config.example.yaml --out .doc_system
python -m doc_system.cli index --out .doc_system/index/index.json          # 默认离线哈希向量
# 用真实语义向量（BAAI/bge-m3 · 硅基流动）：
python -m doc_system.cli index --provider siliconflow --out .doc_system/index/index.json
python -m doc_system.cli retrieve --provider siliconflow --query "用户账户与登录" --k 3

# 5) 读侧产物（零写）
python -m doc_system.cli gen-skill --out .doc_system
python -m doc_system.cli gen-agents-md --out .doc_system
python -m doc_system.cli onboard --out .doc_system

# 6) 治理
python -m doc_system.cli gate
python -m doc_system.cli probe --config config.example.yaml
python -m doc_system.cli jury --question "is the KB helpful?"
python -m doc_system.cli bootstrap --agents-md fixtures/sample_project/AGENTS.md --out .doc_system/constitution.json

# 漂移兜底
python -m doc_system.cli drift --config config.example.yaml
python -m doc_system.cli reconcile --config config.example.yaml   # 构建候选版本（不发布）
python -m doc_system.cli rollback --version v1                    # 回滚 active
```

## 测试

```bash
PYTHONPATH=src python -m unittest discover -s tests   # 30 个用例覆盖 Phase 1~6 + 归因双轨 + 三通道 + files_only root（含 bge-m3 mock，不依赖网络）
```

## roots 扫描范围（配置驱动）

- root 两种写法：`- path: <dir>` 递归扫描（默认）；`- path: <dir>` + `files_only: true` **只扫该目录顶层文件、不递归**。
  后者用于纳入**项目根级核心代码**（`cli.py`、`hermes_state.py`、`run_agent.py` 这类大而关键的入口文件），
  又不会把同目录下的巨型子目录（如 114M 的 context_system/）一并拖入扫描。
- 大文件性能：Python 提取器对 `ast.get_source_segment` 做了整篇只切一次的 O(行数) 优化 +
  一次建父映射（判 method/function 从 O(n²) 降到 O(n)）。实测 846K 的 `cli.py` 提取 **2.58s → 0.17s**（约 15×），符号数不变。

## 向量通道（三件套之一，可插拔）

`index.get_embedder(cfg)` 按 `config.yaml` 的 `embedding.provider` 选择（也可 CLI `--provider` 覆盖）：

| provider | 实现 | 维度 | 依赖 | 适用 |
|---|---|---|---|---|
| `offline`（默认） | `OfflineEmbedder` 确定性哈希 | 256 | 零 | 离线/CI/无 key、可复现 |
| `siliconflow` | `SiliconFlowEmbedder` · BAAI/bge-m3 | 1024 | 硅基流动 API | 真实语义检索、跨语言中英同义 |

- 具备语义能力：跨语言同义对余弦约 0.81~0.87，无关对约 0.44（离线哈希向量恒为 0，无法区分）。
- **API key 不入库**：从环境变量 `SILICONFLOW_API_KEY` 或项目根 `.env` 读取（`.env` 已被 `.gitignore` 排除）；`config.yaml` 只放 `model/api_base/api_key_env/batch_size`，不放明文 key。
- 批量 embedding：`embed_many(texts)` 按 `batch_size` 分组请求，按返回 `data[].index` 还原顺序；失败抛清晰 `RuntimeError`。
- 新 provider 只需继承 `index.Embedder` 实现 `embed_many` + 声明 `dim`，并在 `get_embedder` 注册；`KnowledgeIndex` / `retrieve` 完全不改。

## 已知边界（诚实声明）

- 真实语义向量已接入且实测可用（硅基流动 BAAI/bge-m3）；默认仍 `offline`（零依赖、可复现），启用语义向量需联网 + 有效 key。向量只影响向量通道近邻质量，三通道并列返回结构不变。
- 归因隐式轨默认 `offline` 确定性哈希向量（软分布语义较弱，但零成本、可复现）；`attribution.provider=siliconflow` 可升级为真语义软归属。`attribution.domain_from` 默认 `structural`（目录树 scope，稳定可复现），可切 `vector`（向量 top 域，语义强但随模型漂移）。二者都是并列信号，不预融合。
- 域骨架取目录树 scope（§13.4 Q5），是**结构域**而非纯语义能力域；跨域语义重叠靠 `domain_distribution` 软分布揭示，交 Agent 判断。
- MCP 写通道提供确定性的 `WriteChannel` 核心 + MCP 工具 schema；真实 `mcp` 包环境下的 stdio 服务由 `build_mcp_server()` 启用，离线时 CLI/CI 直接调用核心。
- TypeScript 提取器为占位（`typescript_typedoc`），新语言按 `extract/` 适配器契约扩展即可。
- MkDocs / PageFind / TypeDoc 站等附加产物走 config 开关（默认关），离线不构建、由 CI/人工启用。

设计稿：`docs/design/agent-onboarding-doc-system-design.md`（本仓库仅存放实现；设计稿独立演进）。
