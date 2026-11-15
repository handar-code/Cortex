"""零人可观测（设计稿 §13.8 零人可观测三铁律 + 行为金丝雀 + 探针自证）。

- **外部探针 (Probe)**：自动采集 SLI、自动判达标、不达标自动 fail-closed。不依赖 Agent 自评
  （呼应 §14 不自审：被告不能当法官）。
- **异构 jury**：多个异构「评审」对定性问题（helpfulness / 一致性）投票，dissent 强制可见；
  任一反对即否决（放宽标准时的「同一性」约束同源）。
- **行为金丝雀 (Canary)**：新 active 上线后对比基线 SLI，偏离超阈值自动回滚到上一版本。

三铁律：① 自动采集 ② 自动判 ③ 不达标自动 fail-closed/回滚/降级。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..model import Catalog
from ..store import CatalogStore
from .gate import check_gate


# ----------------------------- 外部探针 -----------------------------
@dataclass
class ProbeReport:
    passed: bool
    slis: dict
    budget_remaining: float
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_SLO = {
    "provenance_coverage_min": 0.95,
    "freshness_age_hours_max": 24.0,
    "drift_allowed": True,          # 漂移期允许服务（读侧不破），但记 budget
    "error_budget": 0.05,           # 允许 5% 的 SLI 不达标余量
}


def run_probe(store: CatalogStore, config: Config, slo: dict | None = None) -> ProbeReport:
    slo = slo or DEFAULT_SLO
    active = store.active_catalog()
    failures: list[str] = []
    if active is None:
        return ProbeReport(passed=False, slis={}, budget_remaining=0.0, failures=["无 active catalog"])

    gate = check_gate(active, slo["provenance_coverage_min"])
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(active.generated_at)).total_seconds() / 3600
    drift = store.detect_drift(config)

    slis = {
        "provenance_coverage": round(gate.provenance_coverage, 4),
        "freshness_age_hours": round(age_h, 2),
        "drift_behind": drift.behind,
        "symbol_count": len(active.symbols),
    }
    if gate.provenance_coverage < slo["provenance_coverage_min"]:
        failures.append(f"溯源覆盖率 {gate.provenance_coverage:.2%} < {slo['provenance_coverage_min']:.2%}")
    if age_h > slo["freshness_age_hours_max"]:
        failures.append(f"新鲜度 {age_h:.1f}h > {slo['freshness_age_hours_max']}h")
    if drift.behind and not slo["drift_allowed"]:
        failures.append("存在未提交漂移且配置禁止漂移")

    budget = slo["error_budget"] - (len(failures) * 0.05 if failures else 0.0)
    return ProbeReport(
        passed=not failures,
        slis=slis,
        budget_remaining=round(max(budget, 0.0), 4),
        failures=failures,
    )


# ----------------------------- 异构 jury -----------------------------
@dataclass
class JuryReport:
    question: str
    votes: list[dict]          # [{voter, approve, reason}]
    decision: bool             # 全部赞成才通过；任一反对即否决
    dissents: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class RuleBasedVoter:
    """代表某一视角的确定性评审（离线降级；真实部署可替换为异构 LLM）。"""

    def __init__(self, name: str, predicate):
        self.name = name
        self._pred = predicate

    def vote(self, catalog: Catalog) -> tuple[bool, str]:
        try:
            ok, reason = self._pred(catalog)
            return (ok, f"[{self.name}] {reason}")
        except Exception as e:  # 评审自身异常视为反对（fail-closed）
            return (False, f"[{self.name}] 评审异常: {e}")


def _v_provenance(c: Catalog):
    g = check_gate(c)
    return (g.passed, f"溯源覆盖率 {g.provenance_coverage:.2%}")


def _v_nonempty(c: Catalog):
    valid = [s for s in c.symbols if s.validity == "valid"]
    nonempty = [s for s in valid if s.docstring.strip()]
    cov = (len(nonempty) / len(valid)) if valid else 1.0
    return (cov >= 0.5, f"有效符号文档覆盖率 {cov:.2%}")


def default_jury():
    """默认异构评审群（≥2 异构视角，呼应 §14 结构性不自审）。"""
    return [
        RuleBasedVoter("provenance", _v_provenance),
        RuleBasedVoter("helpfulness", _v_nonempty),
    ]


def jury_decide(question: str, catalog: Catalog, voters: list | None = None) -> JuryReport:
    voters = voters or default_jury()
    votes = [{"voter": v.name, "approve": ok, "reason": r} for v, (ok, r) in
             [(v, v.vote(catalog)) for v in voters]]
    dissents = [v["reason"] for v in votes if not v["approve"]]
    return JuryReport(question=question, votes=votes, decision=not dissents, dissents=dissents)


# ----------------------------- 行为金丝雀 -----------------------------
@dataclass
class CanaryReport:
    action: str               # "ok" | "rollback"
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def canary_check(
    store: CatalogStore, config: Config, baseline_sli: dict, deviation: float = 0.1
) -> CanaryReport:
    """新 active 上线后对比基线 SLI；溯源覆盖率偏离超阈值 → 自动回滚到上一版本。"""
    active = store.active_catalog()
    if active is None:
        return CanaryReport("ok", "no active")
    gate = check_gate(active)
    cur = gate.provenance_coverage
    base = baseline_sli.get("provenance_coverage", 1.0)
    if abs(cur - base) > deviation:
        # 回滚到 HEAD 前一版本（即发布前版本）
        history = store.history()
        if len(history) >= 2:
            prev = history[-2].version_id
            store.rollback(prev)
            return CanaryReport("rollback", f"覆盖率偏离 {base:.2%}→{cur:.2%} 超阈值 {deviation}，回滚到 {prev}")
        return CanaryReport("ok", "偏离但无历史可回滚")
    return CanaryReport("ok", f"覆盖率 {cur:.2%} 在基线 {base:.2%} 阈值内")
