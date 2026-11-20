# 安全政策

## 支持的版本

| 版本 | 是否接受安全报告 |
|---|---|
| `main`（当前开发分支） | ✅ |
| 历史 tag / 归档镜像仓 | ❌ |

## 报告漏洞

如果你发现安全漏洞，请**不要**在公开 Issue 里提交。

通过 GitHub 私有漏洞报告入口提交：
[Security → Report a vulnerability](https://github.com/handar-code/Cortex/security/advisories/new)

请尽量包含：

- 漏洞类型与影响范围
- 复现步骤（最好附最小示例）
- 你认为合适的修复方向（可选）

我们会在收到后尽快确认并跟进。在修复发布前，请暂不公开细节。

## 密钥处理原则

本项目遵循**零密钥入库**：

- `.env`、`*.local.yaml` 均在 `.gitignore` 中排除
- 仓库内只保留 `.env.example` 占位模板，不含任何真实值
- CI 强制 offline 模式（`tests/conftest.py`），无 key 也能完整验证
- 发布前的安全核查清单见 [`docs/RELEASING.md`](docs/RELEASING.md)

如果你在仓库历史中发现了疑似泄露的密钥，请立即通过上面的私有入口报告，
不要提交公开 Issue 或 PR。

## 关于 API key 的配置

本仓库的 `.env.example` 仅提供占位变量，不含任何真实值或配置说明。
具体环境的 key 配置属于个性化设置，不在公开仓库范围内。
