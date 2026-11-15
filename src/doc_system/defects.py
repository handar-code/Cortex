"""隐式契约缺陷检测（Defect Detection · 补 Cortex 的能力边界）。

**为什么有这个模块**：Cortex 原本能帮 Agent 快速定位代码，但**发现不了 BUG**——
静态依赖图告诉我"谁调用了谁"，却不告诉我"这个契约被违反了"。这正是
"文档系统做完，短期使用者是你"时最常撞上的墙。

本模块提供**静态可判定的缺陷类别**：不依赖运行代码，只依赖 AST 与命名空间
比对，就能确定"这里一定会炸"。

已实现的检查器：

``check_reverse_attribute_contract``
    **反向读模块属性**（reverse attribute contract）。
    场景：模块 A 通过 ``_ra()`` / ``importlib.import_module`` 反向读取模块 B
    的属性。B 的属性若缺失，A 在**没有 try/except 保护**的位置会抛
    ``AttributeError``。

    真实案例（Hermes）：``agent/system_prompt.py`` 的 ``_render_soul()`` 通过
    ``_ra().load_soul_prompt_md(...)`` 取 SOUL 内容，而该符号在
    ``agent/prompt_builder.py`` 有实现却从未被 re-export 到 ``run_agent``。
    该调用位于函数 try/except 之外 → 默认配置（``skip_context_files=False``）
    必然触发 → 整个 system-prompt 渲染终止。

设计原则：
- **只报确定性的缺陷**。凡是"可能有问题"的都不报——那是 LLM 的活，不是静态工具的。
- **零第三方依赖**，纯标准库 ast/pathlib/importlib。
- **可复用**：检查器只依赖 Python 语义，不绑定任何具体项目。
"""
from __future__ import annotations

import ast
import importlib
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Defect:
    """一个确证的静态缺陷。"""

    kind: str                    # 缺陷类别标识
    severity: str                # "error" | "warning"
    symbol: str                  # 出问题的符号名
    location: str                # "path:lineno"
    message: str
    hint: str = ""               # 修复建议

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanReport:
    """一次扫描的结果。"""

    defects: list[Defect] = field(default_factory=list)
    files_scanned: int = 0
    files_failed: int = 0         # 解析失败的 .py 文件数
    symbols_checked: int = 0

    @property
    def passed(self) -> bool:
        return not any(d.severity == "error" for d in self.defects)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "files_scanned": self.files_scanned,
            "files_failed": self.files_failed,
            "symbols_checked": self.symbols_checked,
            "defects": [d.to_dict() for d in self.defects],
        }


# ── 反向属性契约 ────────────────────────────────────────────────────────

#: 常见的「载体」命名——这些名字被调用/引用后返回目标模块对象。
#: 可按项目约定扩展。
DEFAULT_CARRIERS = ("_ra", "_r", "_mod", "_module")


def _collect_carrier_aliases(tree: ast.AST, carriers: tuple[str, ...]) -> set[str]:
    """找出「先赋值后访问」形式里的别名变量。

    形如 ``_r = _ra()`` 之后用 ``_r.attr`` 访问。只匹配直接调用形式
    （``_ra().attr``）会漏掉这类——而它恰恰是最常见的写法。
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in carriers
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases.add(target.id)
    return aliases


def _is_carrier_access(node: ast.Attribute, carriers: tuple[str, ...],
                       aliases: set[str]) -> bool:
    """判断一个 ``Attribute`` 节点是否是在读载体模块的属性。"""
    value = node.value
    # 形式 1：_ra().attr
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in carriers
    ):
        return True
    # 形式 2：_r.attr（_r 是载体函数的别名）
    if isinstance(value, ast.Name) and value.id in aliases:
        return True
    # 形式 3：_ra.attr（载体函数本身当对象用）
    if isinstance(value, ast.Name) and value.id in carriers:
        return True
    return False


def _is_guarded(node: ast.Attribute, parents: list[ast.AST]) -> bool:
    """该属性访问是否被 try/except 包住。

    被包住时缺失只会降级而非崩溃 → 降级为 warning。

    注意要检查 try 的 **body**（受保护的那段代码），而不是 handler ——
    `try: x = obj.MISSING except AttributeError: ...` 里，出问题的读取在
    try 主体内。只看 handler 会完全漏判。
    """
    for parent in parents:
        if isinstance(parent, ast.Try):
            for stmt in parent.body:
                if node in ast.walk(stmt):
                    return True
    return False


def check_reverse_attribute_contract(
    root: str | Path,
    target_module: str,
    carriers: tuple[str, ...] = DEFAULT_CARRIERS,
) -> ScanReport:
    """检查「反向读模块属性」的契约是否成立。

    Args:
        root: 扫描根目录（递归所有 .py）。
        target_module: 被反向引用的目标模块名（需可 import）。
        carriers: 载体函数名白名单。

    Returns:
        ScanReport —— ``passed`` 为 False 表示存在 error 级缺陷。
    """
    root = Path(root)
    report = ScanReport()

    if not root.is_dir():
        report.defects.append(Defect(
            kind="scan_root_missing",
            severity="error",
            symbol=target_module,
            location=str(root),
            message=f"扫描根目录不存在: {root}",
        ))
        return report

    try:
        target = importlib.import_module(target_module)
    except ImportError as exc:
        report.defects.append(Defect(
            kind="target_module_import_failed",
            severity="error",
            symbol=target_module,
            location=str(root),
            message=f"无法导入目标模块 {target_module}: {exc}",
        ))
        return report

    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            report.files_failed += 1
            continue

        report.files_scanned += 1
        aliases = _collect_carrier_aliases(tree, carriers)

        # 建立「属性访问 → 是否在 try 内」的映射
        for parent in ast.walk(tree):
            if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            try_ancestors: list[ast.AST] = []
            for stmt in ast.walk(parent):
                if isinstance(stmt, ast.Try):
                    try_ancestors.append(stmt)

            for node in ast.walk(parent):
                if not isinstance(node, ast.Attribute):
                    continue
                if not _is_carrier_access(node, carriers, aliases):
                    continue
                if hasattr(target, node.attr):
                    continue

                report.symbols_checked += 1
                guarded = _is_guarded(node, try_ancestors)
                report.defects.append(Defect(
                    kind="reverse_attribute_contract",
                    severity="warning" if guarded else "error",
                    symbol=node.attr,
                    location=f"{path}:{node.lineno}",
                    message=(
                        f"反向读取 {target_module}.{node.attr} —— 该属性在目标模块上不存在"
                        + ("（读取点被 try/except 覆盖 → 降级为静默失效）" if guarded else
                           "（读取点无异常保护 → 必然抛 AttributeError）")
                    ),
                    hint=(
                        f"在 {target_module} 中 re-export {node.attr}"
                        f"（若其实现位于其他模块，用 `from <impl_module> import {node.attr}` 并加 `# noqa: F401`）"
                    ),
                ))

    return report
