"""Phase 2~6 端到端测试（基于 fixture sample_project）。

运行：PYTHONPATH=src python -m unittest discover -s tests
"""
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from doc_system.config import Config
from doc_system.model import Catalog
from doc_system.pipeline import ingest, ingest_incremental
from doc_system.store import CatalogStore
from doc_system.render import render_all, build_llms
from doc_system.index import build_index, KnowledgeIndex, OfflineEmbedder, Embedder
from doc_system.access import skill as skill_mod
from doc_system.access import agents_md as agents_mod
from doc_system.access import onboarding as onboard_mod
from doc_system.access.write_channel import WriteChannel
from doc_system.governance.gate import check_gate
from doc_system.governance import trigger
from doc_system.governance import observability as obs
from doc_system.governance import bootstrap as boot

CFG = "config.example.yaml"
ROOT = Path(__file__).resolve().parent.parent


class Phase2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store_dir = Path(self.tmp) / ".docstore"
        self.cfg = Config.load(CFG)
        self.store = CatalogStore.init(self.store_dir, self.cfg.project_name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recompute_drift_reconcile_rollback(self):
        m1 = self.store.recompute(self.cfg, "init")
        self.assertEqual(m1.symbol_count, 18)  # 5 py + AGENTS.md(模块+3节)
        m2 = self.store.recompute(self.cfg, "noop")
        self.assertEqual(m2.symbol_count, 18)
        # 改一个文件制造漂移
        f = ROOT / "fixtures/sample_project/core/user.py"
        orig = f.read_text()
        f.write_text(orig + "\n# drift\n")
        try:
            drift = self.store.detect_drift(self.cfg)
            self.assertTrue(drift.behind)
            cand = self.store.reconcile(self.cfg, set_active=False)
            self.assertIsNotNone(cand)
            self.assertNotEqual(self.store._read_active(), cand.version_id)  # 未发布
            self.store.rollback(cand.version_id)
            self.assertEqual(self.store._read_active(), cand.version_id)
        finally:
            f.write_text(orig)


class Phase3Test(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(CFG)
        self.cat = ingest(self.cfg, out_path=None)
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_llms_and_mkdocs(self):
        written = render_all(self.cat, self.out, self.cfg)
        self.assertIn(str(self.out / "llms" / "llms.txt"), written["llms"])
        self.assertTrue((self.out / "llms" / "llms-full.txt").exists())
        self.assertTrue((self.out / "site" / "mkdocs.yml").exists())
        # 分片存在（user/order/generated 域）
        self.assertTrue((self.out / "llms" / "llms-user.txt").exists())


class Phase4Test(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(CFG)
        self.cat = ingest(self.cfg, out_path=None)

    def test_index_retrieve_two_channels(self):
        idx = build_index(self.cat, k=3)
        r = idx.retrieve("user", k=3)
        self.assertIn("annotation_hits", r)
        self.assertIn("vector_hits", r)
        # 注释通道应命中 user 域
        self.assertTrue(any(h["domain"] == "user" for h in r["annotation_hits"]))
        # 每个 hit 带 vector_neighbors / source_ref / conflict_flag
        for h in r["vector_hits"]:
            self.assertIn("vector_neighbors", h)
            self.assertIn("source_ref", h)
            self.assertIn("conflict_flag", h)


class Phase5Test(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(CFG)
        self.cat = ingest(self.cfg, out_path=None)
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_skill_is_read_only(self):
        md = skill_mod.gen_skill_md(self.cat)
        self.assertIn("仅读", md)
        self.assertNotIn("commit_catalog_change", md)  # 读指南不含写工具

    def test_write_channel_idempotency_and_whitelist(self):
        store = CatalogStore.init(self.tmp / ".docstore", self.cfg.project_name)
        store.recompute(self.cfg, "init")
        ch = WriteChannel(store).set_config(self.cfg)
        r1 = ch.commit_catalog_change("fix user", idempotency_key="k1")
        r2 = ch.commit_catalog_change("fix user", idempotency_key="k1")
        self.assertTrue(r2["idempotent"])
        self.assertEqual(r1["version_id"], r2["version_id"])
        # 字段白名单：越权字段应被拒
        with self.assertRaises(ValueError):
            ch.commit_catalog_change("x", symbols=[{"name": "a", "evil": "pwn"}], idempotency_key="k2")

    def test_agents_md_and_onboarding_idempotent(self):
        md = agents_mod.gen_agents_md(self.cat)
        self.assertIn("唯一入口", md)
        seq = onboard_mod.first_onboarding_sequence(self.cat, None, self.tmp)
        self.assertTrue(seq["idempotent"])


class Phase6Test(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(CFG)
        self.tmp = Path(tempfile.mkdtemp())
        self.store = CatalogStore.init(self.tmp / ".docstore", self.cfg.project_name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gate_passes(self):
        self.store.recompute(self.cfg, "init")
        cat = self.store.active_catalog()
        rep = check_gate(cat)
        self.assertTrue(rep.passed)
        self.assertAlmostEqual(rep.provenance_coverage, 1.0)

    def test_ci_publish_rights(self):
        # 先由非 CI 角色 reconcile（仅构建，不发布）
        trigger.agent_dynamic_maintain(self.store, self.cfg)
        before = self.store._read_active()
        res = trigger.ci_publish(self.store, self.cfg, "release v1")
        self.assertTrue(res["published"])
        self.assertNotEqual(before, res["active"]) if before else None

    def test_probe_and_jury(self):
        self.store.recompute(self.cfg, "init")
        rep = obs.run_probe(self.store, self.cfg)
        self.assertTrue(rep.passed)
        jury = obs.jury_decide("is helpful?", self.store.active_catalog())
        self.assertTrue(jury.decision)

    def test_bootstrap_from_agents_md(self):
        con = boot.bootstrap_from_agents_md(str(ROOT / "fixtures/sample_project/AGENTS.md"))
        self.assertEqual(con.version, 1)
        self.assertTrue(len(con.clauses) >= 3)
        # 修订需 jury 批准
        with self.assertRaises(PermissionError):
            boot.amend(con, ["new clause"], jury_decision=False)


class SiliconFlowEmbedderTest(unittest.TestCase):
    """不依赖网络：mock urllib 响应，锁住 bge-m3 通道解析 / 顺序还原 / 缺 key 报错。"""

    def _fake_response(self, texts, dim=1024):
        data = [
            {"object": "embedding", "index": i, "embedding": [float(i + 1)] * dim}
            for i in range(len(texts))
        ]
        payload = json.dumps({"data": data, "model": "BAAI/bge-m3"}).encode()
        cm = mock.MagicMock()
        cm.__enter__.return_value = io.BytesIO(payload)
        return cm

    def test_embed_many_parses_and_keeps_order(self):
        from doc_system.index import SiliconFlowEmbedder
        emb = SiliconFlowEmbedder(api_key="test-key", batch_size=2)
        texts = ["a", "b", "c"]

        def fake_urlopen(req, timeout=60):
            batch = json.loads(req.data.decode())["input"]
            return self._fake_response(batch)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            vecs = emb.embed_many(texts)
        self.assertEqual(len(vecs), 3)
        self.assertEqual(len(vecs[0]), 1024)
        # 按 index 还原顺序：每批 index 都从 0 起，故 a/b 批=[1,2]，c 批=[1]
        self.assertEqual(vecs[0][0], 1.0)
        self.assertEqual(vecs[1][0], 2.0)

    def test_missing_key_raises(self):
        from doc_system.index import SiliconFlowEmbedder
        saved = os.environ.pop("SILICONFLOW_API_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                SiliconFlowEmbedder(api_key=None, api_key_env="SILICONFLOW_API_KEY")
        finally:
            if saved:
                os.environ["SILICONFLOW_API_KEY"] = saved

    def test_get_embedder_offline_default(self):
        from doc_system.index import get_embedder, OfflineEmbedder
        self.assertIsInstance(get_embedder(None), OfflineEmbedder)
        self.assertEqual(len(get_embedder(None).embed("hello")), 256)


class MultiRootTest(unittest.TestCase):
    """真实 hermes-agent e2e 抓到的两个 bug 的回归锁：
    多 root 下同名文件（__init__.py）rel 碰撞 → ① 增量重算不幂等；② 漂移检测误报。
    这里用临时目录复现「两个 root 各有同名 __init__.py」的最小场景。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # 两个 root，各有同名 __init__.py（触发 rel 碰撞）
        for root in ("alpha", "beta"):
            d = self.tmp / root
            d.mkdir()
            (d / "__init__.py").write_text(f"# {root} init\n", encoding="utf-8")
            (d / "mod.py").write_text(
                f'def f_{root}():\n    """{root} function"""\n    return 1\n', encoding="utf-8"
            )
        self.cfg_path = self.tmp / "cfg.yaml"
        self.cfg_path.write_text(
            "project:\n  name: multi-root-test\n"
            "roots:\n"
            f"  - path: {self.tmp / 'alpha'}\n"
            f"  - path: {self.tmp / 'beta'}\n"
            "blacklist: []\n"
            "languages:\n  - name: python\n    extensions: ['.py']\n    extractor: python_ast\n"
            "triage: {test_patterns: [], generated_patterns: []}\n",
            encoding="utf-8",
        )
        self.cfg = Config.load(self.cfg_path)
        self.store = CatalogStore.init(self.tmp / ".docstore", self.cfg.project_name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rel_globally_unique(self):
        from doc_system.pipeline import _scan_files
        rels = [rel for _, rel in _scan_files(self.cfg)]
        self.assertEqual(len(rels), len(set(rels)), "多 root 下 rel 必须全局唯一")
        self.assertTrue(any(r.startswith("alpha/__init__.py") for r in rels))
        self.assertTrue(any(r.startswith("beta/__init__.py") for r in rels))

    def test_incremental_idempotent_and_drift_consistent(self):
        m1 = self.store.recompute(self.cfg, "init")
        m2 = self.store.recompute(self.cfg, "noop")
        self.assertEqual(m1.symbol_count, m2.symbol_count, "无改动时增量重算必须幂等")
        # 无改动时漂移检测应为 0（口径一致，不能把带前缀 vs 不带前缀误判）
        drift = self.store.detect_drift(self.cfg)
        self.assertFalse(drift.behind, f"无改动不应报漂移: {drift.drifted_files}")
        # 改 1 个文件 → 恰好 1 个漂移
        f = self.tmp / "alpha" / "mod.py"
        orig = f.read_text()
        f.write_text(orig + "\n# changed\n")
        try:
            drift2 = self.store.detect_drift(self.cfg)
            self.assertTrue(drift2.behind)
            self.assertEqual(len(drift2.drifted_files), 1)
            self.assertTrue(drift2.drifted_files[0].startswith("alpha/"))
        finally:
            f.write_text(orig)

    def test_neighbor_pool_approx_flag(self):
        cat = ingest(self.cfg, out_path=None)
        idx = build_index(cat, neighbor_pool=2)
        self.assertTrue(idx.neighbor_approx)
        idx2 = build_index(cat, neighbor_pool=0)
        self.assertFalse(idx2.neighbor_approx)


class AttributionTest(unittest.TestCase):
    """§12 归因双轨 + §13.4 三件套/三通道 回归锁（真实 hermes e2e 抓到的缺口）。

    核心断言：真实项目没有手写 @domain 时，domain 仍被隐式轨/结构域填充（绝不为空），
    且向量方向 ‖ 文本注释方向 各自作为独立通道并列返回，不预融合。
    """

    def setUp(self):
        self.cfg = Config.load(CFG)
        self.cat = ingest(self.cfg, out_path=None)

    def test_domain_never_empty_without_explicit_key(self):
        # fixture 里绝大多数符号无 @domain，归因后 domain 必须全部非空
        empties = [s.name for s in self.cat.symbols if not s.domain]
        self.assertEqual(empties, [], f"归因后仍有空 domain: {empties[:5]}")
        sources = {s.domain_source for s in self.cat.symbols}
        self.assertTrue(sources, "每个符号都应有归因来源标记")
        self.assertTrue(sources <= {"explicit", "implicit", "structural", "both_agree", "conflict", "uncertain"})

    def test_explicit_domain_key_respected(self):
        # 手写 @domain user/order 是显式轨 crisp 信号，必须被尊重（annotation 保留原值）
        user = [s for s in self.cat.symbols if s.annotation == "user"]
        self.assertTrue(user, "应能读到 @domain user 显式键")
        self.assertTrue(all(s.domain == "user" for s in user), "显式键应胜出为 domain")
        self.assertTrue(all(s.domain_source in ("explicit", "both_agree") for s in user))

    def test_implicit_track_produces_soft_distribution(self):
        # 隐式轨：无显式键符号也应带向量软分布（§12.3 多域重归属）
        implicit = [s for s in self.cat.symbols if s.domain_source in ("implicit", "structural", "uncertain")]
        self.assertTrue(implicit)
        self.assertTrue(all(len(s.domain_distribution) >= 1 for s in implicit))
        self.assertTrue(all(0.0 <= s.domain_confidence <= 1.0 for s in implicit))

    def test_triple_payload_and_three_channels(self):
        idx = build_index(self.cat, k=3)
        # payload 三件套字段齐全
        p = idx.entries[0].to_payload(True)
        for key in ("vector", "annotation", "annotation_text", "domain", "domain_source",
                    "domain_distribution", "conflict_flag", "conflict_sources", "vector_neighbors"):
            self.assertIn(key, p)
        # 三通道并列返回（含独立的注释文本通道）
        r = idx.retrieve("user login create", k=3)
        for ch in ("annotation_hits", "annotation_text_hits", "vector_hits", "channels"):
            self.assertIn(ch, r)
        self.assertEqual(set(r["channels"]), {"annotation", "annotation_text", "vector"})
        # 文本通道命中来自 docstring/note，而非仅被拼进向量
        self.assertTrue(any(h.get("annotation_text") for h in r["annotation_text_hits"]))

    def test_llms_multiple_shards_not_single_bucket(self):
        # 归因后 domain 多样 → llms 分片不再退化成单个 _uncategorized
        out = Path(tempfile.mkdtemp())
        try:
            written = render_all(self.cat, out, self.cfg)
            shards = [w for w in written["llms"] if "/llms-" in w and not w.endswith("llms-full.txt")]
            self.assertGreater(len(shards), 1, "应有多个域分片，而非单桶")
            self.assertFalse(any("llms-_uncategorized" in w for w in shards))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_attribution_idempotent_snapshot(self):
        # §12.7 快照：重复归因结果确定、域不漂移（避免「代码没动、文档却变」）
        dom1 = [s.domain for s in self.cat.symbols]
        from doc_system.attribution import attribute_catalog
        attribute_catalog(self.cat, self.cfg)
        dom2 = [s.domain for s in self.cat.symbols]
        self.assertEqual(dom1, dom2)


class FilesOnlyRootTest(unittest.TestCase):
    """根级核心代码纳入能力（用户指出「根级反而是核心代码」）的回归锁。

    files_only:true 的 root 只扫该目录顶层文件、不递归进巨型子目录；
    且大文件提取不得因 ast.get_source_segment 的 O(n·行数) 退化。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        root = self.tmp / "proj"
        root.mkdir()
        (root / "top_core.py").write_text(
            'def top_fn(a, b):\n    """top-level core"""\n    return a + b\n', encoding="utf-8"
        )
        (root / "README.md").write_text("# Root Guide\n\n## Core\n", encoding="utf-8")
        sub = root / "huge_subdir"
        sub.mkdir()
        (sub / "nested.py").write_text(
            'def nested():\n    """should not be scanned"""\n    return 1\n', encoding="utf-8"
        )
        self.cfg_path = self.tmp / "cfg.yaml"
        self.cfg_path.write_text(
            "project:\n  name: files-only-test\n"
            "roots:\n"
            f"  - path: {root}\n    files_only: true\n"
            "blacklist: []\n"
            "languages:\n"
            "  - name: python\n    extensions: ['.py']\n    extractor: python_ast\n"
            "  - name: plaintext\n    extensions: ['.md']\n    extractor: plaintext\n"
            "triage: {test_patterns: [], generated_patterns: []}\n",
            encoding="utf-8",
        )
        self.cfg = Config.load(self.cfg_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_files_only_scans_top_level_not_subdirs(self):
        from doc_system.pipeline import _scan_files
        rels = [rel for _, rel in _scan_files(self.cfg)]
        self.assertTrue(any(r.endswith("top_core.py") for r in rels))
        self.assertTrue(any(r.endswith("README.md") for r in rels))
        self.assertFalse(any("nested" in r for r in rels), "files_only 不应递归进子目录")
        self.assertIn(str(self.cfg.roots[0]), self.cfg.root_files_only)

    def test_ingest_includes_root_core(self):
        cat = ingest(self.cfg, out_path=None)
        names = [s.name for s in cat.symbols]
        self.assertIn("top_fn", names)
        self.assertFalse(any("nested" in s.source_ref for s in cat.symbols))

    def test_large_file_extraction_scales(self):
        # 构造含大量函数的大文件，验证不会 O(n²) 退化（segment 优化后应很快）
        import time
        from doc_system.extract.python import PythonAstExtractor
        body = "\n".join(
            f'def f{i}(x):\n    """func {i} docs"""\n    return x + {i}\n' for i in range(600)
        )
        t0 = time.time()
        syms = PythonAstExtractor().extract("big.py", body, "valid", "")
        dt = time.time() - t0
        self.assertEqual(len(syms), 601)  # 600 函数 + 1 module
        self.assertLess(dt, 3.0, f"大文件提取退化: {dt:.2f}s")


class RerankTest(unittest.TestCase):
    """选项 C（查询时 bge-m3 重排）回归锁：重排应按真语义序覆盖 offline 哈希序。

    用 FakeEmbedder 模拟 bge-m3（含 'targetword' 的文本得高相似度），验证
    `retrieve(..., rerank_provider=...)` 把该符号置顶，且不改变三通道结构。
    """

    def _cat(self):
        from doc_system.model import Catalog, Symbol
        syms = [
            Symbol(name="foo.alpha", kind="function", domain="x", docstring="alpha",
                   signature="()", source_ref="a.py:1", content_hash="h1",
                   validity="valid", reason=""),
            Symbol(name="bar.beta", kind="function", domain="x", docstring="TARGETWORD beta",
                   signature="()", source_ref="b.py:1", content_hash="h2",
                   validity="valid", reason=""),
            Symbol(name="baz.gamma", kind="function", domain="x", docstring="gamma",
                   signature="()", source_ref="c.py:1", content_hash="h3",
                   validity="valid", reason=""),
        ]
        return Catalog(project="t", generated_at="", root_paths=[], symbols=syms, file_hashes={})

    def test_rerank_reorders_by_semantic(self):
        import doc_system.index as idx_mod
        from unittest import mock

        class FakeEmb(Embedder):
            dim = 4

            def embed_many(self, texts):
                out = []
                for t in texts:
                    if "targetword" in t.lower():
                        out.append([1.0, 0.0, 0.0, 0.0])
                    else:
                        out.append([0.0, 1.0, 0.0, 0.0])
                return out

        idx = KnowledgeIndex(self._cat(), embedder=OfflineEmbedder())
        with mock.patch.object(idx_mod, "get_embedder", return_value=FakeEmb()):
            r = idx.retrieve("targetword query", k=3, rerank_provider="siliconflow")
        self.assertEqual(r["vector_hits"][0]["name"], "bar.beta")
        self.assertEqual(len(r["vector_hits"]), 3)
        # 三通道结构不被重排破坏
        for ch in ("annotation_hits", "annotation_text_hits", "vector_hits", "channels"):
            self.assertIn(ch, r)
        self.assertEqual(set(r["channels"]), {"annotation", "annotation_text", "vector"})

    def test_rerank_none_keeps_offline_order(self):
        idx = KnowledgeIndex(self._cat(), embedder=OfflineEmbedder())
        r = idx.retrieve("targetword query", k=3)  # 不传 rerank_provider
        self.assertEqual(len(r["vector_hits"]), 3)
        self.assertNotIn("rerank", r)  # 无额外字段污染


class ConcurrencyTest(unittest.TestCase):
    """SiliconFlowEmbedder 并发嵌入：并行发送 batch 但输出顺序须与输入一致。"""

    def test_concurrent_embeds_preserves_order(self):
        from doc_system.index import SiliconFlowEmbedder

        class StubSF(SiliconFlowEmbedder):
            def _embed_batch(self, batch):
                # 不真正打 API：用 batch 内每段文本的序号决定返回向量，便于校验顺序
                out = []
                for t in batch:
                    n = int(t.split("|")[1])
                    v = [0.0] * 4
                    v[n % 4] = 1.0
                    out.append(v)
                return out

        emb = StubSF(api_key="x", batch_size=4, max_concurrency=8)
        texts = [f"t|{i}" for i in range(100)]  # 25 batches，远大于并发度
        vecs = emb.embed_many(texts)
        self.assertEqual(len(vecs), 100)
        for i, v in enumerate(vecs):
            # 第 i 段应落到第 i%4 维为 1
            self.assertEqual(v[i % 4], 1.0)
            self.assertEqual(sum(v), 1.0)

    def test_max_concurrency_one_is_serial(self):
        from doc_system.index import SiliconFlowEmbedder

        calls = []

        class StubSF(SiliconFlowEmbedder):
            def _embed_batch(self, batch):
                calls.append(tuple(batch))
                return [[1.0, 0.0]] * len(batch)

        emb = StubSF(api_key="x", batch_size=2, max_concurrency=1)
        emb.embed_many(["a", "b", "c"])
        self.assertEqual(len(calls), 2)  # ceil(3/2)=2 batches


class ANNBackendTest(unittest.TestCase):
    """可插拔 ANN 后端：(a) hnsw 与 flat top-k 高度一致；(b) flat 检索行为与原实现一致。"""

    def _rand_unit(self, rng, n, dim):
        import numpy as np

        v = rng.standard_normal((n, dim))
        norm = np.linalg.norm(v, axis=1, keepdims=True)
        v = v / np.where(norm == 0, 1.0, norm)
        return v.tolist()

    def test_hnsw_matches_flat_topk_small_dataset(self):
        # 小数据集下 hnsw（近似）应与 flat（暴力余弦）top-k 集合高度一致（顺序可微差）。
        import numpy as np
        from doc_system.index import FlatBackend, HnswBackend

        rng = np.random.default_rng(20240607)
        dim, n = 32, 80
        vectors = self._rand_unit(rng, n, dim)
        flat = FlatBackend(dim)
        flat.build(vectors)
        hnsw = HnswBackend(dim)
        hnsw.build(vectors)

        k = 10
        mismatches = 0
        for _ in range(20):
            q = self._rand_unit(rng, 1, dim)[0]
            flat_idx = {i for _, i in flat.query(q, k)}
            hnsw_idx = {i for _, i in hnsw.query(q, k)}
            # 小数据集 ef=n 召回充分：top-k 集合应完全重合
            if flat_idx != hnsw_idx:
                mismatches += 1
        self.assertEqual(
            mismatches, 0,
            f"hnsw 与 flat top-k 集合不一致的次数={mismatches}/20（小数据集应全一致）",
        )

    def test_flat_provider_retrieval_matches_brute_force(self):
        # ann_provider="flat" 时检索行为与原实现（暴力余弦）一致；默认亦为 flat。
        from doc_system.index import FlatBackend, KnowledgeIndex, OfflineEmbedder

        cfg = Config.load(CFG)
        cat = ingest(cfg, out_path=None)
        emb = OfflineEmbedder()
        texts = [
            f"{s.name} {s.signature} {s.docstring} {s.content}".strip()[:6000]
            for s in cat.symbols
        ]
        vectors = emb.embed_many(texts)

        # 显式 flat
        idx = KnowledgeIndex(cat, embedder=emb, ann_provider="flat")
        self.assertIsInstance(idx.backend, FlatBackend)

        # 默认（不传 ann_provider）亦应为 flat
        idx_default = KnowledgeIndex(cat, embedder=emb)
        self.assertIsInstance(idx_default.backend, FlatBackend)

        # 期望：对 query 暴力余弦取 top-k（不开启重排）
        q = "user login create"
        qvec = emb.embed(q)
        expected = sorted(
            ((sum(x * y for x, y in zip(qvec, v)), s.name) for s, v in zip(cat.symbols, vectors)),
            key=lambda x: x[0], reverse=True,
        )[:5]
        expected_names = {name for _, name in expected}

        r = idx.retrieve(q, k=5)
        got_names = {h["name"] for h in r["vector_hits"]}
        self.assertEqual(got_names, expected_names)
        # 得分口径一致：flat 的 score 即余弦相似度（保留 4 位）
        for h in r["vector_hits"]:
            sc = round(
                sum(x * y for x, y in zip(qvec, next(v for s, v in zip(cat.symbols, vectors) if s.name == h["name"]))),
                4,
            )
            self.assertAlmostEqual(h["score"], sc, places=4)


if __name__ == "__main__":
    unittest.main()
