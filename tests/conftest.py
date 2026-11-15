"""Pytest 全局配置：无 API key 时强制 offline embedding，保证 CI 零依赖全绿。

doc-system 的默认部署用 siliconflow（BAAI/bge-m3 真语义），但单元测试不应依赖
网络/密钥。因此：

- 未设置 `SILICONFLOW_API_KEY`（CI 干净克隆 / 本地无 .env）时，在 *conftest 导入时*
  就把 index 与 attribution 两个模块各自的 `get_embedder` 绑定都换成 `OfflineEmbedder`
  （确定性哈希向量，纯标准库、零依赖、可复现）；
- 设置了密钥（本地 .env 或 CI secret）时，保留 config 声明的真实 provider，
  顺带验证真语义路径。

为什么放在模块导入时（而非 fixture 内）：
`unittest.TestCase.setUpClass` 在 pytest 的 per-test 函数级 fixture 之前执行，若放在
fixture 里，setUpClass 里的 `ingest()` 仍会走到需要 key 的 siliconflow 分支而报错。
模块级替换在收集开始前即生效，覆盖 setUpClass / setUpModule 的所有调用。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import doc_system.attribution as attr_mod  # noqa: E402
import doc_system.index as idx_mod  # noqa: E402
from doc_system.index import OfflineEmbedder  # noqa: E402

HAS_KEY = bool(os.environ.get("SILICONFLOW_API_KEY"))


def _offline_embedder(_cfg=None):
    """忽略 config 声明的 provider，强制离线确定性向量（无需 key / 网络）。"""
    return OfflineEmbedder()


if not HAS_KEY:
    # attribution 在 import 时已 `from .index import get_embedder` 绑定到自己的命名空间，
    # 因此必须同时替换 `idx_mod.get_embedder` 与 `attr_mod.get_embedder` 两处引用，
    # 否则 attribution 仍会走到需要 key 的 siliconflow 分支。
    idx_mod.get_embedder = _offline_embedder
    attr_mod.get_embedder = _offline_embedder
