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


# ── 导入契约 ────────────────────────────────────────────────────────────

def _module_name_for(path: Path, root: Path) -> str | None:
    """把 .py 文件路径还原成模块名（含 __init__.py → 包名）。"""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    parts = list(rel.parts)
    if not parts or parts[-1].endswith(".py") is False:
        return None
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


def _exported_symbols(tree: ast.AST) -> set[str]:
    """模块对外暴露的符号集合。

    **必须包含 re-export 的名字** —— 一个模块常见的写法是
    ``from .adapter import QQAdapter  # noqa: F401``，符号不在本模块定义，
    但确实能被外部 import。只看 FunctionDef/ClassDef 会把它误判为缺失。

    这个坑很典型：Hermes 的 ``gateway/platforms/qqbot/__init__.py`` 用相对导入
    重导出 5 个符号，第一版静态扫描器因此报了 141 条假阳性中的一大半。
    """
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                symbols.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
    return symbols


def _resolve_relative(module: str, level: int, package: str) -> str:
    """把 ``from .adapter import X`` 解析成绝对模块名。"""
    if level <= 0:
        return module or ""
    base_parts = package.split(".") if package else []
    up = level - 1
    base = base_parts[:len(base_parts) - up] if up else base_parts
    return ".".join([p for p in base if p] + ([module] if module else []))


def check_import_contract(root: str | Path) -> ScanReport:
    """检查仓库内部的 ``from X import Y`` 是否真的能从 X 拿到 Y。

    与 ``check_reverse_attribute_contract`` 互补：后者查的是「运行时反向读
    模块属性」，这里查的是「导入期就成立的契约」。

    两处刻意的设计，用来压低假阳性：

    1. **解析相对导入**（``node.level > 0``）——按当前文件所属包还原绝对模块名。
    2. **目标模块的符号集合包含 re-export**（见 ``_exported_symbols``）。

    局限：只检查能在本仓库内定位到的模块；外部依赖（第三方包 / 标准库）
    一律跳过，不做推测。
    """
    root = Path(root).resolve()
    report = ScanReport()

    if not root.is_dir():
        report.defects.append(Defect(
            kind="scan_root_missing", severity="error", symbol="",
            location=str(root), message=f"扫描根目录不存在: {root}",
        ))
        return report

    # 索引范围必须覆盖**整个仓库**，而不是只扫 root。
    # 否则 root=hermes_cli 时，它导入的 providers 包不在索引里，却会被
    # 当成"仓库内模块"判成符号缺失 → 大量假阳性。
    # 用 pyproject.toml / .git 向上定位仓库根；找不到就退回 root。
    repo_root = root.resolve()
    for cand in [repo_root, *repo_root.parents]:
        if (cand / "pyproject.toml").is_file() or (cand / ".git").exists():
            repo_root = cand
            break

    # 第一遍：为仓库内每个 .py 建立 模块名 → 导出符号集合
    module_symbols: dict[str, set[str]] = {}
    trees: list[tuple[Path, ast.AST]] = []
    # 索引全仓，但只检查 root 下的文件
    index_paths = list(sorted(repo_root.rglob("*.py")))
    check_paths = set(sorted(root.rglob("*.py")))
    for path in index_paths:
        in_scope = path in check_paths
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            report.files_failed += 1
            continue
        if in_scope:
            report.files_scanned += 1
            trees.append((path, tree))
        mod = _module_name_for(path, repo_root)
        if mod:
            module_symbols.setdefault(mod, set()).update(_exported_symbols(tree))

    # 包的导出符号还应包含它的子模块名：`from pkg import submodule` 是合法的，
    # 即使 __init__.py 里没写 import（Python 会在导入时自动解析子模块）。
    for mod in list(module_symbols):
        pass  # 占位，见下方按路径补齐
    for path in index_paths:
        if path.name == "__init__.py":
            pkg = _module_name_for(path, repo_root)
            if pkg is None:
                continue
            for child in path.parent.iterdir():
                if child.is_file() and child.suffix == ".py" and child.name != "__init__.py":
                    module_symbols.setdefault(pkg, set()).add(child.stem)
                elif child.is_dir() and (child / "__init__.py").is_file():
                    module_symbols.setdefault(pkg, set()).add(child.name)

    # 第二遍：检查每条 ImportFrom
    for path, tree in trees:
        mod = _module_name_for(path, repo_root)
        # from .X 的层级基准是「当前文件所在的包」，不是当前模块名本身。
        # 若写错成模块名，gateway/delivery.py 里的 from .config import X
        # 会被解析成 gateway.delivery.config（不存在）→ 大量假阳性。
        try:
            rel = path.relative_to(repo_root)
            parts = list(rel.parent.parts)
            package = ".".join(p for p in parts if p != "." and p)
        except ValueError:
            package = ""

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level > 0 and not node.module:
                # `from . import X` / `from .. import X` —— X 是子模块名，
                # 目标包就是当前包（按层级上溯），检查该子模块是否存在。
                base_parts = [p for p in package.split(".") if p]
                up = node.level - 1
                base = ".".join(base_parts[:len(base_parts) - up]) if up else ".".join(base_parts)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    child_mod = f"{base}.{alias.name}" if base else alias.name
                    if child_mod not in module_symbols:
                        report.defects.append(Defect(
                            kind="import_contract",
                            severity="error",
                            symbol=alias.name,
                            location=f"{path}:{node.lineno}",
                            message=f"相对导入 from {'.' * node.level} import {alias.name} 在仓库内解析不到子模块 {child_mod}",
                            hint="检查包结构与相对层级是否正确",
                        ))
                continue

            target_mod = _resolve_relative(node.module or "", node.level, package)
            if not target_mod:
                continue
            if target_mod not in module_symbols and node.level == 0:
                continue  # 绝对导入指向外部依赖 → 不推测
            # 相对导入解析不到时，不能简单跳过：它可能正是一个断裂的契约。
            # 用「路径末段」再兜一次（如 .impl → pkg.impl），仍找不到才判定缺失。
            if target_mod not in module_symbols and node.level > 0:
                candidates = [
                    m for m in module_symbols
                    if m.endswith("." + target_mod) or m == target_mod
                ]
                if candidates:
                    target_mod = candidates[0]
                else:
                    report.defects.append(Defect(
                        kind="import_contract",
                        severity="error",
                        symbol=node.module or "",
                        location=f"{path}:{node.lineno}",
                        message=(
                            f"相对导入 .{node.module or ''} 在仓库内解析不到目标模块"
                            "（导入期即 ModuleNotFoundError）"
                        ),
                        hint="检查包结构与相对层级是否正确",
                    ))
                    continue

            available = module_symbols[target_mod]
            for alias in node.names:
                if alias.name == "*":
                    continue
                report.symbols_checked += 1
                if alias.name in available:
                    continue
                report.defects.append(Defect(
                    kind="import_contract",
                    severity="error",
                    symbol=alias.name,
                    location=f"{path}:{node.lineno}",
                    message=(
                        f"从 {target_mod} 导入 {alias.name}，但该模块未定义也未 re-export 它"
                        "（导入期即 ImportError）"
                    ),
                    hint=(
                        f"检查 {target_mod} 是否漏了导出；或该符号本应从别的模块导入"
                    ),
                ))

    return report
