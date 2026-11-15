"""核心数据模型：Symbol（统一接口）与 Catalog（快照）。

方案甲统一接口：各语言提取器都归一化到 Symbol[]，上游（甄别/归因/渲染/索引）
只认这一个结构，与具体语言解耦。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def sha256(text: str) -> str:
    """内容哈希（用于增量重算与漂移检测）。取前 16 位即可区分。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class Symbol:
    name: str
    kind: str                 # module | class | function | method | section
    domain: str | None        # 裁决后归属域（§12.6）；显式键/隐式轨/结构域平等融合的结果
    docstring: str
    signature: str
    source_ref: str           # 相对路径:行号
    content_hash: str         # 该符号源码文本哈希
    validity: str = "valid"   # valid | test | generated | unknown
    reason: str = ""
    # ---- §12 归因双轨 + §12.7 catalog（符号→域 + 来源 + 置信度 + 分布 + conflict）+ §13.4 三件套 ----
    annotation: str | None = None        # 注释：@domain 显式键（显式轨，人工信号）——可空，真实项目常无
    annotation_text: str = ""           # 注释文本：docstring / 可读 note（文本方向产物）——独立并行通道
    content: str = ""                   # 全文/长文本载体（MD 等无结构文档的全文、fenced 代码块注释等）。
                                         # 用于向量嵌入与文本通道检索，与显示用 docstring 预览解耦；默认空不破坏代码符号
    domain_source: str = ""             # explicit | implicit | both_agree | conflict | uncertain | structural
    domain_confidence: float = 0.0      # 归属置信度
    domain_distribution: dict = field(default_factory=dict)  # 软分布 {域: 相似度}，§12.3 多域重归属
    conflict_flag: bool = False         # 显式键 ⇄ 向量 冲突（§12.6），交 Agent 裁决、不自动偏向

    def to_dict(self) -> dict:
        return asdict(self)

    def triple(self) -> dict:
        """§13.4 三件套 + 域裁决元数据，供检索 payload / llms 页并列返回，不预融合。"""
        return {
            "annotation": self.annotation,               # 注释：@domain 显式键
            "annotation_text": self.annotation_text,     # 注释文本：docstring / note
            "domain": self.domain,                       # 裁决后归属域
            "domain_source": self.domain_source,
            "domain_confidence": round(self.domain_confidence, 4),
            "domain_distribution": self.domain_distribution,
            "conflict_flag": self.conflict_flag,
        }


@dataclass
class Catalog:
    project: str
    generated_at: str
    root_paths: list[str]
    files_scanned: int = 0
    files_valid: int = 0
    symbols: list[Symbol] = field(default_factory=list)
    # rel_path -> 文件内容哈希；供增量重算与漂移检测使用（§13.1 catalog 快照 + 漂移兜底）
    file_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "generated_at": self.generated_at,
            "root_paths": self.root_paths,
            "files_scanned": self.files_scanned,
            "files_valid": self.files_valid,
            "symbols": [s.to_dict() for s in self.symbols],
            "file_hashes": self.file_hashes,
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, d: dict) -> "Catalog":
        return cls(
            project=d["project"],
            generated_at=d.get("generated_at", ""),
            root_paths=d.get("root_paths", []),
            files_scanned=d.get("files_scanned", 0),
            files_valid=d.get("files_valid", 0),
            symbols=[Symbol(**s) for s in d.get("symbols", [])],
            file_hashes=d.get("file_hashes", {}) or {},
        )
