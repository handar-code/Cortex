"""端到端集成测试：建档 → 索引(flat + hnsw) → 检索（offline，无 API key，不依赖网络）。

覆盖全链路：
  Phase 1  ingest   ：扫描临时小项目 → 抽取符号 → catalog 快照
  Phase 4  build_index：分别用 ann_provider="flat"(暴力余弦) 与 "hnsw"(hnswlib 近似) 建索引
  检索     retrieve ：两条通道（向量 / 注释文本）做语义检索并断言质量

设计约束（release 准备，零依赖可复现）：
  - 全程使用 OfflineEmbedder（确定性哈希向量），绝不调用 siliconflow / 读 .env / 联网。
  - Config 直接构造（不走 Config.load，避免任何 .env 读取副作用）。
  - 临时建档产物写到 tempfile 临时目录，测试结束清理。

本文件为新增独立测试，不改动 src/ 源码，也不触碰 test_basic.py / test_phases.py。
"""
import os
import shutil
import sys
import tempfile
import unittest

# 让测试能 import doc_system 包（src 布局）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from doc_system.config import (  # noqa: E402
    Config,
    LanguageConfig,
    EmbeddingConfig,
    AttributionConfig,
    RetrievalConfig,
)
from doc_system.pipeline import ingest  # noqa: E402
from doc_system.index import build_index, OfflineEmbedder  # noqa: E402


# 临时小项目：每个文件含简单函数/类 + docstring，符号名与 docstring 用词刻意相关，
# 以便 offline（token 哈希，仅对 ASCII 词元生效）向量也能把语义相近的符号聚合到一起。
# 注意：OfflineEmbedder 的正则只抽取 [a-zA-Z_]+ 词元，故 docstring 用英文以与查询对齐。
SAMPLE_PROJECT = {
    "user.py": '''"""User account module."""

class User:
    """User account model handling login and authentication."""

    def authenticate(self, password):
        """Verify user password during login authentication."""
        return True

    def get_profile(self):
        """Return the user display profile."""
        return {}


def logout(user):
    """Sign the user out of the session."""
    return None
''',
    "order.py": '''"""Order and payment module."""

class Order:
    """Shopping order containing items and payment processing."""

    def checkout(self):
        """Process the order payment at checkout."""
        return "paid"

    def add_item(self, item):
        """Add an item to the order."""
        return None
''',
    "cache.py": '''"""Cache utility module."""

def cache_get(key):
    """Retrieve the cached value stored under the key."""
    return None

def cache_set(key, value):
    """Store a value in the cache under the key."""
    return None
''',
    "report.py": '''"""Reporting and statistics module."""

def generate_report(rows):
    """Generate a summary report from the rows."""
    return []

def summarize(rows):
    """Compute a statistical summary of the rows."""
    return {}
''',
}


def _make_config(project_dir: str) -> Config:
    """直接构造 Config（不读 .env、不联网），embedding/attribution 均设 offline。"""
    return Config(
        project_name="integration-e2e",
        roots=[project_dir],
        blacklist=[],
        languages=[
            LanguageConfig(name="python", extensions=[".py"], extractor="python_ast"),
        ],
        triage_test_patterns=["**/test_*.py"],
        triage_generated_patterns=["**/*.generated.*"],
        base_dir=__import__("pathlib").Path(project_dir),
        embedding=EmbeddingConfig(provider="offline"),
        attribution=AttributionConfig(provider="offline", enabled=True),
        retrieval=RetrievalConfig(),
        ann_provider="flat",
    )


def _build_temp_project() -> str:
    """在临时目录铺一个含 4 个 .py 文件的小项目，返回目录路径。"""
    d = tempfile.mkdtemp(prefix="cortex_e2e_")
    for name, code in SAMPLE_PROJECT.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(code)
    return d


def _top_names(result: dict, channel: str = "vector_hits", k: int = 5) -> list[str]:
    """从 retrieve 结果里取某通道 top-k 的符号名。"""
    return [h.get("name") for h in result.get(channel, [])[:k]]


class TestIntegrationE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_dir = _build_temp_project()
        cfg = _make_config(cls.project_dir)
        # ---- Phase 1：建档（ingest）----
        cls.catalog = ingest(cfg, os.path.join(cls.project_dir, "catalog.json"))
        cls.symbol_names = {s.name for s in cls.catalog.symbols}
        assert len(cls.catalog.symbols) > 0, "建档未抽出任何符号"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project_dir, ignore_errors=True)

    # -------------------- 建档阶段断言 --------------------
    def test_ingest_extracted_symbols(self):
        # 关键符号都应被抽出（类 + 方法 + 模块级函数）
        for expected in {"User", "Order", "authenticate", "checkout", "cache_get", "generate_report"}:
            self.assertIn(expected, self.symbol_names,
                          f"建档未抽出预期符号 {expected}")

    # -------------------- flat 索引 + 检索 --------------------
    def test_flat_index_retrieve(self):
        idx = build_index(
            self.catalog,
            embedder=OfflineEmbedder(),
            ann_provider="flat",
        )
        self.assertEqual(idx.ann_provider, "flat")
        r = idx.retrieve("user login authentication", k=5)
        self.assertGreater(len(r["vector_hits"]), 0, "flat 向量通道返回空")
        # 用户相关查询，User / authenticate 应被检索到
        names = _top_names(r, "vector_hits", 5)
        self.assertTrue(
            {"User", "authenticate"} & set(names),
            f"flat 未检索到用户相关符号，实际 top: {names}",
        )

    # -------------------- hnsw 索引 + 检索 --------------------
    def test_hnsw_index_retrieve(self):
        idx = build_index(
            self.catalog,
            embedder=OfflineEmbedder(),
            ann_provider="hnsw",
        )
        self.assertEqual(idx.ann_provider, "hnsw")
        r = idx.retrieve("user login authentication", k=5)
        self.assertGreater(len(r["vector_hits"]), 0, "hnsw 向量通道返回空")
        names = _top_names(r, "vector_hits", 5)
        self.assertTrue(
            {"User", "authenticate"} & set(names),
            f"hnsw 未检索到用户相关符号，实际 top: {names}",
        )

    # -------------------- flat vs hnsw 近似质量重合 --------------------
    def test_flat_and_hnsw_overlap(self):
        """flat(精确) 与 hnsw(近似) 对同一查询，语义相关符号应同时出现（近似索引质量）。"""
        flat_idx = build_index(self.catalog, embedder=OfflineEmbedder(), ann_provider="flat")
        hnsw_idx = build_index(self.catalog, embedder=OfflineEmbedder(), ann_provider="hnsw")

        query = "user login authentication"
        flat_hits = set(_top_names(flat_idx.retrieve(query, k=5), "vector_hits", 5))
        hnsw_hits = set(_top_names(hnsw_idx.retrieve(query, k=5), "vector_hits", 5))

        self.assertTrue(flat_hits, "flat 检索为空")
        self.assertTrue(hnsw_hits, "hnsw 检索为空")

        # 两条查询都必须命中用户相关符号（强语义相关断言，跨后端一致）
        for backend, hits in (("flat", flat_hits), ("hnsw", hnsw_hits)):
            self.assertTrue(
                {"User", "authenticate"} & hits,
                f"{backend} 未检索到用户相关符号，实际: {sorted(hits)}",
            )

        # 近似索引召回质量：前 5 名集合至少重合 2 个（合理近似容差）
        overlap = flat_hits & hnsw_hits
        self.assertGreaterEqual(
            len(overlap), 2,
            f"flat 与 hnsw 结果重合不足(={len(overlap)}): "
            f"flat={sorted(flat_hits)} hnsw={sorted(hnsw_hits)}",
        )

    # -------------------- 注释文本通道也能检索 --------------------
    def test_annotation_text_channel(self):
        """docstring 文本通道应独立于向量通道命中相关符号（§12.1 并列主线）。"""
        idx = build_index(self.catalog, embedder=OfflineEmbedder(), ann_provider="flat")
        r = idx.retrieve("payment checkout order", k=5)
        text_hits = _top_names(r, "annotation_text_hits", 5)
        self.assertTrue(
            {"Order", "checkout"} & set(text_hits),
            f"注释文本通道未命中订单/支付符号，实际: {text_hits}",
        )


if __name__ == "__main__":
    unittest.main()
