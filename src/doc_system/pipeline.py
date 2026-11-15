"""摄取流水线编排：扫描 → 黑名单过滤 → 甄别 → 提取 → 落 catalog 快照。

设计稿 §13.1 主路径落地：确定性优先（内置甄别 + 内置提取）；
只有「无对应提取器」的文件会跳过（留给后续适配器或 AI 升级）。

提供两种入口：
- ingest()          —— 全量重算（每次从头抽）。
- ingest_incremental() —— 基于内容哈希的增量重算：未变文件的符号直接复用，
                          只重抽变更/新增文件；删除文件从快照移除。供 Phase 2
                          的 git 式版本化 store 调用，避免每次全量重抽。
"""
from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from pathlib import Path

from .attribution import attribute_catalog
from .config import Config
from .extract.registry import extractor_for_extension, get_extractor
from .model import Catalog, Symbol, sha256
from .triage import classify


def _is_blacklisted(rel: str, blacklist: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in blacklist)


def _scan_files(config: Config) -> list[tuple[Path, str]]:
    """返回 [(path, rel), ...]，已过滤黑名单/非文件/无提取器。

    `rel` 必须全局唯一：早期版本用「相对单个 root 的路径」，多 root 项目里
    不同 root 下的同名文件（尤其 `__init__.py`）会碰撞，导致 file_hashes 与
    增量重算的按文件分组串味、重算不幂等。多 root 时加 root 名前缀消除碰撞；
    单 root 保持原样（向后兼容既有 catalog 与 fixture 测试）。
    黑名单/甄别仍按不含前缀的 inner 路径匹配，与配置写法一致。
    """
    out: list[tuple[Path, str]] = []
    multi_root = len(config.roots) > 1
    # 为每个 root 分配唯一 label（同名目录自动加序号），保证 rel 全局唯一
    resolved = [(r, (config.base_dir / r).resolve()) for r in config.roots]
    labels: list[str] = []
    seen: dict[str, int] = {}
    for _, rd in resolved:
        n = rd.name
        seen[n] = seen.get(n, 0) + 1
        labels.append(n if seen[n] == 1 else f"{n}-{seen[n]}")
    files_only = getattr(config, "root_files_only", None) or set()
    for (root, root_dir), label in zip(resolved, labels):
        if not root_dir.exists():
            continue
        # files_only:true 的 root 只取该目录顶层文件（不递归）。用于纳入项目根级
        # 核心代码，而不把 114M context_system / 数千 ts 等巨型子目录一并拖入扫描。
        paths = (
            sorted(p for p in root_dir.iterdir() if p.is_file())
            if root in files_only
            else sorted(root_dir.rglob("*"))
        )
        for path in paths:
            if not path.is_file():
                continue
            inner = str(path.relative_to(root_dir)).replace("\\", "/")
            if _is_blacklisted(inner, config.blacklist):
                continue
            ext = path.suffix
            if not extractor_for_extension(ext, config.languages):
                continue
            rel = f"{label}/{inner}" if multi_root else inner
            out.append((path, rel))
    return out


def _extract_one(
    path: Path, rel: str, config: Config, validity: str, reason: str
) -> list[Symbol]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    extractor = get_extractor(
        extractor_for_extension(path.suffix, config.languages)
    )
    return extractor.extract(rel, text, validity, reason)


def ingest(config: Config, out_path: str | Path) -> Catalog:
    """全量重算：每次重新扫描并抽取所有文件。"""
    files = _scan_files(config)
    symbols: list[Symbol] = []
    file_hashes: dict[str, str] = {}
    files_scanned = 0
    files_valid = 0
    for path, rel in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        ext = path.suffix
        # 内置启发式甄别
        validity, reason = classify(
            rel, config.triage_test_patterns, config.triage_generated_patterns
        )
        files_scanned += 1
        if validity == "valid":
            files_valid += 1
        extractor = get_extractor(extractor_for_extension(ext, config.languages))
        symbols.extend(extractor.extract(rel, text, validity, reason))
        file_hashes[rel] = sha256(text)
    catalog = Catalog(
        project=config.project_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        root_paths=[str((config.base_dir / r).resolve()) for r in config.roots],
        files_scanned=files_scanned,
        files_valid=files_valid,
        symbols=symbols,
        file_hashes=file_hashes,
    )
    # §12 归因双轨：抽取后统一裁决 domain（显式 @domain ⇄ 向量隐式轨），
    # 结果随 catalog 快照存留（§12.7）。默认启用，config 可关。
    acfg = getattr(config, "attribution", None)
    if acfg is None or acfg.enabled:
        attribute_catalog(catalog, config)
    if out_path is not None:
        catalog.save(out_path)
    return catalog


def ingest_incremental(
    config: Config, prev: Catalog, out_path: str | Path | None = None
) -> Catalog:
    """增量重算（基于内容哈希，非 mtime）。

    - 当前文件哈希 == 上次记录 → 复用上次该文件抽取的符号（不重抽）。
    - 变更/新增 → 重新抽取。
    - 已从磁盘删除 → 移除。
    返回的 Catalog 仍含完整 symbols，但抽取成本只花在变更文件上。
    """
    files = _scan_files(config)

    # 旧符号按文件归组（source_ref 形如 "rel:lineno" → 取 "rel" 部分）
    prev_by_file: dict[str, list[Symbol]] = {}
    for s in prev.symbols:
        f = s.source_ref.split(":", 1)[0]
        prev_by_file.setdefault(f, []).append(s)

    symbols: list[Symbol] = []
    file_hashes: dict[str, str] = {}
    files_scanned = 0
    files_valid = 0

    for path, rel in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        h = sha256(text)
        validity, reason = classify(
            rel, config.triage_test_patterns, config.triage_generated_patterns
        )
        files_scanned += 1
        if validity == "valid":
            files_valid += 1
        if rel in prev.file_hashes and prev.file_hashes[rel] == h and rel in prev_by_file:
            # 命中：复用上次符号
            symbols.extend(prev_by_file[rel])
        else:
            symbols.extend(_extract_one(path, rel, config, validity, reason))
        file_hashes[rel] = h

    catalog = Catalog(
        project=config.project_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        root_paths=[str((config.base_dir / r).resolve()) for r in config.roots],
        files_scanned=files_scanned,
        files_valid=files_valid,
        symbols=symbols,
        file_hashes=file_hashes,
    )
    # 增量路径同样做归因裁决（幂等：复用符号带旧结果、变更符号补新结果，全量重裁决保持一致），
    # 结果随快照存留，避免「代码没动、domain 却变」。
    acfg = getattr(config, "attribution", None)
    if acfg is None or acfg.enabled:
        attribute_catalog(catalog, config)
    if out_path is not None:
        catalog.save(out_path)
    return catalog
