"""写侧：MCP git 式提交通道（设计稿 §13.7 Q-2 + §13.8 Q-3）。

所有程序化写入（回写候选标注、带 change_reason 提交、回滚、切 active）一律走此通道。
- **非只读签名**：工具名/语义明确为「写」，与读侧 Skill 物理分离（双通道硬分离）。
- **字段白名单**：提交 payload 仅允许预声明字段，多余字段拒绝（防提示注入扩权）。
- **幂等键**：同一 idempotency_key 重复提交返回首次结果，不重复落库。
- 审计由 Phase 6 外部探针写 append-only，本通道不写审计日志（审计外部化）。

本模块提供两层：
1. `WriteChannel` —— 确定性可单测的写操作核心（直接调用）。
2. `MCP_TOOLS` + `handle_tool()` —— 映射到 MCP 工具 schema 的适配层；若环境装有 `mcp` 包，
   可经 `build_mcp_server()` 起 stdio 服务（见模块底部）。测试只依赖第 1 层。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import Config
from ..model import Symbol
from ..store import CatalogStore

# 提交 payload 允许的字段（白名单）；任何超集字段都拒绝
ALLOWED_SYMBOL_FIELDS = {
    "name", "kind", "domain", "docstring", "signature",
    "source_ref", "validity", "reason", "content_hash",
}

# 候选层（软标注）允许的字段（比正式提交更受限）
ALLOWED_CANDIDATE_FIELDS = {"source_ref", "domain", "note", "proposed_by"}


def _filter_whitelist(d: dict, allowed: set[str]) -> dict:
    bad = set(d.keys()) - allowed
    if bad:
        raise ValueError(f"字段越权（白名单外）：{sorted(bad)}")
    return {k: d[k] for k in d if k in allowed}


class WriteChannel:
    def __init__(self, store: CatalogStore):
        self.store = store
        self._idem_path = store.root / "idempotency.json"
        self._cfg: Config | None = None

    def set_config(self, config: Config) -> "WriteChannel":
        """注入目标项目 Config（store 不持有 config，由调用方注入）。"""
        self._cfg = config
        return self

    def _load_idem(self) -> dict:
        if self._idem_path.exists():
            return json.loads(self._idem_path.read_text(encoding="utf-8"))
        return {}

    def _save_idem(self, data: dict) -> None:
        self._idem_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def commit_catalog_change(
        self,
        change_reason: str,
        symbols: list[dict] | None = None,
        idempotency_key: str | None = None,
        agent_id: str = "mcp-writer",
    ) -> dict:
        """git 式提交：强制 change_reason，可幂等。

        - 若提供 idempotency_key 且已用过 → 返回首次结果（幂等，不重复落库）。
        - symbols 仅作记录/候选来源校验（白名单过滤）；实际 catalog 重算由 store 增量完成。
        """
        if not change_reason or not change_reason.strip():
            raise ValueError("change_reason 必填（git 式验收三要素之一）")

        if idempotency_key:
            idem = self._load_idem()
            if idempotency_key in idem:
                return {"idempotent": True, **idem[idempotency_key]}

        # 白名单校验提交 payload
        if symbols is not None:
            for s in symbols:
                _filter_whitelist(s, ALLOWED_SYMBOL_FIELDS)

        meta = self.store.recompute(self._config(), change_reason, agent_id)
        result = {
            "version_id": meta.version_id,
            "change_reason": meta.change_reason,
            "symbol_count": meta.symbol_count,
            "active": self.store._read_active(),
        }
        if idempotency_key:
            idem = self._load_idem()
            idem[idempotency_key] = result
            self._save_idem(idem)
        return {"idempotent": False, **result}

    def rollback(self, version_id: str) -> dict:
        self.store.rollback(version_id)
        return {"rolled_back_to": version_id, "active": self.store._read_active()}

    def propose_candidate(self, candidate: dict, proposed_by: str = "mcp-writer") -> dict:
        """回写候选层软标注（建议权，非发布权）。

        写入候选层文件 `candidates.jsonl`（仅建议，CI 校验门/发布权由 Phase 6 决定）。
        字段走更严白名单 ALLOWED_CANDIDATE_FIELDS。
        """
        c = _filter_whitelist(candidate, ALLOWED_CANDIDATE_FIELDS)
        c["proposed_by"] = proposed_by
        cand_path = self.store.root / "candidates.jsonl"
        with cand_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
        return {"accepted": True, "candidate": c}

    def _config(self) -> Config:
        # store 不直接持 config；写通道从外部注入。若未注入，回退到 store 同级 config 的约定。
        if getattr(self, "_cfg", None) is not None:
            return self._cfg
        raise RuntimeError("WriteChannel 需要注入 Config（set_config）")


# ----------------------- MCP 适配层 -----------------------
MCP_TOOLS = [
    {
        "name": "commit_catalog_change",
        "description": "git 式提交 catalog 变更（写）。强制 change_reason，支持幂等键。",
        "input_schema": {
            "type": "object",
            "properties": {
                "change_reason": {"type": "string"},
                "symbols": {"type": "array", "items": {"type": "object"}},
                "idempotency_key": {"type": "string"},
                "agent_id": {"type": "string"},
            },
            "required": ["change_reason"],
        },
    },
    {
        "name": "rollback_catalog",
        "description": "回滚 active 到指定历史版本（写）。",
        "input_schema": {
            "type": "object",
            "properties": {"version_id": {"type": "string"}},
            "required": ["version_id"],
        },
    },
    {
        "name": "propose_candidate",
        "description": "回写候选层软标注（建议权，非发布权）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate": {"type": "object"},
                "proposed_by": {"type": "string"},
            },
            "required": ["candidate"],
        },
    },
]


def handle_tool(name: str, args: dict, channel: WriteChannel) -> dict:
    """MCP 工具分发（确定性，可单测）。"""
    if name == "commit_catalog_change":
        return channel.commit_catalog_change(
            change_reason=args["change_reason"],
            symbols=args.get("symbols"),
            idempotency_key=args.get("idempotency_key"),
            agent_id=args.get("agent_id", "mcp-writer"),
        )
    if name == "rollback_catalog":
        return channel.rollback(args["version_id"])
    if name == "propose_candidate":
        return channel.propose_candidate(args["candidate"], args.get("proposed_by", "mcp-writer"))
    raise KeyError(f"未知工具: {name}")


def build_mcp_server(channel: WriteChannel):
    """若环境装有 mcp 包，返回已注册工具的 mcp.Server；否则返回 None（离线降级）。

    离线时写通道仍可由 CLI/CI 直接调用 WriteChannel（见 §13.7 双通道：MCP 是形态之一）。
    """
    try:
        from mcp.server import Server  # type: ignore
    except ImportError:
        return None
    app = Server("doc-system-write")

    @app.list_tools()
    async def list_tools():
        return MCP_TOOLS

    @app.call_tool()
    async def call_tool(name: str, arguments: dict):
        return handle_tool(name, arguments, channel)

    return app


def serve_stdio(channel: WriteChannel) -> None:
    """stdio 服务入口（仅当 mcp 可用时真正起服务；否则提示降级）。"""
    app = build_mcp_server(channel)
    if app is None:
        raise RuntimeError("未安装 mcp 包：离线降级，写通道由 CLI/CI 直接调用 WriteChannel。")
    import anyio  # type: ignore

    anyio.run(app.run_stdio_async)
