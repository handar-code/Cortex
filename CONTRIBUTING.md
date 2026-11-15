# Contributing to Cortex

感谢参与！这份文档说明本地怎么跑、怎么提 PR、以及项目的几条硬约束。

## 本地开发

```bash
git clone https://github.com/your-org/cortex.git
cd cortex

# 最小安装（核心功能，够跑 offline 全链路）
pip install -e .

# 可选：启用 hnsw ANN 后端
pip install -e ".[ann]"

# 跑测试（无需任何 API key）
pytest -q          # 期望 43 passed
```

**不要提交 `.env`**（含真实 API key）。项目根的 `.env` 已被 `.gitignore` 排除；密钥只从环境变量 `SILICONFLOW_API_KEY` 读取。

## 核心约束（请在提 PR 前确认）

1. **零人可运行**：任何改动都不能让「无 key、离线、无人工介入」的全链路跑不通。CI 会在**没有** `SILICONFLOW_API_KEY` 的情况下跑测试，必须全绿。
2. **不耦合具体项目**：代码里不能出现任何特定项目的硬编码（目录名、符号名、语言假设）。项目差异一律由 `config.yaml` 驱动。
3. **确定性优先**：内置甄别 + 内置提取是主路径，AI 提取只作兜底升级。涉及随机性的改动必须可复现。
4. **不预融合**：检索三通道（`annotation_hits` / `annotation_text_hits` / `vector_hits`）保持并列，不合并成单一排序。

## 提交与 PR

- 分支命名：`feat/xxx` / `fix/xxx` / `docs/xxx`
- commit message 用中文或英文均可，但请说清「改了什么、为什么」，涉及行为变化时附实测数据
- 提 PR 前请自检：
  - [ ] `pytest -q` 通过（无 key）
  - [ ] `cortex --help` 可用
  - [ ] 若改了 README / 配置示例，同步更新对应文档
  - [ ] 未提交 `.env`、索引产物（`index.json` / `*.hnsw`）、`catalog.snapshot.json`
  - [ ] 未在代码或文档里写入本机绝对路径

## 加一种语言的提取器

实现 `extract/` 下的适配器契约即可，不需要改 `KnowledgeIndex` / `retrieve`：

1. 在 `src/doc_system/extract/` 新建模块，继承 `extract/base.py` 的基类
2. 在 `extract/registry.py` 注册语言键
3. 在 `config.yaml` 的 `languages` 里启用
4. 在 `tests/` 补一条最小用例（建议用 `fixtures/sample_project/` 造样本）

## 加一个向量后端

实现 `index.py` 里的 `VectorBackend` 基类（`build` / `query` / `save_index` / `load_index`），然后在 `_make_backend()` 里按 `ann_provider` 分发。保持与 `FlatBackend` 同口径（cosine 相似度，分数越高越近）。

## 设计文档

改动涉及核心机制（归因双轨、三通道、生命周期、治理）时，**先读 [`docs/design.md`](docs/design.md)**（999 行完整设计稿）——那里有每条设计的立论与已否决的备选方案，避免重复讨论已定的事。

## 安全问题

发现密钥泄漏或安全漏洞，请**不要**开公开 issue，直接联系维护者。
