"""渲染层（设计稿 §13.3 主线三）。

唯一必需产物 = MkDocs 主站（人 HTML + Agent 可读 MD / llms.txt）。
llms.txt 按 **scope → module 树分片**：根路由 llms.txt + 各域分片 llms-<domain>.txt
+ 全量兜底 llms-full.txt。附加产物（typedoc 站 / pagefind / demo）走 config 开关，默认关。

关键设计（§13.3 Q4 已定）：渲染吃 **catalog 快照**，绝不「渲染时现提」——
本模块输入就是已物化的 Catalog，渲染只是把它投影成可读形态。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .model import Catalog, Symbol

DOMAIN_UNCATEGORIZED = "_uncategorized"


def _domain_of(sym: Symbol) -> str:
    return sym.domain or DOMAIN_UNCATEGORIZED


def _symbol_block(sym: Symbol) -> str:
    head = f"### {sym.name} ({sym.kind})"
    if sym.signature:
        head += f"  `{sym.signature}`"
    lines = [
        head,
        f"- domain: `{sym.domain}`" + (f" (source={sym.domain_source}, conf={sym.domain_confidence:.2f})" if sym.domain_source else ""),
        f"- annotation (@domain 显式键): `{sym.annotation or '∅'}`",
        f"- source: `{sym.source_ref}`",
        f"- validity: {sym.validity}",
    ]
    if sym.conflict_flag:
        lines.append("- ⚠ conflict：显式键 ⇄ 向量隐式轨结论不一致，交 Agent 裁决（§12.6）")
    if sym.domain_distribution:
        top = ", ".join(f"{d}={w}" for d, w in list(sym.domain_distribution.items())[:4])
        lines.append(f"- vector 软归属分布（隐式轨，并列参考）: {top}")
    # §13.4 注释文本：docstring / 可读 note（文本方向产物，与向量方向并列呈现）
    if sym.annotation_text and sym.annotation_text.strip() != (sym.docstring or "").strip():
        lines.append(f"- annotation_text: {sym.annotation_text.strip()[:120]}")
    if sym.docstring:
        lines.append("")
        lines.append(sym.docstring.strip())
    # 全文节选：MD 等 content 远长于 docstring 预览时，给 Agent 一段可读摘要（不 dump 整篇）。
    # 代码符号 content 为空 → 跳过；不破坏既有渲染形态。
    _full = getattr(sym, "content", "") or ""
    if _full and _full.strip() != (sym.docstring or "").strip():
        lines.append("")
        lines.append(f"全文(节选): {_full.strip()[:300]}")
    return "\n".join(lines)


# ----------------------------- llms.txt 分片 -----------------------------
def build_llms(catalog: Catalog, out_dir: str | Path) -> list[str]:
    """生成 llms.txt 根路由 + 各域分片 + 全量兜底。返回生成的文件路径列表。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    by_domain: dict[str, list[Symbol]] = {}
    for s in catalog.symbols:
        by_domain.setdefault(_domain_of(s), []).append(s)

    # 全量
    full_path = out / "llms-full.txt"
    full_lines = [f"# {catalog.project} — full catalog", "", f"> {len(catalog.symbols)} symbols."]
    for dom in sorted(by_domain):
        full_lines.append(f"\n## @{dom}")
        for s in by_domain[dom]:
            full_lines.append(_symbol_block(s))
            full_lines.append("")
    full_path.write_text("\n".join(full_lines), encoding="utf-8")
    written.append(str(full_path))

    # 各域分片
    for dom in sorted(by_domain):
        safe = dom.replace("/", "_")
        shard = out / f"llms-{safe}.txt"
        lines = [f"# {catalog.project} — @{dom}", "", f"> {len(by_domain[dom])} symbols in this domain.", ""]
        for s in by_domain[dom]:
            lines.append(_symbol_block(s))
            lines.append("")
        shard.write_text("\n".join(lines), encoding="utf-8")
        written.append(str(shard))

    # 根路由
    root = out / "llms.txt"
    root_lines = [
        f"# {catalog.project} Knowledge Base",
        "",
        f"> Auto-generated. {len(catalog.symbols)} symbols across {len(by_domain)} domains.",
        "",
        "## Domains",
    ]
    for dom in sorted(by_domain):
        safe = dom.replace("/", "_")
        root_lines.append(f"- [@{dom}](llms-{safe}.txt): {len(by_domain[dom])} symbols")
    root_lines.append("")
    root_lines.append("## Full")
    root_lines.append("- [full catalog](llms-full.txt): all symbols")
    root.write_text("\n".join(root_lines), encoding="utf-8")
    written.append(str(root))
    return written


# ----------------------------- MkDocs 主站 -----------------------------
def build_mkdocs(catalog: Catalog, out_dir: str | Path) -> list[str]:
    """生成 MkDocs 站点源（mkdocs.yml + docs/）。返回生成文件路径列表。"""
    out = Path(out_dir)
    docs = out / "docs"
    (docs / "domain").mkdir(parents=True, exist_ok=True)

    by_domain: dict[str, list[Symbol]] = {}
    for s in catalog.symbols:
        by_domain.setdefault(_domain_of(s), []).append(s)

    written: list[str] = []

    # index
    idx = docs / "index.md"
    idx_lines = [
        f"# {catalog.project}",
        "",
        f"Auto-generated knowledge base. {len(catalog.symbols)} symbols, "
        f"{len(by_domain)} domains. Scan: {catalog.files_scanned} files, "
        f"{catalog.files_valid} valid.",
        "",
        "## Domains",
    ]
    for dom in sorted(by_domain):
        idx_lines.append(f"- [{dom}](domain/{dom}.md)")
    idx.write_text("\n".join(idx_lines), encoding="utf-8")
    written.append(str(idx))

    # per-domain pages
    nav = [{"Home": "index.md"}]
    domain_nav = []
    for dom in sorted(by_domain):
        p = docs / "domain" / f"{dom}.md"
        lines = [f"# @{dom}", ""]
        for s in by_domain[dom]:
            lines.append(_symbol_block(s))
            lines.append("")
        p.write_text("\n".join(lines), encoding="utf-8")
        written.append(str(p))
        domain_nav.append({dom: f"domain/{dom}.md"})
    nav.append({"Domains": domain_nav})

    mkdocs_yml = out / "mkdocs.yml"
    mkdocs_yml.write_text(
        "site_name: " + catalog.project + "\n"
        "docs_dir: docs\n"
        "theme:\n  name: material\n"
        "nav:\n"
        + "\n".join(_yaml_nav(nav).splitlines()[1:]) + "\n",
        encoding="utf-8",
    )
    written.append(str(mkdocs_yml))
    return written


def _yaml_nav(nav: list, indent: int = 0) -> str:
    """极简 nav 转 YAML（仅支持本模块产生的结构）。"""
    out = []
    pad = "  " * indent
    for item in nav:
        if isinstance(item, dict):
            for k, v in item.items():
                if isinstance(v, list):
                    out.append(f"{pad}- {k}:")
                    out.append(_yaml_nav(v, indent + 1))
                else:
                    out.append(f"{pad}- {k}: {v}")
        else:
            out.append(f"{pad}- {item}")
    return "\n".join(out)


# ----------------------------- 编排 -----------------------------
def render_all(catalog: Catalog, out_dir: str | Path, config=None) -> dict[str, list[str]]:
    """渲染全部产物。返回 {layer: [paths]}。

    - llms / mkdocs 源始终生成。
    - mkdocs 站仅在 optional.mkdocs=true 且 mkdocs 可用时尝试构建（try/except 不阻塞）。
    - typedoc_site / pagefind / demo 为附加产物，仅当 config 开启时打印提示（离线无法真实构建）。
    """
    out = Path(out_dir)
    result: dict[str, list[str]] = {}
    result["llms"] = build_llms(catalog, out / "llms")
    result["mkdocs_src"] = build_mkdocs(catalog, out / "site")

    optional = (config.optional if config else {}) or {}
    notes: list[str] = []

    if optional.get("mkdocs", False):
        try:
            subprocess.run(
                ["mkdocs", "build", "-d", str(out / "site" / "html")],
                cwd=out / "site",
                check=True,
                capture_output=True,
                timeout=120,
            )
            result["mkdocs_site"] = [str(out / "site" / "html")]
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            notes.append(f"mkdocs 构建跳过（未安装或失败）：{e}")

    for key in ("typedoc_site", "pagefind", "demo"):
        if optional.get(key, False):
            notes.append(f"附加产物 {key} 已开启，但离线环境无法真实构建，需人工/CI 介入（设计稿：config 开启、默认关）。")

    result["notes"] = notes
    return result
