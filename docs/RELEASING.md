# RELEASING.md — 发布安全清单

本清单用于 Cortex 文档系统（通用项目知识库）发布前的安全与一致性核查。
**每次发布前必须逐条确认，任何一项未通过都不得发布。**

---

## 1. 密钥安全（最重要）

> ⚠️ 这是发布流程中优先级最高、最不可妥协的一条。

- `cortex-private` 仓库中保存着**真实**的 `SILICONFLOW_API_KEY`，且该密钥**已经写入 git 历史**。
- **永不**将 `cortex-private` 的内容原样 `push` 到任何公开仓库。
- 如果曾经把 `cortex-private` 的内容（哪怕是部分）误合并到 `cortex`，**必须先用 `git filter-repo` 或 BFG** 将密钥及其历史从 `cortex` 中彻底抹除，再执行发布：

  ```bash
  # 使用 git-filter-repo 抹除密钥历史（示例）
  git filter-repo --replace-text <(echo 'SILICONFLOW_API_KEY==>***REDACTED***')

  # 或使用 BFG Repo-Cleaner
  bfg --replace-text secrets.txt cortex.git
  ```

- 发布后用 `git log -p | grep -i siliconflow` 复核，确保历史中已无明文密钥。
- 如密钥疑似已泄漏，立即在 SiliconFlow 后台**轮换（revoke + regenerate）**该 Key。

---

## 2. 发布源

- **只发布 `cortex` 干净仓库**（已核查无密钥泄漏）。
- **绝不**包含 `cortex-runs/` 目录：其中包含大索引文件（如 `index.json` 约 **323MB**），不应进入发布版本。
- **绝不**包含 `.env` 文件（含本地配置与密钥）。
- 发布产物应仅包含源码、配置示例、文档与测试，确认 `.gitignore` 已正确排除上述文件后再打 tag。

---

## 3. 同步源码

- 发布前必须把 `doc-system` 仓库的**最新代码**同步到 `cortex`，包括：
  - ANN / hnsw 后端实现
  - `rebuild_hnsw.py`
  - `verify_hnsw_load.py`
- `cortex` 仓库中现存的旧快照（约 **13:21** 版本）缺少上述文件；若直接基于旧快照发布，发布的将是**旧版**，缺少 ANN/hnsw 能力。
- 同步完成后，需在 `cortex` 仓库中确认这些文件确实存在，再进入后续验证步骤。

---

## 4. 依赖

- 发布前确认 `pyproject.toml` 已正确声明以下**可选依赖**：
  - **ann 组**：`hnswlib`、`numpy`
  - **remote 组**：`requests`
- 确保可选依赖分组（如 `[project.optional-dependencies]`）写法正确，用户可通过
  `pip install ".[ann,remote]"` 安装完整能力。

---

## 5. 验证

在 `cortex` 仓库中执行以下验证，**全部通过方可发布**：

```bash
# 测试必须 36 passed
PYTHONPATH=src python -m pytest tests/ -q

# HnswBackend 必须可正常导入
PYTHONPATH=src python -c "from doc_system.index import HnswBackend"
```

- 期望结果：`pytest` 输出 `36 passed`；`import HnswBackend` 无报错。
- 若测试未全过或导入失败，回到第 3 步确认源码已正确同步，不得带病发布。

---

## 6. 发版

- 打版本 tag：`git tag vX.Y.Z`（X.Y.Z 按语义化版本递增）。
- 在 GitHub 创建对应的 **Release**，附变更说明。
- 确保 `README.md` 包含** 5 分钟 Quickstart**，让用户可快速上手：

  ```bash
  pip install ".[ann,remote]"
  # 按 README 的 Quickstart 初始化并启动
  ```

- 发布后再次复核第 1、2 条（密钥与发布源），确认公开仓库中无泄漏。

---

### 发布前最终确认（Checklist）

- [ ] 第 1 条：密钥历史已清除，无明文 `SILICONFLOW_API_KEY`
- [ ] 第 2 条：`cortex-runs/` 与 `.env` 未进入发布源
- [ ] 第 3 条：`doc-system` 最新源码（含 ANN/hnsw 后端）已同步至 `cortex`
- [ ] 第 4 条：`pyproject.toml` 声明 ann / remote 可选依赖
- [ ] 第 5 条：`pytest` 36 passed 且 `HnswBackend` 可导入
- [ ] 第 6 条：已打 tag + GitHub Release，README 含 5 分钟 Quickstart
