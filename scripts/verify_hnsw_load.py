#!/usr/bin/env python3
"""验证 KnowledgeIndex.load(ann_provider="hnsw") 的跨进程加载路径 + 检索正确性。

- 经 KnowledgeIndex.load 从磁盘 index.hnsw 加载（不调用 embedding API）；
- 用同样的向量内存构建 FlatBackend 作为 ground truth；
- 取若干条真实向量做 query(k=5)，比较 hnsw 与 flat 的 top-k 集合重合度。

用法：
    export PYTHONPATH=src
    python scripts/verify_hnsw_load.py --index-json <path/to/index.json>
    # 或设置环境变量 INDEX_JSON 后直接运行
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from doc_system.index import FlatBackend, KnowledgeIndex


def main() -> int:
    ap = argparse.ArgumentParser(description="验证 hnsw 跨进程加载与检索")
    ap.add_argument(
        "--index-json",
        default=os.environ.get("INDEX_JSON"),
        required=not os.environ.get("INDEX_JSON"),
        help="已落盘的 index.json 路径；也可由环境变量 INDEX_JSON 提供",
    )
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--probe", type=int, default=20, help="抽样多少条向量做查询")
    args = ap.parse_args()

    p = Path(args.index_json)
    print(f"[verify] 从磁盘加载 hnsw 索引: {p}", flush=True)
    idx = KnowledgeIndex.load(str(p), ann_provider="hnsw")
    print(
        f"[verify] 加载完成 entries={len(idx.entries)} dim={idx.dim} "
        f"backend={type(idx.backend).__name__} 已加载索引(_index is not None)={idx.backend._index is not None}",
        flush=True,
    )

    # ground truth：同向量内存暴力余弦
    vectors = [e.vector for e in idx.entries if e.vector]
    flat = FlatBackend(idx.dim)
    flat.build(vectors)

    k = args.k
    n_probe = min(args.probe, len(vectors))
    total_overlap = 0
    rank1_self = 0
    for i in range(n_probe):
        q = vectors[i]
        hnsw_hits = {lbl for _, lbl in idx.backend.query(q, k)}
        flat_hits = {lbl for _, lbl in flat.query(q, k)}
        # 自身必在 top-k（query==某存储向量，理想 score=1.0）
        if i in hnsw_hits and i in flat_hits:
            rank1_self += 1
        if flat_hits:
            total_overlap += len(hnsw_hits & flat_hits) / len(flat_hits)

    avg_overlap = total_overlap / n_probe
    print(f"[verify] 抽样查询={n_probe}  k={k}")
    print(f"[verify] 平均 top-{k} 重合度(相对 flat)={avg_overlap:.3f}")
    print(f"[verify] 自身命中率(rank 含 i)={rank1_self}/{n_probe}")
    ok = avg_overlap >= 0.8 and rank1_self == n_probe
    print(f"[verify] 结论: {'PASS' if ok else 'FAIL'} (重合度>=0.8 且 自身必命中)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
