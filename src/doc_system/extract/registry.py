"""提取器注册表（方案乙 ↔ 方案甲统一接口的桥）。

config 里语言声明 extractor 名（如 python_ast），这里把它映射到具体实例。
新增语言：写好适配器后在 _REGISTRY 注册即可，下游无需改动。
"""
from __future__ import annotations

from .base import Extractor
from .plaintext import PlainTextExtractor
from .python import PythonAstExtractor

_REGISTRY: dict[str, Extractor] = {
    "python_ast": PythonAstExtractor(),
    "plaintext": PlainTextExtractor(),
    # "typescript_typedoc": TypeScriptExtractor(),  # 占位，后续 Phase
}


def get_extractor(name: str) -> Extractor:
    if name not in _REGISTRY:
        raise KeyError(f"未知提取器: {name}（已注册: {list(_REGISTRY)}）")
    return _REGISTRY[name]


def extractor_for_extension(ext: str, languages: list) -> str | None:
    """按文件扩展名找到对应语言的 extractor 名；找不到返回 None（留给后续/AI 升级）。"""
    for lang in languages:
        if ext in lang.extensions:
            return lang.extractor
    return None
