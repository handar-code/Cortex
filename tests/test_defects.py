"""defects 模块测试：隐式契约缺陷检测。

这些测试用**合成的小工程**而非真实项目，验证检测逻辑本身。
真实案例（Hermes 的 load_soul_prompt_md）在模块 docstring 里有说明。
"""
import sys
import tempfile
import unittest
from pathlib import Path

from doc_system.defects import (
    Defect,
    ScanReport,
    check_reverse_attribute_contract,
)


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class ReverseAttributeContractTest(unittest.TestCase):
    """反向读模块属性的契约检测。"""

    def _make_target_module(self, tmp: Path, body: str = "VALUE = 1\n"):
        """在 tmp 下造一个可 import 的目标模块。"""
        _write(tmp, "fake_target.py", body)
        sys.path.insert(0, str(tmp))
        self.addCleanup(self._cleanup_path, str(tmp))
        import importlib

        importlib.invalidate_caches()
        return "fake_target"

    def _cleanup_path(self, path: str) -> None:
        if path in sys.path:
            sys.path.remove(path)
        sys.modules.pop("fake_target", None)

    # ── 基本能力 ──
    def test_reports_missing_attribute_as_error(self):
        """目标模块缺属性 + 读取点无保护 → error 级缺陷。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = self._make_target_module(tmp, "PRESENT = 1\n")
            _write(tmp, "consumer.py", """
def _ra():
    import fake_target
    return fake_target


def use_it():
    _r = _ra()
    return _r.MISSING
""")
            rep = check_reverse_attribute_contract(root=tmp, target_module=target)
            self.assertFalse(rep.passed)
            self.assertEqual(len(rep.defects), 1)
            d = rep.defects[0]
            self.assertEqual(d.kind, "reverse_attribute_contract")
            self.assertEqual(d.symbol, "MISSING")
            self.assertEqual(d.severity, "error")

    def test_existing_attribute_yields_no_defect(self):
        """属性存在 → 无缺陷。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = self._make_target_module(tmp, "PRESENT = 1\n")
            _write(tmp, "consumer.py", """
def _ra():
    import fake_target
    return fake_target


def use_it():
    _r = _ra()
    return _r.PRESENT
""")
            rep = check_reverse_attribute_contract(root=tmp, target_module=target)
            self.assertTrue(rep.passed)
            self.assertEqual(len(rep.defects), 0)

    # ── 写法覆盖：这是第一版实现漏掉的关键 ──
    def test_detects_alias_form(self):
        """`_r = _ra()` 之后 `_r.X` 的写法必须被检出。

        第一版实现只匹配 `_ra().X`，漏掉了别名写法——而别名写法恰恰是
        最常见的（Hermes 的 agent/system_prompt.py 全部走这条）。
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = self._make_target_module(tmp, "PRESENT = 1\n")
            _write(tmp, "consumer.py", """
def _ra():
    import fake_target
    return fake_target


def use_it():
    _r = _ra()
    return _r.MISSING
""")
            rep = check_reverse_attribute_contract(root=tmp, target_module=target)
            self.assertFalse(rep.passed)
            self.assertEqual(len(rep.defects), 1)
            self.assertEqual(rep.defects[0].symbol, "MISSING")

    def test_detects_direct_call_form(self):
        """`_ra().X` 的写法同样被检出。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = self._make_target_module(tmp, "PRESENT = 1\n")
            _write(tmp, "consumer.py", """
def _ra():
    import fake_target
    return fake_target


def use_it():
    return _ra().MISSING
""")
            rep = check_reverse_attribute_contract(root=tmp, target_module=target)
            self.assertFalse(rep.passed)
            self.assertEqual(rep.defects[0].symbol, "MISSING")

    # ── 严重度分级 ──
    def test_guarded_access_is_warning_not_error(self):
        """读取点被 try/except 包住 → warning（降级而非崩溃）。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = self._make_target_module(tmp, "PRESENT = 1\n")
            _write(tmp, "consumer.py", """
def _ra():
    import fake_target
    return fake_target


def use_it():
    _r = _ra()
    try:
        return _r.MISSING
    except AttributeError:
        return None
""")
            rep = check_reverse_attribute_contract(root=tmp, target_module=target)
            self.assertEqual(len(rep.defects), 1)
            self.assertEqual(rep.defects[0].severity, "warning")
            # warning 不阻断
            self.assertTrue(rep.passed)

    # ── 鲁棒性 ──
    def test_missing_root_reports_error(self):
        rep = check_reverse_attribute_contract(
            root="/nonexistent/path/xyz", target_module="os")
        self.assertFalse(rep.passed)
        self.assertEqual(rep.defects[0].kind, "scan_root_missing")

    def test_unimportable_target_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            rep = check_reverse_attribute_contract(
                root=td, target_module="definitely_not_a_real_module_xyz")
            self.assertFalse(rep.passed)
            self.assertEqual(rep.defects[0].kind, "target_module_import_failed")

    def test_syntax_error_file_is_counted_not_crashed(self):
        """坏文件不应让扫描崩溃。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = self._make_target_module(tmp, "PRESENT = 1\n")
            _write(tmp, "broken.py", "def ( this is not python\n")
            rep = check_reverse_attribute_contract(root=tmp, target_module=target)
            self.assertEqual(rep.files_failed, 1)
            self.assertEqual(rep.files_scanned, 1)  # 只有 fake_target.py

    def test_report_to_dict_shape(self):
        rep = ScanReport()
        rep.defects.append(Defect(
            kind="k", severity="error", symbol="s",
            location="p:1", message="m", hint="h"))
        d = rep.to_dict()
        self.assertFalse(d["passed"])
        self.assertEqual(d["defects"][0]["symbol"], "s")


if __name__ == "__main__":
    unittest.main()
