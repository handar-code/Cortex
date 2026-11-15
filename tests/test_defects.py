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


class ImportContractTest(unittest.TestCase):
    """导入契约检测（check_import_contract）。

    重点覆盖那些容易产生假阳性的写法——这些坑都是在真实 Hermes 项目上
    逐个踩出来并修掉的。
    """
    from doc_system.defects import check_import_contract as _check

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.addCleanup(self.td.cleanup)
        # pyproject.toml 让扫描器把这里当仓库根
        _write(self.root, "pyproject.toml", "[project]\nname='fixture'\n")

    def check(self, root=None):
        return ImportContractTest._check(root=root or self.root)

    # ── 真缺陷 ──
    def test_missing_symbol_is_error(self):
        _write(self.root, "pkg/__init__.py", "def present(): pass\n")
        _write(self.root, "use.py", "from pkg import missing\n")
        rep = self.check()
        self.assertFalse(rep.passed)
        self.assertEqual(len(rep.defects), 1)
        self.assertEqual(rep.defects[0].symbol, "missing")
        self.assertEqual(rep.defects[0].kind, "import_contract")

    def test_reexported_symbol_is_ok(self):
        """`from .impl import X` 形式的 re-export 不能被误判为缺失。"""
        _write(self.root, "pkg/__init__.py", "from .impl import present  # noqa: F401\n")
        _write(self.root, "pkg/impl.py", "def present(): pass\n")
        _write(self.root, "use.py", "from pkg import present\n")
        self.assertTrue(self.check().passed)

    def test_package_submodule_import_is_ok(self):
        """`from pkg import submodule` 合法，即使 __init__.py 没显式 import。"""
        _write(self.root, "pkg/__init__.py", "")
        _write(self.root, "pkg/helper.py", "X = 1\n")
        _write(self.root, "use.py", "import pkg.helper\n")
        self.assertTrue(self.check().passed)

    def test_relative_import_inside_package_is_ok(self):
        """包内 `from .config import X` —— package 必须按文件所在包算，
        按模块名算会解析出 pkg.mod.config 这个不存在的路径 → 假阳性。"""
        _write(self.root, "pkg/__init__.py", "")
        _write(self.root, "pkg/config.py", "VALUE = 1\n")
        _write(self.root, "pkg/consumer.py", "from .config import VALUE\n")
        self.assertTrue(self.check().passed)

    def test_from_dot_import_submodule_is_ok(self):
        """`from . import jiter_preload as _x` 形式。"""
        _write(self.root, "pkg/__init__.py", "from . import jiter_preload  # noqa: F401\n")
        _write(self.root, "pkg/jiter_preload.py", "X = 1\n")
        self.assertTrue(self.check().passed)

    def test_missing_relative_submodule_is_error(self):
        _write(self.root, "pkg/__init__.py", "from . import nope\n")
        rep = self.check()
        self.assertFalse(rep.passed)
        self.assertTrue(any(d.symbol == "nope" for d in rep.defects))

    def test_external_import_is_skipped(self):
        """外部依赖不推测 —— 不该因为没装就报缺陷。"""
        _write(self.root, "use.py", "from some_third_party_pkg import whatever\n")
        self.assertTrue(self.check().passed)

    def test_scan_subdir_still_indexes_whole_repo(self):
        """root 传子目录时，仍要索引全仓，否则跨目录的内部导入会被误判。"""
        _write(self.root, "providers/__init__.py", "def list_providers(): pass\n")
        _write(self.root, "hermes_cli/auth.py", "from providers import list_providers\n")
        rep = self.check(root=self.root / "hermes_cli")
        self.assertTrue(rep.passed, msg=str([d.message for d in rep.defects]))
        self.assertEqual(rep.files_scanned, 1)
