"""内置启发式甄别（valid/invalid triage）。

设计原则（§13.1）：内置确定性规则**优先**；只有当规则覆盖不到时才升级为 AI 提取。
本模块实现「确定性优先」那一端——文件名/路径模式匹配到即判为 test / generated。
"""
from __future__ import annotations

import fnmatch


def classify(
    rel_path: str,
    test_patterns: list[str],
    generated_patterns: list[str],
) -> tuple[str, str]:
    """返回 (validity, reason)。

    validity ∈ {valid, test, generated, unknown}。
    优先级：test 模式 > generated 模式 > 默认 valid。
    """
    for pat in test_patterns:
        if fnmatch.fnmatch(rel_path, pat):
            return ("test", f"matches test pattern: {pat}")
    for pat in generated_patterns:
        if fnmatch.fnmatch(rel_path, pat):
            return ("generated", f"matches generated pattern: {pat}")
    return ("valid", "no heuristic rule matched")
