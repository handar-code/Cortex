"""索引层（设计稿 §13.4 主线四 · 三件套）。

三件套统一索引（§13.4 口径）：
- **向量 (vector)**：符号语义的 embedding。默认 offline（确定性哈希，零依赖）；
  可插拔为 siliconflow（BAAI/bge-m3 语义向量，经硅基流动 API）。
- **注释 (annotation)**：`@domain` 显式键。
- **注释文本 (annotation_text)**：docstring / 可读 note。

检索返回 **双主线并列、不做事前融合**（§13.4 决策）：
- annotation_hits：按 `@domain` 精确命中（注释通道）
- vector_hits：向量最近的前 k 个（向量通道，含最近邻 vector_neighbors）
两者一起返回给 Agent，由 Agent 自行裁决；冲突原样并列（conflict_flag 标记，不拦截）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import EmbeddingConfig
from .model import Catalog, Symbol

OFFLINE_DIM = 256

# 嵌入文本安全长度上限：bge-m3 等语义模型有上下文上限（~8K token）。
# 长文档（如 66KB 设计稿 module.content）超出会被 API 拒（HTTP 400）。
# 仅截断「嵌入输入」，catalog 仍存全文；section 级符号粒度更细、可全量嵌入，
# module 锚点截断到前段即可——这是模型输入约束，不是语料裁剪。
MAX_EMBED_CHARS = 6000

# 查询时重排（选项 C）的候选池大小：先取这批 top-N 候选，再用 bge-m3 真语义重排挑前 k。
# 足够大以覆盖 offline 哈希漏掉的相关项，又不至于每次查询嵌入太多（按次计费、成本低）。
RERANK_CANDIDATES = 20


def _symbol_embed_text(s: "Symbol") -> str:
    """构造符号的嵌入文本（向量通道 / 重排共用），与索引构建保持一致。"""
    return f"{s.name} {s.signature} {s.docstring} {s.content}".strip()[:MAX_EMBED_CHARS]


# ---------- Embedder 基类与实现 ----------
class Embedder:
    """向量通道基类：子类实现 embed_many，单条 embed 复用批量接口。"""

    dim: int

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]


class OfflineEmbedder(Embedder):
    """确定性离线 embedding（哈希技巧到固定维度，无外部模型依赖）。作为默认 provider。"""

    dim = OFFLINE_DIM

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", (text or "").lower())
            for t in tokens:
                h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class SiliconFlowEmbedder(Embedder):
    """BAAI/bge-m3 语义向量，经硅基流动 Embedding API。

    API key 从环境变量 / 项目根 .env 读取（见 Config._load_local_env），绝不写进源码。
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "BAAI/bge-m3",
        api_base: str = "https://api.siliconflow.cn/v1/embeddings",
        api_key_env: str = "SILICONFLOW_API_KEY",
        batch_size: int = 32,
    ):
        self.api_key = api_key or os.environ.get(api_key_env)
        if not self.api_key:
            raise RuntimeError(
                f"缺少硅基流动 API key：请设置环境变量 {api_key_env}，"
                f"或在项目根 .env 中写入 `{api_key_env}=...`。"
            )
        self.model = model
        self.api_base = api_base
        self.api_key_env = api_key_env
        self.batch_size = batch_size
        self.dim = 1024  # BAAI/bge-m3 输出维度

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            body = json.dumps(
                {"model": self.model, "input": batch, "encoding_format": "float"}
            ).encode("utf-8")
            req = urllib.request.Request(
                self.api_base,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"硅基流动 Embedding 调用失败（model={self.model}）：{e}"
                ) from e
            vecs = {d["index"]: d["embedding"] for d in data.get("data", [])}
            out.extend(vecs[j] for j in range(len(batch)))
        return out


def get_embedder(cfg: EmbeddingConfig | None) -> Embedder:
    cfg = cfg or EmbeddingConfig()
    if cfg.provider == "siliconflow":
        return SiliconFlowEmbedder(
            model=cfg.model,
            api_base=cfg.api_base,
            api_key_env=cfg.api_key_env,
            batch_size=cfg.batch_size,
        )
    if cfg.provider != "offline":
        raise ValueError(f"未知 embedding provider: {cfg.provider!r}")
    return OfflineEmbedder()


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class IndexEntry:
    symbol: Symbol
    vector: list[float]
    annotation_neighbors: list[dict] = field(default_factory=list)  # vector 最近邻
    conflict_flag: bool = False

    def to_payload(self, include_vector: bool = True) -> dict:
        """§13.4 payload：三件套并列 + 向量近邻 + 域裁决元数据 + conflict。

        顶层字段 `vector` / `annotation`(@domain 显式键) / `annotation_text`(docstring)
        三者成对并列返回，不做服务端预融合；由 Agent 自行裁决权重与冲突。
        """
        s = self.symbol
        d = s.to_dict()  # 已含 annotation / annotation_text / domain / domain_* / 归因 conflict_flag
        if include_vector:
            d["vector"] = self.vector
        else:
            d.pop("vector", None)
        d["vector_neighbors"] = self.annotation_neighbors
        # 冲突口径合并（§12.6 归因冲突 ⇄ 文件内 module/成员域不一致），标明来源供 Agent 判断
        attr_conflict = bool(s.conflict_flag)
        file_conflict = bool(self.conflict_flag)
        d["conflict_flag"] = attr_conflict or file_conflict
        d["conflict_sources"] = [
            name for name, on in
            (("attribution", attr_conflict), ("file_domain", file_conflict)) if on
        ]
        # §12.7 域裁决元数据显式并列（triple(): 域 + 来源 + 置信度 + 软分布）
        return d


class KnowledgeIndex:
    def __init__(
        self,
        catalog: Catalog,
        embedder: Embedder | None = None,
        neighbor_k: int = 3,
        neighbor_pool: int = 0,
    ):
        self.catalog = catalog
        self.embedder = embedder or OfflineEmbedder()
        self.dim = self.embedder.dim
        self.neighbor_approx = False  # 是否启用了候选池近似（供报告/探针用）
        self.entries: list[IndexEntry] = []
        # 1) 构建向量 + 标注（批量 embedding，减少 API 往返）
        # 嵌入文本纳入 content（MD 全文 / fenced 注释等长文本），使全文可被向量通道检索；
        # 代码符号 content 为空，行为不变（仍只用 name/signature/docstring）。
        # 截断到 MAX_EMBED_CHARS：长文档（如 66KB 设计稿）超出 bge-m3 上限会被 API 拒（400）。
        texts = [_symbol_embed_text(s) for s in catalog.symbols]
        vectors = self.embedder.embed_many(texts)
        for s, vec in zip(catalog.symbols, vectors):
            self.entries.append(IndexEntry(symbol=s, vector=vec))
        # 2) 向量最近邻（每个符号的最近 neighbor_k 个）
        self._compute_neighbors(neighbor_k, neighbor_pool)
        # 3) 冲突标记：文件内 module 域 vs 成员域不一致
        self._compute_conflicts()

    def _compute_neighbors(self, k: int, pool: int = 0) -> None:
        """计算每个符号的 k 个向量近邻。

        朴素实现是 O(n²)（每个符号对全部符号算余弦），在小项目（几百条）没问题，
        但真实大型项目近万符号时约需上亿次相似度运算、不可接受。
        pool > 0 且 n > pool 时启用「确定性均匀采样候选池」近似：先按步长从全体
        条目中均匀取 pool 个构成共享候选池，每个符号只在池内找近邻，复杂度降为
        O(n·pool)。pool=0（默认）保持精确全量。真实系统可再换成 ANN 向量库。
        """
        n = len(self.entries)
        base: list[IndexEntry] | None = None
        if pool and n > pool:
            step = n / pool
            idxs = sorted({min(n - 1, int(t * step)) for t in range(pool)})
            base = [self.entries[j] for j in idxs]
            self.neighbor_approx = True
        for i, e in enumerate(self.entries):
            candidates = base if base is not None else self.entries
            scored = []
            for o in candidates:
                if o is e:
                    continue
                sc = _cosine(e.vector, o.vector)
                scored.append((sc, o))
            scored.sort(key=lambda x: x[0], reverse=True)
            e.annotation_neighbors = [
                {
                    "name": o.symbol.name,
                    "source_ref": o.symbol.source_ref,
                    "score": round(sc, 4),
                }
                for sc, o in scored[:k]
            ]

    def _compute_conflicts(self) -> None:
        # 按文件归组：module 符号域 vs 其子符号域
        by_file: dict[str, list[IndexEntry]] = {}
        for e in self.entries:
            f = e.symbol.source_ref.split(":", 1)[0]
            by_file.setdefault(f, []).append(e)
        for f, ents in by_file.items():
            mod = next((e for e in ents if e.symbol.kind == "module"), None)
            if not mod:
                continue
            mod_dom = mod.symbol.domain
            for e in ents:
                if e is mod:
                    continue
                if e.symbol.domain is not None and mod_dom is not None and e.symbol.domain != mod_dom:
                    e.conflict_flag = True

    def _semantic_rerank(self, query: str, entries: list, provider: str) -> list:
        """选项 C：用真语义向量（bge-m3 等）对候选符号重排。

        只嵌入 query + 候选符号的文本（候选池 ~20，按次计费、成本低），重算余弦后返回
        `[(score, entry), ...]` 降序。不改动 corpus 向量（离线哈希仍作候选召回），仅修正排序。
        """
        from .config import EmbeddingConfig
        emb = get_embedder(EmbeddingConfig(provider=provider))
        texts = [query] + [_symbol_embed_text(e.symbol) for e in entries]
        vecs = emb.embed_many(texts)
        qv = vecs[0]
        out = [(_cosine(qv, v), e) for e, v in zip(entries, vecs[1:])]
        out.sort(key=lambda x: x[0], reverse=True)
        return out

    # ---- 检索：向量方向 ‖ 文本注释方向 并列（§13.4，不预融合、交 Agent 裁决）----
    def retrieve(
        self,
        query: str,
        k: int = 5,
        include_vector: bool = True,
        rerank_provider: str = None,
        rerank_candidates: int = RERANK_CANDIDATES,
    ) -> dict:
        q_tokens = set(t.lower() for t in re.findall(r"[@]?([a-zA-Z_][\w./\-]*)", query))

        # 通道 A · 向量方向：语义 top-k（Embedding 相似度）
        qvec = self.embedder.embed(query)
        scored = sorted(
            ((_cosine(qvec, e.vector), e) for e in self.entries),
            key=lambda x: x[0],
            reverse=True,
        )
        # 选项 C · 查询时 bge-m3 重排：用真语义向量对「候选池」重新排序，覆盖 offline 哈希噪声。
        # 先取较大候选池（给重排留空间），再用 bge-m3 重算相似度挑前 k——零额外离线成本，
        # 仅按次 API 计费，质量接近真语义索引（推荐解，详见 TEST_REPORT §11）。
        if rerank_provider:
            cand = [e for _, e in scored[: max(k, rerank_candidates)]]
            scored = self._semantic_rerank(query, cand, rerank_provider)
        vector_hits = [
            e.to_payload(include_vector) | {"score": round(sc, 4)}
            for sc, e in scored[:k]
        ]

        # 通道 B · 注释（@domain 显式键 / 裁决域）精确命中（支持 "user" 或 "@user"）
        annotation_hits = []
        for e in self.entries:
            dom = e.symbol.domain
            ann = e.symbol.annotation
            if (dom and dom in q_tokens) or (ann and ann in q_tokens):
                annotation_hits.append(e.to_payload(include_vector))

        # 通道 C · 注释文本方向：docstring / 可读 note 的词项重叠（独立并行通道）
        # 这是 §12.1「纯文本/注释方向」与向量方向并列的落地：6032 条 docstring
        # 不再只被拼进向量原料，而是作为一条独立检索主线把命中项并列返回给 Agent。
        text_scored = []
        for e in self.entries:
            # 文本注释通道：优先 annotation_text（docstring/note），再回退到 content（MD 全文等长文本），
            # 最后回退 docstring。保证 MD 全文与代码注释都能走这条独立并行通道。
            blob = e.symbol.annotation_text or e.symbol.content or e.symbol.docstring or ""
            toks = set(t.lower() for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", blob))
            if not toks:
                continue
            overlap = len(q_tokens & toks)
            if overlap:
                # 简易余弦重叠分（避免长 docstring 被无条件抬高）
                sc = overlap / (len(q_tokens) ** 0.5 * len(toks) ** 0.5)
                text_scored.append((sc, e))
        text_scored.sort(key=lambda x: x[0], reverse=True)
        annotation_text_hits = [
            e.to_payload(include_vector) | {"score": round(sc, 4)}
            for sc, e in text_scored[:k]
        ]

        return {
            "query": query,
            # 三通道并列、互不为主、不预融合 → Agent 自裁决（§12.6 / §13.4）
            "annotation_hits": annotation_hits,       # 注释：@domain 显式键 / 裁决域
            "annotation_text_hits": annotation_text_hits,  # 注释文本：docstring 词项命中
            "vector_hits": vector_hits,               # 向量：语义 top-k
            "channels": {
                "annotation": len(annotation_hits),
                "annotation_text": len(annotation_text_hits),
                "vector": len(vector_hits),
            },
            "query_vector": qvec if include_vector else None,
            "embedding_provider": type(self.embedder).__name__,
        }

    def save(self, path: str | Path) -> None:
        """把索引（含三件套 payload）落盘，供 Agent 直接读取。"""
        payload = [e.to_payload(True) for e in self.entries]
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def build_index(
    catalog: Catalog,
    embedder: Embedder | None = None,
    out_path: str | Path | None = None,
    k: int = 3,
    neighbor_pool: int = 0,
) -> KnowledgeIndex:
    idx = KnowledgeIndex(
        catalog, embedder=embedder, neighbor_k=k, neighbor_pool=neighbor_pool
    )
    if out_path is not None:
        idx.save(out_path)
    return idx
