"""AGENTS.md 入口生成 + 零人幂等首次接手协议（设计稿 §13.7）。

AGENTS.md 是**唯一入口**（与 §14.7 宪法自举同源）：新 Agent 读到它即可知道如何接手本项目，
无需人类手把手教。首次接手协议保证**幂等**：重复触发不会重复建树/重复写入。
"""
from __future__ import annotations

from pathlib import Path

from ..model import Catalog


def gen_agents_md(
    catalog: Catalog,
    skill_rel: str = ".doc_system/skill/SKILL.md",
    llms_rel: str = ".doc_system/llms/llms.txt",
) -> str:
    return f"""# AGENTS.md — 项目接手指南（自动生成）

本文件由通用项目知识库文档系统生成，是 Agent 接手本项目的**唯一入口**。

## 如何接手（零人幂等首次接手协议）
1. 读本项目知识库的读指南 Skill：`{skill_rel}`（仅读，无写能力）。
2. 读 llms.txt 根路由：`{llms_rel}`，按需拉取分片或全量。
3. 需要语义检索时，用三件套索引（见 Skill 指南）。
4. 若发现代码有效/无效判定、域归属需要更正 → 经 **MCP 写通道** 提议候选（建议权），
   由 CI 校验门 + 异构 jury 决定发布（你没有直接发布权）。
5. 重复触发以上步骤是**幂等**的：已发布的 catalog 不会被重建，只有漂移才会触发自动对账。

## 约定
- 本系统通用、与本项目解耦；结构/语言/触发均来自配置文件。
- 读侧永不被阻断；写侧受质量门禁与分权约束（见设计稿）。
"""


def write_agents_md(catalog: Catalog, out_dir: str | Path, **kw) -> str:
    out = Path(out_dir) / "AGENTS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = gen_agents_md(catalog, **kw)
    out.write_text(text, encoding="utf-8")
    return str(out)
