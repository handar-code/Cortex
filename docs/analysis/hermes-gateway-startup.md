# Hermes Gateway `gateway/run.py` 启动顺序与拆分边界分析

> 目标文件：`<target-project>/gateway/run.py`（**28756 行**）
> 分析方法：**全文通读**（非仅靠符号表/静态依赖）。本报告每条结论均对应具体行号。
> 参考索引：`<CORTEX_RUNS_DIR>/catalog.snapshot.json`（仅用于反查公开符号，不替代通读）

---

## 1. 概览

### 1.1 顶层结构
- **模块头 + 常量 + import**：`1–141`
- **模块级辅助函数**（hygiene、压缩提示、脱敏、状态模板、配置装载、t4 审计、provider kwargs、媒体事件、环境/重启变量等）：`142–3775`
- **`TurnRunner` 类**（单轮执行引擎：进度消息、语音 ack、run_sync、prompt mirror、审批、澄清、background review）：`3775–5990`
- **`GatewayRunner` 类声明 + 类属性 + mixin 基类**：`5990–6104`
- **`GatewayRunner.__init__`**（配置装载、SQLite 打开、session store、agent 缓存、pairing、hooks、看门狗句柄）：`6104–6489`
- **`GatewayRunner` 主体方法**（连接/适配器生命周期、会话状态、Telegram 话题、致命错误处理、scale-to-zero、外部 drain、cron/看门狗、stop、消息分发、agent 执行等）：`6489–27953`
- **模块级后台函数**（`_run_planned_stop_watcher`、`_start_gateway_housekeeping`、`_start_cron_ticker` shim、`_await_thread_exit`、`_shutdown_gateway_health_export`）：`27619–27953`
- **入口链 `start_gateway` → `main` → `_exit_after_graceful_shutdown`**：`27965–28756`

### 1.2 入口函数链
```
main()  [28626]  (CLI argparse, asyncio.run(start_gateway))
  └─> start_gateway()  [27965]  (async; 完整启动+优雅关闭编排)
        ├─ record_boot_fingerprint()            [27983]
        ├─ 重复实例守卫 / PID 锁                [27986–28146]
        ├─ GatewayRunner(config)  [28196]  →  __init__ [6104–6489]  (同步重活)
        ├─ 信号处理器 + planned-stop 守护线程     [28209–28356]
        ├─ PID 文件 + runtime lock 竞争窗口       [28358–28387]
        ├─ MCP tool discovery (executor)          [28409–28420]
        ├─ await runner.start()  [28422]  →  start() [11048–12037] (适配器初始化核心)
        ├─ cron 调度守护线程                       [28475–28526]
        ├─ housekeeping 守护线程                  [28528–28538]
        ├─ systemd watchdog                       [28540–28545]
        ├─ await runner.wait_for_shutdown()        [28547]
        └─ 协同关闭 + os._exit 兜底                [28550–28623] → _exit_after_graceful_shutdown [28684]
```

### 1.3 进程内并发模型（一句话）
- **1 个 asyncio 事件循环**：`start_gateway` 经 `asyncio.run` 启动；所有平台心跳、watcher 协程、agent 回合都在同一 loop 上。
- **OS 线程（daemon）**：planned-stop-watcher（`28350`）、cron 调度（`28519`）、housekeeping（`28531`）、systemd watchdog（`13128` 起的 `SystemdWatchdog`）、Dream Engine（`11165` 起的独立线程）。
- **线程池**（`ThreadPoolExecutor(max_workers=10)`，`22688`）：承接阻塞型工作（`run_in_executor` / `_run_in_executor_with_context`），见 19440、20881、21665。
- **协程任务**（`_spawn_supervised` → `asyncio.create_task`）：session 过期、stall、kanban×2、reconnect、handoff、async-delegation、scale-to-zero、drain-control（`11948–12033`）。

---

## 2. A. 运行时启动顺序（按真实执行顺序，带行号阶段表）

下表为 `start_gateway` 与 `GatewayRunner.start` 的真实先后。**同步阻塞**=在调用线程/loop 线程上直接阻塞；**异步 await**=在 loop 上 await（不冻结其它协程，但会延迟同 loop 后续）；**后台线程**=另起线程，不阻塞 loop。

| # | 阶段 | 起止行号 | 函数/动作 | 阻塞性质 | 备注 |
|---|------|---------|----------|---------|------|
| 1 | CLI 入口 | `28626–28711` | `main()`：argparse、读 `--config`（`28649`）、`asyncio.run(start_gateway(config))` | 同步（阻塞进程） | 整个进程生命周期都在 `asyncio.run` 内 |
| 2 | Boot fingerprint | `27983–27984` | `record_boot_fingerprint()`（`gateway.code_skew`） | 同步（轻量） | 快照 checkout revision，用于检测 git pull 后的 code skew |
| 3 | 重复实例守卫 | `27986–28146` | 读 `get_running_pid()`；若 `--replace` 则 `terminate_pid` + 轮询等待 | **同步阻塞**（见 C-3：28054 `sleep(0.5)`×20=10s、28078 `sleep(0.25)`×20=5s）；否则直接 `return False` | `28133` 非 replace 则打印并退出；release 旧实例的 scoped locks（`28124`） |
| 4 | 技能同步 | `28148–28153` | `sync_skills(quiet=True)` | 同步（快，跳过未变） | try/except 吞掉 |
| 5 | 日志初始化 | `28158–28159` | `setup_logging(mode="gateway")` | 同步（轻量） | |
| 6 | 安全态势审计 | `28165–28177` | `log_startup_security_warnings` | 同步（非阻塞，warn-only） | |
| 7 | Runner 构造 | `28196` → `__init__ 6104–6489` | `GatewayRunner(config)` | **同步重活**（见 C-4） | 配置装载 `6110`、SQLite 打开 `6156`/`6382`、**VACUUM/prune `6404–6418`**、pairing `6452`、hooks `6457`、agent cache `6306` |
| 8 | 信号处理器 | `28209–28335` | 安装 `shutdown_signal_handler` / `restart_signal_handler`（`loop.add_signal_handler`） | 异步回调（loop 上） | Windows 走 `NotImplementedError` 兜底 |
| 9 | planned-stop 守护线程 | `28349–28356` | 启动 `_run_planned_stop_watcher`（daemon） | **后台线程**（`poll_interval=0.5`，`27705` `stop_event.wait`） | Windows SIGTERM 兜底的关闭驱动；POSIX 上为安全网 |
| 10 | PID 文件 + runtime 锁 | `28358–28387` | `acquire_gateway_runtime_lock()` + `write_pid_file()` + `atexit` 注册 | 同步（文件/锁，竞争窗口关键） | 关闭 `--replace` 竞态窗口：先 claim 锁再连适配器 |
| 11 | 生命周期账本 | `28394–28398` | `record_startup()` | 同步（轻量） | |
| 12 | Nous auth keepalive | `28400–28405` | `start_nous_auth_keepalive()` | 同步（非阻塞） | |
| 13 | Windows venv import | `28407` | `_ensure_windows_gateway_venv_imports()` | 同步 | |
| 14 | **MCP tool discovery** | `28409–28420` | `await _loop.run_in_executor(None, discover_mcp_tools)` | **异步 await + 线程池**（见 C-1） | 内部 120s 阻塞等待被移入 executor（`28418`）；注释见 `28409–28414` |
| 15 | **Runner.start()** | `28422–28430` → `start() 11048–12037` | 适配器初始化核心（见下方子表） | **异步 await**（内部逐平台 await 连接） | 见 A-子表 |
| 16 | 恢复关机 flush 消息 | `28432–28440` | `recover_pending_to_db()` | 异步 await（轻量） | |
| 17 | cron 调度线程 | `28475–28526` | `cron_provider.start(cron_stop, ...)` 在 daemon 线程 | **后台线程** | `resolve_cron_scheduler` 区分内置 60s ticker 与外部（如 chronos） |
| 18 | housekeeping 线程 | `28528–28538` | `_start_gateway_housekeeping(cron_stop, ...)` 在 daemon 线程 | **后台线程** | 独立于 cron provider，保证小时级清理 |
| 19 | systemd watchdog | `28540–28545` | `start_watchdog()` → `_start_systemd_watchdog` `13119` | 后台线程（sd_notify） | **仅在所有适配器/cron/housekeeping 都 running 后才启动** |
| 20 | 等待关闭 | `28547–28548` | `await runner.wait_for_shutdown()` | 异步 await（阻塞直到 shutdown 事件） | 主循环在此挂起 |
| 21 | 协同关闭 cron/housekeeping | `28562–28583` | `cron_stop.set()` + `cron_provider.stop()` + `_await_thread_exit`（**轮询 await，非 join**） | 异步 await（轮询 `27948` `await asyncio.sleep(poll)`） | 注释 `28563–28570`：cron 投递协程在 loop 上，join 会死锁 |
| 22 | 关闭 planned-stop 线程 | `28585–28587` | `_planned_stop_watcher_stop.set()` + `join(timeout=2)` | 同步 join（2s 上限） | |
| 23 | 关闭 MCP | `28589–28594` | `shutdown_mcp_servers()` | 同步（try/except） | |
| 24 | 退出码 | `28596–28621` | `SystemExit` / `return False` / `SystemExit(75)` | — | 信号触发的非预期关闭 → 返回 False（`28612`）让 systemd Restart=on-failure 复活 |
| 25 | 最终硬退出 | `28684–28752` | `_exit_after_graceful_shutdown`：flush stdio → 释放 PID/lock → `mark_exited` → `drain_log_queue(timeout=1.0)` → **`os._exit(exit_code)`** `28752` | 同步（进程退出） | `os._exit` 而非 `sys.exit`，绕过 `Py_FinalizeEx`（见 C-5） |

### A-子表：`GatewayRunner.start()` 内部真实顺序（`11048–12037`）
| 子阶段 | 行号 | 性质 | 说明 |
|--------|------|------|------|
| faulthandler 启用 | `11059–11096` | 同步（打开日志文件） | SIGUSR2 文件 dump（POSIX） |
| loop liveness 守卫 | `11103` | 异步（启动看门狗线程） | `_start_loop_liveness_guards` `10977` |
| **Dream Engine 启动** | `11106–11170` | **后台线程**（`start_dream_engine`） | 同步读 `config.yaml`（`11117–11124`）；独立 60s tick，不依赖 cron |
| systemd 时序对齐检查 | `11172–11192` | 同步（只读判定） | 检查 `TimeoutStopSec` 是否覆盖 drain |
| runtime 状态写入 | `11228–11239` | 同步 | `write_runtime_status("starting")` |
| health OTLP export | `11240–11247` | 同步（非阻塞失败） | |
| 安全公告 | `11254–11271` | 同步 | 供应链安全建议，不阻塞 |
| 关闭中止判定 | `11272` | 异步 await | `_abort_startup_if_shutdown_requested` |
| allowlist 警告 | `11275–11341` | 同步（仅日志） | |
| open-policy 启动违规 | `11343–11363` | 同步 | 违规则 `_request_clean_exit` 并返回 |
| 插件发现 | `11365–11377` | 同步 | `discover_plugins()` |
| relay 适配器注册 | `11384–11409` | 同步（含网络调用 `self_provision_relay`） | `_provision_relay` 可能发 HTTP |
| shell hooks + webhooks | `11420–11434` | 同步 | |
| 事件 hooks | `11437` | 同步 | `self.hooks.discover_and_load()` |
| 进程检查点恢复 | `11441–11447` | 同步 | `process_registry.recover_from_checkpoint()` |
| 会话恢复（clean/unclean） | `11449–11485` | 异步 await | 干净关闭则跳过 suspend；否则 `_recover_unclean_sessions` `11028` |
| stuck-loop 检测 | `11491–11496` | 同步 | `_suspend_stuck_loop_sessions` |
| **逐平台适配器连接循环** | `11515–11685` | **异步 await（顺序）** | 每平台：`_create_adapter` `11538` → set handlers → `_connect_initial_adapter_with_timeout` `11577`（默认 30s、Telegram 180s，见 C-2） |
| 次级 profile 适配器 | `11690–11712` | 异步 await | `_start_secondary_profile_adapters` `13691` |
| 无连接降级处理 | `11733–11807` | 同步（日志/状态） | 全失败 → fatal config 退出码 |
| delivery_router 装配 | `11812–11813` | 同步 | `self.delivery_router.adapters = self.adapters` |
| `_running=True` | `11815–11816` | 同步 | |
| loop heartbeat 任务 | `11822–11836` | 异步（`asyncio.create_task`） | `loop_heartbeat_forever` |
| gateway:startup hook | `11842` | 异步 await | |
| channel directory 构建 | `11850–11856` | 异步 await | `build_channel_directory` |
| update 通知 | `11860` | 异步 await | `_send_update_notification` |
| 连接后 settle 延迟 | `11873–11874` | **异步 await `asyncio.sleep(1.0)`** | 让新连适配器稳定 |
| restart/home 通知 | `11886–11900` | 异步 await | |
| 投递义务重投 + resume 调度 | `11912–11914` | 异步 await | |
| 启动动作 | `11919–11922` | 异步 await | `run_startup_actions` |
| 恢复 watchers | `11935–11943` | 异步 await（分批，`asyncio.sleep(0)` 让出） | |
| **启动后台 watcher（8+）** | `11948–12033` | **异步 `asyncio.create_task`**（同 loop） | session_expiry `11948`、session_stall `11952`、kanban×2 `11957/11963`、reconnect `11988`、handoff `11998`、async-delegation `12004`、scale-to-zero（条件）`12018`、drain-control `12033` |

**并发/异步交错小结**：
- `start_gateway` 与 `runner.start()` 都是 async，但**逐平台连接是顺序 await**（C-2 的延迟来源）。
- MCP discovery、Dream Engine、cron、housekeeping、planned-stop、systemd watchdog 都**离线/线程化**，不冻结 loop。
- 8+ 个后台 watcher 与所有平台心跳**共享同一事件循环**，因此任何在 loop 上跑的阻塞 IO 都会拖慢全部平台（这是 C 节重点关注的"假死"根因）。

---

## 3. B. 功能分段边界（每块给行号区间 + 职责）

> 用户标注的边界已逐项核实，结论见"核实"列。除核实项外，以下区间按代码职责切分。

| 块 | 行号区间 | 一句话职责 | 块内主要符号 |
|----|---------|-----------|-------------|
| **B1 模块头/常量/import** | `1–141` | 依赖导入 + 全局调优常量 + 脱敏/噪声正则 | `hermes_bootstrap`、stdlib、`_AGENT_CACHE_MAX_SIZE`、`_PLATFORM_CONNECT_TIMEOUT_SECS_DEFAULT=30`、`_TELEGRAM_CONNECT_TIMEOUT_SECS_DEFAULT=180`、`_TELEGRAM_NOISY_STATUS_RE` |
| **B2 模块级辅助（hygiene/压缩/脱敏/状态）** | `142–~2445` | 历史/压缩提示/脱敏/状态模板/时间戳/环境/重启变量工具 | `_hygiene_cooldown_for_failure` `142`、`_gateway_compression_progress_notices_enabled` `311`、`_redact_*` `574–707`、`_prepare_gateway_status_message` `732`、`_telegramize_command_mentions` `873`、`_auto_continue_freshness_window` `956`、`_ensure_ssl_certs` `1728`、`_planned_restart_notification_*` `1799`、`_reload_runtime_env_preserving_config_authority` `1839`、`_current_max_iterations` `1906`、`MultiplexConfigError` `1931`、`_profile_runtime_scope` `1945` |
| **B3 模块级辅助（t4 审计/provider/媒体/工具）** | `2446–2907` | t4 压缩审计、provider kwargs、凭证池、媒体事件、音频探测 | `_get_t4_audit_logger` `2446`、`_own_policy_open_startup_violation` `2513`、`_resolve_runtime_agent_kwargs` `2596`、`_credential_pool_for_provider` `2689`、`_event_media_*` `2754`、`_probe_audio_duration` `2866` |
| **B4 回合进程看护 + 配置装载工具** | `2908–3775` | 回合进程回收/超时看守（线程）、skill 检查、配置装载/yaml 解析、channel override、hermes bin 解析 | `_dequeue_pending_event` `2908`、`_reap_gateway_turn_processes` `2926`、`_watch_gateway_turn_inactivity` `3036`（**线程看门狗**）、`_check_unavailable_skill` `3144`、`_load_gateway_config` `3230`、`_resolve_gateway_model` `3341`、`_resolve_hermes_bin` `3410`、`_parse_session_key` `3437`、`_drain_gateway_watch_events` `3495`、`_dispose_unused_adapter` `3716`、`reconnect_backoff` `3775` |
| **B5 `TurnRunner`（单轮执行引擎）** | `3775–5990` | **一次 agent 回合的执行细节**：进度消息、语音 ack、run_sync（真正调 run_agent）、prompt mirror、审批/澄清/background review 回调 | `TurnRunner.__init__` `3792`、`send_progress_messages` `4057`、`voice_ack_callback` `4412`、`run_sync` `4506`、`_send_prompt_mirror_coro` `5012`、`_clarify_callback_sync` `5191`、`_approval_notify_sync` `5356` |
| **B6 `GatewayRunner` 声明 + 类属性** | `5990–6104` | 类定义、mixin 基类（`GatewayAuthorizationMixin, GatewayKanbanWatchersMixin, GatewaySlashCommandsMixin`）、类级看门狗句柄 | `class GatewayRunner` `5990` |
| **B7 `GatewayRunner.__init__`** | `6104–6489` | 配置装载、SQLite 打开、session/agent 缓存、pairing、hooks、看门狗句柄、scale-to-zero 时钟 | `load_gateway_config_for_runner` `6110`、`SessionStore` `6156`、`AsyncSessionDB` `6382`、**`maybe_auto_prune_and_vacuum` `6404–6418`**、`PairingStore` `6452`、`HookRegistry` `6457`、`_agent_cache` `6306` |
| **B8 连接/适配器生命周期 + 会话状态** | `6489–7747` | 适配器连接超时、致命错误处理、安全断开、会话状态/KEY、Telegram 话题、session runtime 解析、scale-to-zero 助手起始 | `_connect_adapter_with_timeout` `6840`、`_connect_initial_adapter_with_timeout` `6878`、`_handle_adapter_fatal_error` `7443`、`_resolve_session_agent_runtime` `7164`、`_scale_to_zero_*` `7747`（**核实：scale-to-zero 边界 `7741` 准确**） |
| **B9 scale-to-zero + 队列/目标 drain** | `7747–8155` | idle 检测、状态/队列/目标续作、外部 drain 进出、**drain-control watcher** | `_scale_to_zero_watcher` `7929`、`_enqueue_fifo` `8009`、`_enter_external_drain` `8155`、`_drain_control_watcher` `8199` |
| **B10 平台运行时状态 + 推理/层级 + 会话限额** | `8155–8968` | 外部/内部 drain 状态、平台 runtime 状态、推理配置、service tier、会话并发上限、压缩在途、pending 事件队列 | `_update_platform_runtime_status` `8231`、`_load_reasoning_config` `8432`、`_load_service_tier` `8518`、`_claim_active_session_slot` `8846`、`_session_has_compression_in_flight` `8913` |
| **B11 busy/steer + 活跃 agent 排空** | `8968–9502` | busy 消息/steer、活跃 agent 状态、drain 准备 | `_handle_active_session_busy_message` `9060`、`_drain_active_agents` `9502` |
| **B12 关闭/重启/启动恢复** | `9502–10683` | agent 资源清理、重启编排、启动恢复事件、pending 重投、resume 调度、clean-shutdown 标记、unclean 会话恢复 | `_finalize_shutdown_agents` `9776`、`request_restart` `10514`、`_run_startup_resume_event` `10552`、`_redeliver_pending_obligations` `10697`、`_schedule_resume_pending_sessions` `10804`、`_recover_unclean_sessions` `11028` |
| **B13 循环看护 + 启动收尾** | `10683–11048` | resume pending、loop liveness 守卫、clean-shutdown 标记消费 | `_consume_clean_shutdown_marker` `11017`、`_start_loop_liveness_guards` `10977`（核实：此处非 kanban） |
| **B14 `start()` 启动编排（核心）** | `11048–12037` | **整个启动顺序的编排核心**：faulthandler、Dream Engine、relay、插件、会话恢复、逐平台连接、watcher 启动 | `start` `11048`、**Dream Engine `11106`（核实准确）**、`_create_adapter` 调用于 `11538`、watcher spawn `11948–12033` |
| **B15 supervised spawn + 过期/stall watcher** | `12050–12814` | 受监管任务生成（自动重启+退避）、handoff watcher、session 过期 watcher、stall watcher | `_spawn_supervised` `12050`、`_handoff_watcher` `12144`、`_session_expiry_watcher` `12399`、`_session_stall_watcher` `12814` |
| **B16 重连 watcher + systemd watchdog** | `12814–13119` | reconnect watcher、active profile、systemd watchdog 启停 | `_ensure_reconnect_watcher_running` `12852`（**核实：12846 实际是"Kanban board watchers"注释引出 reconnect watcher，kanban 方法体在 mixin `gateway/kanban_watchers.py`**）、`_platform_reconnect_watcher` `12876`、`_start_systemd_watchdog` `13119` |
| **B17 关闭实现 `stop` / `wait_for_shutdown`** | `13119–13691` | systemd watchdog 停、关闭主实现 `_stop_impl`/`_stop_impl_body`、等待关闭 | `_stop_systemd_watchdog` `13135`、`stop` `13143`、`_stop_impl` `13164`、`_stop_impl_body` `13274`、`wait_for_shutdown` `13687` |
| **B18 次级 profile 适配器 + 适配器工厂** | `13691–14223` | 次级 profile 适配器、profile 消息/致命处理器、**`_create_adapter` 工厂** | `_start_secondary_profile_adapters` `13691`、`_create_adapter` `14223`（**核实：14243 为"Plugin-registered platforms (checked first)"注释，准确**） |
| **B19 适配器创建/授权/异步委派/slash busy** | `14223–14872` | `_create_adapter` 实现（先查 platform_registry 插件，再内置链）、授权检查、异步委派会话、busy slash 命令、`_handle_message` 入口 | `_create_adapter` `14223`、`_make_adapter_auth_check` `14354`、`_resolve_async_delegation_session` `14439`、`_handle_busy_slash_command` `14686` |
| **B20 `_handle_message`（入站分发）** | `14872–16404` | **总入站分发器**：路由、会话解析、外部 drain 新回合门、会话槽 claim | `_handle_message` `14872`、**外部 drain 门 `16280`（核实准确）**、会话 key 解析 `16260+` |
| **B21 各 handler + 入站文本准备 + 自主对话** | `16404–17225` | moa/one-shot 恢复、模型覆盖、入站文本准备、澄清回复、autonomous chat loop | `_restore_moa_one_shot` `16404`、`_prepare_inbound_message_text` `16437`、`_run_autonomous_chat_loop` `17125` |
| **B22 `_handle_message_with_agent`（核心回合）** | `17225–19306` | **一次完整 agent 回合的编排**：会话解析、skill 加载、**turn lease**、run agent、流式 | `_handle_message_with_agent` `17225`、**turn lease `17517`（核实准确）**、`_run_agent` 调用 |
| **B23 T4 滚动压缩（压缩后回复）** | `19082–19553`（核心 `19326–19553`） | **回合后滚动压缩**：实例化压缩 agent → `run_in_executor` 压缩 → 写回 transcript → 落 memory/fulltext | `_rolling_compress_after_reply` `19326`、**T4.E `run_in_executor` 压缩 `19436`（核实准确）**、T4.F~T4.I `19468–19560` |
| **B24 会话信息/slash 访问/目标/心跳/语音** | `19605–20745` | 会话信息格式化、slash 访问控制、goal/heartbeat、语音频道 | `_format_session_info` `19605`、`_check_slash_access` `19728`、`_get_goal_manager_for_event` `19996`、`_handle_voice_channel_*` `20271+` |
| **B25 后台任务 + 话题管理 + MCP reload** | `20745–21665` | background task、Telegram/Discord 话题重命名、MCP reload | `_run_background_task` `20745`、`_execute_mcp_reload` `21648` |
| **B26 slash 确认/通知/executor/视觉转录** | `21665–22665` | slash 确认、update/restart/home 通知、run_in_executor、vision/STT 富化 | `_run_in_executor_with_context` `22665`、`_enrich_message_with_vision` `22787` |
| **B27 executor + 视觉/转录 + 投递 + 进程看护** | `22665–24176` | 网关线程池、图像/转录、完成投递、异步委派 watcher、进程 watcher、agent config 签名、会话模型覆盖 | `get_executor` `22676`、`_transcribe_pending_audio_event_once` `23030`、`_deliver_completion_notification` `23439`、`_async_delegation_watcher` `23590`、`_run_process_watcher` `23676` |
| **B28 turn lease / run generation / agent 缓存 / run_agent** | `24176–25688` | turn lease 释放、会话 run generation、agent 缓存驱逐、proxy run agent、`_run_agent` | `_release_turn_lease` `24239`、`_enforce_agent_cache_cap` `25142`、`_run_agent_via_proxy` `25403`、`_run_agent` `25688` |
| **B29 `_run_agent_inner`（回合执行内部）** | `25688–27619` | **agent 回合内部**：stream consumer、interrupt monitor、long-running 通知、工具执行钩子 | `_run_agent_inner` `25841`、`write_tool_log` `26235`、`track_agent` `26443`、`monitor_for_interrupt` `26476`、`_notify_long_running` `26556` |
| **B30 模块级后台函数** | `27619–27953` | planned-stop watcher、housekeeping、cron ticker shim、await_thread_exit、health export 关闭 | `_run_planned_stop_watcher` `27619`、`_start_gateway_housekeeping` `27708`、`_start_cron_ticker` `27896`（**DEPRECATED shim**）、`_await_thread_exit` `27930` |
| **B31 入口链** | `27965–28756` | `start_gateway` / `main` / `_exit_after_graceful_shutdown` | `start_gateway` `27965`、`main` `28626`、`_exit_after_graceful_shutdown` `28684` |

**核实结论（用户标注的 6 处分节注释）**：`7741`(scale-to-zero)、`11106`(Dream Engine)、`12846`(Kanban watchers)、`14243`(plugin platforms)、`16280`(external-drain gate)、`17517`(turn lease)、`19082–19553`(T4 压缩) **全部存在且标注准确**。补充：12846 处是 "Kanban board watchers" 注释 + reconnect watcher 起点；kanban 实际方法体已抽到 mixin `gateway/kanban_watchers.py`（由 `GatewayKanbanWatchersMixin` 提供）。

---

## 4. C. 拖慢进程 / 容易假死的点（按严重度排序，逐条给行号 + 原因 + 影响面）

> 评分维度：是否冻结事件循环（最严重）、是否在启动关键路径、是否有真实超时保护、是否可能无限等待。

### C-1 【严重】MCP tool discovery 的 120s 阻塞等待 —— 已部分缓解
- **位置**：`28409–28420`（`start_gateway`）；实际 120s 阻塞在 `tools.mcp_tool.discover_mcp_tools` 内部。另有 reload 路径 `21665–21670`。
- **原因**：`discover_mcp_tools()` 内部对一个不可达/慢的 MCP server 有 **120s 阻塞等待**（注释 `28411` 明确写 "uses a blocking 120s wait internally"）。
- **改法/现状**：已被移入 executor：`await _loop.run_in_executor(None, discover_mcp_tools)`（`28418`）。同样在 reload 处用 `loop.run_in_executor(None, discover_mcp_tools)`（`21668`）。**效果**：不再冻结平台心跳（Discord shard / Telegram polling）。
- **残余风险**：executor 线程本身仍会被阻塞最多 120s（bounded，off-loop，所以 loop 不死），但这是"慢启动"而非"假死"。若 MCP server 列表很多且逐个慢，总发现时间线性放大。影响面：全平台心跳 + 后续 `runner.start()` 的延迟（MCP 发现先于 `runner.start()`，`28409` 在 `28422` 之前，所以拖慢整个 ready 时间）。
- **建议**：给每个 server 单独的超时而非全局 120s；或在 executor 内用 `concurrent.futures.wait` 带超时聚合。

### C-2 【严重】逐平台适配器连接串行 + Telegram 冷启动 180s 超时
- **位置**：启动连接循环 `11515–11685`；连接实现 `_connect_adapter_with_timeout` `6840`、`_connect_initial_adapter_with_timeout` `6878`；常量 `_TELEGRAM_CONNECT_TIMEOUT_SECS_DEFAULT = 180.0`（`87`）。
- **原因**：`start()` 里对每个 enabled 平台**顺序 `await` 连接**（`11577`）。默认超时 30s（`_PLATFORM_CONNECT_TIMEOUT_SECS_DEFAULT=30` `83`），但 **Telegram 冷启动超时 180s**（`87`，因为要先跑一轮真实 `getUpdates`）。一个慢的 Telegram 连接会让排在它后面的所有平台连接延迟最多 180s。
- **缓解**：用了 **detach-on-timeout** 模式（`6860–6876`）：`asyncio.wait({task}, timeout=timeout)` 超时后 `task.cancel()` 并转交 `consume_detached_task_result`，**不会永久卡住**（注释 `6854–6868` 解释为何不用 `wait_for`）。所以**有界、不会无限挂**，但仍是真实启动延迟。
- **影响面**：首条消息延迟、READY 状态（systemd watchdog `28540` 在 adapters 之后才启动）推迟。
- **建议**：将逐平台连接改为 `asyncio.gather` + 每平台独立超时，或给 `_create_adapter` 的连接并行化。

### C-3 【中】`--replace` 路径上的同步 `time.sleep`（启动关键路径阻塞）
- **位置**：`28054` `time.sleep(0.5)`（×20 = 最多 10s）、`28078` `time.sleep(0.25)`（×20 = 最多 5s）。
- **原因**：`--replace` 杀掉旧实例后，**在主 `start_gateway` 协程里同步轮询**旧 PID 是否存在（`28050–28054`、`28074–28078`）。这些 `time.sleep` 在 loop 线程上直接阻塞（因为是 `async def start_gateway` 内未 `await` 的普通 sleep）。
- **影响面**：替换一个不肯死的旧实例时，loop 被冻 10–15s（心跳/其它协程停滞）。正常非 replace 启动不走此路。
- **建议**：改为 `await asyncio.sleep(...)`，避免冻结 loop。

### C-4 【中】`GatewayRunner.__init__` 在构造时同步执行 SQLite VACUUM/prune
- **位置**：`6404–6418` `maybe_auto_prune_and_vacuum`（含 `VACUUM`）；`6403–6407` `maybe_auto_archive`。构造入口 `28196`。
- **原因**：`__init__` 在启动关键路径上同步打开 SQLite（`SessionStore` `6156`、`AsyncSessionDB` `6382`）并**可能执行 `VACUUM`**（`6410`）。VACUUM 是阻塞磁盘 IO，数据库大时可卡数秒到分钟。
- **缓解**：受 `state_meta` 的 `min_interval_hours` 门控（默认一天一次）+ 配置 `auto_prune`/`auto_archive` 开关（`6403`/`6408`），所以不是每次启动都触发。
- **影响面**：冷启动首秒被拖慢；日志以 WARNING 记录 SQLite 不可用（`6390`）。
- **建议**：把 VACUUM/prune 挪到 housekeeping 线程（已有时钟）或构造后 fire-and-forget，避免阻塞 boot。

### C-5 【中/根因】`os._exit` 兜底 —— 反映"wedged non-daemon 线程"假死风险
- **位置**：`_exit_after_graceful_shutdown` `28684`；`os._exit(exit_code)` `28752`；相关注释 `28659–28691`、`28722–28752`。
- **原因**：注释明确——一个**被卡住的非 daemon 线程**（例如没有超时的 ThreadPoolExecutor 工具/LLM 调用）会阻塞 `Py_FinalizeEx`（`wait_for_thread_shutdown` join 所有 non-daemon 线程），导致网关半关半开、supervisor 无法重启（`#53107`）。用 `os._exit` 绕过 `atexit`/`Py_FinalizeEx` 是兜底；但**根因是没有超时的阻塞调用**。
- **影响面**：关闭阶段假死；`main` 所有退出路径都经此兜底（`28665–28669`）。
- **建议**：给所有进线程池的阻塞调用（C-6 表）加超时；`shutdown_mcp_servers`/`discover_mcp_tools` 已有 executor，但工具执行本身需超时。

### C-6 【中】网关线程池（`max_workers=10`）无统一超时
- **位置**：`_get_executor` `22676`（**`max_workers=10`** `22688`）、`_run_in_executor_with_context` `22665`（保留 contextvars）。
- **原因**：T4 压缩 `19440`、run_sync 代理工作 `20881`、`_execute_mcp_reload` 的 discover/shutdown `21665` 等都走该池。**若 10 个 slot 被无超时阻塞调用占满，新工作会排队**（线程池无界排队）。
- **影响面**：回合级阻塞、压缩排队；与 C-5 的假死根因同源。
- **建议**：给每个提交任务配 `concurrent.futures.wait(timeout=...)` / `as_completed`，或增大 workers + 每任务超时。

### C-7 【低/中】housekeeping 线程里 `fut.result(timeout=30)` 阻塞该线程
- **位置**：`_start_gateway_housekeeping` `27708`；channel directory 刷新 `27753–27769`，其中 `fut.result(timeout=30)` `27767`。
- **原因**：housekeeping 是后台线程，刷新 channel directory 时 `safe_schedule_threadsafe` 到 loop 并 `fut.result(timeout=30)` 等结果。**若 loop 被冻，该线程最多阻塞 30s**（不影响 loop，但会延迟本次清理 tick）。
- **影响面**：清理节拍漂移；不致命。

### C-8 【低】`_connect_adapter_with_timeout` detach-on-timeout 后的遗留协程
- **位置**：`6860–6876`。
- **原因**：超时后 `task.cancel()` + `consume_detached_task_result`，被取消的 `adapter.connect()` 协程仍在后台跑（可能持有 aiohttp session / 子进程）；若 connect() 吞掉 CancelledError，该协程可能**永久泄漏**。
- **影响面**：资源泄漏累积（长时间运行网关）；非立即假死。
- **建议**：connect() 必须真正响应 cancel；或对 detach 任务做计数上限。

### C-9 【低】watcher 初始延迟 + settle 延迟（可接受）
- **位置**：`_session_expiry_watcher` 初始 `await asyncio.sleep(60)` `12408`；`_session_stall_watcher` 初始 `await asyncio.sleep(10)` `12890`；`start()` 连接后 `await asyncio.sleep(1.0)` `11874`。
- **原因**：故意让位给启动完成。**均在 loop 上 await，不冻结 loop**（只是延迟该 watcher 首跑）。属正常设计。

### C-10 【注意】`run_in_executor` 已正确使用的点（反向证据，说明哪些**没**包）
- **已包**：MCP discovery `28418`、MCP reload `21665`、T4 压缩 `19440`、run_sync 阻塞工作 `20881`、`_run_in_executor_with_context` `22665`。
- **未包（潜在）**：`GatewayRunner.__init__` 的 SQLite VACUUM（C-4）在构造时的同步调用；relay `self_provision_relay`（`11397`）可能的网络调用在 `start()` 同步路径；Dream Engine 的 `config.yaml` 读（`11117`）同步但轻量。

**"看起来像死锁/忙等"的区段**：
- `_await_thread_exit`（`27930–27950`）用 `await asyncio.sleep(poll)` 轮询 `is_alive()`——这是**有意的、正确的**（避免 join 死锁，注释 `27933–27943` 详述），不是 bug。
- `_run_planned_stop_watcher`（`27669–27705`）用 `stop_event.wait(poll_interval)` 轮询标记——**有 sleep，非忙等**。
- 无发现裸 `while True` 无 sleep 的忙等循环（已用 grep 核对 `_TELEGRAM_NOISY_STATUS_RE` 等均为正则/常量）。

---

## 5. D. 拆分约束（不可动的公开符号清单）

### 5.1 `__init__.py` re-export 现状
`gateway/__init__.py` **只** re-export 来自 `.config` / `.session` / `.delivery` 的符号（21 个）。**`gateway.run` 的任何符号都没有进 `__init__.py`**——所有外部引用都是显式 `from gateway.run import X`。因此拆分 `run.py` 时，必须要么保留 `gateway/run.py` 作为**薄 re-export 门面**，要么批量改 ~50 处调用点。

### 5.2 被外部 `from gateway.run import X` 引用的符号（拆分后断点）
按"断掉代价"排序（最高危在前）：

**【最高危：动态/单例/全局，拆出去必断】**
- **`_gateway_runner_ref`**：模块级 weakref 单例（`6110` 设置、`_gateway_runner_ref = _weakref.ref(self)`）。被 `gateway/pairing.py:207`、`gateway/platforms/api_server.py:749/2553`、`tests/.../test_cronjob_run_immediate.py:62` 等引用。**拆出 `GatewayRunner` 必须保留该全局引用点的可达性。**
- **`_hermes_home`**：模块级 `Path` 全局变量。被 **~30 个测试文件** `monkeypatch.setattr("gateway.run._hermes_home", ...)` 以及 `slash_commands.py`、`relay/__init__.py`、`api_server.py` 引用。**拆出会被大面积打补丁逻辑打穿。**
- **`_profile_runtime_scope`**：上下文管理器（`1945`）。被 `gateway/slash_commands.py:422/2556/4549`、`gateway/platforms/api_server.py:2030/2037` 引用。
- **`logger`**：被 `gateway/authz_mixin.py:397` `from gateway.run import logger` 引用。**logger 本身应抽到独立模块。**

**【高危：配置/kwargs helper，被多模块依赖】**
- `_load_gateway_config`（`3230`）：被 `slash_commands.py`（多处）、`relay/__init__.py`（≥9 处 late import）、`api_server.py` 引用。
- `_resolve_gateway_model`（`3341`）：`slash_commands.py`、`api_server.py`、`docs` 引用。
- `_resolve_runtime_agent_kwargs`（`2596`）、`_resolve_runtime_agent_kwargs_for_provider`（`2667`）：`api_server.py`、`slash_commands.py`、`authz` 路径。
- `_resolve_hermes_bin`（`3410`）、`_checkpoint_agent_kwargs`（`3292`）、`_platform_config_key`（`3208`）、`_platform_has_bot_credential`（`2016`）、`_credential_pool_for_provider`（`2689`）：`slash_commands.py`、`api_server.py`、`tests`。

**【中危：常量 + 专用 helper】**
- `_AGENT_PENDING_SENTINEL`、`_INTERRUPT_REASON_STOP`、`_CONVERSATION_SCOPED_STATE`：被大量 `tests/...` 与 `slash_commands.py` 引用（sentinel 在 5547/862/1323/1483/5534 等）。
- `_telegramize_command_mentions`（`873`）、`_redact_approval_command`（`603`）：`slash_commands.py`、`tui_gateway/server.py`、`api_server.py`。
- `_collect_history_media_paths`（`1661`）：`gateway/platforms/base.py:3488`。
- `_reap_gateway_turn_processes`（`2926`）：`gateway/platforms/api_server.py:749`。
- `_resolve_progress_thread_id`（`790`）：`tests/.../test_relay_slack_prompt_dm_root.py`。
- `_start_cron_ticker`（`27896`）、`_PLATFORM_CONNECT_TIMEOUT_SECS_DEFAULT`（`83`）：`tests/gateway/test_startup_restart_race.py`、`scripts/profile_gateway_startup.py`。

**【入口符号】**
- `start_gateway`（`27965`）：`cli.py:18292`、`scripts/hermes-gateway:297`、`scripts/profile_*`。**必须保留为稳定入口。**
- `GatewayRunner`（`5990`）、`TurnRunner`（`3780`）：`tests`、`scripts/profile_gateway_startup.py` 直接 import。

### 5.3 动态/字符串拿的东西（拆出去最容易断）
- **`gateway.platform_registry`**：`_create_adapter`（`14245` `platform_registry.is_registered` / `create_adapter`）按平台名字符串动态创建适配器——**插件平台全靠注册表字符串**，拆 `GatewayRunner` 时 `_create_adapter` 必须随之保持可调用。
- **`tools.mcp_tool.discover_mcp_tools` / `shutdown_mcp_servers`**：`start_gateway` 与 `_execute_mcp_reload` 按字符串模块路径 late import（`28416`/`21665`）。
- **`gateway.status.*`**：`start_gateway` 全程 late import `acquire_gateway_runtime_lock` / `get_running_pid` / `terminate_pid` / `write_pid_file` / `release_all_scoped_locks` 等（PID 锁/单例守卫全在这）——这些与启动守卫强耦合，拆 `start_gateway` 时必须带走。
- **`loop_heartbeat_forever` / `_arm_loop_floor_timer` / `start_loop_liveness_watchdog`**：loop liveness 守卫，被 `_start_loop_liveness_guards`（`10988/10995`）late import。

### 5.4 拆分"安全区"
- 纯函数型辅助（B2/B3：压缩提示、脱敏、正则、时间戳、`_probe_audio_duration` 等）无外部动态依赖，可整体抽到 `gateway/gateway_helpers.py` 且不影响调用方（调用方大多也走 `from gateway.run import`）。
- `TurnRunner`（B5）自包含，仅被 `GatewayRunner` 构造并引用 `run_agent`，可独立成 `gateway/turn_runner.py`，但需保留 `from gateway.run import TurnRunner` 门面或更新 tests。

---

## 6. 建议的模块划分

> 目标：按"依赖方向单向、无循环、保留 `gateway.run` 门面以不破坏 D-2 的 ~50 处引用"。

### 6.1 推荐拆分
| 新模块 | 抽取自（B 块） | 职责 | 依赖方向 |
|--------|--------------|------|---------|
| `gateway/gateway_constants.py` | B1 (`1–141`) | 全局常量、正则、超时默认值、logger | 被所有人依赖（叶子） |
| `gateway/helpers.py` | B2/B3 (`142–2907` 纯函数部分) | hygiene、压缩提示、脱敏、状态模板、t4 审计、媒体事件、配置装载工具 | 叶子，依赖 `agent.*`/`hermes_cli.*` |
| `gateway/turn_runner.py` | B5 (`3775–5990`) | `TurnRunner` 单轮执行 | 依赖 `helpers`、`agent` |
| `gateway/config_load.py` | B4 配置装载 (`3230–3470` 等) + `_load_gateway_config` | 配置/模型/yaml 解析 | 叶子（被 runner/helpers 引用） |
| `gateway/runner.py` | B6–B29 的 `GatewayRunner` 类本体 | 生命周期 + 适配器 + 会话 + 分发 + agent 执行 | 依赖 helpers/config_load/turn_runner/platforms |
| `gateway/gateway_entry.py` | B31 + B30 部分 (`27965–28756`, `27619–27953`) | `start_gateway` / `main` / `_exit_after_graceful_shutdown` + 后台函数（planned-stop/housekeeping/cron shim/await_thread_exit） | 依赖 runner/config_load/status |
| `gateway/status.py`（已存在，需保留接口）| — | PID 锁/单例守卫 | 被 gateway_entry 强依赖 |

> **mixin 已部分外置**：`GatewayKanbanWatchersMixin`（`gateway/kanban_watchers.py`）、`GatewaySlashCommandsMixin`、`GatewayAuthorizationMixin` 已是 mixin，说明作者已在朝这个方向拆。继续把 `GatewayRunner` 的方法按 B8–B29 的职责 group 抽成 mixin 或独立模块最自然。

### 6.2 依赖方向（应为单向 DAG）
```
gateway_constants  →  helpers  →  config_load
                              ↘  turn_runner
helpers / config_load / turn_runner  →  runner
runner  →  gateway_entry  →  (status, lifecycle_ledger, shutdown_watchdog …)
```
- **无循环依赖风险点**：`runner` 需要 `status`（PID 锁）、`shutdown_watchdog`、`cron.scheduler_provider`、`dream`、`relay`——这些都应作为 `runner` 的**下游 late-import 依赖**，且它们不应反向 import `runner`。当前 `relay/__init__.py` 与 `slash_commands.py` 反向 `from gateway.run import _load_gateway_config` 是**已存在的反向依赖**，拆分时需将 `_load_gateway_config` 下沉到 `config_load` 以打破环。
- **`_gateway_runner_ref` / `_hermes_home` / `_profile_runtime_scope` / `logger`** 这 4 个全局必须留在 `gateway.run` 门面或一个新建 `gateway/_globals.py` 中，并被 `runner` 在 `__init__` 里写入、`authz_mixin`/`pairing`/`api_server` 读取——这是唯一会引入"全局单例"耦合的点，建议抽到 `gateway/_globals.py` 并显式导出。

### 6.3 拆分落地顺序建议
1. **先抽叶子**：`gateway_constants.py` + `helpers.py`（纯函数，零风险，可立即抽并保留 `gateway/run.py` 的 `from .helpers import *` 门面）。
2. **抽 `config_load.py`**：搬 `_load_gateway_config` 等，更新 `relay/__init__.py`、`slash_commands.py` 的 late import 目标。
3. **抽 `turn_runner.py`**：搬 `TurnRunner`，保留 `from gateway.run import TurnRunner` 门面。
4. **抽 `gateway_entry.py`**：搬 `start_gateway`/`main`/`_exit_after_graceful_shutdown` + 后台函数；`start_gateway` 内所有的 `from gateway.status import ...` 保持 late import。
5. **最后拆 `GatewayRunner` 本体**为 `runner.py` + 多个 mixin（连接/会话/分发/agent 执行/看门狗分组），每步保留 `gateway/run.py` 作为 `from .runner import GatewayRunner, start_gateway` 的薄门面，从而**完全不破坏 D-2 的 ~50 处外部引用**。

---

## 7. 诚实声明 / 不确定点
- 本报告对 **B14 `start()`（11048–12037）、B20 `_handle_message`（14872–16404）、B22 `_handle_message_with_agent`（17225–19306）、B29 `_run_agent_inner`（25688–27619）** 等超大方法采用了"读关键区段 + 方法地图"的方式，未逐行精读每个分支；但所有标出的阶段边界、await 点、阻塞调用均来自实际行号读取。
- C-1 的"120s 阻塞等待"具体实现在 `tools/mcp_tool.py`（非本文件），本文件只看到调用点与"已移入 executor"的注释（`28409–28414`）；**无法在本文件内确认 120s 常量是否仍在 mcp_tool 内**，需交叉读 `tools/mcp_tool.py` 复核。
- C-4 的 VACUUM 是否每次启动都触发取决于 `state_meta` 上次运行时间戳，本报告**未读 `maybe_auto_prune_and_vacuum` 内部**，仅基于 `6404–6418` 的调用与注释推断其受 `min_interval_hours` 门控。
- 用户标注的 6 处分节注释 **全部核实存在且准确**（见 B 表"核实"列）；无编造行号。
