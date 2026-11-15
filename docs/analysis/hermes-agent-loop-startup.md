# Hermes Agent 启动顺序与 Agent 循环结构分析报告

> 分析对象（已通读）：`run_agent.py`(9024 行)、`hermes_cli/main.py`(12815 行)、`cli.py`(18743 行)
> 辅助通读：`agent/conversation_loop.py`(7665 行，真正的循环体所在)、`hermes_cli/__init__.py`(92 行)
> 约束：未修改 `<target-project>` 中任何文件，纯分析。所有行号均来自实际文本定位。

---

## 1. 概览：三文件规模、入口函数链、彼此关系

### 1.1 入口注册（pyproject.toml `[project.scripts]`）
```
hermes            = "hermes_cli.main:main"   # 当前主入口
hermes-agent      = "run_agent:main"         # 独立 agent 演示/脚本入口
hermes-acp        = "acp_adapter.entry:main" # ACP 适配入口（不在本次范围）
```

### 1.2 三个文件的真实角色（与任务预设的重要修正）
**任务预设："run_agent.py 是 Agent 主循环，是整个项目的心脏"。经通读后必须修正这一判断：**

| 文件 | 真实角色 | 关键入口 |
|------|----------|----------|
| `run_agent.py` | **门面/兼容性外壳（facade）**。含 `AIAgent` 巨型类（416–8580，约 8164 行），但该类 90% 方法是 `Forwarder — see agent.<submodule>` 形式的转发器；真正的循环逻辑已被抽到 `agent/` 子模块。`main()`(8808) 是最简 AIAgent 演示入口。`run_agent.py` 自身只保留 re-export 块(107–226)、client/锁/流写入管理、main()。 | `main()` @8808；`AIAgent` @416；`run_conversation`(转发) @8616 |
| `agent/conversation_loop.py` | **真正的 Agent 主循环**（被 run_agent 转发到这里）。`run_conversation` @1410，主 `while` 循环 @1598。 | `run_conversation` @1410；循环 @1598 |
| `hermes_cli/main.py` | **外层命令分发器（umbrella CLI）**。argparse 注册约 50 个子命令（`hermes chat/gateway/setup/status/...`），`main()` @11287。 | `main()` @11287；`cmd_chat` @2579；`build_top_level_parser` @11005/11335 |
| `cli.py` | **内层交互式 TUI（REPL）**。18743 行，定义 `HermesCLI` 与 `main()` @18214。本身**不登记为 console_script**，而是被 `hermes_cli/main.py` 的 `cmd_chat` 调用。 | `main()` @18214；`HermesCLI` 类 |

### 1.3 入口函数链（最重要的事实）
```
用户执行 hermes
  └─ hermes_cli.main:main()                         [hermes_cli/main.py:11287]
       ├─ build_top_level_parser()                  [hermes_cli/main.py:11005]
       └─ 分发到 cmd_* 子命令
            └─ cmd_chat()                           [hermes_cli/main.py:2579]
                 └─ from cli import main as cli_main [hermes_cli/main.py:2803]
                      └─ cli.main()                 [cli.py:18214]  ← 交互式 REPL
                           └─ AIAgent.run_conversation(user_query)
                                └─ run_conversation (run_agent 转发器) [run_agent.py:8616]
                                     └─ agent.conversation_loop.run_conversation [conversation_loop.py:1410]
                                          └─ while (...) 主循环   [conversation_loop.py:1598]

用户执行 hermes-agent
  └─ run_agent:main()                              [run_agent.py:8808]
       └─ AIAgent(...) → agent.run_conversation(user_query)  [run_agent.py:8977]
            └─ (同上) conversation_loop.run_conversation
```

**结论**：`run_agent.py` 与 `cli.py`、`hermes_cli/main.py` 不是"重复实现"。三者是分层关系——`run_agent.main` 是最简脚本入口，`hermes_cli.main` 是外层命令壳，`cli.py` 是内层交互 REPL，三者最终都汇聚到 `agent.conversation_loop.run_conversation`。`run_agent.py` 的 `AIAgent` 大量方法已是转发器，**物理拆分时它应主要成为"保留 re-export 与 main() 的薄兼容层"**。

---

## 2. A. Agent 循环运行时序（带行号的阶段表）

> 循环体真实位置：`agent/conversation_loop.py`。以下行号均指该文件，除非特别说明。

### 2.1 主循环结构
- 进入：`run_conversation()` @1410；外层 `while` 循环 @1598：
  ```python
  while (api_call_count < agent.max_iterations and agent.iteration_budget.remaining > 0) or agent._budget_grace_call:
  ```
  - `max_iterations` 默认 **90**（run_agent.py:450，构造函数参数），但 `run_agent.main()` 把 `max_turns`(默认 10，run_agent.py:8813) 作为 `max_iterations` 传入。
  - 内层重试循环 `while retry_count < max_retries:` @2382（`max_retries = agent._api_max_retries`，默认 **3**，config.yaml `api_max_retries` 可配，run_agent.py:869–874）。
- 退出条件：
  - 正常结束：模型返回 `finish_reason` 无工具调用，循环自然走完后续逻辑后 `return`（约 7361 之后的收尾）。
  - 达上限：`api_call_count >= max_iterations` 或 `iteration_budget.remaining <= 0` → `_handle_max_iterations` 收尾（run_agent.py:8582 转发 `agent.chat_completion_helpers`）。
  - 用户中断：`agent._interrupt_requested` @1613 → `interrupted=True; break` @1618；或 `/redirect`、`/steer` 在响应返回时交叉（@2701–2715）。
  - 异常：`run_conversation` 的 `except BaseException`（run_agent.py:8738）将 KeyboardInterrupt/TimeoutError 映射为 cancelled/timed_out 后 `raise`。

### 2.2 每轮（每个 API 迭代）的阶段表
下表给出单轮迭代内的阶段、行号、同步/异步性质、耗时说明。

| # | 阶段 | 行号 | 同步/异步 | 耗时/阻塞说明 |
|---|------|------|-----------|----------------|
| 1 | 进入迭代、redirect 抽取、`_checkpoint_mgr.new_turn()` | 1599–1610 | 同步 | 轻量 |
| 2 | 中断检查 `_interrupt_requested` → break | 1613–1618 | 同步 | 轻量 |
| 3 | `api_call_count += 1`；`iteration_budget.consume()` | 1620–1633 | 同步 | 轻量（预算记账） |
| 4 | `step_callback`（gateway hooks，agent:step 事件） | 1636–1661 | 同步 | 轻量（异常被吞） |
| 5 | 前置 steer 抽取并注入工具消息 | 1669–1718 | 同步（持 `_pending_steer_lock`） | 轻量 |
| 6 | **构造 prompt**：`_sanitize_tool_call_arguments`、`repair_message_sequence_with_cursor`、逐条 `_clone_message_for_send`、注入 system/用户标签、MoA 上下文、prefill、context-engine 选择、sanitize、去 thinking-only、归一化空白、`_canonicalize_api_tool_calls`、去 surrogate、`build_prompt_cache_plan` 缓存标记 | 1720–2081 | 同步 | **O(n) per 迭代**（n=消息数）；每次全量重建 `api_messages` 列表并逐条深克隆/清洗。**随对话变长线性增长**，是单轮固定开销的主要来源 |
| 7 | token 估算 `estimate_messages_tokens_rough` + 工具 token | 2111–2124 | 同步 | O(n)，每轮都跑 |
| 8 | **Pre-API 压缩门**：`should_compress()` 判定 → `agent._compress_context()` | 2158–2315 | 同步（可能较重） | 见 §4 阻塞点；`compression_attempts` 上限默认 3（1550）；压缩锁竞争会 `continue`（跳过本轮不计预算） |
| 9 | 思考 spinner / vprint 状态输出 | 2343–2369 | 同步 | 轻量 |
| 10 | **模型调用（重试循环内）**：`run_llm_execution_middleware(... _perform_api_call ...)` → `_interruptible_streaming_api_call`（始终优先流式，run_agent.py:7275） @2673/2633 | 2382–2720 | **同步阻塞**（流式 SDK 拉取，带 90s stale 检测与 60s read 超时） | **单轮最大耗时点**：模型推理延迟，可被 stale/request timeout 打断重试 |
| 11 | 重定向/steer 交叉检测（响应与 redirect 竞态） | 2701–2715 | 同步 | 轻量 |
| 12 | 响应校验、防重放、reasoning 复制、空响应/思考前缀重试判定 | 2737–3100（约） | 同步 | 含 `time.sleep(0.2)` 中断轮询 @2978 |
| 13 | **处理工具调用**：`_execute_tool_calls`（并发/顺序/分段） | 6680（循环内 6263 起编排）→ run_agent.py:8450 → 转发 `agent.tool_executor` | **并发**：并行安全批次用线程池；其余顺序 | **工具执行耗时点**，见 §4 |
| 14 | 工具结果回灌、uid 去重、cap delegate、dedup、追加 assistant(tool_calls) + tool 结果 | 6263–6680 | 同步 | 轻量 |
| 15 | **Post-response 压缩门**：用真实 `last_prompt_tokens` 再判 `should_compress` → `_compress_context` | 6773–6790 | 同步 | 见 §4 |
| 16 | 空响应重试退避 `jittered_backoff(base=5,max=120)` + `time.sleep(0.2)` 轮询 | 7087–7127 | 同步阻塞 | 最多 3 次（_empty_content_retries<3） |
| 17 | 判断继续 / 下一轮 | 循环顶部 1598 | 同步 | — |

### 2.3 Streaming / 多轮工具嵌套
- **流式**：模型调用始终优先走 `_interruptible_streaming_api_call`（conversation_loop.py:2633 → run_agent.py:7275 转发 `agent.chat_completion_helpers`）。流式带 90s stale-stream 检测、60s read 超时（run_agent.py:2172–2176 注释）。无 stream consumer 时仍走流式仅做健康检测（run_agent.py:2570–2622 注释明确说明"避免静默挂起"）。
- **流式中断**：`_claim_stream_writer` / `_stream_writer_superseded` 单写者令牌机制（run_agent.py:7123–7161），被取代的流自动丢弃其 delta，避免交错污染 UI。
- **多轮工具调用的嵌套循环**：`_execute_tool_calls`（run_agent.py:8450）按 `_plan_tool_batch_segments` 把一批工具调用切成"并行安全段"与"顺序屏障段"，并行段经 `execute_tool_calls_concurrent`（线程池，`agent.tool_executor`）并发执行，顺序段串行；混合批次按发射顺序分段执行（run_agent.py:8450–8490）。**这是主循环内部的二级并发**（线程池 worker，由 `_tool_worker_threads` / `_tool_worker_threads_lock` 跟踪，run_agent.py:3940–3943）。

---

## 3. B. CLI 启动顺序（两套 CLI 各自路径 + 关系判定）

### 3.1 `hermes_cli/main.py`（当前主入口 `hermes`）
执行路径（行号指 hermes_cli/main.py）：
1. `main()` @11287 → `from hermes_cli._parser import build_top_level_parser` @11335（解析 argv）。
2. 早期初始化：`from hermes_cli import _startup_fast` @83；`_early_recovery` @97；`_ensure_utf8()`（hermes_cli/__init__.py:92，导入即执行）；dotenv/配置加载在子命令内按需 lazy import（如 `load_config` 在多处 @963/1049/2571）。
3. 子命令注册：约 50 个 `build_*_parser` 导入 @439–485（sessions/cron/sync/gateway/profile/model/setup/login/logout/auth/status/pause/webhook/hooks/doctor/verify/security/approvals/dump/debug/backup/import/config/skin/console/version/update/uninstall/dashboard/gui/logs/prompt_size/memory/acp/tools/insights/monitoring/skills/pairing/plugins/mcp/claw…）。
4. 分发到 `cmd_*`（如 `cmd_chat` @2579）。**认证/配置加载发生在具体子命令内部**，非统一早期步骤。
5. `cmd_chat` 内 `from cli import (...)` @2370，并在 2803 `from cli import main as cli_main`，把交互式 REPL 交给 `cli.py`。
6. session 恢复：在 `cli.py` 的 REPL 启动路径里（`load_config` / `active_sessions.try_acquire_active_session` @4782 等 lazy import）。

### 3.2 `cli.py`（交互式 REPL，`hermes chat` 的实际承载）
执行路径（行号指 cli.py）：
1. `main()` @18214。
2. 大量 lazy import `from hermes_cli.*`：config/skin_engine/banner/model_switch/curses_ui/voice/plugins/commands/mcp_startup/lifecycle…（覆盖全文件，如 @212/@922/@957/@1027/@1049/@1288/@1317）。
3. 构建 `HermesCLI` 实例并进入 prompt-toolkit 事件循环（REPL）。
4. 用户消息经 `process_command`(@23)、slash 命令（`COMMANDS_BY_CATEGORY` @7714 注册表驱动）到 `_init_agent`(@9) 创建 AIAgent，最终调 `agent.run_conversation`（即回到 §2 循环）。

### 3.3 关系判定（明确结论）
- **不是重复实现，是分层互补**：
  - `hermes_cli/main.py` = **外层 argparse 命令壳**（安装/配置/serve/gateway/各类管理子命令），`cli.py` = **内层交互 REPL**。
  - `cli.py:main` 不是 console_script，而是被 `hermes_cli/main.py:cmd_chat` 调用的子例程（hermes_cli/main.py:2803）。
  - `run_agent.py:main` 是完全独立的第三套最简入口（`hermes-agent`）。
- 配置加载/认证/session 恢复**不在统一启动点**，而是分散在各子命令与 `cli.py` REPL 内部按需 lazy import——这意味着"启动顺序"没有单一的早早期阶段，而是每命令自取所需。拆分时要注意各 `cmd_*` 对 `hermes_cli.config.load_config` 的隐式依赖（main.py 中约 30+ 处 `from hermes_cli.config import load_config`）。

---

## 4. C. 拖慢进程 / 容易假死的点（按严重度排序）

> 行号优先给 `conversation_loop.py`，工具/客户端相关给 `run_agent.py`。

| 严重度 | 位置 | 现象 / 原因 | 影响面 |
|--------|------|-------------|--------|
| 🔴 P0 | `conversation_loop.py:1598` 主循环无硬 wall-clock 超时；`run_agent.py:5842` SDK `max_retries=0`（自管重试） | **单轮模型调用可能长时间挂起**：虽有过载保护（90s stale、60s read，run_agent.py:2172–2176），但流式连接若被代理以 SSE ping 保活却永不返回，依赖 stale 检测；且 Z.AI 过载会 `max_retries = max(max_retries, zai_coding_overload_retry_ceiling())`（conversation_loop.py:4730）**抬高重试上限**，叠加 `jittered_backoff(base=2,max=60)`（:5831）与 `base=5,max=120`（:2946），最坏可达数分钟/轮。 | 整进程假死、用户 Ctrl-C 才退 |
| 🔴 P0 | `conversation_loop.py:2250 / 4684 / 4943 / 5090 / 5244 / 6790` 共 **6 处** `_compress_context()` | **上下文压缩是重活**：每轮可能触发 2 次（pre-API + post-response），压缩锁 `_active_compression_lock_holder`（run_agent.py:3059）串行化；压缩本身含 LLM 调用（summary LLM），且与主循环、background-review 线程竞争同一会话压缩锁（conversation_loop.py:1064–1090 "compression_deferred" 处理）。 | 长上下文对话显著变慢、偶发 deferred 退化为"压缩耗尽" |
| 🟠 P1 | `conversation_loop.py:2111` `estimate_messages_tokens_rough` + `1720–2081` 每轮全量重建 `api_messages` | **每轮 O(n) 全量重建 + 估算**，n=消息数。长对话（数百条消息、含 base64 图片）时每轮都要 clone+清洗+归一化所有消息；`estimate_messages_tokens_rough` 早期曾 `str(msg)` 重序列化 base64（注释 @2106–2108 已优化为 image-stripped 估算，但仍 O(n)）。叠加多轮迭代 = **O(n·iter) 准线性膨胀**。 | 长会话每轮固定开销随历史线性增长 |
| 🟠 P1 | `conversation_loop.py:6680` `_execute_tool_calls` → `agent.tool_executor`（并发/顺序） | **工具执行无统一超时保护**（run_agent.py 仅转发；本次未在 run_agent.py 内发现全局 per-tool timeout，具体超时在 `agent.tool_executor` 与各 tool 实现如 terminal_tool 内部——属"不确定，需另读"）。并行批次用线程池（run_agent.py:3940 `_tool_worker_threads`），若某工具永久阻塞，线程长期占用且无 watchdog。 | 个别慢/挂工具拖住整轮 |
| 🟡 P2 | `conversation_loop.py:2978 / 4698 / 4976 / 5277 / 5891 / 7123` 共 6 处 `time.sleep`（0.2s / 2s） | 均为同步阻塞 sleep，未包 `run_in_executor`：退避轮询 `time.sleep(0.2)`（每 150 次打印活跃状态，:2982/:5895/:7125 注释 "monitor knows we're alive"），压缩重试 `time.sleep(2)`。单处短，但处于主循环线程，累积仍阻塞 UI 事件循环。 | 轻微卡顿、状态刷新延迟 |
| 🟡 P2 | `run_agent.py:5530` `_openai_client_lock`（RLock）、`2708/2778` `_session_persist_lock`、`8226` `_compression_commit_fence_lock`、`3880+` `_pending_redirect_lock/_pending_steer_lock` | 多把锁在主循环 + 后台线程（interrupt 检测、stale-stream 检测、background-review fork）间共享。SQLite session DB 写入走 `_session_persist_lock`（run_agent.py:2778 `_flush_messages_to_session_db_unlocked`），并发写有被串行化/锁竞争风险。 | 偶发写入延迟、压缩锁 skip |
| 🟢 P3 | `run_agent.py:1613` `importlib.reload` 热重载（`hot_reload.register_agent`） | 开发期热重载用 importlib 重装模块，可能使 `run_agent.X` 引用在 reload 后失效（tests 注释 @55 提到 `~28 test files` 依赖 `patch("run_agent.OpenAI")`）。 | 仅开发模式；影响测试稳定性 |
| 🟢 P3 | 主循环 `while`(1598) / 重试 `while`(2382) 均有明确出口；未发现 `while True` 无出口 | 无无限循环风险（已核查 run_agent.py 与 conversation_loop.py 均无裸 `while True`）。 | — |

**C 部分前 5 个最严重阻塞点（摘要，将回传给 team-lead）**：
1. 模型调用无 wall-clock 上限 + Z.AI 重试上限被抬高 → 单轮可挂数分钟（conversation_loop.py:1598/4730/5831）。
2. 6 处上下文压缩 _compress_context（conversation_loop.py:2250 等）含 LLM 调用且受压缩锁串行化。
3. 每轮 O(n) 全量重建 api_messages + token 估算（conversation_loop.py:1720–2081/2111），长会话线性膨胀。
4. 工具执行无统一超时、线程池无 watchdog（conversation_loop.py:6680 → agent.tool_executor）。
5. 6 处同步 time.sleep 阻塞主循环线程（conversation_loop.py:2978 等）。

---

## 5. D. 拆分约束（不可动的符号精确清单 + tests 引用分类统计）

### 5.1 `run_agent.py` 被外部引用的符号（物理拆分的硬约束）
这些符号被 `from run_agent import X` 或 `mock.patch("run_agent.X")` 引用，**拆出去必须保留 `run_agent.X` 兼容层**（re-export）。来源：re-export 注释块 107–226 + tests 统计。

**(a) re-export 块显式声明（run_agent.py:107–226）**：
- `process_bootstrap`: `OpenAI`(113)、`_SafeWriter`(114)、`_get_proxy_for_base_url`(115)
- `model_tools`: `get_tool_definitions`(140)、`get_toolset_for_tool`(141)、`handle_function_call`(142)、`check_toolset_requirements`(143)
- `agent.model_metadata`: `estimate_request_tokens_rough`(159)
- `agent.context_compressor`: `COMPRESSED_SUMMARY_METADATA_KEY`(165)、`ContextCompressor`(166)
- `agent.retry_utils`: `jittered_backoff`(168)
- `agent.prompt_builder`: `DEFAULT_AGENT_IDENTITY`(170)、`build_skills_system_prompt`(171)、`build_context_files_prompt`(172)、`build_environment_hints`(173)、`build_nous_subscription_prompt`(174)、`load_soul_md`(175)
- `agent.message_sanitization`: `_SURROGATE_RE`(179)、`_sanitize_surrogates`(180)、`_sanitize_structure_surrogates`(181)、`_sanitize_messages_surrogates`(182)、`_escape_invalid_chars_in_json_strings`(183)、`_repair_tool_call_arguments`(184)、`_strip_non_ascii`(185)、`_sanitize_messages_non_ascii`(186)、`_sanitize_tools_non_ascii`(187)、`_strip_images_from_messages`(188)、`_sanitize_structure_non_ascii`(189)、`coalesce_tool_call_id`(190)、`uniquify_tool_call_ids`(191)
- `agent.codex_responses_adapter`: `_derive_responses_function_call_id`(194)、`_deterministic_call_id`(195)、`_split_responses_tool_id`(196)、`_summarize_user_message_for_log`(197)
- `agent.tool_dispatch_helpers`: `_should_parallelize_tool_batch`(215)、`_is_destructive_command`(216)、`_extract_parallel_scope_path`(217)、`_paths_overlap`(218)、`_is_multimodal_tool_result`(219)、`_multimodal_text_summary`(220)、`_append_subdir_hint_to_multimodal`(221)、`_extract_file_mutation_targets`(222)、`_extract_landed_file_mutation_paths`(223)、`_extract_error_preview`(224)、`_trajectory_normalize_msg`(225)
- 另：`_get_proxy_from_env`(177, `# noqa: F401`)

**(b) 被 tests 直接 `mock.patch("run_agent.X")` / `run_agent.X` 引用的其它符号**（来自 tests 统计，非 re-export 注释列出，仍属硬约束）：
`AIAgent`(245 次)、`time`(24)、`test_run_agent`(17)、`load_soul_md`(8)、`cleanup_vm`(7)、`cleanup_browser`(7)、`build_nous_subscription_prompt`(7)、`build_environment_hints`(7)、`build_context_files_prompt`(7)、`_set_interrupt`(5)、`get_toolset_for_tool`(2)、`build_skills_system_prompt`(2)、`await_count`(2)、`_try_shrink_image_parts_in_messages`(2)、`_hermes_home`(1)、`run_conversation`(1)、`interrupt`(1)、`_is_destructive_command`(1)、`_invoke_tool`(1)、`_touch_activity`(1)、`_tool_result_content_for_active_model`(1)、`_try_strip_image_parts_from_tool_messages`(1)、`_emit_credits_notices`(1)、`_call_chat_completions`(1)、`ContextCompressor`(1)、`jittered_backoff`(1)、`logger`(3)、`sys`(1)、`assert_not_called`/`assert_awaited_once`(测试断言，非符号)。
> 注：`run_agent.py`(75 次) 与 `run_agent.test_run_agent`(17 次) 多为路径/模块引用，非符号。

**(c) 动态解析符号（拆出去最易断）**：
- `run_agent.py:1613` `importlib.reload` 热重载——reload 后 `run_agent.X` 引用需仍有效（测试 `@55` 注释提到 ~28 test files 依赖 `patch("run_agent.OpenAI")`）。
- `run_agent.py:438` `class _StreamErrorEvent`、流式单写者令牌机制（7123+）属内部，但被 streaming 路径强依赖。
- `_ra().<X>` 间接寻址：`agent/system_prompt.py` 通过 `run_agent` 模块属性访问符号（run_agent.py:110 注释），拆包后需保留属性访问兼容。

### 5.2 `hermes_cli/main.py` 被外部引用的符号
tests 中 `hermes_cli.main.*` 出现在 **38 个文件**，主要符号（按出现次数）：
`subprocess`(14)、`select_provider_and_model`(9)、`shutil`(8)、`_prompt_provider_choice`(8)、`_resolve_session_by_name_or_id`(7)、`_has_any_provider_configured`(7)、`_find_stale_dashboard_pids`(7)、`_run_with_idle_timeout`(6)、`_run_npm_install_deterministic`(6)、`_resolve_node_runtime_npm`(6)、`_desktop_build_needed`(6)、`_require_tty`(5)、`_is_windows`(5)、`_scan_dashboard_processes`(4)、`sys`(3)、`cmd_chat`(3)、`_write_desktop_build_stamp`(3)、`_time`(3)、`_purge_electron_build_cache`(3)、`_kill_stale_dashboard_processes`(3)、`_desktop_macos_relaunchable_fixup`(3)；以及单次的 `main`(1)、`cmd_setup`(1)、`_print_version_info`(1)、`_prepare_agent_startup`(1)、`_relative_time`(1，被 cli.py:7937 引用) 等。
> 这些多为 `from hermes_cli.main import <func>`（桌面构建/会话解析辅助函数）。拆分 hermes_cli/main.py 时需为这些保留兼容 re-export，或更新调用方（cli.py 直接 `from hermes_cli.main import _relative_time`、`_print_version_info` 见 cli.py:7937/10190）。

### 5.3 `cli.py` 被外部引用的符号
tests 中 `cli.*` 出现在 **141 个文件**，且有 **261 处 `from cli import` / `import cli`**（确认 cli 是重度被依赖模块）：
高频：`cli.py`(108, 多为路径)、`cli.post`(107)、`cli._cprint`(64)、`cli.agent`(58)、`cli.conversation_history`(55)、`cli.session_id`(48)、`cli._pending_input`(42)、`cli._app`(34)、`cli.get`(31)、`cli.photon_auth`(29)、`cli._session_db`(29)、`cli.process_command`(23)、`cli._on_tool_progress`(23)、`cli._approval_state`(22)、`cli._agent_running`(22)、`cli.load_cli_config`(20)、`cli._interrupt_queue`(19)、`cli.console`(17)、`cli._stream_delta`(17)、`cli._prompt_stash`(15)、`cli.save_config_value`(15)、`cli.handle_bang_shell`(15)、`cli.HermesCLI`(13)、`cli._should_handle_background_command_inline`(11)、`cli.model`(13)、`cli._prune_stale_worktrees`(13)、`cli._voice_barge_capture`(10)、`cli.chat`(7)、`cli.session_start`(8)、`cli._init_agent`(9) 等。
> cli.py 既被 261 处测试 import，也被 hermes_cli/main.py 以 `from cli import main as cli_main`(2803) 调用 —— **cli.py 是拆分风险最高的文件**（体量最大、被依赖最广、含 REPL 状态机）。建议优先把它按 mixin 已部分解耦的现状（`hermes_cli.cli_agent_setup_mixin`/`cli_commands_mixin`/`cli_billing_mixin`，cli.py:54–56）继续外提，而非整体搬迁。

### 5.4 `gateway.run.*`（对照，gateway 侧另由其他 worker 负责，此处仅统计供参考）
tests 中 `gateway.run.*` 出现在 **61 个文件**：`_hermes_home`(28)、`_resolve_runtime_agent_kwargs`(25)、`_resolve_gateway_model`(24)、`_load_gateway_config`(21)、`GatewayRunner`(19)、`_gateway_runner_ref`(11)、`asyncio`(8)、`os`(6)、`merge_pending_message_event`(5)、`_resolve_runtime_agent_kwargs_for_provider`(4)、`time`(3)、`__file__`(3)、`_current_max_iterations`(2)、`main`(2)、`logger`(2)、`build_channel_directory`(2) 等。

### 5.5 `hermes_cli/__init__.py` re-export / 暴露清单
`__init__.py`(92 行) 本身**没有集中的 re-export 表**，导入即执行的副作用是 `_ensure_utf8()`(92)。它通过属性暴露并被外部 `from hermes_cli import X` 引用的有：`__version__`(17)、`__release_date__`(18)、`managed_scope`(cli.py:632)、`codex_runtime_switch`(cli.py:9624)、`kanban_db`(cli.py:18140)、`profiles`(main.py:9571)、`_startup_fast`(main.py:83)、`_early_recovery`(main.py:97)、`secrets_cli`/`onepassword_secrets_cli`(main.py:11424, 经 `from hermes_cli import ...`)。拆 `hermes_cli/__init__.py` 时需保留这些属性。

### 5.6 tests 引用分类统计总表
| 前缀 | 命中文件数 | 典型高频符号（次数） | 备注 |
|------|-----------|----------------------|------|
| `run_agent.*` | 172 | AIAgent(245) OpenAI(119) get_tool_definitions(83) check_toolset_requirements(80) handle_function_call(59) | 大量 `mock.patch`，属硬兼容约束 |
| `gateway.run.*` | 61 | _hermes_home(28) _resolve_runtime_agent_kwargs(25) _resolve_gateway_model(24) GatewayRunner(19) | gateway 侧（其他 worker 负责） |
| `hermes_cli.main.*` | 38 | select_provider_and_model(9) _prompt_provider_choice(8) _resolve_session_by_name_or_id(7) | 桌面/会话辅助函数 |
| `cli.*` | 141（+261 处 `from cli import`） | post(107) _cprint(64) agent(58) conversation_history(55) session_id(48) HermesCLI(13) | 体量最大、依赖最广 |

---

## 6. 建议的模块划分（依赖方向、循环依赖风险、兼容性 shim）

### 6.1 依赖方向（现状）
```
hermes_cli.main  ──► cli  ──► hermes_cli.* (config/skin/model_switch/...)
       │                    └─► AIAgent(run_agent) ──► agent.conversation_loop
       │                                                       └─► agent.* (压缩/工具/流式)
       └─► hermes_cli.subcommands.*  (各自 lazy import hermes_cli.<domain>)
run_agent.main ──► AIAgent ──► (同上)
```
**关键观察**：`run_agent.py` 的 `AIAgent` 已是 facade，真正逻辑在 `agent/`；`cli.py` 已通过 `cli_*_mixin` 部分解耦。`hermes_cli/main.py` 是 thin dispatcher（多数 `cmd_*` 仅做 lazy import + 调 `hermes_cli.<domain>`）。

### 6.2 建议拆分目标
1. **run_agent.py → 薄门面**：保留 `main()`(8808)、`AIAgent` 骨架（client/锁/流写入/生命周期）、以及 §5.1 的全部 re-export（107–226）。把 AIAgent 中剩余的转发方法继续下沉到 `agent/`（实际大部分已在那里）。兼容层只需保留 `run_agent.X` 属性 + `mock.patch` 友好（`patch` 挂的是模块属性，re-export 即可满足）。
2. **cli.py → 按 mixin 继续外提**：已有 `cli_agent_setup_mixin`/`cli_commands_mixin`/`cli_billing_mixin` 先例。把 REPL 状态机（pending_input/interrupt_queue/approval_state/voice/spinner… 这些 tests 高频引用符号）稳定为公开 API 后，逐步把 18743 行拆成 `hermes_cli/repl/*` 子模块。
3. **hermes_cli/main.py → 保持 dispatcher，按 subcommand 文件归并**：~50 个 `build_*_parser` 已分文件（hermes_cli/subcommands/），`cmd_*` 可按领域迁入对应 `hermes_cli.<domain>_cmd`，main.py 只留解析+分发。

### 6.3 循环依赖风险
- **当前无硬循环**：`cli.py → hermes_cli.*` 单向；`hermes_cli.main → cli` 仅在 `cmd_chat` 内 lazy import（2803），而 cli 不 import hermes_cli.main（只 import hermes_cli 子模块）——**未形成环**。但 `hermes_cli.main` 与 `cli` 互相知晓，拆分时若把 `cmd_chat` 逻辑移入 cli 或反之，需避免 `cli` 顶层 `import hermes_cli.main`。
- **run_agent ↔ agent**：`run_agent` import `agent.*`；`agent.system_prompt` 经 `_ra()` 反向读 `run_agent` 属性（run_agent.py:110）。这是**唯一已知的双向弱耦合**，拆 run_agent 时必须保留 `run_agent.X` 模块级属性，否则 `agent/system_prompt.py` 的 `_ra().<X>` 会断。

### 6.4 兼容性 shim 该怎么留
- **run_agent.py**：保留 §5.1(a)(b) 全部 re-export（107–226 注释已为拆分写好 `# noqa: F401 # re-exported for tests that mock.patch("run_agent.X")`）。**不要删除或重命名这些名字**——tests 有 172 文件引用、OpenAI 单符号 119 次、AIAgent 245 次。
- **cli.py**：261 处 `from cli import` 是强约束。若重命名模块，必须在 `hermes_cli/__init__.py` 或新位置提供 `cli` 兼容别名（或批量改 tests）。建议**先内部分解、暂缓改模块名**。
- **hermes_cli/main.py**：38 文件引用其辅助函数，多为桌面构建/会话解析。若移走 `cmd_chat`/`_resolve_session_by_name_or_id` 等，需在 `hermes_cli.main` 保留 re-export 或更新 cli.py 的 `from hermes_cli.main import _relative_time/_print_version_info`(cli.py:7937/10190)。

### 6.5 拆分前必做的"假死"治理（与拆分解耦，但应在拆分同期做）
见 §4。最高优先级：给模型调用加**硬 wall-clock 上限**（不要仅靠 stale 检测，且不要把 Z.AI 重试上限无限抬高，conversation_loop.py:4730）；为 `_compress_context` 设每轮预算上限（已有 `max_compression_attempts=3` 但压缩本身无耗时上限）；把 6 处 `time.sleep` 改为非阻塞/异步轮询或包 `run_in_executor`；为工具执行线程加 watchdog 超时。

---

## 7. 不确定项（诚实标注）
- **工具执行超时**：run_agent.py 仅做转发（`_execute_tool_calls` @8450 → `agent.tool_executor`），本次未在 run_agent.py 内发现统一的 per-tool 超时；真实超时逻辑在 `agent.tool_executor` 与各 tool 实现（如 terminal_tool）中，**需另读确认**。
- **`_compress_context` 内部复杂度**：压缩是否 O(n²)（如反复 re-tokenize 全历史）未在本次范围核实，仅确认其被每轮最多 2 次调用且含 LLM summary 调用。
- **`agent.conversation_loop.py` 是否还有更外层循环/调度**：本次确认 `run_conversation` 内主 `while` @1598 为单轮迭代循环；更大粒度的"会话级"循环（如 resume、多 turn 会话）在 cli.py REPL 层，未逐行追踪。
- 行号均来自实际文本定位，但文件在活跃开发中，重读时若存在后增行可能产生少量偏移；re-export 注释块(107–226)、main()(8808)、循环(1598/2382) 等锚点已交叉验证，偏移风险低。
