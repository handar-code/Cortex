"""触发分权（设计稿 §13.5 + §14 运行主体）。

三种触发角色，权力各不相同（结构性分权，呼应 §14 不自审）：
- **git hook**：代码提交时触发 reconcile → 仅**构建**候选版本（set_active=False），不发布。
- **Agent 动态维护**：Agent 运行中按需触发 reconcile → 同样仅构建，不发布。
- **CI 发布权**：唯一能 `set_active=True` 的角色（ci_publish）。发布前先跑质量门禁(gate)，
  门禁不过则 fail-closed 拒绝发布。

审计：所有提交由 store 落 append-only 日志（审计外部化，Agent 写不到）。
"""
from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..store import CatalogStore
from .gate import check_gate


def git_hook_script() -> str:
    """生成 pre-commit hook 脚本（触发 reconcile，不发布）。"""
    return """#!/usr/bin/env bash
# 文档系统 pre-commit 钩子：触发 catalog 漂移对账（仅构建候选版本，不发布）
set -e
doc-system reconcile --config doc_system.config.yaml --store .docstore || true
# 真正的发布由 CI 流水线执行（唯一发布权）
"""


def git_hook_reconcile(store: CatalogStore, config: Config) -> object:
    """git hook 触发：仅构建候选版本，不发布。"""
    return store.reconcile(config, agent_id="git-hook", set_active=False)


def agent_dynamic_maintain(store: CatalogStore, config: Config) -> object:
    """Agent 动态维护触发：仅构建候选版本，不发布。"""
    return store.reconcile(config, agent_id="agent-dynamic", set_active=False)


def ci_publish(
    store: CatalogStore,
    config: Config,
    change_reason: str,
    provenance_min: float = 0.95,
) -> dict:
    """CI 发布权（唯一发布路径）。

    发布前跑质量门禁；门禁不过 → fail-closed 拒绝发布（返回 passed=False）。
    过 → recompute(set_active=True) 原子切换 active。
    """
    # 先构建候选版本（不发布），对其跑质量门禁
    preview = store.recompute(config, f"ci-preview: {change_reason}", agent_id="ci", set_active=False)
    candidate = store.get_version(preview.version_id)
    gate = check_gate(candidate, provenance_min)
    if not gate.passed:
        # fail-closed：回退 active 到之前，不发布
        return {
            "published": False,
            "reason": "gate failed (fail-closed)",
            "gate": gate.to_dict(),
            "active": store._read_active(),
        }
    # 发布：把候选版本置为 active
    store.rollback(preview.version_id)  # rollback 即「重指向该版本为 active」
    return {
        "published": True,
        "version_id": preview.version_id,
        "gate": gate.to_dict(),
        "active": store._read_active(),
    }


def _empty_catalog():
    from ..model import Catalog
    return Catalog(project="__none__", generated_at="", root_paths=[])
