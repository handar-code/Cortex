"""提取器统一接口（方案甲统一接口的实现契约）。

每个语言适配器都必须实现 extract()，返回统一 Symbol[]。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..model import Symbol


class Extractor(ABC):
    @abstractmethod
    def extract(
        self,
        rel_path: str,
        text: str,
        validity: str,
        reason: str,
    ) -> list[Symbol]:
        """从单个文件文本中提取符号，归一化为 Symbol[]。"""
        ...

    @property
    @abstractmethod
    def kinds(self) -> list[str]:
        """该提取器能产出的 Symbol.kind 列表（仅用于自描述/校验）。"""
        ...
