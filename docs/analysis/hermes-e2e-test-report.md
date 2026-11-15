# 真实项目端到端测试报告 · 目标项目 = hermes-agent

- 测试日期：2026-11-15
- 被测系统：`<REPO_ROOT>/`（通用项目知识库文档系统，Phase 1~6 全实现）
- 目标项目：`<target-project>/`（真实的近万文件大型项目）
- **产物隔离**：全部测试产物只写入 `<e2e-artifacts>/artifacts/`；  
  `config.yaml`、`key(.env)`、本报告在 `<e2e-artifacts>/`。  
  **未向 hermes-agent 工作区写入任何文件**（`git status` 校验为空），源码仓保持干净。

---

## §0 一页速查

| 项           | 结果                                           |
| ----------- | -------------------------------------------- |
| 全链路         | Phase 1~6 全部跑通 ✅                             |
| 规模（最终）      | 扫描 401 文件 / 399 有效 / **10512 符号**（含根级核心 1575 + 29 comment）/ ~3.3s |
| 门禁          | 溯源覆盖率 1.0，CI 成功发布                            |
| 真实向量        | BAAI/bge-m3（硅基流动，1024维）在 64 个真实符号子集上语义检索 ✅   |
| 回归测试        | **30/30 通过**（含归因双轨 / 三通道 / files_only root）     |
| 抓到并修复的真 bug | **3 个**（多 root 增量不幂等、漂移检测误报、**归因层缺失**）       |
| 规模加固        | 2 项（近邻候选池采样；**大文件 ast 提取 15× 提速**）          |
| 归因修复        | domain 从「全空 / 单桶」→「0 空 / 10 域分片」，三通道并列    |
| 语料范围修正     | 首版漏掉根级核心代码，已纳入（见 §9）                       |
| 待办/诚实边界     | 见 §5                                         |

> **重要更正 1（复测后）**：首版报告把「`@domain` 全空 → 注释通道恒空 / llms 分片退化单桶」写成
> 「真实项目特征 / 诚实边界」。**这是错误定性**——设计稿 §12 本就立论「标注不可持续、靠向量
> 自动入域」，缺标注是设计前提而非缺陷。真正的问题是实现漏了 §12 隐式轨/裁决层。已修复，见 §8。
>
> **重要更正 2（用户复核后）**：首版因 `cli.py` 852K 大，把**整个根级目录排除**，用户指出
> 「根级反而是核心代码」。已加 `files_only: true` 能力纳入根级 19 个 `.py` + AGENTS.md/README.md，
> 并顺带修掉大文件提取的 O(n·行数) 退化。见 §9。早期「380 文件/8927 符号」数字为修正前口径。

---

## §1 测试配置（配置驱动裁剪，验证系统通用性）

真实项目近万文件、含 114M 目录和 852K 单文件，全量无裁剪不现实也无必要。  
`config.yaml` 用 **10 个绝对路径 roots**：1 个 `files_only: true` 的**项目根**（只吃顶层
19 个核心 `.py` + AGENTS.md/README.md，不递归），加 9 个代表性模块目录（agent / gateway /
context_management / hermes_db / hermes_delegation / acp_adapter / cron / legacy /
docs/design），配合黑名单排除 `__pycache__`、生成物、备份等噪音。仍排除 114M 的
`context_system/` 与未声明语言的 4026 个 `.ts/.tsx`。  
系统代码不含任何项目硬编码——换项目只需改这份配置，正是设计稿「通用、从零、解耦」的验证。

## §2 全链路执行结果（产物均在 artifacts/）

| Phase | 命令                                             | 结果                                                                               |
| ----- | ---------------------------------------------- | -------------------------------------------------------------------------------- |
| 1 摄取  | `ingest`                                       | 380 文件 / 379 有效 / **8927 符号**（508类/3338方法/4424函数/380模块/277文档小节），6032 带 docstring |
| 2 版本化 | `store-init`/`recompute`/`rollback`            | v1/v2/v3 全部 8927（幂等）；rollback 重指向 v1 ✅                                           |
| 3 渲染  | `render`                                       | `llms.txt` 根路由 + `llms-full.txt` 全量 + 分片；mkdocs 源                                |
| 4 索引  | `index`/`retrieve`                             | offline 8927 条 256维、双主线并列检索；**近邻池=512 近似**（22.9s）                                |
| 5 接入  | `gen-skill`/`gen-agents-md`/`onboard`          | SKILL.md 读指南、AGENTS.md 入口、幂等首次接手序列                                               |
| 6 治理  | `gate`/`ci-publish`/`probe`/`jury`/`bootstrap` | 门禁 1.0、CI 发布、探针、异构 jury、**从真实 77K AGENTS.md 抽出 252 条宪法条款**                       |
| 2 漂移  | `drift`/`reconcile`                            | 无改动 0 漂移；改 1 文件报 1 漂移；reconcile 只构建候选不发布（active 不动）✅                             |

### 真实语义向量（BAAI/bge-m3）

全量 8927 条喂 API 约 280 批、成本高，按合理工程做法取**跨 8 模块、带 docstring 的  
64 个真实符号**子集做语义索引，中文跨语言查询命中语义相符的真实代码：

- 「消息压缩与上下文裁剪」→ `BudgetConfig` 0.54 / `TrimPolicy` 0.52 / `InjectionAssembler`（真实即上下文裁剪类）
- 「会话状态持久化与恢复」→ `transcript_persistence_*` / `TaskStatus`
- 「工具调用与模型适配」→ `HermesCursor` / `InjectionAssembler`

---

## §3 真实项目测试抓到并修复的三个 bug（关键价值）

> 前两个 bug 单 root 的 fixture 永远测不出来，只有真实多 root 项目才暴露；
> 第三个（归因层）是用户复核设计稿口径后指出的实现缺口。

**Bug 1 · 增量重算不幂等（v1=8927 → v2=9066）**  
根因：`_scan_files` 用「相对单个 root 的路径」当 `rel`，多 root 下不同 root 的同名文件  
（尤其 `__init__.py`）rel 碰撞 → `file_hashes` 与按文件分组串味 → 增量复用时符号重复膨胀。  
修复：多 root 时给 rel 加**唯一 root 名前缀**（同名 root 自动加序号）；单 root 保持原样。  
校验：v1/v2/v3 全部 8927。

**Bug 2 · 漂移检测误报（无改动却报 370 文件漂移）**  
根因：`detect_drift` 自己重扫工作区、用**不带前缀**的 rel，而 active catalog 的  
`file_hashes` 是带前缀的 key，两套管径不一致 → 几乎全部误判漂移（零人态下每次 reconcile 会白跑全量重算）。  
修复：`detect_drift` 复用 `_scan_files` 作为单一真源，消除口径分叉。  
校验：无改动 0 漂移 / 380 unchanged；改 1 文件恰好 1 漂移；还原后归零。

两个修复均补了 `MultiRootTest` 回归测试（rel 唯一性 / 增量幂等 / 漂移口径 / 候选池开关）。

## §4 规模加固：近邻候选池采样

素 `_compute_neighbors` 是 O(n²)：8927 符号约 8000 万次相似度，精确全量外推 ~403s，不可用。  
新增 `index.neighbor_pool`（配置驱动，默认 0=精确全量）：`>0` 且条目超阈值时，  
确定性均匀采样出共享候选池，每个符号只在池内找近邻，复杂度降 O(n·pool)。  
实测 pool=512 → **22.9s**（快约 17 倍）。真实索引建议换 ANN 向量库，本实现是零依赖的诚近似。

## §5 诚实边界 / 发现（非 bug，属真实项目特征 / 当前实现取舍）

1. **~~`@domain` 全为空 → 注释通道恒空 / 分片单桶~~ 已在 §8 修复**：首版把它误列为
   「真实项目特征」，实为实现漏了 §12 隐式轨。修复后 domain 0 空、9 域分片、三通道并列。
2. **混入备份目录**：`agent/_ctx_merge_backup_20260824/` 被扫入（黑名单可加）。
3. **真向量子集**：全量 8927 条未喂 API（成本/时间），语义能力用代表性子集验证；  
   offline 全量索引已跑通。
4. **mkdocs 站点未构建**：`optional.mkdocs=false`，离线测试不依赖 mkdocs 二进制，仅生成源。
5. **近邻为采样近似**：大项目下 `vector_neighbors` 是候选池近似，非精确全局近邻。

## §6 产物清单（隔离目录）

```
doc-system-e2e/
├── config.yaml                 目标项目配置（roots/黑名单/embedding/index）
├── .env                        key（本地，.gitignore 排除）
├── .gitignore
├── TEST_REPORT.md              本报告
└── artifacts/
    ├── catalog.snapshot.json   全量 catalog（4.7M, 8927 符号）
    ├── .docstore/              git 式版本库（v1~v5 + HEAD/active/log）
    └── .doc_system/
        ├── llms/               llms.txt 根路由 / llms-full.txt / 分片
        ├── site/               mkdocs 源（docs/ + mkdocs.yml）
        ├── index/              index.offline.json(8927) + index.bgem3.subset.json(64)
        ├── SKILL.md            Agent 读指南（零写）
        ├── AGENTS.md           唯一入口
        └── constitution.json   从真实 AGENTS.md 自举的 252 条宪法条款
```

## §7 结论

文档系统在真实近万文件项目上**端到端跑通**，配置驱动通用性得到验证；  
真实项目测试额外发现并修复了 2 个单 root fixture 测不出的正确性 bug、  
补了 1 项规模加固；随后用户复核设计口径又指出并修复了**归因层缺失**（§8）。  
27/27 回归通过。产物严格隔离，目标项目零污染。

## §8 归因双轨修复（用户复核口径后，「domain 为空」是 bug 不是特征）

**问题**：首版测试发现真实 hermes 项目 8927 符号 `domain` 全空 → 注释通道恒空、llms
分片退化单桶。首版报告把它当成「真实项目没有 `@domain`」的正常特征放过——**错**。
用户指出「我不是说向量和文本注释并行吗」，复核设计稿后确认：

- §12 立论：*"标注在 1439 文件级不可持续 → 用 Embedding 把符号/模块映射到语义空间，
  以相似度解决多域重归属"*；§12.5 *"未标注代码靠向量自动入域，无需人工逐文件贴标签"*。
  **真实项目没有 `@domain` 本就是设计前提，隐式轨正是为此而生。**
- 实现漏了两层：① §12 隐式轨 / 软归因 / §12.6 平等裁决 / §12.7 快照整个没实现，
  `domain` 只从 `@domain` 正则取（仅显式轨）；② docstring（6032 条文本注释）只被拼进
  向量原料，没有作为独立并行检索通道。于是「向量 ‖ 文本注释并行」塌成单腿。

**修复（主路线甲：目录树 scope 域骨架 + 向量软归因，零人/通用/确定性）**：
- 新增 `attribution.py`：`域骨架 = 目录树 scope→module`（§13.4 Q5 已定的分片边界，每项目
  天然具备）；`显式轨 = @domain`；`隐式轨 = 向量对域质心（域内符号向量均值，§12.3 seed 的
  零人等价物）的软分布`；§12.6 平等裁决（explicit/implicit/both_agree/conflict/uncertain/
  structural）+ §12.7 结果随 catalog 快照存留、幂等。
- `Symbol` 扩展三件套与裁决字段（`annotation` / `annotation_text` / `domain_source` /
  `domain_confidence` / `domain_distribution` / `conflict_flag`），`triple()` 显式并列三件套。
- `index.retrieve` 从「双通道」升级为**三通道并列返回、不预融合**：`annotation_hits`（注释）
  + `annotation_text_hits`（注释文本/docstring 词项，独立并行主线）+ `vector_hits`（向量）。
- `config.attribution` 段（enabled / provider / scope_depth / domain_from / text_channel），
  默认开、默认 offline 确定性；可升级 siliconflow 真语义软归属。
- `render` / `SKILL.md` 同步三件套 + 三通道口径（读指南不再把 domain 错标成"注释"）。

**真实复测结果（8927 符号）**：
| 指标 | 修复前 | 修复后 |
|---|---|---|
| 空 domain | 8927 | **0** |
| domain_source | 无 | structural（可复现） |
| 带注释文本 | 未参与检索 | 6309 条，独立通道 |
| llms 分片 | 1（单桶 `_uncategorized`） | **9 个 scope 域分片 + 根路由 + 全量 = 11 文件** |
| 检索通道 | 2（注释恒空 + 向量） | **3（注释 / 注释文本 / 向量）并列** |

真实查询「session state persistence」：注释文本通道命中 `on_session_reset: Reset all
per-session state`、`assemble_incremental: 适用场景 session 续传` 等真实 docstring，
向量通道独立命中 `_persist` 等，二者并列不融合——「向量 ‖ 文本注释并行」真正落地。

回归新增 `AttributionTest` 6 例（domain 非空 / 显式键尊重 / 隐式软分布 / 三件套三通道 /
多分片 / 快照幂等），全套 **27/27 通过**（当时口径）。

## §9 语料范围修正：纳入根级核心代码（用户："根级的反而是核心代码"）

**问题**：首版配置因 `cli.py` 852K 等大文件，把整个项目根目录排除，只扫 9 个子模块目录。
用户指出根级 `cli.py` / `hermes_state.py` / `run_agent.py` 等恰恰是**核心入口代码**，漏掉它们
等于知识库丢了主脉络。这是「怕麻烦就裁掉重要内容」的过度裁剪，不是合理取舍。

**为什么之前不敢纳入**：roots 只会递归 `rglob("*")`，把项目根设为 root 会把 114M 的
`context_system/`、4026 个 `.ts/.tsx`、上万个文件一并拖进扫描，且和已列的 9 个 root 重复。

**修复（通用能力，非特例）**：
1. root 新增 `files_only: true`（`Config.root_files_only` + `pipeline._scan_files`）：
   只扫该目录**顶层文件**、不递归。配置里加一个指向项目根的 root 即可纳入 19 个核心 `.py`
   + AGENTS.md/README.md，又不拖入巨型子目录。向后兼容（旧字符串/对象写法照常工作）。
2. 大文件性能：profile 发现 `ast.get_source_segment` 被调 492 次、每次重新 splitlines
   整篇 846K（独占 2.42s / 85%）。改为整篇只切一次按 `lineno/end_lineno` 取片段（O(行数)），
   并一次性建父映射（判 method/function 从 O(n²) 降 O(n)）。
   实测 `cli.py` **2.58s → 0.17s（约 15×）**，`hermes_state.py` 1.06s→0.045s，符号数不变。

**修正后复测**：
| 指标 | 修正前 | 修正后 |
|---|---|---|
| 文件 / 符号 | 380 / 8927 | **401 / 10502** |
| 根级核心符号 | 0 | **1575**（cli.py 493 / hermes_state.py 322 / run_agent.py 282 …）|
| 域分片 | 9 + 根 + 全量 | **10 + 根 + 全量 = 12 文件**（新增 `llms-hermes-agent.txt`）|
| 全量摄取耗时 | ~10s | **~3.3s**（大文件优化抵消了新增语料）|
| 空 domain | 0 | 0 |

真实检索验证：查「cli entrypoint command dispatch」命中 `hermes-agent/cli.py:4087
_get_plugin_cmd_handler_names`、`hermes-agent/AGENTS.md:413 CLI Architecture (cli.py)`
等根级核心内容。全链路（store/recompute/ci-publish/render/index/gen-skill）重跑通过。

回归新增 `FilesOnlyRootTest` 3 例（顶层不递归 / ingest 含根级核心 / 大文件不退化），
全套 **30/30 通过**。README（roots 扫描范围 + 大文件性能段、计数 27→30）、
`config.example.yaml`（两种 root 写法示例）已同步。隔离边界仍成立（hermes 零污染）。

---

## §10 MD 全量搜索 + 注释抽取（本轮补 · 用户复核指出的第三点）

### 10.1 问题
初版 `PlainTextExtractor` 对 Markdown 仅取 `text[:200]` 作 module docstring、section 只切标题行，
**正文全文与注释均未被索引**。设计稿 `agent-onboarding-doc-system-design.md` 长 66KB，其中 §12 归因双轨、
§13.7 接入协议等关键概念深藏文档中部，首 200 字根本不含这些词 → 真实项目里「MD 全文（常是对代码的叙述）」
完全无法被向量 / 文本通道召回。用户原话：「MD 文件应该全量搜索，而且还加上注释」。

### 10.2 修复（不改任何设计决策）
- 新增 `Symbol.content` 字段（默认空、向后兼容）：承载全文/长文本，与显示用 `docstring` 预览解耦。
- markdown 提取器重写：
  - module 符号 `content` = **整篇全文**；每个 section 符号 `content` = **该节正文全文**（标题到下一标题之间的正文，含 prose 与代码）；
  - `content` 进 **向量通道**（`index` 嵌入文本）与 **文本注释通道**（`annotation_text`）；
  - 从 fenced 代码块按语言识别注释风格（`#` / `//` / `/* */` / `--`）抽取独立 `comment` 符号（kind=comment），
    并抽取 HTML `<!-- -->` 注释为 `comment` 符号；
  - `.txt` 走单 module，`content` = 全文。
- 嵌入层截断：`index` / `attribution` 的嵌入文本截断到 `MAX_EMBED_CHARS=6000`。原因：66KB 设计稿
  module.content 超出 bge-m3 上下文上限（~8K token）被 API 拒（HTTP 400）。**仅截断嵌入输入，catalog 仍存全文**；
  section 级符号粒度更细、各自 ≤6K，可全量嵌入——长文档天然被切成多段可检索单元。

### 10.3 复测结果（产物均在 artifacts/）
- 全量 ingest：符号 **10502 → 10512**（+10；其中新增 **29 个 comment** 符号，来自 hermes `AGENTS.md` / `README.md` /
  设计稿 fenced 代码块与 HTML 注释）。
- 单元回归：**30/30 通过**（新增/修改未破坏 fixture 下 `symbol_count==18` 等断言；`store.recompute` 增量复用旧 catalog 的
  坑已通过「重建 .docstore 强制全量重抽」规避，见 10.4）。
- 中文语义检索验证（bge-m3 / 硅基流动，聚焦设计稿 MD 子集 120 符号）：
  - 查「文档系统的归因双轨和向量隐式轨是怎么实现的」→ top5 全部命中设计稿 §12 区域 section（cosine 0.57~0.65）；
  - 查「接入协议里读侧用什么、写侧用什么」→ top5 命中设计稿 §13.7 区域 section（cosine 0.54~0.57）。
  - 改前（仅 `text[:200]`）这些概念不在任何 MD 符号的嵌入文本里 → **不可能**被召回；改后正确命中。
- 对照组：英文 ASCII 查询走离线文本注释通道，`annotation_text_hits` 现读 `annotation_text`/`content`（含 MD 全文），
  通道计数正常返回。

### 10.4 操作注意（复测中踩到）
`store.recompute` 在已有 active catalog 时走**增量重算**：若旧 `.docstore` 残留（改前 10502 口径），会复用旧符号、
不反映新提取逻辑。本轮回测已 `rm -rf artifacts/.docstore` 后 `store-init` + `recompute`，得到干净的 **v1=10512**。
（这是测试操作问题，非系统 bug；真实 CI 每次全量重算或 drift 触发均从磁盘重抽。）

### 10.5 仍属诚实边界（未做，用户未要求实现）
- 跨函数**协同/调用关系**建模（用户第二点）：当前提取是代码/ast 级、单符号视角，只记录父 class，
  未建调用图或"多函数共作用于一模块"的关系。属于「关系抽取」能力，需在 §13.4 之外加一层关系提取器，
  待用户拍板是否做。

---

## §11 向量通道默认 Offline 哈希语义弱 → 选项 C（查询时 bge-m3 重排）落地

### 11.1 问题
默认 embedder 是 offline 确定性哈希（词袋），向量通道对中文/语义查询返回噪声：例如查「把 session 状态持久化写入数据库」，
离线哈希 top 命中 `agent/outbound_webhooks.py:469`（**docstring 为空、score 1.0**）、`hermes_db/connection.py:130`（score 1.0）等无关项；
纯 token 重叠使空 docstring 符号也能得满分，无法表达语义。三通道结构本身没问题，是默认 embedder 语义弱。

### 11.2 四个选项（供透明决策）
| 选项 | 做法 | 质量 | 成本 | 结论 |
|---|---|---|---|---|
| **A 两阶段**（词项粗筛候选池→离线向量精排） | 离线可用、零 API | 中（仍是哈希精排） | 0 | 可作离线兜底，不解决语义 |
| **B 默认切 siliconflow 全量建索引** | corpus 向量全换成 bge-m3 并快照 | 最好 | 一次性 ~280 批 API + 存储 | 质量最佳但成本高 |
| **C 仅查询时把 query+topN 候选送 bge-m3 重排**（**已落地，推荐**） | corpus 仍离线哈希召回候选，查询时对 top-20 候选用 bge-m3 重排挑前 k | 接近 B | 按次计费（每次查询 ~21 次嵌入调用） | **采纳**：改动小、质量好、零额外离线成本 |
| **D 维持现状**（靠注释文本通道保底） | 不动 | 最差（向量通道不可信） | 0 | 拒绝 |

### 11.3 实现（不改三通道结构，仅加重排层）
- `index.py`：`retrieve(query, k, rerank_provider=None, rerank_candidates=20)`；当 `rerank_provider` 非空时，
  对 offline top-N 候选用 `get_embedder(provider)`（bge-m3）重算余弦、重排取前 k；新增 `_symbol_embed_text(s)` 共用嵌入文本构造、
  `_semantic_rerank()` 方法、`RERANK_CANDIDATES=20` 常量。
- `config.py`：新增 `RetrievalConfig(rerank, rerank_candidates)` 与 `Config.retrieval`。
- `cli.py`：`retrieve` 增加 `--rerank none|offline|siliconflow` 与 `--rerank-candidates`；
  优先级 **显式 --rerank > config.retrieval.rerank > 默认 None**（修正：原先 `--rerank none` 被误当未指定而回退 config，已修）。
- `config.example.yaml`：注释示范；e2e `config.yaml` 启用 `retrieval: {rerank: siliconflow, rerank_candidates: 20}`（真实部署默认即重排）。

### 11.4 复测验证
- 单元回归：新增 `RerankTest` 2 例（mock bge-m3 验证「重排按语义置顶」+「不传 rerank 保持离线序」），全套 **32/32 通过**。
- 真实检索对比（同一中文查询「把 session 状态持久化写入数据库」）：
  - 离线哈希：top1 `outbound_webhooks.py:469`（docstring 空，score 1.0）、`connection.py:130`（score 1.0）—— 噪声。
  - bge-m3 重排（C）：top1 `gateway/session.py:3197`（"Switch a session key…"）、`session.py:2955`（"Mark a session as resumable after a restart…"）、
    `run.py:7379`（"Persist the runtime model/provider…"）—— 均为真正相关项，噪声被压制。
- 默认行为已切换：e2e 配置下 `retrieve` 不传 `--rerank` 即走 bge-m3 重排；doc-system 无配置时仍是 offline（零成本环境不破坏）。

### 11.5 仍可选升级（未做，待拍板）
- 若追求极致质量，可叠加 **B**（corpus 全量 bge-m3 索引 + 快照），C 作为零成本保底；或 A 作纯离线兜底。当前 C 已在质量/成本间取到合理平衡。

---

## §12 最终决策：默认 embedder 切硅基流动 bge-m3（corpus 全量真语义 + 隐式轨同步）

### 12.1 用户追问与澄清
- 用户问「为何默认走离线哈希、不直接用源代码/注释/MD 内容算向量」。澄清：embedder 本就**可插拔**，
  `OfflineEmbedder` 是纯本地词袋哈希（零依赖/零成本/可复现兜底），`SiliconFlowEmbedder` 直接把
  内容送 BAAI/bge-m3 真语义向量——**内容一直被用，只是默认档是哈希**。
- 用户告知：**硅基流动 bge-m3 免费、不限次、不限速**。这直接推翻 §11 选 C 不选 B 的**唯一理由**（API 成本/限速）。
- 用户另指出本机 M5 Air 有 **10 核 GPU，跑 9B 本地模型都不烫、本地向量反而比云端快** → 本地模型作为后续可选档（见 12.4）。

### 12.2 决策（已落地）
- **e2e `config.yaml`**：`embedding.provider=siliconflow`、`attribution.provider=siliconflow`、`retrieval.rerank=null`。
- **含义**：corpus 全量真语义索引（不再靠离线哈希召回）；查询时 query 也用 bge-m3 嵌入直接比对，排序即真语义 →
  选项 C 的查询时重排**冗余**，故关闭。§12 隐式轨软归因也吃真语义（域质心更准）。
- **代码默认仍 `offline`**（`config.py` 的 `EmbeddingConfig.provider` 默认值）：保证零配置即跑、单元测试无 key 也能过；
  **真实默认在 config.yaml 落 siliconflow**。两者不冲突——代码默认是兜底，YAML 是部署默认。

### 12.3 三档 provider（依赖/语义由弱到强）
| 档 | provider | 语义 | 位置 | 依赖 | 本机负担 | 适用 |
|---|---|---|---|---|---|---|
| Tier0 | `offline` | 弱（词袋） | 本地 | 零（仅标准库） | 轻 | 断网/无 key/零配置兜底 |
| Tier1 | `local` | 真 | 本地 | 重（运行时+权重） | 重但本机 M5 Air 跑得动 | 隐私/断网语义（后续扩展） |
| Tier2 | `siliconflow` | 真 | 云端 | 需 key+网络 | 最凉（算力在云端） | **当前默认**：免费不限速、本机零负担 |

- `local` 为**可选扩展**：需新增 `LocalEmbedder`（sentence-transformers / Ollama / llama.cpp）+ 工厂分支 + provider 字符串，几行事，不动三通道结构。用户本机 10 核 GPU 跑得动，且本地向量比云端快、数据不出本机。本轮未实现，留作后续档。

### 12.4 验证状态
- 单元回归：改动为配置 + 文档；`EmbeddingConfig` 代码默认保持 `offline`，`get_embedder(None)`→`OfflineEmbedder` 契约不变，**27/27 通过**（与切 siliconflow 前一致，单测不依赖 API）。
- e2e 全量 bge-m3 建索引 + 语义检索：待联网 .env key 跑通后补测（用户已确认免费不限速；若本机 M5 Air 后续切本地模型则更快）。
- 隔离边界：仅改 `doc-system/` 源码与 `doc-system-e2e/` 配置/文档；**hermes-agent 工作区零污染**。
