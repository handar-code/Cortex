"""Catalog 的 git 式版本化存储（设计稿 §13.1 catalog 快照 + §13.5 + §13.8 Q-3）。

每次重算产出 = 一次提交（commit），强制 change_reason，可回滚到任意历史提交；
catalog 的 active 即「当前已发布版」。并兜住「源码改了但还没提交进 catalog」的漂移窗口。

存储布局（纯文件，零外部依赖）：
    <store>/
      versions/<vid>.json   每条提交：catalog 快照 + 元数据
      HEAD                  最新提交 id
      active                当前已发布版本 id（可被 rollback 重指向任意历史）
      log.json              append-only 提交日志（审计线索）

设计原则（呼应 §13.8 零人可观测三铁律 + git 式验收三要素）：
- 每变必提交 / 每提交必带 change_reason / 任一提交可回滚。
- 漂移期读侧不破：active 始终可读；漂移只标脏、不卡读取。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .model import Catalog, sha256
from .pipeline import _scan_files, ingest, ingest_incremental


@dataclass
class CommitMeta:
    version_id: str
    parent: str | None
    change_reason: str
    agent_id: str
    created_at: str
    fingerprint: str          # catalog 内容哈希，用于完整性/漂移
    files_scanned: int
    files_valid: int
    symbol_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DriftReport:
    behind: bool                       # 工作区是否领先于已发布 catalog
    drifted_files: list[str]           # 内容哈希已变或新增
    removed_files: list[str]           # 已从磁盘消失
    unchanged: int
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class CatalogStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.versions_dir = self.root / "versions"

    # ---- 生命周期 ----
    @classmethod
    def init(cls, root: str | Path, project: str = "") -> "CatalogStore":
        root = Path(root)
        (root / "versions").mkdir(parents=True, exist_ok=True)
        store = cls(root)
        # 写入初始空日志
        if not (root / "log.json").exists():
            (root / "log.json").write_text("[]", encoding="utf-8")
        if not (root / "HEAD").exists():
            (root / "HEAD").write_text("", encoding="utf-8")
        if not (root / "active").exists():
            (root / "active").write_text("", encoding="utf-8")
        return store

    def exists(self) -> bool:
        return self.versions_dir.exists()

    # ---- 内部读写 ----
    def _counter(self) -> int:
        # 用版本数量作为序号（v1, v2, ...），简单且单调递增
        existing = list(self.versions_dir.glob("v*.json"))
        return len(existing) + 1

    def _read_active(self) -> str | None:
        v = (self.root / "active").read_text(encoding="utf-8").strip()
        return v or None

    def _write_active(self, vid: str) -> None:
        (self.root / "active").write_text(vid, encoding="utf-8")

    def _read_head(self) -> str | None:
        v = (self.root / "HEAD").read_text(encoding="utf-8").strip()
        return v or None

    def _append_log(self, meta: CommitMeta) -> None:
        log_path = self.root / "log.json"
        log = json.loads(log_path.read_text(encoding="utf-8") or "[]")
        log.append(meta.to_dict())
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 查询 ----
    def history(self) -> list[CommitMeta]:
        log_path = self.root / "log.json"
        if not log_path.exists():
            return []
        return [CommitMeta(**m) for m in json.loads(log_path.read_text(encoding="utf-8") or "[]")]

    def active_catalog(self) -> Catalog | None:
        vid = self._read_active()
        if not vid:
            return None
        return self.get_version(vid)

    def get_version(self, vid: str) -> Catalog:
        p = self.versions_dir / f"{vid}.json"
        if not p.exists():
            raise KeyError(f"版本不存在: {vid}")
        return Catalog.from_dict(json.loads(p.read_text(encoding="utf-8"))["catalog"])

    # ---- 漂移检测（§13.1 漂移兜底原则 1+2）----
    def detect_drift(self, config: Config) -> DriftReport:
        active = self.active_catalog()
        if active is None:
            return DriftReport(behind=False, drifted_files=[], removed_files=[], unchanged=0)
        # 重新扫描工作区当前文件哈希。
        # 必须与 ingest 用同一套 rel key：统一走 _scan_files（含 root 前缀 + 黑名单），
        # 否则多 root 时漂移检测会把「带前缀 key」对「不带前缀 key」全部误判为漂移。
        current: dict[str, str] = {}
        for path, rel in _scan_files(config):
            current[rel] = sha256(path.read_text(encoding="utf-8", errors="ignore"))
        drifted, removed, unchanged = [], [], 0
        for rel, h in current.items():
            if rel not in active.file_hashes:
                drifted.append(rel)
            elif active.file_hashes[rel] != h:
                drifted.append(rel)
            else:
                unchanged += 1
        for rel in active.file_hashes:
            if rel not in current:
                removed.append(rel)
        return DriftReport(
            behind=bool(drifted or removed),
            drifted_files=sorted(drifted),
            removed_files=sorted(removed),
            unchanged=unchanged,
            details={"current_count": len(current), "stored_count": len(active.file_hashes)},
        )

    # ---- 提交（原子写新版本 + 发布为 active）----
    def _commit(self, catalog: Catalog, change_reason: str, agent_id: str, set_active: bool = True) -> CommitMeta:
        vid = f"v{self._counter()}"
        parent = self._read_head() or None
        fp = sha256(json.dumps(catalog.to_dict()["symbols"], sort_keys=True, ensure_ascii=False))
        meta = CommitMeta(
            version_id=vid,
            parent=parent,
            change_reason=change_reason,
            agent_id=agent_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            fingerprint=fp,
            files_scanned=catalog.files_scanned,
            files_valid=catalog.files_valid,
            symbol_count=len(catalog.symbols),
        )
        payload = {"meta": meta.to_dict(), "catalog": catalog.to_dict()}
        (self.versions_dir / f"{vid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.root / "HEAD").write_text(vid, encoding="utf-8")
        if set_active:
            self._write_active(vid)  # 发布：默认新提交即 active
        # 否则保持原 active 不变（仅构建版本，不发布）
        self._append_log(meta)
        return meta

    def recompute(
        self,
        config: Config,
        change_reason: str,
        agent_id: str = "system",
        set_active: bool = True,
    ) -> CommitMeta:
        """增量重算并提交（§13.1 增量重算 + §13.8 git 式提交）。

        - 已有 active → 在其基础上增量重算；
        - 无 active（首次）→ 全量重算作为初始提交（v1）。
        - set_active=False → 构建版本但不发布（供 git hook / Agent 动态维护使用；
          **只有 CI 发布权**才以 set_active=True 调用，落实「触发分权」）。
        """
        prev = self.active_catalog()
        catalog = (
            ingest(config, out_path=None)
            if prev is None
            else ingest_incremental(config, prev, out_path=None)
        )
        return self._commit(catalog, change_reason, agent_id, set_active=set_active)

    # ---- 漂移自动对账（§13.1 漂移兜底原则 3）----
    def reconcile(
        self, config: Config, agent_id: str = "auto-reconciler", set_active: bool = False
    ) -> CommitMeta | None:
        report = self.detect_drift(config)
        if not report.behind:
            return None
        reason = (
            f"auto-reconcile: 工作区漂移 {len(report.drifted_files)} 变更 / "
            f"{len(report.removed_files)} 删除"
        )
        # 默认仅构建版本、不发布（落实「只有 CI 有发布权」的触发分权）
        return self.recompute(config, reason, agent_id, set_active=set_active)

    # ---- 回滚（§13.8 Q-3：任一提交可重新置为 active）----
    def rollback(self, vid: str) -> None:
        if not (self.versions_dir / f"{vid}.json").exists():
            raise KeyError(f"无法回滚：版本不存在 {vid}")
        self._write_active(vid)  # 仅重指向，不删除新版本

    # ---- TTL 巡检兜底（§13.8 U3：长 TTL 巡检，漂移存疑时重抽）----
    def ttl_patrol(
        self, config: Config, max_age_hours: float = 24.0, agent_id: str = "ttl-patrol"
    ) -> CommitMeta | None:
        active = self.active_catalog()
        if active is None:
            return self.recompute(config, "ttl-patrol: 无 active，初始化", agent_id)
        created = datetime.fromisoformat(active.generated_at)
        age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        if age_h >= max_age_hours:
            # 长 TTL 兜底：先查漂移，有变则重算，无变只累加 stale 计数（不重裁）
            return self.reconcile(config) or self.recompute(
                config, "ttl-patrol: 超时但无漂移，刷新时间戳", agent_id
            )
        return None
