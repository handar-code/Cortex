"""归因双轨层（设计稿 §12.2 显式轨⇄隐式轨 + §12.6 平等裁决 + §12.7 catalog 快照）。

真实项目常在文件级没有 `@domain` 人工标注（§12 立论：标注债务不可持续）。因此 domain
绝不能只靠显式轨。本模块补齐此前实现遗漏的「隐式轨」与「裁决」：

- **域骨架（scope→module 目录树）**：从符号 source_ref 的路径层级自动导出候选域。
  这正是 §13.4 Q5 已定的「分片边界 = 通用项目层级 scope→module 目录树」，零标注、
  零人、通用、确定性。域边界 ≈ 向量聚类边界（同一刀法两面），故不再另立决策。
- **显式轨 · annotation**：源码里的 `@domain` 键（人工信号，crisp）——可为空，这是常态。
- **隐式轨 · 向量软归因**：以每个域内符号向量的均值为「域质心」（§12.3 每域 seed 的
  零人等价物：用域内符号自动充当 seed，无需人工贴标），每个符号向量对各质心算相似度，
  归一化成软分布 {域: 分}，top 域即隐式归属建议（§12.3 多域重归属 / 语义混淆暴露）。
- **§12.6 平等裁决**（显式键 ⇄ 向量，互不为主、不预融合、交 Agent）：
    仅显式 / 显式不在骨架域      → 显式说了算（source=explicit）
    显式 且 向量 top 一致          → 双方印证（source=both_agree，高置信）
    显式 且 向量 top 不一致        → 标 conflict_flag，不自动偏向任一方（交 Agent 裁决）
    仅隐式（无显式键）            → 采纳向量 top 域（source=implicit），带置信度与软分布
    隐式低于阈值                  → uncertain（domain 用结构域兜底，仍不为空）
- **§12.7 快照**：结果（域 + 来源标记 + 置信度 + 分布 + conflict）算一次写进 catalog
  快照，随版本化；不每次实时重算，避免「代码没动、文档却变」。

向量通道复用 index.get_embedder：默认 offline 确定性哈希向量（零人、零成本、可复现、
不喂 API），可经 config `attribution.provider` 升级为 siliconflow 真语义向量。与主线一
「内置识别优先 → 统一接口 → 内置不行再 AI 升级」同构。
"""
from __future__ import annotations

import re
from collections import defaultdict

from .config import Config, EmbeddingConfig
from .index import _cosine, get_embedder
from .model import Catalog, Symbol

# 结构域兜底名（当某符号既无显式键、路径也无层级时）
FALLBACK_DOMAIN = "_root"

_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")


def scope_domain(source_ref: str, depth: int = 1) -> str:
    """从 source_ref 导出目录树 scope 域（§13.4 Q5 的 scope→module 层级）。

    source_ref 形如 "<label>/<dir>/<file>.py:<line>"（多 root 带 label 前缀，见 pipeline）。
    depth=1 取第一段（scope），depth=2 取前两段（scope/module）。去掉文件名与行号。
    """
    path = source_ref.split(":", 1)[0].replace("\\", "/")
    parts = [p for p in path.split("/") if p]
    # 最后一段是文件名，去掉
    dirs = parts[:-1]
    if not dirs:
        return FALLBACK_DOMAIN
    return "/".join(dirs[:depth]) if depth >= 1 else dirs[0]


def _symbol_text(s: Symbol) -> str:
    # 纳入 content（MD 全文等长文本），让隐式轨语义软归属也能吃到全文，而非只看 docstring 截断；
    # 截断到 MAX_EMBED_CHARS，避免超长文档（如 66KB 设计稿）超出 bge-m3 上下文上限导致 API 400。
    from .index import MAX_EMBED_CHARS
    return f"{s.name} {s.signature} {s.docstring} {s.content or s.annotation_text or ''}".strip()[:MAX_EMBED_CHARS]


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    """把余弦相似度（可能有负）平移到非负并归一化为软分布，保留语义梯度。"""
    if not scores:
        return {}
    lo = min(scores.values())
    shifted = {k: v - lo + 1e-6 for k, v in scores.items()}  # 平移到 >0
    total = sum(shifted.values()) or 1.0
    dist = {k: round(v / total, 4) for k, v in shifted.items()}
    return dict(sorted(dist.items(), key=lambda kv: kv[1], reverse=True))


def attribute_catalog(
    catalog: Catalog,
    config: Config | None = None,
    embedder=None,
    scope_depth: int = 1,
    implicit_threshold: float = 0.0,
) -> Catalog:
    """对 catalog 做归因双轨裁决，结果写回每个 Symbol 并随快照存留。

    返回同一 catalog（原地修改，便于流水线串联）。
    """
    symbols = catalog.symbols
    if not symbols:
        return catalog

    # 配置覆盖（若调用方未显式传参）
    acfg = getattr(config, "attribution", None) if config else None
    domain_from = "structural"
    if acfg:
        scope_depth = acfg.scope_depth
        implicit_threshold = acfg.implicit_threshold
        domain_from = acfg.domain_from

    # ---- 1) 域骨架：目录树 scope（Q5）= 隐式轨的候选域 taxonomy ----
    # 注意：开发者写的 @domain 显式键是「显式轨」，与结构域属不同命名空间，
    # 不并入向量质心候选域（否则每个自定义键都会与结构 top 域「不一致」而被误判 conflict）。
    structural = {s.source_ref: scope_domain(s.source_ref, scope_depth) for s in symbols}
    domain_taxonomy = sorted(set(structural.values()))

    # ---- 2) 向量：每个符号文本 → 向量；每个域 → 质心（域内符号向量均值） ----
    if embedder is None:
        provider = (acfg.provider if acfg else "offline")
        ecfg = getattr(config, "embedding", None) if config else None
        if provider == "siliconflow" and ecfg is not None:
            # 复用 embedding 段的完整接口参数（api_base / model / key / 并发），只覆盖
            # provider。本地端点（Ollama / llama.cpp / LM Studio）与云端都是同一个
            # OpenAI 兼容接口；否则这里 new 一个默认 EmbeddingConfig 会回落到硅基流动
            # 云端地址，导致「主向量走本地、隐式轨偷偷打云端」的半套配置。
            embedder = get_embedder(EmbeddingConfig(
                provider=provider,
                model=ecfg.model,
                api_base=ecfg.api_base,
                api_key_env=ecfg.api_key_env,
                batch_size=ecfg.batch_size,
                max_concurrency=ecfg.max_concurrency,
            ))
        else:
            embedder = get_embedder(EmbeddingConfig(provider=provider))
    texts = [_symbol_text(s) for s in symbols]
    vecs = embedder.embed_many(texts)

    by_domain: dict[str, list[list[float]]] = defaultdict(list)
    for s, vec in zip(symbols, vecs):
        by_domain[structural[s.source_ref]].append(vec)
    dim = len(vecs[0]) if vecs else 0
    centroids: dict[str, list[float]] = {}
    for dom, vlist in by_domain.items():
        n = len(vlist) or 1
        centroids[dom] = [sum(v[d] for v in vlist) / n for d in range(dim)]

    # ---- 3) 隐式软分布 + §12.6 平等裁决（逐符号） ----
    for s, vec in zip(symbols, vecs):
        s.annotation_text = s.annotation_text or (s.docstring or "")
        struct_dom = structural[s.source_ref]
        raw = {d: _cosine(vec, c) for d, c in centroids.items()}
        top_dom = max(raw, key=raw.get) if raw else struct_dom
        top_score = raw.get(top_dom, 0.0)
        dist = _normalize(raw)
        s.domain_distribution = dist

        ann = s.annotation  # 显式轨（可能 None）
        confidence = dist.get(top_dom, 0.0)  # 用软分布 top 占比作置信（0~1，可比）
        if ann and ann not in domain_taxonomy:
            # 仅显式轨出现（自定义域键）：显式 crisp 说了算，无冲突
            s.domain, s.domain_source = ann, "explicit"
            s.domain_confidence, s.conflict_flag = 1.0, False
        elif ann and top_dom == ann:
            # 显式 ⇄ 向量一致：双方印证
            s.domain, s.domain_source = ann, "both_agree"
            s.domain_confidence, s.conflict_flag = max(confidence, 0.9), False
        elif ann and top_dom != ann:
            # 显式 ⇄ 向量冲突：不自动偏向任一方。domain 取显式（稳定路由键），
            # 标 conflict，并把「向量 top 域」并列进软分布交 Agent 裁决（§12.6）
            s.domain, s.domain_source = ann, "conflict"
            s.domain_confidence, s.conflict_flag = confidence, True
            dist[ann] = round(dist.get(ann, 0.0) + confidence, 4)
            s.domain_distribution = dict(sorted(dist.items(), key=lambda kv: kv[1], reverse=True))
        else:
            # 无显式键（真实项目主流）：结构域与向量隐式轨并列、互不为主
            # - domain 归属键由 config.domain_from 决定（默认 structural，Q5 目录树可复现）
            # - 向量软分布始终写入 domain_distribution，作为独立隐式轨供 Agent 交叉判断
            if domain_from == "vector" and top_score >= implicit_threshold:
                s.domain, s.domain_source = top_dom, "implicit"
            elif confidence >= implicit_threshold:
                s.domain, s.domain_source = struct_dom, "structural"
            else:
                s.domain, s.domain_source = struct_dom, "uncertain"
            s.domain_confidence, s.conflict_flag = confidence, False

    return catalog
