"""Phase 1 基础行为测试（stdlib unittest，无额外依赖）。

验证：配置加载、内置甄别、Python 提取器的 @domain 识别、统一 Symbol[]。
"""
import os
import sys
import unittest

# 让测试能 import doc_system 包（src 布局）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from doc_system.config import Config  # noqa: E402
from doc_system.pipeline import ingest  # noqa: E402
from doc_system.triage import classify  # noqa: E402


class TestTriage(unittest.TestCase):
    def test_classify_test(self):
        v, r = classify("core/test_user.py", ["**/test_*.py"], [])
        self.assertEqual(v, "test")

    def test_classify_valid_default(self):
        v, _ = classify("core/user.py", ["**/test_*.py"], ["**/*.generated.*"])
        self.assertEqual(v, "valid")


class TestIngestFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = Config.load(os.path.join(ROOT, "config.example.yaml"))
        cls.cfg = cfg
        cls.cat = ingest(cfg, os.path.join(ROOT, "_test_catalog.json"))

    def test_counts(self):
        self.assertEqual(self.cat.files_scanned, 6)  # 含 fixture 的 AGENTS.md
        self.assertEqual(self.cat.files_valid, 5)  # test_user.py 不计有效；AGENTS.md 计有效

    def test_domains_extracted(self):
        domains = {s.domain for s in self.cat.symbols if s.domain}
        self.assertIn("user", domains)
        self.assertIn("order", domains)

    def test_test_file_flagged(self):
        test_syms = [s for s in self.cat.symbols if s.source_ref.startswith("core/test_user.py")]
        self.assertTrue(test_syms)
        self.assertTrue(all(s.validity == "test" for s in test_syms))


if __name__ == "__main__":
    unittest.main()
