# 本仓库已归档（ARCHIVED）

> ⚠️ **不要再在本仓库提交代码或作为发布源。** 唯一权威仓是 `<REPO_ROOT>`。

## 为什么归档

早期为了避免把含 API key 的 `cortex-private` 直接发布，曾把本仓库作为「干净发布镜像」，
由 `doc-system` 同步源码过来后再发。实践中**多仓并行维护导致源码持续漂移**（旧快照缺 ANN 后端、
CI 与集成测试要靠 worker 分别补两遍），维护成本高于收益。

现已收敛为**单仓**：`doc-system` 自身即干净发布源——`.env` / `.docstore/` / 运行时产物
均已由 `.gitignore` 排除，git 历史经核查无明文 `SILICONFLOW_API_KEY`（见权威仓
`docs/RELEASING.md` 第 1 条的复核命令）。因此不再需要镜像仓。

## 归档状态

- 本仓库保留作为历史快照，**不删除目录、不合并 git 历史、不再双写**。
- 归档时状态：`41 passed`（offline 无 key）、含 CI + 集成测试、与权威仓 `src/` 一致。
- 权威仓此后新增的能力（`cortex` CLI 入口、`config.quickstart.yaml`、README 5 分钟 Quickstart 重写、
  单仓发布清单等）**不会再同步到本仓**，请以权威仓为准。

## 去哪里

| 需求 | 位置 |
|---|---|
| 源码 / 提交 / PR / CI | `<REPO_ROOT>` |
| 5 分钟 Quickstart | 权威仓 `README.md` |
| 发布安全清单 | 权威仓 `docs/RELEASING.md` |
| 操作总纲（9 类） | `~/.workbuddy/skills/cortex-onboard/OPERATIONS.md` |

归档日期：2026-11-15
