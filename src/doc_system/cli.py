"""命令行入口（全链路：Phase 1~6）。

子命令：
  ingest          全量扫描 → 甄别 → 提取 → catalog 快照（Phase 1）
  store-init     初始化 git 式 catalog 存储（Phase 2）
  recompute      增量重算并提交新版本（Phase 2）
  reconcile      漂移自动对账（仅构建候选版本，不发布）
  drift          检测工作区漂移
  rollback       回滚 active 到指定版本
  render         渲染 MkDocs 源 + llms.txt 分片（Phase 3）
  index          构建三件套索引 + 向量近邻（Phase 4）
  retrieve       检索（双主线并列：注释通道 + 向量通道）
  gen-skill      生成读指南 SKILL.md（Phase 5 读侧）
  gen-agents-md  生成 AGENTS.md 入口（Phase 5）
  onboard        零人幂等首次接手序列（Phase 5）
  gate           写侧质量门禁（Phase 6）
  ci-publish     CI 发布权：门禁过则原子切 active（Phase 6 触发分权）
  probe          外部探针自动判 SLI（Phase 6 可观测）
  jury           异构 jury 表决（Phase 6）
  bootstrap      宪法 AI 自举（从 AGENTS.md 抽取第一版核心）（Phase 6）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config, EmbeddingConfig
from .governance import bootstrap as boot
from .governance import observability as obs
from .governance import trigger
from .governance.gate import check_gate
from .model import Catalog
from .pipeline import ingest
from .render import render_all
from .store import CatalogStore
from .access import skill as skill_mod
from .access import agents_md as agents_mod
from .access import onboarding as onboard_mod
from .access.write_channel import WriteChannel
from .index import build_index, get_embedder


def _load_cfg(args) -> Config:
    return Config.load(args.config)


def _store(args) -> CatalogStore:
    return CatalogStore(args.store)


def _active(args) -> Catalog:
    s = CatalogStore(args.store)
    c = s.active_catalog()
    if c is None:
        raise SystemExit("无 active catalog；请先 store-init + recompute（或 ci-publish）。")
    return c


def _embedder(args) -> Embedder:
    """按 --provider 覆盖 > --config 的 embedding 段 > 默认 offline 选择向量通道。"""
    Config._load_local_env(Path.cwd())  # best-effort 加载 .env（含硅基流动 API key）
    if getattr(args, "provider", None):
        return get_embedder(EmbeddingConfig(provider=args.provider))
    if getattr(args, "config", None):
        return get_embedder(_load_cfg(args).embedding)
    return get_embedder(None)


def _neighbor_pool(args) -> int:
    """近邻候选池上限：--neighbor-pool 显式值 > config 的 index.neighbor_pool > 0(精确全量)。"""
    if getattr(args, "neighbor_pool", None) is not None:
        return args.neighbor_pool
    if getattr(args, "config", None):
        return _load_cfg(args).index_neighbor_pool
    return 0


def _ann_provider(args) -> str:
    """向量检索后端：--ann-provider 显式值 > config 的 ann_provider > flat(默认零依赖)。"""
    if getattr(args, "ann_provider", None):
        return args.ann_provider
    if getattr(args, "config", None):
        return _load_cfg(args).ann_provider
    return "flat"


# ----------------------------- Phase 1 -----------------------------
def cmd_ingest(args):
    cfg = _load_cfg(args)
    cat = ingest(cfg, args.out)
    print(f"[ingest] 项目={cat.project} 扫描={cat.files_scanned} 有效={cat.files_valid} 符号={len(cat.symbols)}")
    return 0


# ----------------------------- Phase 2 -----------------------------
def cmd_store_init(args):
    CatalogStore.init(args.store, _load_cfg(args).project_name)
    print(f"[store-init] 已初始化存储: {args.store}")
    return 0


def cmd_recompute(args):
    cfg = _load_cfg(args)
    s = _store(args)
    meta = s.recompute(cfg, args.reason, agent_id=args.agent)
    print(f"[recompute] {meta.version_id} 符号={meta.symbol_count} active={s._read_active()}")
    return 0


def cmd_reconcile(args):
    cfg = _load_cfg(args)
    s = _store(args)
    meta = s.reconcile(cfg, agent_id=args.agent)
    if meta is None:
        print("[reconcile] 无漂移，未产生新版本")
    else:
        print(f"[reconcile] 构建候选版本 {meta.version_id}（未发布）")
    return 0


def cmd_drift(args):
    cfg = _load_cfg(args)
    s = _store(args)
    rep = s.detect_drift(cfg)
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_rollback(args):
    s = _store(args)
    s.rollback(args.version)
    print(f"[rollback] active -> {s._read_active()}")
    return 0


# ----------------------------- Phase 3 -----------------------------
def cmd_render(args):
    cfg = _load_cfg(args)
    cat = _active(args)
    res = render_all(cat, args.out, cfg)
    for layer, paths in res.items():
        if layer == "notes":
            for n in paths:
                print(f"[render][note] {n}")
        else:
            print(f"[render] {layer}: {len(paths)} 文件 -> {args.out}/{layer}")
    return 0


# ----------------------------- Phase 4 -----------------------------
def cmd_index(args):
    cat = _active(args)
    pool = _neighbor_pool(args)
    ann = _ann_provider(args)
    idx = build_index(cat, embedder=_embedder(args), out_path=args.out, k=args.neighbors, neighbor_pool=pool, ann_provider=ann)
    tag = f" 近邻池={pool}(近似)" if idx.neighbor_approx else " 近邻=精确全量"
    print(f"[index] 条目={len(idx.entries)} 邻居k={args.neighbors} provider={type(idx.embedder).__name__} ann={idx.ann_provider}{tag} -> {args.out}")
    return 0


def cmd_retrieve(args):
    cat = _active(args)
    pool = _neighbor_pool(args)
    ann = _ann_provider(args)
    idx = build_index(cat, embedder=_embedder(args), k=args.neighbors, neighbor_pool=pool, ann_provider=ann)
    # 重排 provider：显式 --rerank 优先（none=强制关）；只有"未传 --rerank"才回退 config.retrieval.rerank
    explicit = getattr(args, "rerank", None)
    if explicit is not None:
        rerank = None if explicit == "none" else explicit
    else:
        cfg_rerank = (_load_cfg(args).retrieval if getattr(args, "config", None) else None)
        rerank = cfg_rerank.rerank if cfg_rerank else None
    rcands = getattr(args, "rerank_candidates", None) or (
        (_load_cfg(args).retrieval.rerank_candidates if getattr(args, "config", None) else None) or 20
    )
    r = idx.retrieve(args.query, k=args.k, rerank_provider=rerank, rerank_candidates=rcands)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


# ----------------------------- Phase 5 -----------------------------
def cmd_gen_skill(args):
    cat = _active(args)
    p = skill_mod.write_skill_md(cat, args.out)
    print(f"[gen-skill] {p}")
    return 0


def cmd_gen_agents_md(args):
    cat = _active(args)
    p = agents_mod.write_agents_md(cat, args.out)
    print(f"[gen-agents-md] {p}")
    return 0


def cmd_onboard(args):
    cat = _active(args)
    s = _store(args)
    seq = onboard_mod.first_onboarding_sequence(cat, s, args.out)
    print(json.dumps(seq, ensure_ascii=False, indent=2))
    return 0


# ----------------------------- Phase 6 -----------------------------
def cmd_gate(args):
    cat = _active(args)
    rep = check_gate(cat, args.min)
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_ci_publish(args):
    cfg = _load_cfg(args)
    s = _store(args)
    res = trigger.ci_publish(s, cfg, args.reason, provenance_min=args.min)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_probe(args):
    cfg = _load_cfg(args)
    s = _store(args)
    rep = obs.run_probe(s, cfg)
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_jury(args):
    cat = _active(args)
    rep = obs.jury_decide(args.question, cat)
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_bootstrap(args):
    con = boot.bootstrap_from_agents_md(args.agents_md)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    con.save(out)
    print(f"[bootstrap] v{con.version} 条款数={len(con.clauses)} -> {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="doc-system", description="通用项目知识库文档系统")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_out(sp, default="catalog.snapshot.json"):
        sp.add_argument("--config", required=True)
        sp.add_argument("--out", default=default)

    sp = sub.add_parser("ingest"); sp.add_argument("--config", required=True); sp.add_argument("--out", default="catalog.snapshot.json"); sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("store-init"); sp.add_argument("--config", required=True); sp.add_argument("--store", default=".docstore"); sp.set_defaults(func=cmd_store_init)
    sp = sub.add_parser("recompute"); sp.add_argument("--config", required=True); sp.add_argument("--store", default=".docstore"); sp.add_argument("--reason", required=True); sp.add_argument("--agent", default="system"); sp.set_defaults(func=cmd_recompute)
    sp = sub.add_parser("reconcile"); sp.add_argument("--config", required=True); sp.add_argument("--store", default=".docstore"); sp.add_argument("--agent", default="auto-reconciler"); sp.set_defaults(func=cmd_reconcile)
    sp = sub.add_parser("drift"); sp.add_argument("--config", required=True); sp.add_argument("--store", default=".docstore"); sp.set_defaults(func=cmd_drift)
    sp = sub.add_parser("rollback"); sp.add_argument("--store", default=".docstore"); sp.add_argument("--version", required=True); sp.set_defaults(func=cmd_rollback)

    sp = sub.add_parser("render"); sp.add_argument("--config", required=True); sp.add_argument("--store", default=".docstore"); sp.add_argument("--out", default=".doc_system"); sp.set_defaults(func=cmd_render)
    sp = sub.add_parser("index"); sp.add_argument("--config", default=None); sp.add_argument("--store", default=".docstore"); sp.add_argument("--out", default=".doc_system/index/index.json"); sp.add_argument("--neighbors", type=int, default=3); sp.add_argument("--provider", default=None, choices=["offline", "siliconflow"], help="向量通道：默认读 config embedding 段，否则 offline"); sp.add_argument("--neighbor-pool", type=int, default=None, help="近邻候选池上限(大项目采样近似,0=精确全量);默认读 config"); sp.add_argument("--ann-provider", default=None, choices=["flat", "hnsw"], help="向量检索后端: flat(暴力余弦,默认) | hnsw(hnswlib 近似最近邻);默认读 config.ann_provider"); sp.set_defaults(func=cmd_index)
    sp = sub.add_parser("retrieve"); sp.add_argument("--config", default=None); sp.add_argument("--store", default=".docstore"); sp.add_argument("--query", required=True); sp.add_argument("--k", type=int, default=5); sp.add_argument("--neighbors", type=int, default=3); sp.add_argument("--provider", default=None, choices=["offline", "siliconflow"]); sp.add_argument("--neighbor-pool", type=int, default=None, help="近邻候选池上限;默认读 config"); sp.add_argument("--ann-provider", default=None, choices=["flat", "hnsw"], help="向量检索后端: flat(默认) | hnsw;默认读 config.ann_provider"); sp.add_argument("--rerank", default=None, choices=["none", "offline", "siliconflow"], help="查询时重排(选项C): 用 bge-m3 对 top-N 候选项重排,覆盖 offline 哈希噪声;默认读 config.retrieval.rerank"); sp.add_argument("--rerank-candidates", type=int, default=None, help="重排候选池大小;默认读 config.retrieval.rerank_candidates(20)"); sp.set_defaults(func=cmd_retrieve)

    sp = sub.add_parser("gen-skill"); sp.add_argument("--store", default=".docstore"); sp.add_argument("--out", default=".doc_system"); sp.set_defaults(func=cmd_gen_skill)
    sp = sub.add_parser("gen-agents-md"); sp.add_argument("--store", default=".docstore"); sp.add_argument("--out", default=".doc_system"); sp.set_defaults(func=cmd_gen_agents_md)
    sp = sub.add_parser("onboard"); sp.add_argument("--store", default=".docstore"); sp.add_argument("--out", default=".doc_system"); sp.set_defaults(func=cmd_onboard)

    sp = sub.add_parser("gate"); sp.add_argument("--store", default=".docstore"); sp.add_argument("--min", type=float, default=0.95); sp.set_defaults(func=cmd_gate)
    sp = sub.add_parser("ci-publish"); sp.add_argument("--config", required=True); sp.add_argument("--store", default=".docstore"); sp.add_argument("--reason", required=True); sp.add_argument("--min", type=float, default=0.95); sp.set_defaults(func=cmd_ci_publish)
    sp = sub.add_parser("probe"); sp.add_argument("--config", required=True); sp.add_argument("--store", default=".docstore"); sp.set_defaults(func=cmd_probe)
    sp = sub.add_parser("jury"); sp.add_argument("--store", default=".docstore"); sp.add_argument("--question", required=True); sp.set_defaults(func=cmd_jury)
    sp = sub.add_parser("bootstrap"); sp.add_argument("--agents-md", required=True); sp.add_argument("--out", default=".doc_system/constitution.json"); sp.set_defaults(func=cmd_bootstrap)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
