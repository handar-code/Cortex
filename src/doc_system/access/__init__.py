"""接入协议包（设计稿 §13.7 主线七）。

读 = Skill 读指南（零写能力）；写 = MCP git 式提交（非只读签名 + 字段白名单 + 幂等键）。
AGENTS.md 为唯一入口；零人幂等首次接手协议。审计外部化（由 Phase 6 探针写，非 Agent 写）。
"""
from __future__ import annotations
