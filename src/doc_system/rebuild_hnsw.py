#!/usr/bin/env python3
"""Hermes 建档产物 → hnswlib 跨进程索引重建脚本（零 API 消耗）。

读取已落盘的 index.json，复用其中内嵌的 bge-m3 向量（严禁重新调用 embedding API），
构建 hnswlib 近似最近邻索引并落盘为同目录的 `<stem>.hnsw`，同时写 `<stem>.meta.json`。

用法：
    export PYTHONPATH=src
    python -m doc_system.rebuild_hnsw --index-json <path/to/index.json>
    # 或设置环境变量 INDEX_JSON 后直接运行（见 main()）

产物（与 index.json 同目录）：
    index.hnsw        hnswlib 原生索引文件（图 + 向量，可跨进程加载）
    index.meta.json   {"ann_provider":"hnsw","dim":...,"n":...}
index.json 的 entries 结构保持不变（不重写）。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .index import HnswBackend


def rebuild(index_json: Path) -> dict:
    index_json = Path(index_json)
    if not index_json.exists():
        raise SystemExit(f"找不到 index.json: {index_json}")
    p = index_json
    hnsw_path = p.parent / (p.stem + ".hnsw")
    meta_path = p.parent / (p.stem + ".meta.json")

    t0 = time.time()
    print(f"[rebuild] 读取 index.json: {p}", flush=True)
    data = json.loads(p.read_text(encoding="utf-8"))
    vectors = [e["vector"] for e in data if e.get("vector")]
    n = len(vectors)
    if n == 0:
        raise SystemExit("index.json 中没有任何 vector，无法构建 hnsw。")
    dim = len(vectors[0])
    print(f"[rebuild] 条目数={n} 维度={dim}  读取耗时={time.time()-t0:.1f}s", flush=True)

    if any(len(v) != dim for v in vectors):
        raise SystemExit(f"向量维度不一致（期望 {dim}），拒绝构建。")

    t1 = time.time()
    backend = HnswBackend(dim)
    backend.build(vectors)
    print(f"[rebuild] hnsw build 完成 耗时={time.time()-t1:.1f}s", flush=True)

    backend.save_index(hnsw_path)
    meta_path.write_text(
        json.dumps(
            {"ann_provider": "hnsw", "dim": dim, "n": n},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    size = hnsw_path.stat().st_size if hnsw_path.exists() else 0
    print(
        f"[rebuild] 落盘: {hnsw_path} ({size/1024/1024:.1f} MB)\n"
        f"[rebuild] meta: {meta_path}",
        flush=True,
    )
    return {"n": n, "dim": dim, "hnsw": str(hnsw_path), "size": size}


def main() -> int:
    ap = argparse.ArgumentParser(description="index.json → hnsw 跨进程索引重建")
    ap.add_argument(
        "--index-json",
        default=os.environ.get("INDEX_JSON"),
        required=not os.environ.get("INDEX_JSON"),
        help="已落盘的 index.json 路径（含内嵌向量）；也可由环境变量 INDEX_JSON 提供",
    )
    args = ap.parse_args()
    info = rebuild(args.index_json)
    print(f"[rebuild] 完成: {json.dumps(info, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
