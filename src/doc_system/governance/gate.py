"""写侧质量门禁（设计稿 §13.6 主线六 · Q-A/Q-B）。

核心：**声明级溯源**（每个符号必须可追溯到源：source_ref + content_hash）+ 门禁阈值。
- 读侧不拦截（§13.6 Q-B）：门禁只卡「发布」，不卡「读取」（读取由 catalog active 直接服务）。
- fail-closed：门禁不过 → 禁止发布新版本（由 trigger.ci_publish 在发布前调用本门禁）。

返回可量化的 SLI，供 observability 探针直接消费。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..model import Catalog


@dataclass
class GateReport:
    passed: bool
    provenance_coverage: float      # 有效符号中可追溯比例
    files_scanned: int
    files_valid: int
    symbol_count: int
    threshold: float
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def check_gate(catalog: Catalog, provenance_min: float = 0.95) -> GateReport:
    """写侧质量门禁：声明级溯源覆盖率 >= 阈值才放行发布。

    溯源判定：symbol.validity == 'valid' 且同时具备 source_ref 与 content_hash。
    """
    valid = [s for s in catalog.symbols if s.validity == "valid"]
    traced = [
        s for s in valid
        if s.source_ref and s.content_hash
    ]
    coverage = (len(traced) / len(valid)) if valid else 1.0
    failures: list[str] = []
    if coverage < provenance_min:
        failures.append(
            f"溯源覆盖率 {coverage:.2%} < 阈值 {provenance_min:.2%}（{len(valid)-len(traced)} 个有效符号不可追溯）"
        )
    return GateReport(
        passed=not failures,
        provenance_coverage=coverage,
        files_scanned=catalog.files_scanned,
        files_valid=catalog.files_valid,
        symbol_count=len(catalog.symbols),
        threshold=provenance_min,
        failures=failures,
    )
