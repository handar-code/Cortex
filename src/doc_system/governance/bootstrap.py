"""宪法 AI 自举（设计稿 §14.7）。

零人态下不可变核心**不由人工撰写**，而是从目标项目既有 AGENTS.md/设计原则**自动抽取**
为第一版核心（宪法起点 = AI 自举）。后续修订走 typed amendment，须经异构 jury 批准
（呼应 §14 结构性不自审：提议者≠审核者）。

宪法本身是「可被 AI 接手系统」的最高约束层；本模块产出其第一版并支持受控修订。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Constitution:
    version: int
    source: str                      # 自举来源（如 "AGENTS.md@<path>"）
    bootstrap_method: str           # "ai-self-bootstrap"
    clauses: list[str] = field(default_factory=list)
    amended_by_jury: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json_dumps(self.to_dict()), encoding="utf-8")


def json_dumps(d) -> str:
    import json
    return json.dumps(d, ensure_ascii=False, indent=2)


def bootstrap_from_agents_md(path: str | Path) -> Constitution:
    """从目标项目 AGENTS.md 抽取第一版核心条款（AI 自举，零人）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"自举源不存在: {path}")
    text = p.read_text(encoding="utf-8")
    clauses: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # 抽取：标题行、列表项、或带编号的原则行
        if re.match(r"^#{1,3}\s+\S", s):
            clauses.append(s.lstrip("#").strip())
        elif re.match(r"^[-*]\s+\S", s):
            clauses.append(s.lstrip("-*").strip())
        elif re.match(r"^\d+[\.、]\s+\S", s):
            clauses.append(re.sub(r"^\d+[\.、]\s+", "", s))
    return Constitution(
        version=1,
        source=f"AGENTS.md@{p}",
        bootstrap_method="ai-self-bootstrap",
        clauses=clauses or ["（AGENTS.md 未提取出显式条款；核心为空，待后续 amendment 补充）"],
    )


def amend(
    constitution: Constitution,
    new_clauses: list[str],
    jury_decision: bool,
    voter_notes: list[str] | None = None,
) -> Constitution:
    """受控修订：须经异构 jury 批准（jury_decision=True）方可生效。"""
    if not jury_decision:
        raise PermissionError("修订被 jury 否决（§14 不自审：提议者≠审核者），宪法保持原版。")
    merged = list(constitution.clauses) + new_clauses
    return Constitution(
        version=constitution.version + 1,
        source=constitution.source,
        bootstrap_method=constitution.bootstrap_method,
        clauses=merged,
        amended_by_jury=True,
    )
