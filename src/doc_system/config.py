"""配置模型与加载。

系统不硬编码任何项目：roots / blacklist / languages / triage 全部来自配置文件。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class LanguageConfig:
    name: str
    extensions: list[str]
    extractor: str


@dataclass
class EmbeddingConfig:
    # provider（三档，依赖/语义由弱到强）：
    #   offline      确定性哈希向量，纯标准库、零依赖、零成本、可复现（断网/无 key/零配置兜底）
    #   siliconflow  BAAI/bge-m3 语义向量，经硅基流动 API（当前免费、不限次、不限速；1024 维）
    #   local        （可选扩展）本地向量模型，真语义+数据不出本机+断网可用，需装运行时与权重
    # 代码默认 offline（零配置即跑、单元测试无 key 也能过）；真实部署在 config.yaml 把
    # provider 设为 siliconflow（推荐：corpus 全量真语义、本机零负担）。
    provider: str = "offline"
    model: str = "BAAI/bge-m3"
    api_base: str = "https://api.siliconflow.cn/v1/embeddings"
    api_key_env: str = "SILICONFLOW_API_KEY"
    batch_size: int = 32


@dataclass
class AttributionConfig:
    """§12 归因双轨配置（零人、确定性、配置驱动；默认开）。"""
    enabled: bool = True              # 是否启用归因（domain 永不因缺 @domain 而全空）
    # 隐式轨向量来源（三档同 EmbeddingConfig.provider）：offline 确定性 / siliconflow 真语义 /
    # local 本地模型。代码默认 offline；真实部署在 config.yaml 设 siliconflow（推荐）。
    provider: str = "offline"
    scope_depth: int = 1             # 域骨架层级：1=scope，2=scope/module（§13.4 Q5 目录树）
    implicit_threshold: float = 0.0  # 隐式归属置信阈值，低于则 uncertain
    text_channel: bool = True        # §13.4 注释文本(docstring)独立检索通道
    # 无显式键时 domain 的归属键：
    #   structural = 目录树 scope（Q5 分片、确定性可复现，向量软分布并列作隐式轨）— 默认
    #   vector     = 向量 top 域（§12.5「向量自动入域」，语义强但随模型漂移）
    domain_from: str = "structural"


@dataclass
class RetrievalConfig:
    """§13.4 检索增强（选项 C · 查询时重排）。

    默认 rerank=None：向量通道用 corpus 的原生 embedder（offline 哈希 / 已建的真语义索引）。
    rerank="siliconflow"：在查询时对 top-N 候选用 bge-m3 真语义重排，覆盖 offline 哈希噪声；
    按次计费、零额外离线成本，质量接近全量真语义索引（推荐解）。
    注意：当 corpus 已是 bge-m3 真语义索引（embedding.provider=siliconflow）时，查询时 query
    也用 bge-m3 嵌入直接比对，排序即真语义 → rerank 冗余，应置 null。仅 corpus 退回 offline
    哈希时，才需要 rerank=siliconflow 补语义。
    """
    rerank: str = None   # None | "siliconflow"（其他 provider 经 EmbeddingConfig 扩展）
    rerank_candidates: int = 20  # 重排候选池大小（越大召回越全、API 调用越多）


@dataclass
class Config:
    project_name: str
    roots: list[str]
    blacklist: list[str]
    languages: list[LanguageConfig]
    triage_test_patterns: list[str]
    triage_generated_patterns: list[str]
    base_dir: Path  # 配置文件所在目录，用于解析相对根路径
    optional: dict = None  # 附加产物开关（typedoc_site / pagefind / demo 等，默认全关）
    embedding: EmbeddingConfig = None  # 向量通道 provider 配置
    attribution: AttributionConfig = None  # §12 归因双轨配置
    retrieval: RetrievalConfig = None  # §13.4 检索增强（选项 C · 查询时重排）
    index_neighbor_pool: int = 0  # 近邻候选池上限（0=精确全量 O(n²)；大项目设 >0 启用采样近似 O(n·pool)）
    root_files_only: set = None  # 声明 files_only:true 的 root：只扫顶层文件、不递归（纳入根级核心代码而不拖入巨型子目录）

    @staticmethod
    def _load_local_env(base_dir: Path) -> None:
        """把项目根目录的 .env 注入环境变量（不覆盖已存在的）。便于本地放 API key 而不进源码。"""
        env_path = base_dir / ".env"
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        p = Path(path)
        cls._load_local_env(p.parent)
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        proj = raw.get("project", {})
        raw_roots = raw.get("roots", [])
        # root 支持两种写法：字符串（旧，递归）或 {path, files_only}（新，可只扫顶层文件）
        roots = [(r["path"] if isinstance(r, dict) else r) for r in raw_roots]
        root_files_only = {
            r["path"] for r in raw_roots if isinstance(r, dict) and r.get("files_only")
        }
        blacklist = raw.get("blacklist", [])
        languages = [LanguageConfig(**l) for l in raw.get("languages", [])]
        triage = raw.get("triage", {})
        return cls(
            project_name=proj.get("name", "unnamed"),
            roots=roots,
            root_files_only=root_files_only,
            blacklist=blacklist,
            languages=languages,
            triage_test_patterns=triage.get("test_patterns", []),
            triage_generated_patterns=triage.get("generated_patterns", []),
            base_dir=p.parent,
            optional=raw.get("optional", {}) or {},
            embedding=EmbeddingConfig(**raw.get("embedding", {})),
            attribution=AttributionConfig(**raw.get("attribution", {})),
            retrieval=RetrievalConfig(**(raw.get("retrieval", {}) or {})),
            index_neighbor_pool=int((raw.get("index", {}) or {}).get("neighbor_pool", 0)),
        )
