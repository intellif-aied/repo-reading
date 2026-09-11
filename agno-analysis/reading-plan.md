# Agno 阅读路线

贯穿示例：购物清单 add_item；正文沿同步非流式执行，另对照流式与异步边界。

| 顺序 | 对应文件/符号 | 本章解决的问题 |
| --- | --- | --- |
| 01 · 入口与贯穿示例 | `libs/agno/pyproject.toml`<br>`cookbook/02_agents/05_state_and_session/session_state_basic.py` | 阅读目的：把项目的构建边界与一次真实操作对应起来。主包是 libs/agno，此外有 agnoctl、agno_infra 与大量 cookbook。本文从购物清单示例出发，跟踪 add_item 如何把自然语言请求变成持久化状态。 |
| 02 · Agent.run 是门面，dispatch 建立运行边界 | `libs/agno/agno/agent/agent.py`<br>`libs/agno/agno/agent/_run.py` | 阅读目的：找到用户 API 的第一跳，区分调用前检查与运行内错误。 |
| 03 · 四种数据对象，各自存什么 | `libs/agno/agno/run/base.py`<br>`libs/agno/agno/run/agent.py`<br>`libs/agno/agno/session/agent.py` | 阅读目的：建立接下来所有模块共享的词汇，避免把消息历史、状态和输出混为一谈。 |
| 04 · 读取会话与合并状态 | `libs/agno/agno/agent/_storage.py`<br>`libs/agno/agno/agent/_run.py` | 阅读目的：解释第二次操作怎样看到第一次留下的 shopping_list，以及谁负责保存子运行。 |
| 05 · Python 工具如何变成模型可调用的接口 | `libs/agno/agno/agent/_tools.py` | 阅读目的：跟踪 tools=[add_item] 从用户函数到 Function，再到带上下文的执行对象。 |
| 06 · Prompt 是按顺序构造的消息列表 | `libs/agno/agno/agent/_messages.py` | 阅读目的：了解 instructions、历史、当前输入如何汇合，为什么不能在历史原件上标记状态。 |
| 07 · 外层编排与内层模型循环 | `libs/agno/agno/agent/_run.py`<br>`libs/agno/agno/models/base.py`<br>`libs/agno/agno/models/openai/responses.py` | 阅读目的：找到 Agent 生命周期向模型协议的关键交接；这是整个框架最值得先读的分层。 |
| 08 · 工具调用、状态注入与下一轮请求 | `libs/agno/agno/models/base.py`<br>`libs/agno/agno/tools/function.py` | 阅读目的：解释模型产生的 tool_calls 如何实际执行 add_item，并形成下一轮模型可读的信息。 |
| 09 · 循环什么时候停止，工具上限意味着什么 | `libs/agno/agno/models/base.py` | 阅读目的：把正常终止、暂停、调用限额和中途检查点区分开。 |
| 10 · 完成输出与持久化所有权 | `libs/agno/agno/agent/_response.py`<br>`libs/agno/agno/agent/_run.py`<br>`libs/agno/agno/agent/_session.py` | 阅读目的：从 model_response 回到可返回的 RunOutput，并追踪数据落盘的两次交接。 |
| 11 · SQLite 的 session 行不再携带全部 runs | `libs/agno/agno/db/sqlite/sqlite.py` | 阅读目的：验证上一节的持久化模型确实落到适配器，而不是停留在函数命名。 |
| 12 · 异步、流式与后台任务是不同维度 | `libs/agno/agno/agent/_run.py`<br>`libs/agno/agno/models/base.py`<br>`libs/agno/agno/agent/_managers.py` | 阅读目的：回到示例中的 stream=True，并理解模型调用之外还存在什么并发。 |
| 13 · 暂停是持久化状态，不是睡眠等待 | `libs/agno/agno/agent/_run.py`<br>`libs/agno/agno/run/base.py` | 阅读目的：了解需要用户确认的工具怎样把控制权交还调用者，以及恢复调用要携带什么。 |
| 14 · 错误、重试与工具批次检查点 | `libs/agno/agno/agent/_run.py`<br>`libs/agno/agno/agent/_init.py` | 阅读目的：判断失败后有哪些证据被保留，哪些行为可能重复发生。 |
| 15 · Knowledge 与记忆怎样接入主循环 | `libs/agno/agno/agent/_tools.py`<br>`libs/agno/agno/agent/_messages.py`<br>`libs/agno/agno/knowledge/knowledge.py` | 阅读目的：说明检索为什么可以复用工具闭环，同时把显式会话状态和长期记忆分开。 |
| 16 · Team、Workflow 与 AgentOS 的三种组合方式 | `libs/agno/agno/team/_tools.py`<br>`libs/agno/agno/team/_default_tools.py`<br>`libs/agno/agno/workflow/step.py`<br>`libs/agno/agno/os/routers/agents/router.py` | 阅读目的：从单 Agent 的闭环向外看，定位多 Agent 协作、代码流程编排与 HTTP 服务的不同职责。 |
| 17 · 用测试阅读边界，不把摘录校验当测试通过 | `libs/agno/tests/unit/agent/test_history_copy_on_write.py`<br>`libs/agno/tests/unit/models/test_generator_session_state.py`<br>`libs/agno/tests/unit/agent/test_checkpoint_steps.py`<br>`libs/agno/tests/unit/db/test_sqlite_session_storage_format.py`<br>`libs/agno/tests/unit/agent/test_error_message_flush.py` | 阅读目的：把前述关键不变量对应到可定位的测试预期。 |
| 18 · 收束调用链与后续阅读范围 |  | 阅读目的：用一条可复述的路径把各章连回最初的购物清单操作。 |

完成标准：关键调用交接有执行捕获的源码证据；实现事实、设计推断和测试预期分开；未覆盖模块在末章列出。
