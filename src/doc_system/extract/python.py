"""Python 提取器（方案乙）：用标准库 ast 抽取符号。

抽取：module / class / function / method；从 docstring 或源码中识别 `@domain` 显式键；
对每段源码计算内容哈希（供增量重算与漂移检测）。
不执行代码，纯静态解析。
"""
from __future__ import annotations

import ast
import re

from ..model import Symbol, sha256
from .base import Extractor

_DOMAIN_RE = re.compile(r"@domain\s+([\w./\-]+)")


def _detect_domain(docstring: str | None, source: str) -> str | None:
    if docstring:
        m = _DOMAIN_RE.search(docstring)
        if m:
            return m.group(1)
    m = _DOMAIN_RE.search(source)
    return m.group(1) if m else None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = [a.arg for a in args.posonlyargs] + [a.arg for a in args.args]
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    parts += [a.arg for a in args.kwonlyargs]
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _module_name(rel_path: str) -> str:
    p = rel_path.replace("\\", "/")
    if p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".")


def _parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """一次遍历建立 {子节点 id: 父节点}，把判定 method/function 从 O(n²) 降到 O(n)。

    原 `_parent_of` 对每个符号都全树 ast.walk 一遍，大文件（如 846K 的 cli.py
    含 492 个 def）会退化到数十万次节点访问。真实项目根级核心代码必须走这里。
    """
    parent_of: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_of[id(child)] = parent
    return parent_of


class PythonAstExtractor(Extractor):
    @property
    def kinds(self) -> list[str]:
        return ["module", "class", "function", "method"]

    def extract(
        self,
        rel_path: str,
        text: str,
        validity: str,
        reason: str,
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        tree = ast.parse(text)
        module_doc = ast.get_docstring(tree)
        mod_domain = _detect_domain(module_doc, text)
        symbols.append(
            Symbol(
                name=_module_name(rel_path),
                kind="module",
                domain=mod_domain,                 # 归因层裁决前的显式键占位，随后被覆盖
                docstring=module_doc or "",
                signature="",
                source_ref=f"{rel_path}:1",
                content_hash=sha256(text),
                validity=validity,
                reason=reason,
                annotation=mod_domain,             # §13.4 注释：@domain 显式键（显式轨）
                annotation_text=module_doc or "",  # §13.4 注释文本：docstring
            )
        )
        parent_of = _parent_map(tree)
        # ast.get_source_segment 每次调用都会重新 splitlines 整篇源码；大文件里
        # 数百个符号 → O(n·文件行数)（实测 cli.py 独占 2.42s / 85% 耗时）。
        # 这里整篇只切一次，按 lineno/end_lineno 取片段，语义等价但降到 O(文件行数)。
        lines = text.splitlines(keepends=True)

        def segment(node: ast.AST) -> str:
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if not start or not end:
                return ""
            return "".join(lines[start - 1 : end])

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parent = parent_of.get(id(node))
                ds = ast.get_docstring(node)
                src = segment(node)
                if isinstance(node, ast.ClassDef):
                    kind, sig = "class", ""
                else:
                    kind = "method" if isinstance(parent, ast.ClassDef) else "function"
                    sig = _signature(node)
                dom = _detect_domain(ds, src)
                symbols.append(
                    Symbol(
                        name=node.name,
                        kind=kind,
                        domain=dom,                    # 归因层裁决前的显式键占位
                        docstring=ds or "",
                        signature=sig,
                        source_ref=f"{rel_path}:{node.lineno}",
                        content_hash=sha256(src),
                        validity=validity,
                        reason=reason,
                        annotation=dom,                # §13.4 注释：@domain 显式键
                        annotation_text=ds or "",      # §13.4 注释文本：docstring
                    )
                )
        return symbols
