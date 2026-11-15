# GitHub 上传清单

> 上传前把下面几段复制到对应位置。

---

## 1. 仓库 Description（GitHub → Settings → General → Description，最多 350 字符）

```
Cortex · 通用项目知识库文档系统：把代码库编译成结构化、可检索、可追溯的知识库，让新 Agent 查表接手而非通读。零人可运行、零密钥离线跑通、Python 3.10+。
```

**英文版**（如果仓库以英文为主）：

```
Cortex — a universal project knowledge-base doc system. Compiles any codebase into a structured, searchable, traceable knowledge base so new agents look things up instead of reading everything. Zero-human operable, runs offline with no API key. Python 3.10+.
```

---

## 2. Topics（GitHub → Settings → General → Topics，最多 20 个）

```
agent
knowledge-base
codebase-onboarding
documentation
retrieval
vector-search
llm
context-engineering
developer-tools
static-analysis
rag
python
ai-tools
repository-documentation
```

---

## 3. 一句话介绍（About / 社交简介）

```
把代码库编译成结构化知识库，让新 Agent 查表接手，而不是通读 3 万行。
```

---

## 4. Topics 描述 / 项目定位（可用于 GitHub Profile README 或发布说明）

### 4.1 项目定位

Cortex 是一个**通用的项目知识库文档系统**——它不服务于某个具体项目，而是为「接手任意代码库的新 Agent」提供基础设施。

核心思路是**把代码库编译成结构化知识库**：符号目录（catalog）→ 归因（domain）→ 向量索引，Agent 通过检索拿证据链，而不是通读源码。

它与「再写一份文档」的根本区别在于：

- **不腐烂**：代码一改，增量重算 + 快照对账自动发现漂移
- **不烧上下文**：Agent 只取命中的符号与片段，不是全量文档
- **可追溯**：每个符号带溯源与快照版本
- **可复用**：一次建档，任意 Agent、任意次接手都直接读索引

### 4.2 四条核心设计

1. **归因双轨** —— `@domain` 显式标注 ‖ 向量隐式软归属，平等并行、互不为主。真实项目没人手写标注，所以 domain 永不因缺标注而全空。
2. **三通道并列** —— `annotation_hits` / `annotation_text_hits` / `vector_hits` 不预融合，交 Agent 裁决。
3. **git 式版本化** —— 每次重算 = 一次提交，强制 `change_reason`、可回滚、`active` 标记已发布版。
4. **零人可运行** —— 全链路离线、零密钥跑通；AI 全自主为默认，人类审核是可选层。

### 4.3 实测指标

近万文件的大型项目 → **401 文件 / 10512 符号 / 10 域分片 / ~3.3s / 溯源覆盖率 1.0 / 回归 43 passed**。

---

## 5. 依赖（README 已有专节，此处留一份精简版）

### 核心依赖（1 个）

```
pyyaml>=6.0
```

全链路零第三方依赖即可跑通，embedding 走内置 offline 确定性哈希——不联网、不需要密钥。

### 可选依赖

| 组 | 依赖 | 用途 |
|---|---|---|
| `ann` | `hnswlib>=0.8.0`、`numpy>=1.23` | ANN 近似最近邻后端 |

真语义向量**不需要额外依赖**：本地模型服务（Ollama / llama.cpp / LM Studio / vLLM）与云端（硅基流动 BAAI/bge-m3）都是同一个 OpenAI 兼容 `POST /v1/embeddings`，由标准库 `urllib` 实现，改 `api_base` 即可切换。

### 环境要求

- Python >= 3.10（CI 覆盖 3.10 / 3.11 / 3.12 / 3.13）
- 纯 Python，无需编译工具链（除非装 `ann` 组）
- 外部服务：默认不需要

---

## 6. 上传前最后核对

```bash
# 1) 工作区与历史都不能有本机路径
P="$HOME"                      # 用变量拼路径，避免本仓自身含绝对路径字面量
git grep -n "$P" -- . || echo "OK 工作区"
for c in $(git rev-list --all); do git grep -n "$P" $c -- . 2>/dev/null; done | wc -l   # 应为 0

# 2) 不能有明文 key
git grep -nE "sk-[A-Za-z0-9]{20,}" -- . || echo "OK 工作区"
for c in $(git rev-list --all); do git grep -nE "sk-[A-Za-z0-9]{20,}" $c -- . 2>/dev/null; done | wc -l   # 应为 0

# 3) 不能跟踪 .env 与大产物
git ls-files | grep -E "^\.env$|index\.json|\.hnsw$|catalog\.snapshot" || echo "OK 无敏感/大文件"

# 4) 测试
pytest -q     # 43 passed

# 5) CLI 可用
cortex --help
```

---

## 7. 首次推送

```bash
cd ~/Desktop/cortex

# 创建远端后推送（本仓远端为 https://github.com/handar-code/Cortex.git）
git remote add origin https://github.com/handar-code/Cortex.git
git push -u origin main
```

推送后建议在 Settings 开启：

- **Branch protection**：`main` 要求 PR + CI 全绿才可合并
- **Discussions**：用于设计提案讨论
- **Pages**（可选）：若用 MkDocs 产物发布文档站
