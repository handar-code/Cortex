# RELEASING.md — 发布安全清单

本清单用于 Cortex 文档系统（pip 包 `doc-system`，命令 `cortex`）发布前的安全与一致性核查。
**每次发布前必须逐条确认，任何一项未通过都不得发布。**

> **仓库口径（单仓）**：**本仓库就是 Cortex 唯一主仓**（通用项目知识库文档系统），开发即发布。
> 早期曾存在一个 `doc-system` 代码副本（克隆时丢失了设计文档）与一个「含真实密钥的本地私有副本」，
> 前者已合并其全部领先项后降级为历史来源、后者永不 push，均不再维护、不再双写、不作为发布源。
> 详见根目录 `ARCHIVED.md`。收敛的目的：消除多仓并行维护导致的源码漂移。

---

## 1. 密钥安全（最重要）

> ⚠️ 这是发布流程中优先级最高、最不可妥协的一条。

- `cortex-private` 仓库中保存着**真实**的 `SILICONFLOW_API_KEY`，且该密钥**已经写入 git 历史**。
- **永不**将 `cortex-private` 的内容原样 `push` 到任何公开仓库；**永不**把该仓的 `.git` 合并进本仓库。
- `.env` 已被本仓库 `.gitignore` 排除；`config.yaml` / `config.example.yaml` / `config.quickstart.yaml`
  只放 `api_key_env` 字段名，不放明文 key。发布前用下面的命令复核：

  ```bash
  # 工作区与历史都不得出现明文 key（应只命中 api_key_env 这类字段名引用）
  git grep -n -i "sk-\|SILICONFLOW_API_KEY=" -- . ':!docs/RELEASING.md' || echo "OK: 无明文 key"
  git log -p | grep -iE "sk-[A-Za-z0-9]{20,}" && echo "!! 历史含疑似明文 key" || echo "OK: 历史干净"
  ```

- 如果曾经把 `cortex-private` 的内容（哪怕是部分）误合并进本仓库，必须先用 `git filter-repo` 或 BFG
  将密钥及其历史彻底抹除，再执行发布：

  ```bash
  git filter-repo --replace-text <(echo 'SILICONFLOW_API_KEY==>***REDACTED***')
  # 或 bfg --replace-text secrets.txt repo.git
  ```

- 如密钥疑似已泄漏，立即在 SiliconFlow 后台**轮换（revoke + regenerate）**该 Key。

---

## 2. 发布源

- **只发布本仓库**（单仓）。不再有「同步到镜像」这一步。
- **绝不**包含 `cortex-runs/` 目录：含大索引文件（如 `index.json` 约 **323MB**）。
- **绝不**包含 `.env` / `.docstore/` / `.doc_system/` / `catalog.snapshot.json` / `_*.json` / `__pycache__/`
  （均已在 `.gitignore` 中排除）。
- 发布产物应仅包含源码、配置示例、文档、测试与 CI 配置，确认 `.gitignore` 生效后再打 tag。

---

## 3. 源码一致性（单仓，无需跨仓同步）

- 本仓库自身即为唯一源码来源，发布前确认工作区干净、无未提交改动：

  ```bash
  git status --short          # 应为空
  git log --oneline -5        # 确认 HEAD 含预期改动
  ```

- 确认以下能力都在本仓库内（历史上曾因旧快照缺失而漏发）：
  - `src/doc_system/index.py` 含 `VectorBackend` / `FlatBackend` / `HnswBackend`、`ann_provider`
  - `src/doc_system/rebuild_hnsw.py`
  - `scripts/verify_hnsw_load.py`
  - `.github/workflows/ci.yml`、`tests/conftest.py`、`tests/test_integration_e2e.py`
  - `pyproject.toml` 含 `[project.scripts]`（`cortex` 入口）与 ann / remote 可选依赖组

---

## 4. 依赖

- 确认 `pyproject.toml` 已声明：核心 `pyyaml`；可选 **ann 组** `hnswlib>=0.8.0` + `numpy`、
  **remote 组** `requests`。
- 用户安装方式：`pip install ".[ann,remote]"`（全量）／`pip install .`（仅 offline 路径）。
- `requires-python = ">=3.10"`，与 CI 矩阵 3.10–3.13 一致。

---

## 5. 验证

在本仓库执行以下验证，**全部通过方可发布**：

```bash
pytest -q                                                    # 43 passed，无需 API key
python -c "from doc_system.index import HnswBackend"         # hnsw 后端可导入
cortex --help                                                # CLI 入口可用
cortex ingest --config config.quickstart.yaml --out /tmp/cat.json   # Quickstart 离线跑通
```

- 期望结果：`43 passed`（无 key 也全绿，由 `tests/conftest.py` 强制 offline 保证）；
  `cortex --help` 显示 `usage: cortex`；Quickstart 建出 18 个符号。
- CI：push 到 `main` / 开 PR 时 GitHub Actions 自动跑 Python 3.10–3.13 矩阵，**四个版本全绿**方可发布。
- 若测试未全过、导入失败或 CI 飘红，回到第 3 步排查，不得带病发布。

---

## 6. 发版

- 打版本 tag：`git tag vX.Y.Z`（按语义化版本递增）。
- 在 GitHub 创建对应 **Release**，附变更说明（重点列出新增能力：ANN 后端 / CLI 入口 / CI）。
- `README.md` 已含** 5 分钟 Quickstart**（零配置 offline 全链路），无需再补：

  ```bash
  pip install -e ".[ann]"
  cortex ingest --config config.quickstart.yaml --out catalog.json
  ```

- 发布后再次复核第 1、2 条（密钥与发布源），确认公开仓库中无泄漏。

---

### 发布前最终确认（Checklist）

- [ ] 第 1 条：工作区与 git 历史均无明文 `SILICONFLOW_API_KEY`；`cortex-private` 未 push、未合并
- [ ] 第 2 条：`cortex-runs/`、`.env`、运行时产物未进入发布源
- [ ] 第 3 条：本仓库 `git status` 干净，ANN/hnsw/CLI/CI/集成测试文件齐全
- [ ] 第 4 条：`pyproject.toml` 声明 ann / remote 可选依赖与 `cortex` 入口
- [ ] 第 5 条：`pytest` **43 passed** + `HnswBackend` 可导入 + `cortex --help` 可用 + CI 四版本全绿
- [ ] 第 6 条：已打 tag + GitHub Release，README 含 5 分钟 Quickstart
