# 本地配置指南：从零 key 到真语义

Cortex 的全部能力都在**这一个仓库**里，不需要第二份「私有副本」。
区别只在于环境变量填什么 —— 而环境变量**不入库**。

这份指南说明如何在本机启用真语义向量（Tier1），以及为什么这样做是安全的。

---

## 为什么不需要第二个仓库

早期项目曾有一个 `cortex-private` 副本用来存放真实 API key。实践发现两个问题：

1. **源码漂移** —— 两份仓库各自演进，修 bug 要改两遍，稍不留神就缺文件。
2. **泄露风险** —— key 放在另一个 git 仓里，一旦误 push 就是公开事故。

现在的做法：**唯一主仓 + 本地环境变量**。代码只有一份，隐私只在 `.env` 里。

---

## 档位选择

| 档 | provider | 是否需要 key | 适用 |
|---|---|---|---|
| Tier0 `offline` | 确定性哈希 | 否 | 5 分钟 Quickstart、CI、断网、可复现验证 |
| Tier1 `siliconflow` | OpenAI 兼容接口 | 是（或本地服务） | 生产建档、真语义检索、跨语言同义 |

**绝大多数验证在 Tier0 完成即可**，全量 60 个测试在 Tier0 下无 key 全绿。
只有当你需要真语义质量时，才升级到 Tier1。

---

## 升级到 Tier1（云端）

### 1. 建 `.env`

```bash
cp .env.example .env
```

编辑 `.env`，填入你自己的 key：

```bash
SILICONFLOW_API_KEY=sk-你的真实key
```

`.env` 已被 `.gitignore` 排除，**不会出现在 `git status` 里，更不会被 push**。

### 2. 用 API 配置跑

`config.api.yaml` 就是为 Tier1 准备的模板，key 从 `.env` 读取，配置文件本身不含任何密钥：

```bash
pip install -e .

cortex ingest    --config config.api.yaml --out catalog.json
cortex store-init --config config.api.yaml --store .docstore
cortex recompute  --config config.api.yaml --store .docstore --reason init
cortex ci-publish --config config.api.yaml --store .docstore --reason release
cortex index      --config config.api.yaml --store .docstore --out .doc_system/index/index.json
cortex retrieve   --config config.api.yaml --store .docstore --query "user login" --k 3
```

---

## 升级到 Tier1（本地模型服务）

Ollama / llama.cpp / LM Studio / vLLM 暴露的都是同一个 OpenAI 兼容接口，
所以**不需要新代码** —— 只改 `config.api.yaml` 的 3 行：

```yaml
embedding:
  api_base: "http://localhost:11434/v1/embeddings"   # Ollama 默认端口
  model:    "bge-m3"                                  # 保持 1024 维
  # key 填任意占位值
```

`.env` 里写：

```bash
SILICONFLOW_API_KEY=local
```

> 隐式归因轨（`attribution`）会跟随同一份 `api_base`，不会出现
> 「主向量走本地、隐式轨偷偷打云端」的半套配置。

---

## 安全边界

仓库里有几道明确的防线：

- `.env` / `*.local.yaml` / `_*.json` / `.docstore/` / `.doc_system/` 均在 `.gitignore`
- `.env.example` 只有占位符，可安全入库
- CI 强制 offline（见 `tests/conftest.py`），无 key 也能全绿，不会因为缺 key 而失败
- 提交前自检命令见下方

### 提交前自检

```bash
# 确认 .env 没被跟踪
git ls-files | grep -E '(^|/)\.env$' && echo "危险：.env 被跟踪了！" || echo "安全：.env 未被跟踪"

# 确认工作区没有意外文件
git status --short
```

---

## 相关文档

- [5 分钟 Quickstart](../README.md#5-分钟-quickstart零配置--无-api-key--不联网) —— Tier0 全流程
- [发布安全清单](RELEASING.md) —— 防泄露检查项
- [仓库关系](ARCHIVED.md) —— 为什么收敛为单仓
