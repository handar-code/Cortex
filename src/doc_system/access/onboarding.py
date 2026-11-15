"""零人幂等首次接手协议（设计稿 §13.7 首次接手流程）。

保证**幂等**：重复触发不会重复建树/重复写入；只有漂移才会触发自动对账。
输出一个只读步骤序列，供新 Agent 按顺序接手。
"""
from __future__ import annotations

from pathlib import Path

from ..model import Catalog
from ..store import CatalogStore


def first_onboarding_sequence(
    catalog: Catalog, store: CatalogStore | None, out_dir: str | Path
) -> dict:
    """返回接手步骤序列；幂等由调用方保证（重复触发不产生副作用）。"""
    out = Path(out_dir)
    steps = [
        {"step": 1, "action": "read", "target": str(out / "AGENTS.md"),
         "desc": "唯一入口：项目接手指南"},
        {"step": 2, "action": "read", "target": str(out / "llms" / "llms.txt"),
         "desc": "llms.txt 根路由，按需拉分片/全量"},
        {"step": 3, "action": "read", "target": str(out / "skill" / "SKILL.md"),
         "desc": "读指南 Skill（零写能力）"},
        {"step": 4, "action": "read", "target": str(out / "index" / "index.json"),
         "desc": "三件套索引（向量/注释/注释文本 + 近邻）"},
        {"step": 5, "action": "optional-write", "target": "MCP 写通道",
         "desc": "发现需更正 → 经 MCP 提议候选（建议权），CI 决定发布"},
    ]
    return {
        "idempotent": True,
        "catalog_project": catalog.project,
        "active_version": store._read_active() if store else None,
        "steps": steps,
    }
