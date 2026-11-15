"""纯文本 / Markdown 提取器（方案乙）。

Markdown：按标题切片为 section 符号；每个 section 携带其**正文全文**到
`content` / `annotation_text`，使「MD 全文 = 对代码的叙述」可被向量 + 文本双通道检索；
模块级 symbol 携带整篇全文；并在 fenced 代码块与 HTML 注释中抽取「注释」作为独立
`comment` 符号（即「MD 也要全量搜索 + 加上注释」的落地）。

纯文本(.txt)：单模块符号，`content` = 全文。

用于 wiki / reports / 文档类源（无 AST 的语言/格式）。
"""
from __future__ import annotations

import hashlib
import re

from ..model import Symbol
from .base import Extractor

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_DOMAIN_RE = re.compile(r"@domain\s+([\w./\-]+)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)(.*)$")
_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)

# fenced 代码块语言 → 注释风格（用于抽取代码注释为独立 comment 符号）
_CODE_COMMENT_STYLE: dict[str, str] = {
    "py": "#", "python": "#", "sh": "#", "bash": "#", "shell": "#",
    "yaml": "#", "yml": "#", "toml": "#", "dockerfile": "#", "makefile": "#",
    "r": "#", "jq": "#",
    "js": "//", "javascript": "//", "ts": "//", "typescript": "//",
    "java": "//", "c": "//", "cpp": "//", "c++": "//", "c#": "//", "cs": "//",
    "go": "//", "rust": "//", "rs": "//", "swift": "//", "kotlin": "//",
    "scala": "//", "jsonc": "//",
    "sql": "--",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _preview(text: str, n: int = 300) -> str:
    """取首段预览（限长），用于渲染/显示，避免整篇 dump。"""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = paras[0] if paras else text.strip()
    return body[:n]


class PlainTextExtractor(Extractor):
    @property
    def kinds(self) -> list[str]:
        return ["module", "section", "comment"]

    def extract(
        self,
        rel_path: str,
        text: str,
        validity: str,
        reason: str,
    ) -> list[Symbol]:
        ext = "." + rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
        if ext == ".md":
            return self._extract_markdown(rel_path, text, validity, reason)
        return self._extract_plain(rel_path, text, validity, reason)

    # ----------------------------- 纯文本 -----------------------------
    def _extract_plain(self, rel_path, text, validity, reason) -> list[Symbol]:
        return [Symbol(
            name=rel_path,
            kind="module",
            domain=None,
            docstring=_preview(text),
            signature="",
            source_ref=f"{rel_path}:1",
            content_hash=_sha(text),
            validity=validity,
            reason=reason,
            annotation=None,                  # 纯文本无 @domain 显式键 → 靠隐式轨/结构域
            annotation_text=text.strip(),     # §13.4 注释文本：全文 note（独立并行通道）
            content=text,                     # 全文：向量 + 文本通道检索语料
        )]

    # ----------------------------- Markdown -----------------------------
    def _extract_markdown(self, rel_path, text, validity, reason) -> list[Symbol]:
        symbols: list[Symbol] = []
        lines = text.split("\n")  # 0-based index → 行号 = i+1
        n = len(lines)

        # 1) HTML 注释（全局，可能跨行）→ comment 符号；记录行范围避免与代码注释重复计
        html_consumed: set[int] = set()
        for m in _HTML_COMMENT_RE.finditer(text):
            start = text.count("\n", 0, m.start()) + 1
            end = text.count("\n", 0, m.end()) + 1
            for ln in range(start, end + 1):
                html_consumed.add(ln)
            symbols.append(self._comment_sym(
                rel_path, m.group(1).strip(), start, validity, reason))

        # 2) fenced 代码块范围 + 语言（用于选注释抽取风格）
        fences: list[tuple[int, int, str]] = []
        in_code = False
        code_start = 0
        code_lang = ""
        for i, line in enumerate(lines):
            fm = _FENCE_RE.match(line)
            if fm:
                if not in_code:
                    in_code = True
                    code_start = i + 1
                    code_lang = fm.group(2).strip().lower()
                else:
                    fences.append((code_start, i + 1, code_lang))
                    in_code = False
        if in_code:  # 文件末尾未闭合
            fences.append((code_start, n, code_lang))
        code_ranges = [(s, e) for s, e, _ in fences]

        def in_code_line(ln: int) -> bool:
            return any(s <= ln <= e for s, e in code_ranges)

        # 3) 抽取代码注释（按 fenced 块语言选风格）
        for s, e, lang in fences:
            style = _CODE_COMMENT_STYLE.get(lang)
            if style is None:
                continue
            if style == "#":
                self._collect_line_comments(lines, s, e, "#", html_consumed,
                                            symbols, rel_path, validity, reason)
            elif style == "//":
                self._collect_c_style_comments(lines, s, e, html_consumed,
                                               symbols, rel_path, validity, reason)
            elif style == "--":
                self._collect_line_comments(lines, s, e, "--", html_consumed,
                                            symbols, rel_path, validity, reason)

        # 4) 模块级全文符号（整篇全文）
        symbols.insert(0, Symbol(
            name=rel_path,
            kind="module",
            domain=None,
            docstring=_preview(text),
            signature="",
            source_ref=f"{rel_path}:1",
            content_hash=_sha(text),
            validity=validity,
            reason=reason,
            annotation=None,
            annotation_text=text.strip(),     # §13.4 注释文本：全文 note
            content=text,                     # 全文：向量 + 文本通道检索语料
        ))

        # 5) 按标题切片 section；body = 直到下一个标题之间的正文（含 prose 与代码）
        cur_title: str | None = None
        cur_line = 0
        body_lines: list[str] = []

        def flush_section() -> None:
            nonlocal cur_title, cur_line, body_lines
            if cur_title is None:
                return
            body = "\n".join(body_lines).strip()
            dm = _DOMAIN_RE.search(cur_title)
            sec_dom = dm.group(1) if dm else None
            symbols.append(Symbol(
                name=cur_title,
                kind="section",
                domain=sec_dom,
                docstring=_preview(body, 200),
                signature="",
                source_ref=f"{rel_path}:{cur_line}",
                content_hash=_sha(body),
                validity=validity,
                reason=reason,
                annotation=sec_dom,           # §13.4 注释：标题里的 @domain 显式键
                annotation_text=body,          # §13.4 注释文本：该节正文全文
                content=body,                 # 该节正文全文：向量 + 文本通道检索语料
            ))
            cur_title = None
            body_lines = []

        for i, line in enumerate(lines):
            ln = i + 1
            # 代码 / HTML 注释行不计入 section 正文（已作为 comment 抽出）
            if ln in html_consumed or in_code_line(ln):
                continue
            hm = _HEADING_RE.match(line)
            if hm:
                flush_section()
                cur_title = hm.group(2).strip()
                cur_line = ln
                body_lines = []
            elif cur_title is not None:
                body_lines.append(line)
        flush_section()
        return symbols

    # ----------------------------- 注释抽取工具 -----------------------------
    def _comment_sym(self, rel_path, ctext, line, validity, reason) -> Symbol:
        return Symbol(
            name=f"{rel_path}#comment",
            kind="comment",
            domain=None,
            docstring=ctext,
            signature="",
            source_ref=f"{rel_path}:{line}",
            content_hash=_sha(ctext),
            validity=validity,
            reason=reason,
            annotation=None,
            annotation_text=ctext,
            content=ctext,                    # 注释全文：向量 + 文本通道检索语料
        )

    def _flush_run(self, run, start, symbols, rel_path, validity, reason) -> None:
        ctext = "\n".join(run).strip()
        if ctext:
            symbols.append(self._comment_sym(rel_path, ctext, start, validity, reason))

    def _collect_line_comments(self, lines, s, e, prefix, consumed,
                               symbols, rel_path, validity, reason) -> None:
        """收集 `#` / `--` 风格的连续注释行，每段合并为一个 comment 符号。"""
        run: list[str] = []
        run_start = 0
        for i in range(s - 1, e):
            ln = i + 1
            if ln in consumed:
                if run:
                    self._flush_run(run, run_start, symbols, rel_path, validity, reason)
                    run = []
                continue
            stripped = lines[i].strip()
            if stripped.startswith(prefix):
                if not run:
                    run_start = ln
                run.append(stripped[len(prefix):].strip())
            elif run:
                self._flush_run(run, run_start, symbols, rel_path, validity, reason)
                run = []
        if run:
            self._flush_run(run, run_start, symbols, rel_path, validity, reason)

    def _collect_c_style_comments(self, lines, s, e, consumed,
                                  symbols, rel_path, validity, reason) -> None:
        """抽取 `//` 行注释与 `/* ... */` 块注释。"""
        i = s - 1
        while i < e:
            ln = i + 1
            if ln in consumed:
                i += 1
                continue
            stripped = lines[i].strip()
            if stripped.startswith("//"):
                run = [stripped[2:].strip()]
                j = i + 1
                while j < e:
                    jln = j + 1
                    if jln in consumed:
                        break
                    sj = lines[j].strip()
                    if sj.startswith("//"):
                        run.append(sj[2:].strip())
                        j += 1
                    else:
                        break
                if "\n".join(run).strip():
                    symbols.append(self._comment_sym(rel_path, "\n".join(run).strip(), ln, validity, reason))
                i = j
                continue
            if "/*" in stripped:
                buf = [stripped[stripped.index("/*") + 2:]]
                closed = "*/" in stripped
                if closed:
                    buf[0] = buf[0].split("*/", 1)[0]
                k = i + 1
                while k < e and not closed:
                    kln = k + 1
                    if kln in consumed:
                        break
                    seg = lines[k]
                    if "*/" in seg:
                        buf.append(seg.split("*/", 1)[0])
                        closed = True
                        break
                    buf.append(seg)
                    k += 1
                ctext = "\n".join(b.strip() for b in buf).strip()
                if ctext:
                    symbols.append(self._comment_sym(rel_path, ctext, ln, validity, reason))
                i = (k if closed else e)
                continue
            i += 1
