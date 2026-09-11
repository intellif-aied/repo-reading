# Agno 3.0.9：从一次工具调用到会话持久化

*2026-09-09T08:28:09Z by Showboat 0.6.1*
<!-- showboat-id: a625fadc-5867-47f8-a4ba-1cc9043c5406 -->

分析日期：2026-09-09。上游：[agno-agi/agno](https://github.com/agno-agi/agno)，固定版本 `4253dedc95250afddfcea05dfc28f26a4833a117`，包版本 `3.0.9`；开始分析时源码工作区干净。分析对象是本地 checkout，不推断它代表上游最新版本。

本文以购物清单 add_item 为主线。正文、章节与链接通过 Showboat note 写入，源码片段通过 Showboat exec 的只读 sed 捕获。所有命令从分析目录发起，以 --workdir ../agno 在源码目录执行。

[阅读路线](reading-plan.md) · [架构图](architecture.html) · [主流程](flow.html) · [恢复流程](recovery.html)。图中节点链接到本 commit，图表回链指向分析仓库的 GitHub walkthrough。

```bash
git rev-parse HEAD
```

```output
4253dedc95250afddfcea05dfc28f26a4833a117
```

```bash
git status --porcelain
```

```output
```

章节顺序：

- 01 · 入口与贯穿示例
- 02 · Agent.run 是门面，dispatch 建立运行边界
- 03 · 四种数据对象，各自存什么
- 04 · 读取会话与合并状态
- 05 · Python 工具如何变成模型可调用的接口
- 06 · Prompt 是按顺序构造的消息列表
- 07 · 外层编排与内层模型循环
- 08 · 工具调用、状态注入与下一轮请求
- 09 · 循环什么时候停止，工具上限意味着什么
- 10 · 完成输出与持久化所有权
- 11 · SQLite 的 session 行不再携带全部 runs
- 12 · 异步、流式与后台任务是不同维度
- 13 · 暂停是持久化状态，不是睡眠等待
- 14 · 错误、重试与工具批次检查点
- 15 · Knowledge 与记忆怎样接入主循环
- 16 · Team、Workflow 与 AgentOS 的三种组合方式
- 17 · 用测试阅读边界，不把摘录校验当测试通过
- 18 · 收束调用链与后续阅读范围

## 01 · 入口与贯穿示例

阅读目的：把项目的构建边界与一次真实操作对应起来。主包是 libs/agno，此外有 agnoctl、agno_infra 与大量 cookbook。本文从购物清单示例出发，跟踪 add_item 如何把自然语言请求变成持久化状态。

源码：[libs/agno/pyproject.toml:1](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/pyproject.toml#L1)。

```bash
sed -n '1,12p' libs/agno/pyproject.toml
```

```output
[project]
name = "agno"
version = "3.0.9"
description = "The programming language for agentic software."
requires-python = ">=3.9,<4"
readme = "README.md"
license = { file = "LICENSE" }
authors = [
  {name = "Agno Team", email = "hello@agno.com"}
]
keywords = ["agno", "agentos", "agents"]
classifiers = [
```

源码：[cookbook/02_agents/05_state_and_session/session_state_basic.py:8](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/cookbook/02_agents/05_state_and_session/session_state_basic.py#L8)。

```bash
sed -n '8,48p' cookbook/02_agents/05_state_and_session/session_state_basic.py
```

```output
from agno.agent import Agent, RunOutput  # noqa
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIResponses
from agno.run import RunContext


def add_item(run_context: RunContext, item: str) -> str:
    """Add an item to the shopping list."""
    if run_context.session_state is None:
        run_context.session_state = {}

    run_context.session_state["shopping_list"].append(item)  # type: ignore
    return f"The shopping list is now {run_context.session_state['shopping_list']}"  # type: ignore


# Create an Agent that maintains state
# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model=OpenAIResponses(id="gpt-5-mini"),
    # Initialize the session state with a counter starting at 0 (this is the default session state for all users)
    session_state={"shopping_list": []},
    db=SqliteDb(db_file="tmp/agents.db"),
    tools=[add_item],
    # You can use variables from the session state in the instructions
    instructions="Current state (shopping list) is: {shopping_list}",
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Example usage
    agent.print_response("Add milk, eggs, and bread to the shopping list", stream=True)
    print(f"Final session state: {agent.get_session_state()}")

    # Alternatively,
    # response: RunOutput = agent.run("Add milk, eggs, and bread to the shopping list")
    # print(f"Final session state: {response.session_state}")
```

示例声明 OpenAIResponses、SqliteDb、默认 shopping_list 和 Python 工具。add_item 的 item 是业务参数，run_context 则由框架注入。源码主入口演示 stream=True；为便于逐步解释，正文沿同文件注释中的 agent.run(...) 非流式路径追踪，再在第 12 节对照流式/异步实现。

典型输入是“添加 milk、eggs、bread”。若模型分别发出对应工具调用，列表将依次扩展；这是条件推演，实际调用次数和顺序取决于模型，不是本次实测输出。示例不会在本分析中执行，避免创建数据库和调用付费模型。

## 02 · Agent.run 是门面，dispatch 建立运行边界

阅读目的：找到用户 API 的第一跳，区分调用前检查与运行内错误。

源码：[libs/agno/agno/agent/agent.py:1482](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/agent.py#L1482)。

```bash
sed -n '1482,1507p' libs/agno/agno/agent/agent.py
```

```output
    ) -> Union[RunOutput, Iterator[Union[RunOutputEvent, RunOutput]]]:
        return _run.run_dispatch(
            self,
            input=input,
            stream=stream,
            stream_events=stream_events,
            user_id=user_id,
            session_id=session_id,
            session_state=session_state,
            run_context=run_context,
            run_id=run_id,
            audio=audio,
            images=images,
            videos=videos,
            files=files,
            knowledge_filters=knowledge_filters,
            add_history_to_context=add_history_to_context,
            add_dependencies_to_context=add_dependencies_to_context,
            add_session_state_to_context=add_session_state_to_context,
            dependencies=dependencies,
            metadata=metadata,
            output_schema=output_schema,
            yield_run_output=yield_run_output,
            debug_mode=debug_mode,
            **kwargs,
        )
```

源码：[libs/agno/agno/agent/_run.py:1338](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L1338)。

```bash
sed -n '1338,1380p' libs/agno/agno/agent/_run.py
```

```output
    if has_async_db(agent):
        raise RuntimeError("`run` method is not supported with an async database. Please use `arun` method instead.")

    # Refused here rather than at the persist below, which runs after the model call.
    if isinstance(agent.media_storage, AsyncMediaStorage):
        raise ValueError("Cannot use sync run() with an AsyncMediaStorage. Use arun() instead.")

    # Set the id for the run and register it immediately for cancellation tracking
    run_id = run_id or str(uuid4())

    if (add_history_to_context or agent.add_history_to_context) and not agent.db and not agent.team_id:
        log_warning(
            "add_history_to_context is True, but no database has been assigned to the agent. History will not be added to the context."
        )

    background_tasks = kwargs.pop("background_tasks", None)
    if background_tasks is not None:
        from fastapi import BackgroundTasks

        background_tasks: BackgroundTasks = background_tasks  # type: ignore

    # Validate input against input_schema if provided
    validated_input = validate_input(input, agent.input_schema)

    # Normalise hook & guardrails
    if not agent._hooks_normalised:
        if agent.pre_hooks:
            agent.pre_hooks = normalize_pre_hooks(agent.pre_hooks)  # type: ignore
        if agent.post_hooks:
            agent.post_hooks = normalize_post_hooks(agent.post_hooks)  # type: ignore
        agent._hooks_normalised = True

    # Initialize session
    session_id, user_id = initialize_session(agent, session_id=session_id, user_id=user_id)

    # Initialize the Agent
    agent.initialize_agent(debug_mode=debug_mode)

    image_artifacts, video_artifacts, audio_artifacts, file_artifacts = validate_media_object_id(
        images=images, videos=videos, audios=audio, files=files
    )

    # Create RunInput to capture the original user input
```

源码：[libs/agno/agno/agent/_run.py:1401](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L1401)。

```bash
sed -n '1401,1447p' libs/agno/agno/agent/_run.py
```

```output
    # Resolve all run options centrally
    opts = resolve_run_options(
        agent,
        stream=stream,
        stream_events=stream_events,
        yield_run_output=yield_run_output,
        add_history_to_context=add_history_to_context,
        add_dependencies_to_context=add_dependencies_to_context,
        add_session_state_to_context=add_session_state_to_context,
        dependencies=dependencies,
        knowledge_filters=knowledge_filters,
        metadata=metadata,
        session_metadata=session_metadata,
        output_schema=output_schema,
    )

    agent.model = cast(Model, agent.model)

    # Initialize run context
    run_context = run_context or RunContext(
        run_id=run_id,
        session_id=session_id,
        user_id=user_id,
        session_state=session_state,
        dependencies=opts.dependencies,
        knowledge_filters=opts.knowledge_filters,
        metadata=opts.metadata,
        output_schema=opts.output_schema,
    )
    # Apply options with precedence: explicit args > existing run_context > resolved defaults.
    opts.apply_to_context(
        run_context,
        dependencies_provided=dependencies is not None,
        knowledge_filters_provided=knowledge_filters is not None,
        metadata_provided=metadata is not None,
        user_id=user_id,
    )

    # Prepare arguments for the model (must be after run_context is fully initialized)
    response_format = get_response_format(agent, run_context=run_context) if agent.parser_model is None else None

    # Create a new run_response for this attempt
    run_response = RunOutput(
        run_id=run_id,
        session_id=session_id,
        agent_id=agent.id,
        user_id=user_id,
```

Agent.run 把参数转给 run_dispatch；后者拒绝同步 API 搭配异步 DB/媒体存储，验证 input_schema，初始化 session 和 Agent，再构造 RunInput、RunContext、RunOutput。resolve_run_options 集中解决参数优先级，opts.apply_to_context 保留“显式参数 > 已有上下文 > 解析后的默认值”的语义。

这些初始化检查位于 _run 的 try/except 外，因此不能笼统说“所有异常都会变成 RunOutput.status=ERROR”。stream 分支返回迭代器，非流式分支调用 _run；生成器的执行与消费绑定，这会影响连接和取消的生命周期。

## 03 · 四种数据对象，各自存什么

阅读目的：建立接下来所有模块共享的词汇，避免把消息历史、状态和输出混为一谈。

源码：[libs/agno/agno/run/base.py:16](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/run/base.py#L16)。

```bash
sed -n '16,45p' libs/agno/agno/run/base.py
```

```output
@dataclass
class RunContext:
    run_id: str
    session_id: str
    user_id: Optional[str] = None

    workflow_id: Optional[str] = None
    workflow_name: Optional[str] = None

    dependencies: Optional[Dict[str, Any]] = None
    knowledge_filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
    metadata: Optional[Dict[str, Any]] = None
    session_state: Optional[Dict[str, Any]] = None
    output_schema: Optional[Union[Type[BaseModel], Dict[str, Any]]] = None

    # Live reference to the current run's message list. Available in tool hooks
    # via run_context.messages. Hooks receive a shallow copy (via _safe_hook_call)
    # so accidental list mutations (.clear(), .append()) won't corrupt the run.
    # Individual Message objects are shared references — do not mutate them.
    messages: Optional[List[Message]] = None

    # Runtime-resolved callable factory results
    tools: Optional[List[Any]] = None
    knowledge: Optional[Any] = None
    members: Optional[List[Any]] = None

    # Per-run additive tools from the client (e.g., AG-UI frontend tools)
    # Merged AFTER agent.tools during tool resolution
    client_tools: Optional[List[Any]] = None

```

源码：[libs/agno/agno/run/agent.py:618](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/run/agent.py#L618)。

```bash
sed -n '618,675p' libs/agno/agno/run/agent.py
```

```output
class RunOutput:
    """Response returned by Agent.run() or Workflow.run() functions"""

    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    session_id: Optional[str] = None
    parent_run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    user_id: Optional[str] = None

    # Input media and messages from user
    input: Optional[RunInput] = None

    content: Optional[Any] = None
    content_type: str = "str"

    reasoning_content: Optional[str] = None
    reasoning_steps: Optional[List[ReasoningStep]] = None
    reasoning_messages: Optional[List[Message]] = None

    model_provider_data: Optional[Dict[str, Any]] = None

    model: Optional[str] = None
    model_provider: Optional[str] = None
    messages: Optional[List[Message]] = None
    metrics: Optional[RunMetrics] = None
    additional_input: Optional[List[Message]] = None

    tools: Optional[List[ToolExecution]] = None

    images: Optional[List[Image]] = None  # Images attached to the response
    videos: Optional[List[Video]] = None  # Videos attached to the response
    audio: Optional[List[Audio]] = None  # Audio attached to the response
    files: Optional[List[File]] = None  # Files attached to the response
    response_audio: Optional[Audio] = None  # Model audio response

    citations: Optional[Citations] = None
    references: Optional[List[MessageReferences]] = None

    followups: Optional[List[str]] = None

    metadata: Optional[Dict[str, Any]] = None
    session_state: Optional[Dict[str, Any]] = None

    created_at: int = field(default_factory=lambda: int(time()))

    events: Optional[List[RunOutputEvent]] = None

    status: RunStatus = RunStatus.running
    # Queue-attempt generation stamp: set by the queue worker when attempt N
    # claims this run. Terminal writes carry their attempt and are fenced
    # against a NEWER stored value, so a presumed-dead attempt's late write
    # cannot clobber its successor. None outside durable-queue execution.
    queue_attempt: Optional[int] = None

    # User control flow (HITL) requirements to continue a run when paused, in order of arrival
    requirements: Optional[list[RunRequirement]] = None
```

源码：[libs/agno/agno/session/agent.py:15](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/session/agent.py#L15)。

```bash
sed -n '15,45p' libs/agno/agno/session/agent.py
```

```output
@dataclass
class AgentSession:
    """Agent Session that is stored in the database"""

    # Session UUID
    session_id: str

    # ID of the agent that this session is associated with
    agent_id: Optional[str] = None
    # ID of the team that this session is associated with
    team_id: Optional[str] = None
    # # ID of the user interacting with this agent
    user_id: Optional[str] = None
    # ID of the workflow that this session is associated with
    workflow_id: Optional[str] = None

    # Session Data: session_name, session_state, images, videos, audio
    session_data: Optional[Dict[str, Any]] = None
    # Metadata stored with this agent
    metadata: Optional[Dict[str, Any]] = None
    # Agent Data: agent_id, name and model
    agent_data: Optional[Dict[str, Any]] = None
    # List of all runs in the session
    runs: Optional[List[Union[RunOutput, TeamRunOutput]]] = None
    # Summary of the session
    summary: Optional["SessionSummary"] = None

    # The unix timestamp when this session was created
    created_at: Optional[int] = None
    # The unix timestamp when this session was last updated
    updated_at: Optional[int] = None
```

| 对象 | 生命周期与职责 | 购物清单中的内容 |
| --- | --- | --- |
| RunInput | 本次原始输入与媒体 | 用户添加商品的请求 |
| RunContext | 本次执行所需的身份、依赖和可变状态 | session_state.shopping_list |
| RunOutput | 本次结果、messages、tools、metrics、status、requirements | 回答、工具执行记录、最终状态 |
| AgentSession | 跨 run 的会话容器 | session_data、runs、summary |

Message 是传给模型的对话单元；它不等于 RunOutput。RunContext.messages 是当前消息列表的活引用，而 hook 接收到的保护性浅拷贝也并不隔离内部 Message 对象。源码的 dataclass 是便利的内存模型，不应由字段布局反推数据库表结构。

## 04 · 读取会话与合并状态

阅读目的：解释第二次操作怎样看到第一次留下的 shopping_list，以及谁负责保存子运行。

源码：[libs/agno/agno/agent/_storage.py:635](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_storage.py#L635)。

```bash
sed -n '635,675p' libs/agno/agno/agent/_storage.py
```

```output
def read_or_create_session(
    agent: Agent,
    session_id: str,
    user_id: Optional[str] = None,
) -> AgentSession:
    from time import time
    from uuid import uuid4

    # Returning cached session if we have one
    cached_session = agent._get_cached_session(session_id, user_id=user_id)
    if cached_session is not None:
        return cached_session

    # Try to load from database
    agent_session = None
    if agent.db is not None and agent.team_id is None and agent.workflow_id is None:
        log_debug(f"Reading AgentSession: {session_id}")

        agent_session = cast(AgentSession, read_session(agent, session_id=session_id, user_id=user_id))

    if agent_session is None:
        # Creating new session if none found
        log_debug(f"Creating new AgentSession: {session_id}")
        from copy import deepcopy

        session_data = {}
        if agent.session_state is not None:
            session_data["session_state"] = deepcopy(agent.session_state)
        agent_session = AgentSession(
            session_id=session_id,
            agent_id=agent.id,
            user_id=user_id,
            agent_data=get_agent_data(agent),
            session_data=session_data,
            # Copy so the session record never aliases the shared Agent's dict
            metadata=deepcopy(agent.metadata),
            created_at=int(time()),
        )
        if agent.introduction is not None:
            introduction_run = RunOutput(
                run_id=str(uuid4()),
```

源码：[libs/agno/agno/agent/_storage.py:552](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_storage.py#L552)。

```bash
sed -n '552,575p' libs/agno/agno/agent/_storage.py
```

```output
def load_session_state(agent: Agent, session: AgentSession, session_state: Dict[str, Any]):
    """Load and return the stored session_state from the database, optionally merging it with the given one"""

    # Get the session_state from the database and merge with proper precedence
    # At this point session_state contains: agent_defaults + run_params
    if session.session_data is not None and "session_state" in session.session_data:
        session_state_from_db = session.session_data.get("session_state")

        if (
            session_state_from_db is not None
            and isinstance(session_state_from_db, dict)
            and len(session_state_from_db) > 0
            and not agent.overwrite_db_session_state
        ):
            # This preserves precedence: run_params > db_state > agent_defaults
            merged_state = session_state_from_db.copy()
            merge_dictionaries(merged_state, session_state)
            session_state.clear()
            session_state.update(merged_state)

    # Update the session_state in the session
    if session.session_data is not None:
        session.session_data["session_state"] = session_state

```

源码：[libs/agno/agno/agent/_run.py:439](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L439)。

```bash
sed -n '439,465p' libs/agno/agno/agent/_run.py
```

```output
                    agent_session = read_or_create_session(agent, session_id=session_id, user_id=user_id)

                # 2. Update metadata and session state
                if not (attempt == 0 and pre_session is not None):
                    update_metadata(agent, session=agent_session)

                # Initialize session state. Get it from DB if relevant.
                run_context.session_state = load_session_state(
                    agent,
                    session=agent_session,
                    session_state=run_context.session_state if run_context.session_state is not None else {},
                )
                _initialize_session_state(
                    run_context.session_state,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_context.run_id,
                )

                # 3. Resolve dependencies
                if run_context.dependencies is not None:
                    resolve_run_dependencies(
                        agent, run_context=run_context, run_input=run_response.input, session=agent_session
                    )

                raise_if_cancelled(run_response.run_id)  # type: ignore

```

read_or_create_session 先查会话缓存，再查数据库；没有已有会话时，以 deepcopy(agent.session_state) 创建 session_data。加载后的数据库状态再与本次传入状态合并，运行值覆盖数据库同名值；overwrite_db_session_state 会改变这一合并分支。_initialize_session_state 还放入 current_* 运行标识，收尾时清除。

历史消息是否进入模型由 add_history_to_context 控制；状态存在 DB 并不意味着全部历史自动进入 prompt。team_id/workflow_id 所标记的成员不走独立 Agent 会话读写分支，持久化所有权交给编排层。新会话默认值采用深拷贝，但不能由此推导同一个 session 的并发更新具有事务隔离。

## 05 · Python 工具如何变成模型可调用的接口

阅读目的：跟踪 tools=[add_item] 从用户函数到 Function，再到带上下文的执行对象。

源码：[libs/agno/agno/agent/_tools.py:145](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_tools.py#L145)。

```bash
sed -n '145,169p' libs/agno/agno/agent/_tools.py
```

```output

    agent_tools: List[Union[Toolkit, Callable, Function, Dict]] = []

    # Resolve callable factories
    resolve_callable_tools(agent, run_context)
    resolve_callable_knowledge(agent, run_context)

    resolved_tools = get_resolved_tools(agent, run_context)
    resolved_knowledge = get_resolved_knowledge(agent, run_context)

    # Append client_tools (e.g., AG-UI frontend tools) if present
    if run_context.client_tools:
        resolved_tools = list(resolved_tools or []) + list(run_context.client_tools)

    # Connect tools that require connection management
    _init.connect_connectable_tools(agent)

    # Add provided tools
    if resolved_tools is not None:
        # If not running in async mode, raise if any tool is async
        _raise_if_async_tools_in_list(resolved_tools)
        agent_tools.extend(resolved_tools)

    # Add tools for accessing memory
    if agent.read_chat_history:
```

源码：[libs/agno/agno/agent/_tools.py:480](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_tools.py#L480)。

```bash
sed -n '480,536p' libs/agno/agno/agent/_tools.py
```

```output
                log_warning(f"Duplicate tool name '{tool.name}' already registered on agent; skipping the duplicate.")
                if emit_toolkit_instructions and source_toolkit is not None:
                    add_toolkit_instructions(source_toolkit)
                continue
            _function_names.append(tool.name)

            tool = tool._per_run_copy()
            # Respect the function's explicit strict setting if set
            effective_strict = strict if tool.strict is None else tool.strict
            tool.process_entrypoint(strict=effective_strict)

            tool._agent = agent
            if agent._team is not None:
                tool._team = agent._team
            if strict and tool.strict is None:
                tool.strict = True
            if agent.tool_hooks is not None:
                tool.tool_hooks = agent.tool_hooks
            _functions.append(tool)
            log_debug(f"Added tool {tool.name}")

            # Add instructions from the Function
            if tool.add_instructions and tool.instructions is not None:
                agent._tool_instructions.append(tool.instructions)

            # DB-loaded toolkit members are bare Functions. Their live owning
            # Toolkit is restored by Registry.rehydrate_function; add its
            # guidance after all its member Functions, matching live Toolkit
            # instruction order, and only once.
            if emit_toolkit_instructions and source_toolkit is not None:
                add_toolkit_instructions(source_toolkit)

        elif callable(tool):
            try:
                function_name = tool.__name__

                if function_name in _function_names:
                    log_warning(
                        f"Duplicate tool name '{function_name}' already registered on agent; skipping the duplicate."
                    )
                    continue
                _function_names.append(function_name)

                # from_callable caches the derivation and returns an isolated
                # per-run copy, so no further copy is needed before mutating it.
                _func = Function.from_callable(tool, strict=strict)
                # Detect @approval sentinel on raw callable
                _approval_type = getattr(tool, "_agno_approval_type", None)
                if _approval_type is not None:
                    _func.approval_type = _approval_type
                    if _approval_type == "required" and not any(
                        [_func.requires_user_input, _func.requires_confirmation, _func.external_execution]
                    ):
                        _func.requires_confirmation = True
                    elif _approval_type == "audit" and not any(
                        [_func.requires_user_input, _func.requires_confirmation, _func.external_execution]
                    ):
```

源码：[libs/agno/agno/agent/_tools.py:557](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_tools.py#L557)。

```bash
sed -n '557,598p' libs/agno/agno/agent/_tools.py
```

```output
def determine_tools_for_model(
    agent: Agent,
    model: Model,
    processed_tools: List[Union[Toolkit, Callable, Function, Dict]],
    run_response: RunOutput,
    run_context: RunContext,
    session: AgentSession,
    async_mode: bool = False,
) -> List[Union[Function, dict]]:
    _functions: List[Union[Function, dict]] = []

    # Get Agent tools
    if processed_tools is not None and len(processed_tools) > 0:
        log_debug("Processing tools for model")
        _functions = parse_tools(
            agent, tools=processed_tools, model=model, run_context=run_context, async_mode=async_mode
        )

    # Update the session state for the functions
    if _functions:
        # Check if any functions need media before collecting
        needs_media = any(
            entrypoint_accepts_media(func.entrypoint)
            for func in _functions
            if isinstance(func, Function) and func.entrypoint is not None
        )

        # Only collect media if functions actually need them
        joint_images = collect_joint_images(run_response.input, session) if needs_media else None
        joint_files = collect_joint_files(run_response.input) if needs_media else None
        joint_audios = collect_joint_audios(run_response.input, session) if needs_media else None
        joint_videos = collect_joint_videos(run_response.input, session) if needs_media else None

        for func in _functions:  # type: ignore
            if isinstance(func, Function):
                func._run_context = run_context
                func._images = joint_images
                func._files = joint_files
                func._audios = joint_audios
                func._videos = joint_videos

    return _functions
```

get_tools 解析动态工厂、合并 client_tools，并连接需要生命周期管理的工具。parse_tools 接受 Toolkit、Function、Callable 及 provider 原生 dict；Callable 通过 Function.from_callable 生成描述，已有 Function 使用每次运行的副本处理。最后 determine_tools_for_model 将本次 RunContext 绑定到各 Function。

模型看到参数 schema，框架保留真正的 Python entrypoint 与注入对象。provider 原生 dict 不一定是本地函数，不能假定所有工具都由 FunctionCall.execute 执行。同步路径也会提前拒绝异步工具。

## 06 · Prompt 是按顺序构造的消息列表

阅读目的：了解 instructions、历史、当前输入如何汇合，为什么不能在历史原件上标记状态。

源码：[libs/agno/agno/agent/_messages.py:60](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_messages.py#L60)。

```bash
sed -n '60,98p' libs/agno/agno/agent/_messages.py
```

```output
def format_message_with_state_variables(
    agent: Agent,
    message: Any,
    run_context: Optional[RunContext] = None,
) -> Any:
    """Format a message with the session state variables from run_context."""
    if not isinstance(message, str):
        return message

    # A message without "{" cannot contain a {var} placeholder, and without "$"
    # Template.safe_substitute is an identity transform - skip the regex and
    # template machinery entirely for the common plain-text case.
    if "{" not in message and "$" not in message:
        return message

    # Extract values from run_context
    session_state = run_context.session_state if run_context else None
    dependencies = run_context.dependencies if run_context else None
    metadata = run_context.metadata if run_context else None
    user_id = run_context.user_id if run_context else None

    # Should already be resolved and passed from run() method
    format_variables = ChainMap(
        session_state if session_state is not None else {},
        dependencies or {},
        metadata or {},
        {"user_id": user_id} if user_id is not None else {},
    )

    converted_msg = message
    for var_name in format_variables.keys():
        # Only convert standalone {var_name} patterns, not nested ones
        pattern = r"\{" + re.escape(var_name) + r"\}"
        replacement = "${" + var_name + "}"
        converted_msg = re.sub(pattern, replacement, converted_msg)

    # Use Template to safely substitute variables
    template = string.Template(converted_msg)
    try:
```

源码：[libs/agno/agno/agent/_messages.py:1133](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_messages.py#L1133)。

```bash
sed -n '1133,1150p' libs/agno/agno/agent/_messages.py
```

```output
    # Initialize the RunMessages object (no media here - that's in RunInput now)
    run_messages = RunMessages()

    # 1. Add system message to run_messages
    system_message = get_system_message(
        agent,
        session=session,
        run_context=run_context,
        tools=tools,
        add_session_state_to_context=add_session_state_to_context,
        input=input,
    )
    if system_message is not None:
        run_messages.system_message = system_message
        run_messages.messages.append(system_message)

    # 2. Add extra messages to run_messages if provided
    if agent.additional_input is not None:
```

源码：[libs/agno/agno/agent/_messages.py:1175](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_messages.py#L1175)。

```bash
sed -n '1175,1212p' libs/agno/agno/agent/_messages.py
```

```output

    # 3. Add history to run_messages
    if add_history_to_context:
        # Only skip messages from history when system_message_role is NOT a standard conversation role.
        # Standard conversation roles ("user", "assistant", "tool") should never be filtered
        # to preserve conversation continuity.
        skip_role = (
            agent.system_message_role if agent.system_message_role not in ["user", "assistant", "tool"] else None
        )

        history: List[Message] = session.get_messages(
            last_n_runs=agent.num_history_runs,
            limit=agent.num_history_messages,
            skip_roles=[skip_role] if skip_role else None,
            agent_id=agent.id if agent.team_id is not None else None,
        )

        if len(history) > 0:
            history_copy = [copy_history_message(msg) for msg in history]

            # Filter tool calls from history if limit is set (before adding to run_messages)
            if agent.max_tool_calls_from_history is not None:
                filter_tool_calls(history_copy, agent.max_tool_calls_from_history)

            log_debug(f"Adding {len(history_copy)} messages from history")

            run_messages.messages += history_copy

    # 4. Add user message to run_messages
    user_message: Optional[Message] = None

    # 4.1 Build user message if input is None, str or list and not a list of Message/dict objects
    if (
        input is None
        or isinstance(input, str)
        or (
            isinstance(input, list)
            and not (
```

源码：[libs/agno/agno/agent/_messages.py:1276](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_messages.py#L1276)。

```bash
sed -n '1276,1300p' libs/agno/agno/agent/_messages.py
```

```output
                except Exception as e:
                    log_warning(f"Failed to validate message: {str(e)}")

    # Add user message to run_messages
    if user_message is not None:
        run_messages.user_message = user_message
        run_messages.messages.append(user_message)

    # Read offloaded media back on every message headed for the model: a member's history arrives as input.
    media_storage = _resolve_media_storage(agent)
    if media_storage is not None:
        from agno.utils.media_offload import refresh_messages_media

        refresh_messages_media(run_messages.messages, media_storage)

    # Set messages on run_context so tool hooks can access the current message history
    run_context.messages = run_messages.messages

    return run_messages


async def aget_run_messages(
    agent: Agent,
    *,
    run_response: RunOutput,
```

get_run_messages 顺序组装 system message、additional_input、历史消息、当前用户消息。system message 的构建路径通过 format_message_with_state_variables 插值，因此示例中的 {shopping_list} 反映构造消息时的状态。工具改变列表之后，模型下一轮首先通过 tool result 得知变化；不要假定每次工具调用后都会重新构建整条 system message。

历史使用 copy_history_message 后再标记 from_history，避免污染缓存会话中的消息；历史工具筛选也作用于拷贝。历史窗口与工具结果占用决定模型输入规模，这一代价不同于存储层只写当前 run。

## 07 · 外层编排与内层模型循环

阅读目的：找到 Agent 生命周期向模型协议的关键交接；这是整个框架最值得先读的分层。

源码：[libs/agno/agno/agent/_run.py:538](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L538)。

```bash
sed -n '538,580p' libs/agno/agno/agent/_run.py
```

```output
                    user_id=user_id,
                    existing_future=learning_future,
                    run_context=run_context,
                )

                raise_if_cancelled(run_response.run_id)  # type: ignore

                # 8. Reason about the task
                handle_reasoning(agent, run_response=run_response, run_messages=run_messages, run_context=run_context)

                # Check for cancellation before model call
                raise_if_cancelled(run_response.run_id)  # type: ignore

                # 9. Generate a response from the Model (includes running function calls)
                agent.model = cast(Model, agent.model)

                model_response: ModelResponse = call_model_with_fallback(
                    agent.model,
                    agent.fallback_config,
                    messages=run_messages.messages,
                    tools=_tools,
                    tool_choice=agent.tool_choice,
                    tool_call_limit=agent.tool_call_limit,
                    response_format=response_format,
                    run_response=run_response,
                    send_media_to_model=agent.send_media_to_model,
                    compression_manager=agent.compression_manager if agent.compress_tool_results else None,
                    **result_store_kwargs(agent),
                    after_tool_results=build_after_tool_results_callback(
                        agent,
                        run_response=run_response,
                        session=agent_session,
                        run_messages=run_messages,
                        run_context=run_context,
                    ),
                )

                # Check for cancellation after model call
                raise_if_cancelled(run_response.run_id)  # type: ignore

                # If an output model is provided, generate output using the output model
                generate_response_with_output_model(agent, model_response, run_messages, run_response=run_response)

```

源码：[libs/agno/agno/models/base.py:689](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/base.py#L689)。

```bash
sed -n '689,740p' libs/agno/agno/models/base.py
```

```output
        _log_messages(messages)
        model_response = ModelResponse()

        function_call_count = 0

        _tool_dicts = self._format_tools(tools) if tools is not None else []
        _functions = {tool.name: tool for tool in tools if isinstance(tool, Function)} if tools is not None else {}

        _compress_tool_results = compression_manager is not None and compression_manager.compress_tool_results
        _compression_manager = compression_manager if _compress_tool_results else None

        while True:
            # Compress tool results if compression is enabled and threshold is met
            if _compression_manager is not None and _compression_manager.should_compress(
                messages, tools, model=self, response_format=response_format
            ):
                _compression_manager.compress(
                    messages, run_metrics=run_response.metrics if run_response is not None else None
                )

            # Get response from model
            assistant_message = Message(role=self.assistant_message_role)
            # Initialize message metrics and start timer before model call
            self._ensure_message_metrics_initialized(assistant_message)
            self._process_model_response(
                messages=messages,
                assistant_message=assistant_message,
                model_response=model_response,
                response_format=response_format,
                tools=_tool_dicts,
                tool_choice=tool_choice or self._tool_choice,
                run_response=run_response,
                compress_tool_results=_compress_tool_results,
            )

            # Accumulate metrics for non-stream responses
            if run_response is not None and model_response.response_usage is not None:
                from agno.metrics import accumulate_model_metrics

                accumulate_model_metrics(model_response, self, self.model_type, run_response.metrics)

            # Add assistant message to messages
            messages.append(assistant_message)

            # Log response and metrics
            assistant_message.log(metrics=True, use_compressed_content=_compress_tool_results)

            # Handle tool calls if present
            if assistant_message.tool_calls:
                # Prepare function calls
                function_calls_to_run = self._prepare_function_calls(
                    assistant_message=assistant_message,
```

源码：[libs/agno/agno/models/base.py:1120](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/base.py#L1120)。

```bash
sed -n '1120,1136p' libs/agno/agno/models/base.py
```

```output
        provider_response = self._invoke_with_retry(
            assistant_message=assistant_message,
            messages=messages,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice or self._tool_choice,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        )

        # Set TTFT after response arrives (guard ensures first-call-wins)
        if run_response and run_response.metrics:
            run_response.metrics.set_time_to_first_token()

        # Populate the assistant message
        self._populate_assistant_message(assistant_message=assistant_message, provider_response=provider_response)

```

源码：[libs/agno/agno/models/base.py:226](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/base.py#L226)。

```bash
sed -n '226,245p' libs/agno/agno/models/base.py
```

```output

    def _invoke_with_retry(self, **kwargs) -> ModelResponse:
        """
        Invoke the model with retry logic for ModelProviderError.

        This method wraps the invoke() call and retries on ModelProviderError
        with optional exponential backoff.
        """
        last_exception: Optional[ModelProviderError] = None
        retries_with_guidance_count = kwargs.pop("retries_with_guidance_count", 0)

        for attempt in range(self.retries + 1):
            try:
                return self.invoke(**kwargs)
            except ModelProviderError as e:
                last_exception = ModelProviderError.classify(e)
                # Check if error is non-retryable
                if not self._is_retryable_error(last_exception):
                    log_error(f"Non-retryable model provider error: {str(e)}")
                    raise last_exception from e
```

源码：[libs/agno/agno/models/openai/responses.py:770](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/openai/responses.py#L770)。

```bash
sed -n '770,801p' libs/agno/agno/models/openai/responses.py
```

```output
    def invoke(
        self,
        messages: List[Message],
        assistant_message: Message,
        response_format: Optional[Union[Dict, Type[BaseModel]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[RunOutput] = None,
        compress_tool_results: bool = False,
    ) -> ModelResponse:
        """
        Send a request to the OpenAI Responses API.
        """
        try:
            request_params = self.get_request_params(
                messages=messages,
                response_format=response_format,
                tools=tools,
                tool_choice=tool_choice,
                run_response=run_response,
            )

            assistant_message.metrics.start_timer()

            provider_response = self.get_client().responses.create(
                **self._get_model_request_kwargs(),
                input=self._format_messages(messages, compress_tool_results, tools=tools),  # type: ignore
                **request_params,
            )

            # Stop the timer before polling so wall-clock polling wait is not counted as inference time.
            # For background mode, the initial create() measures submission latency; the polling loop
```

_run 在准备消息、可选记忆/学习任务及推理之后，通过 call_model_with_fallback 调用模型响应层。Model.response 持有 while True：创建 assistant Message，调用 _process_model_response，追加模型回复，再判断是否执行工具。OpenAIResponses.invoke 在适配层调用 SDK 的 responses.create，把框架 Message 转成供应商请求。

实现事实：运行策略主要在 agent/_run.py，供应商无关的工具往返在 models/base.py，协议和返回格式转换在 models/openai/responses.py。设计解读：这种分层让同一套生命周期可以接不同模型，但 Model 基类也承担工具执行、压缩与检查点回调，阅读时不能把它仅视作 HTTP 接口。fallback 包装器在本路线只定位交接，不展开每个 fallback 策略。

## 08 · 工具调用、状态注入与下一轮请求

阅读目的：解释模型产生的 tool_calls 如何实际执行 add_item，并形成下一轮模型可读的信息。

源码：[libs/agno/agno/models/base.py:738](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/base.py#L738)。

```bash
sed -n '738,759p' libs/agno/agno/models/base.py
```

```output
                # Prepare function calls
                function_calls_to_run = self._prepare_function_calls(
                    assistant_message=assistant_message,
                    messages=messages,
                    model_response=model_response,
                    functions=_functions,
                )
                function_call_results: List[Message] = []

                # Execute function calls
                for function_call_response in self.run_function_calls(
                    function_calls=function_calls_to_run,
                    function_call_results=function_call_results,
                    current_function_call_count=function_call_count,
                    function_call_limit=tool_call_limit,
                    result_store=result_store,
                ):
                    if isinstance(function_call_response, ModelResponse):
                        # The session state is updated by the function call
                        if function_call_response.updated_session_state is not None:
                            model_response.updated_session_state = function_call_response.updated_session_state

```

源码：[libs/agno/agno/tools/function.py:2256](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/tools/function.py#L2256)。

```bash
sed -n '2256,2276p' libs/agno/agno/tools/function.py
```

```output

    def _build_entrypoint_args(self) -> Dict[str, Any]:
        """Builds the arguments for the entrypoint."""
        from inspect import signature

        sig = signature(self.function.entrypoint)  # type: ignore
        entrypoint_args: Dict[str, Any] = {}

        # Check if the entrypoint has an agent argument (by name)
        if "agent" in sig.parameters:
            entrypoint_args["agent"] = self.function._agent
        # Check if the entrypoint has a team argument (by name)
        if "team" in sig.parameters:
            entrypoint_args["team"] = self.function._team
        # Check if the entrypoint has a run_context argument
        if "run_context" in sig.parameters:
            entrypoint_args["run_context"] = self.function._run_context
        # Check if the entrypoint has an fc argument
        if "fc" in sig.parameters:
            entrypoint_args["fc"] = self

```

源码：[libs/agno/agno/tools/function.py:2597](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/tools/function.py#L2597)。

```bash
sed -n '2597,2607p' libs/agno/agno/tools/function.py
```

```output

        entrypoint_args = self._build_entrypoint_args()
        # Sanitize before any hook runs, so a hook used as an authorization gate cannot
        # read an identity the call will not actually execute with.
        self._drop_injected_overrides(entrypoint_args)

        # Execute pre-hook if it exists
        self._handle_pre_hook()

        # Check cache if enabled and not a generator function
        cached_result = None
```

源码：[libs/agno/agno/tools/function.py:2641](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/tools/function.py#L2641)。

```bash
sed -n '2641,2679p' libs/agno/agno/tools/function.py
```

```output
                )
                result = execution_chain(self.function.name, self.function.entrypoint, self.arguments or {})
            elif from_cache:
                result = cached_result
            else:
                if self.arguments is None or self.arguments == {}:
                    result = self.function.entrypoint(**entrypoint_args)
                else:
                    result = self.function.entrypoint(**entrypoint_args, **self.arguments)

            # Handle generator case
            if isgenerator(result):
                self.result = result  # Store generator directly, can't cache
                # For generators, don't capture updated_session_state yet -
                # session_state is passed by reference, so mutations made during
                # generator iteration are already reflected in the original dict.
                # Returning None prevents stale state from being merged later.
                execution_result = FunctionExecutionResult(
                    status="success", result=self.result, updated_session_state=None
                )
            else:
                self.result = result
                # Only cache non-generator results, and never re-save a result
                # that was just served from cache
                if cacheable and not from_cache:
                    self._save_entrypoint_result(cache_key, cache_file, entrypoint_args, raw_results)

                updated_session_state = None
                run_context = entrypoint_args.get("run_context") or entrypoint_args.get("_agno_run_context")
                if run_context is not None:
                    session_state = getattr(run_context, "session_state", None)
                    updated_session_state = session_state if isinstance(session_state, dict) else None

                execution_result = FunctionExecutionResult(
                    status="success", result=self.result, updated_session_state=updated_session_state
                )

        except AgentRunException as e:
            log_debug(f"{e.__class__.__name__}: {e}")
```

源码：[libs/agno/agno/models/base.py:812](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/base.py#L812)。

```bash
sed -n '812,823p' libs/agno/agno/models/base.py
```

```output
                function_call_count += self._limit_charge_for(function_call_results, result_store)

                # Format and add results to messages
                self.format_function_call_results(
                    messages=messages,
                    function_call_results=function_call_results,
                    compress_tool_results=_compress_tool_results,
                    **model_response.extra or {},
                )

                if any(msg.images or msg.videos or msg.audio or msg.files for msg in function_call_results):
                    # Handle function call media
```

模型回复中的函数名与参数经 _prepare_function_calls 匹配成 FunctionCall。_build_entrypoint_args 按签名注入 RunContext；execute 在 pre-hook 前调用 _drop_injected_overrides，剔除模型参数中企图覆盖框架注入值的字段。执行成功后，函数结果和更新后的状态被归入执行结果，再转换为 tool messages 追加到对话。

购物清单因此发生两种同步变化：Python session_state 中 append 商品；messages 中加入工具输出。下一次模型调用可以据此回答或继续调用工具。对于生成器工具，创建生成器时函数体尚未执行，所以状态必须在消费之后捕获；第 17 节的测试专门表达这个不变量。

## 09 · 循环什么时候停止，工具上限意味着什么

阅读目的：把正常终止、暂停、调用限额和中途检查点区分开。

源码：[libs/agno/agno/models/base.py:2426](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/base.py#L2426)。

```bash
sed -n '2426,2460p' libs/agno/agno/models/base.py
```

```output
        for fc in function_calls:
            # The read-back tools exist only because offloading replaced a result
            # the model was told to go and read. Counting them against the limit
            # can refuse the very read the run needs to answer.
            counts_against_limit = result_store is None or fc.function.name not in NEVER_OFFLOADED_TOOLS
            if function_call_limit is not None and counts_against_limit:
                current_function_call_count += 1
                # We have reached the function call limit, so we add an error result to the function call results
                if current_function_call_count > function_call_limit:
                    log_debug(
                        f"Tool call limit ({function_call_limit}) reached. "
                        f"Skipping: {fc.function.name} (call #{current_function_call_count})"
                    )
                    function_call_results.append(self.create_tool_call_limit_error_result(fc))
                    continue

            paused_tool_executions = []

            # The function requires user confirmation (HITL)
            if fc.function.requires_confirmation:
                paused_tool_executions.append(
                    ToolExecution(
                        tool_call_id=fc.call_id,
                        tool_name=fc.function.name,
                        tool_args=fc.arguments,
                        requires_confirmation=True,
                        approval_type=fc.function.approval_type,
                        external_execution_silent=fc.function.external_execution_silent,
                    )
                )

            # The function requires user input (HITL)
            if fc.function.requires_user_input:
                user_input_schema = fc.function.user_input_schema
                if fc.arguments and user_input_schema:
```

源码：[libs/agno/agno/models/base.py:830](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/base.py#L830)。

```bash
sed -n '830,874p' libs/agno/agno/models/base.py
```

```output
                for function_call_result in function_call_results:
                    function_call_result.log(metrics=True, use_compressed_content=_compress_tool_results)

                # Check if we should stop after tool calls
                if any(m.stop_after_tool_call for m in function_call_results):
                    break

                # Per-turn checkpoint hook: post-gather barrier. Tool results have been
                # appended to messages; fire the hook before deciding whether to loop or break.
                # Failure to checkpoint must not kill a working run — log and continue.
                if after_tool_results is not None:
                    try:
                        after_tool_results(model_response)
                    except Exception as e:
                        log_error(f"after_tool_results callback failed: {e}")

                # If we have any tool calls that require confirmation, break the loop
                if any(tc.requires_confirmation for tc in model_response.tool_executions or []):
                    break

                # If we have any tool calls that require external execution, break the loop
                if any(tc.external_execution_required for tc in model_response.tool_executions or []):
                    break

                # If we have any tool calls that require user input, break the loop
                if any(tc.requires_user_input for tc in model_response.tool_executions or []):
                    break

                # Check if run_response has requirements (e.g., from member agent HITL)
                # This handles cases where a tool (like delegate_task_to_member) propagates
                # HITL requirements from a member agent to the team's run_response
                if run_response is not None and run_response.requirements:
                    if any(not req.is_resolved() for req in run_response.requirements):
                        break

                # Continue loop to get next response
                continue

            # No tool calls or finished processing them
            break

        log_debug(f"{self.get_provider()} Response End", center=True, symbol="-")

        # Save to cache if enabled
        if self.cache_response:
```

普通本地工具批次执行完后，工具结果已经追加到 messages；若仍需工作则继续 while True。没有 tool_calls 时终止。stop_after_tool_call 提前 break，确认/外部执行/补充输入需求也会结束当前模型循环，将控制权还给 Agent。

tool_call_limit 在本地工具执行前计数，超过限制会添加错误结果并跳过函数。它不是一个“模型最多请求 N 次”的总循环上限；启用结果卸载时，读回工具还有不计入配额的例外。检查点回调位于 stop_after_tool_call 检查之后，所以这条提前终止分支不会触发该批次回调。该位置细节比“每批工具一定保存”的笼统描述更准确。

## 10 · 完成输出与持久化所有权

阅读目的：从 model_response 回到可返回的 RunOutput，并追踪数据落盘的两次交接。

源码：[libs/agno/agno/agent/_response.py:945](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_response.py#L945)。

```bash
sed -n '945,965p' libs/agno/agno/agent/_response.py
```

```output
def update_run_response(
    agent: Agent,
    model_response: ModelResponse,
    run_response: RunOutput,
    run_messages: RunMessages,
    run_context: Optional[RunContext] = None,
):
    # Get output_schema from run_context
    output_schema = run_context.output_schema if run_context else None

    # Handle structured outputs
    if output_schema is not None and model_response.parsed is not None:
        # We get native structured outputs from the model
        if model_should_return_structured_output(agent, run_context=run_context):
            # Update the run_response content with the structured output
            run_response.content = model_response.parsed
            # Update the run_response content_type with the structured output class name
            run_response.content_type = "dict" if isinstance(output_schema, dict) else output_schema.__name__
    else:
        # Update the run_response content with the model response content
        run_response.content = model_response.content
```

源码：[libs/agno/agno/agent/_response.py:982](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_response.py#L982)。

```bash
sed -n '982,997p' libs/agno/agno/agent/_response.py
```

```output
    # Update the run_response tools with the model response tool_executions.
    # Dedupe by tool_call_id: with checkpoint="tool-batch" the per-batch callback
    # already wrote tools into run_response, so naive extend would duplicate
    # every execution. Replace existing entries (in place, preserving order)
    # and append only genuinely new ones.
    if model_response.tool_executions is not None:
        if run_response.tools is None:
            run_response.tools = list(model_response.tool_executions)
        else:
            existing_by_id = {t.tool_call_id: i for i, t in enumerate(run_response.tools) if t.tool_call_id}
            for tool in model_response.tool_executions:
                if tool.tool_call_id and tool.tool_call_id in existing_by_id:
                    run_response.tools[existing_by_id[tool.tool_call_id]] = tool
                else:
                    run_response.tools.append(tool)

```

源码：[libs/agno/agno/agent/_run.py:630](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L630)。

```bash
sed -n '630,670p' libs/agno/agno/agent/_run.py
```

```output
                        session=agent_session,
                        user_id=user_id,
                        debug_mode=debug_mode,
                        background_tasks=background_tasks,
                        **kwargs,
                    )
                    deque(post_hook_iterator, maxlen=0)

                # Check for cancellation
                raise_if_cancelled(run_response.run_id)  # type: ignore

                # 14. Wait for background tasks
                wait_for_open_threads(
                    memory_future=memory_future,  # type: ignore
                    learning_future=learning_future,  # type: ignore
                )
                merge_background_metrics(
                    run_response.metrics,
                    collect_background_metrics(memory_future, learning_future),
                )

                # 15. Create session summary
                if agent.session_summary_manager is not None and agent.enable_session_summaries:
                    # Upsert the RunOutput to Agent Session before creating the session summary
                    agent_session.upsert_run(run=run_response)
                    try:
                        agent.session_summary_manager.create_session_summary(
                            session=agent_session, run_metrics=run_response.metrics
                        )
                    except Exception as e:
                        log_warning(f"Error in session summary creation: {str(e)}")

                run_response.status = RunStatus.completed

                # 16. Cleanup and store the run response and session
                cleanup_and_store(
                    agent, run_response=run_response, session=agent_session, run_context=run_context, user_id=user_id
                )

                # Log Agent Telemetry
                log_agent_telemetry(agent, session_id=agent_session.session_id, run_id=run_response.run_id)
```

源码：[libs/agno/agno/agent/_run.py:6134](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L6134)。

```bash
sed -n '6134,6159p' libs/agno/agno/agent/_run.py
```

```output
        )

    # Add scrubbed RunOutput to Agent Session
    session.upsert_run(run=storage_copy)
    run_index = resolve_run_index(session, storage_copy)

    # Calculate session metrics
    update_session_metrics(agent, session=session, run_response=run_response)

    # Update session state before saving the session
    if run_context is not None and run_context.session_state is not None:
        if session.session_data is not None:
            session.session_data["session_state"] = run_context.session_state
        else:
            session.session_data = {"session_state": run_context.session_state}

    # Persist the session row and this single run (both O(1))
    _session.save_session(agent, session=session)
    _session.save_run(
        agent,
        run=storage_copy,
        session_id=session.session_id,
        user_id=session.user_id,
        run_index=run_index,
    )

```

源码：[libs/agno/agno/agent/_session.py:244](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_session.py#L244)。

```bash
sed -n '244,263p' libs/agno/agno/agent/_session.py
```

```output
    if _init.has_async_db(agent):
        raise ValueError("Cannot use sync save_session() with an async database. Use asave_session() instead.")
    # If the agent is a member of a team, do not save the session to the database
    if (
        agent.db is not None
        and agent.team_id is None
        and agent.workflow_id is None
        and session.session_data is not None
    ):
        if session.session_data is not None and isinstance(session.session_data.get("session_state"), dict):
            session.session_data["session_state"].pop("current_session_id", None)
            session.session_data["session_state"].pop("current_user_id", None)
            session.session_data["session_state"].pop("current_run_id", None)

        _storage.upsert_session(agent, session=session)
        log_debug(f"Created or updated AgentSession record: {session.session_id}")


async def asave_session(agent: Agent, session: Union[AgentSession, TeamSession, WorkflowSession]) -> None:
    """
```

源码：[libs/agno/agno/agent/_session.py:318](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_session.py#L318)。

```bash
sed -n '318,328p' libs/agno/agno/agent/_session.py
```

```output
    """
    from agno.agent import _init, _storage

    if _init.has_async_db(agent):
        raise ValueError("Cannot use sync save_run() with an async database. Use asave_run() instead.")

    if agent.db is not None and agent.team_id is None and agent.workflow_id is None:
        _storage.upsert_run(agent, run=run, session_id=session_id, user_id=user_id, run_index=run_index)
        log_debug(f"Saved run {run.run_id} to session {session_id}")


```

正常路径先处理输出/后置 hooks，等待后台任务并合并 metrics，可选生成 summary，设置 completed，然后 cleanup_and_store。后者停止计时、处理媒体/存储拷贝、调用 persist_run_in_session。

持久化路径先 session.upsert_run 维护内存列表，确定 run_index，更新 session_data.session_state，然后分别 save_session 与 save_run。成员 Agent 跳过独立保存。源码注释里的 O(1) 指当前 session/run 的行级写入数量，不是整个运行、JSON 编码或历史读取的总复杂度。

## 11 · SQLite 的 session 行不再携带全部 runs

阅读目的：验证上一节的持久化模型确实落到适配器，而不是停留在函数命名。

源码：[libs/agno/agno/db/sqlite/sqlite.py:1782](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/db/sqlite/sqlite.py#L1782)。

```bash
sed -n '1782,1800p' libs/agno/agno/db/sqlite/sqlite.py
```

```output
        try:
            table = self._get_table(table_type="sessions", create_table_if_not_found=True)
            if table is None:
                return None

            # The JSON columns take the dicts as-is; the engine's json_serializer encodes them
            session_dict = session.to_dict(include_runs=False)

            if isinstance(session, AgentSession):
                values = dict(
                    session_type=SessionType.AGENT.value,
                    agent_id=session_dict.get("agent_id"),
                    user_id=session_dict.get("user_id"),
                    agent_data=session_dict.get("agent_data"),
                    session_data=session_dict.get("session_data"),
                    summary=session_dict.get("summary"),
                    metadata=session_dict.get("metadata"),
                )
            elif isinstance(session, TeamSession):
```

源码：[libs/agno/agno/db/sqlite/sqlite.py:1820](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/db/sqlite/sqlite.py#L1820)。

```bash
sed -n '1820,1846p' libs/agno/agno/db/sqlite/sqlite.py
```

```output

            update_values = {k: v for k, v in values.items() if k != "session_type"}
            # The legacy `runs` column is intentionally left untouched here. Runs now
            # live in the runs table; the legacy column stays as a frozen backup and is
            # only reclaimed by the explicit cleanup_legacy_runs_column() helper. Nulling
            # it on write would lose history for sessions not yet migrated to the runs table.

            with self.Session() as sess, sess.begin():
                stmt = sqlite.insert(table).values(
                    session_id=session_dict.get("session_id"),
                    created_at=session_dict.get("created_at") or int(time.time()),
                    updated_at=session_dict.get("created_at") or int(time.time()),
                    **values,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["session_id"],
                    set_=dict(updated_at=int(time.time()), **update_values),
                    where=(table.c.user_id == session_dict.get("user_id")) | (table.c.user_id.is_(None)),
                )
                stmt = stmt.returning(*table.columns)  # type: ignore
                result = sess.execute(stmt)
                row = result.fetchone()
                if row is None:
                    return None
                session_raw = deserialize_session_json_fields(dict(row._mapping))

            if not deserialize:
```

源码：[libs/agno/agno/db/sqlite/sqlite.py:1045](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/db/sqlite/sqlite.py#L1045)。

```bash
sed -n '1045,1087p' libs/agno/agno/db/sqlite/sqlite.py
```

```output
        """
        try:
            runs_table = self._get_table(table_type="runs", create_table_if_not_found=True)
            if runs_table is None:
                return

            row = build_single_run_row(
                run=run,
                session_id=session_id,
                user_id=user_id,
                run_index=run_index,
            )

            with self.Session() as sess, sess.begin():
                # Backfill a monotonic run_index when the run arrives without one
                # (e.g. a background/continue save that couldn't resolve its position).
                # A NULL index has no position and breaks ORDER BY run_index.
                if row.get("run_index") is None:
                    # Computed INSIDE the insert statement: SQLite holds the
                    # database write lock for the whole statement, so two
                    # concurrent backfills cannot read the same MAX (the old
                    # two-statement read-then-insert could - a busy-waiting
                    # second writer landed a duplicate index after the first
                    # committed). ON CONFLICT still preserves existing indexes.
                    row["run_index"] = (
                        select(func.coalesce(func.max(runs_table.c.run_index) + 1, 0))
                        .where(runs_table.c.session_id == session_id)
                        .scalar_subquery()
                    )

                stmt = sqlite.insert(runs_table).values(**row)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["run_id"],
                    set_=dict(
                        status=stmt.excluded.status,
                        run_data=stmt.excluded.run_data,
                        user_id=stmt.excluded.user_id,
                        parent_run_id=stmt.excluded.parent_run_id,
                        updated_at=stmt.excluded.updated_at,
                        # Preserve a non-null run_index; only fill it in for a legacy row
                        # that was stored as NULL (COALESCE keeps the existing value if set).
                        run_index=func.coalesce(runs_table.c.run_index, stmt.excluded.run_index),
                    ),
```

SqliteDb.upsert_session 使用 session.to_dict(include_runs=False)，旧 runs 列作为迁移前备份保留；当前运行写入专门的 runs 表。session 行通过 SQLite INSERT ... ON CONFLICT 更新，并对 user_id 冲突加写入条件。运行协调层计算 run_index；SQLite 适配器在索引缺失时，还会在 INSERT 内用 MAX(run_index)+1 回填，更新时保留已有索引。因此不能照搬 save_run 注释中的“缺省会写 NULL”作为本 SQLite 实现结论。

设计边界：persist_run_in_session 先后调用两个保存函数，不能仅凭每个适配器方法内部有事务，就声称 session 行、run 行以及外部工具副作用共享一个原子事务。已完成的 append、外部 API 或工具写入也不会因为后续模型失败自动回滚。

## 12 · 异步、流式与后台任务是不同维度

阅读目的：回到示例中的 stream=True，并理解模型调用之外还存在什么并发。

源码：[libs/agno/agno/agent/_run.py:1453](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L1453)。

```bash
sed -n '1453,1496p' libs/agno/agno/agent/_run.py
```

```output

    run_response.model = agent.model.id if agent.model is not None else None
    run_response.model_provider = agent.model.provider if agent.model is not None else None

    # Start the run metrics timer, to calculate the run duration
    run_response.metrics = RunMetrics()
    run_response.metrics.start_timer()

    if opts.stream:
        response_iterator = _run_stream(
            agent,
            run_response=run_response,
            run_context=run_context,
            session_id=session_id,
            user_id=user_id,
            add_history_to_context=opts.add_history_to_context,
            add_dependencies_to_context=opts.add_dependencies_to_context,
            add_session_state_to_context=opts.add_session_state_to_context,
            response_format=response_format,
            stream_events=opts.stream_events,
            yield_run_output=opts.yield_run_output,
            debug_mode=debug_mode,
            background_tasks=background_tasks,
            pre_session=agent_session,
            **kwargs,
        )
        return response_iterator
    else:
        response = _run(
            agent,
            run_response=run_response,
            run_context=run_context,
            session_id=session_id,
            user_id=user_id,
            add_history_to_context=opts.add_history_to_context,
            add_dependencies_to_context=opts.add_dependencies_to_context,
            add_session_state_to_context=opts.add_session_state_to_context,
            response_format=response_format,
            debug_mode=debug_mode,
            background_tasks=background_tasks,
            pre_session=agent_session,
            **kwargs,
        )
        return response
```

源码：[libs/agno/agno/models/base.py:2800](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/models/base.py#L2800)。

```bash
sed -n '2800,2840p' libs/agno/agno/models/base.py
```

```output
        else:
            function_calls_to_run = [
                fc
                for fc in function_calls_to_run
                if not (
                    fc.function.requires_confirmation
                    or fc.function.external_execution
                    or fc.function.requires_user_input
                )
            ]

        # gather even for a single call: its cancel bookkeeping re-raises
        # caller cancellation even when a tool swallows the CancelledError
        # thrown into it, and its task wrapper isolates the tool's contextvars.
        # A bare await loses both; replicating them needs Task.cancelling(),
        # which requires Python 3.11.
        results = await asyncio.gather(
            *(self.arun_function_call(fc) for fc in function_calls_to_run), return_exceptions=True
        )

        # Separate async generators from other results for concurrent processing
        async_generator_results: List[Any] = []
        non_async_generator_results: List[Any] = []

        for result in results:
            if isinstance(result, BaseException):
                non_async_generator_results.append(result)
                continue

            function_call_success, function_call_timer, function_call, function_execution_result = result

            # Check if this result contains an async generator
            if isinstance(function_call.result, (AsyncGeneratorType, AsyncIterator)):
                async_generator_results.append(result)
            else:
                non_async_generator_results.append(result)

        # Process async generators with real-time event streaming using asyncio.Queue
        async_generator_outputs: Dict[int, Tuple[Any, str, Optional[BaseException]]] = {}
        event_queue: asyncio.Queue = asyncio.Queue()
        active_generators_count: int = len(async_generator_results)
```

源码：[libs/agno/agno/agent/_managers.py:196](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_managers.py#L196)。

```bash
sed -n '196,215p' libs/agno/agno/agent/_managers.py
```

```output
    """
    # Cancel any existing future from a previous retry attempt
    # Note: cancel() only works if the future hasn't started yet
    if existing_future is not None and not existing_future.done():
        existing_future.cancel()

    # Create new future if conditions are met
    has_content = run_messages.user_message is not None or (
        run_messages.extra_messages is not None and len(run_messages.extra_messages) > 0
    )
    if (
        has_content
        and agent.memory_manager is not None
        and agent.update_memory_on_run
        and not agent.enable_agentic_memory
    ):
        log_debug("Starting memory creation in background thread.")
        return agent.background_executor.submit(make_memories, agent, run_messages=run_messages, user_id=user_id)

    return None
```

源码：[libs/agno/agno/agent/_run.py:754](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L754)。

```bash
sed -n '754,770p' libs/agno/agno/agent/_run.py
```

```output

                return run_response
    finally:
        # Cancel background futures on error (wait_for_open_threads handles waiting on success)
        for future in (memory_future, learning_future):
            if future is not None and not future.done():
                future.cancel()
                try:
                    future.result(timeout=0)
                except Exception:
                    pass

        # Always disconnect connectable tools
        disconnect_connectable_tools(agent)
        # Always clean up the run tracking
        cleanup_run(run_response.run_id)  # type: ignore

```

run(stream=True) 返回同步迭代器；arun 非流式提供异步结果路径，流式提供异步迭代路径。Agent 的 _run/_run_stream/_arun/_arun_stream 是多条实现路径，不能仅凭同步阅读就宣称异步行为全部等价。Model 的异步工具批次使用 asyncio.gather，普通同步 run_function_calls 按列表顺序执行。

记忆提取在条件满足时交给 background_executor；学习提取有类似辅助路径。成功路径等待这些任务，finally 尝试取消尚未结束的 future、断开 connectable tools 并清理运行跟踪。future.cancel 无法强制中止已开始的线程。工具内部共享可变状态、同 session 并发写入等问题仍需应用自己的约束；这些代码不构成全局并发安全证明。

## 13 · 暂停是持久化状态，不是睡眠等待

阅读目的：了解需要用户确认的工具怎样把控制权交还调用者，以及恢复调用要携带什么。

源码：[libs/agno/agno/agent/_run.py:236](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L236)。

```bash
sed -n '236,257p' libs/agno/agno/agent/_run.py
```

```output
def handle_agent_run_paused(
    agent: Agent,
    run_response: RunOutput,
    session: AgentSession,
    user_id: Optional[str] = None,
    run_context: Optional[RunContext] = None,
) -> RunOutput:
    run_response.status = RunStatus.paused
    if not run_response.content:
        run_response.content = get_paused_content(run_response)

    # Stamp approval_id on tools before storing so the DB has the complete data.
    create_approval_from_pause(
        db=agent.db, run_response=run_response, agent_id=agent.id, agent_name=agent.name, user_id=user_id
    )

    cleanup_and_store(agent, run_response=run_response, session=session, run_context=run_context, user_id=user_id)

    log_debug(f"Agent Run Paused: {run_response.run_id}", center=True, symbol="*")

    # We return and await confirmation/completion for the tools that require it
    return run_response
```

源码：[libs/agno/agno/agent/_run.py:3438](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L3438)。

```bash
sed -n '3438,3460p' libs/agno/agno/agent/_run.py
```

```output
    if run_response is None and (run_id is not None and (session_id is None and agent.session_id is None)):
        raise ValueError("Session ID is required to continue a run from a run_id.")

    if has_async_db(agent):
        raise Exception("continue_run() is not supported with an async DB. Please use acontinue_run() instead.")

    # Refused here rather than at the persist below, which runs after the model call.
    if isinstance(agent.media_storage, AsyncMediaStorage):
        raise ValueError("Cannot use sync continue_run() with an AsyncMediaStorage. Use acontinue_run() instead.")

    background_tasks = kwargs.pop("background_tasks", None)
    if background_tasks is not None:
        from fastapi import BackgroundTasks

        background_tasks: BackgroundTasks = background_tasks  # type: ignore

    session_id = run_response.session_id if run_response is not None else session_id
    run_id: str = run_response.run_id if run_response is not None else run_id  # type: ignore

    session_id, user_id = initialize_session(
        agent,
        session_id=session_id,
        user_id=user_id,
```

源码：[libs/agno/agno/run/base.py:374](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/run/base.py#L374)。

```bash
sed -n '374,408p' libs/agno/agno/run/base.py
```

```output
class RunStatus(str, Enum):
    """State of the main run response"""

    pending = "PENDING"
    running = "RUNNING"
    completed = "COMPLETED"
    paused = "PAUSED"
    cancelled = "CANCELLED"
    error = "ERROR"
    # Marker for a run whose response was regenerated via /continue?regenerate=true
    # (replace_original defaults to true). The new regenerated run sits alongside it
    # as a sibling (via fork mechanics); the old run keeps this status so
    # history-builders can skip it when rebuilding context. Pass replace_original=false
    # to keep the original COMPLETED and visible instead.
    regenerated = "REGENERATED"


# Canonical set of run statuses excluded when rebuilding message history/context.
# Single source of truth: session.get_messages (agent + team) and the DB-level
# bounded-history read (agno.db.utils.HISTORY_SKIP_STATUSES) both derive from this,
# so the full-load and "most recent N" read paths can never return different
# history windows for the same session.
HISTORY_SKIP_STATUSES: list["RunStatus"] = [
    RunStatus.paused,
    RunStatus.cancelled,
    RunStatus.error,
    RunStatus.regenerated,
]
```

Model 层发现 HITL 需求后停止循环；Agent 将状态设为 paused，创建 approval 记录并保存，然后立即返回 RunOutput。恢复由 continue_run/continue_run_dispatch 接收原 RunOutput 或 run_id；仅给 run_id 时需要可定位的 session_id。它重新加载上下文并处理 requirements，而非让一个后台线程一直等待用户。

RunStatus 还包括 pending、running、completed、cancelled、error、regenerated。历史重建排除 paused/cancelled/error/regenerated，因此“数据库里保存了失败运行”与“失败运行会进入下一次 prompt”是两件事。恢复分支详见 [恢复流程图](recovery.html)，本节不是完整审批接口或权限审计。

## 14 · 错误、重试与工具批次检查点

阅读目的：判断失败后有哪些证据被保留，哪些行为可能重复发生。

源码：[libs/agno/agno/agent/_run.py:676](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L676)。

```bash
sed -n '676,696p' libs/agno/agno/agent/_run.py
```

```output
                run_response = _handle_run_cancellation(run_response, e, run_messages)
                try:
                    if agent_session is not None:
                        cleanup_and_store(
                            agent,
                            run_response=run_response,
                            session=agent_session,
                            run_context=run_context,
                            user_id=user_id,
                        )
                except Exception as store_err:
                    log_warning(f"Failed to persist cancelled run: {store_err}")
                return run_response
            except (InputCheckError, OutputCheckError) as e:
                # Handle exceptions during streaming
                run_response.status = RunStatus.error
                flush_in_flight_messages_on_error(run_response, locals().get("run_messages"))
                # If the content is None, set it to the error message
                if run_response.content is None:
                    run_response.content = str(e)

```

源码：[libs/agno/agno/agent/_run.py:712](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L712)。

```bash
sed -n '712,751p' libs/agno/agno/agent/_run.py
```

```output
                    if agent_session is not None:
                        cleanup_and_store(
                            agent,
                            run_response=run_response,
                            session=agent_session,
                            run_context=run_context,
                            user_id=user_id,
                        )
                except Exception as store_err:
                    log_warning(f"Failed to persist cancelled run: {store_err}")
                return run_response

            except Exception as e:
                if attempt < num_attempts - 1:
                    # Calculate delay with exponential backoff if enabled
                    if agent.exponential_backoff:
                        delay = agent.delay_between_retries * (2**attempt)
                    else:
                        delay = agent.delay_between_retries

                    log_warning(f"Attempt {attempt + 1}/{num_attempts} failed. Retrying in {delay}s...: {str(e)}")
                    time.sleep(delay)
                    continue

                run_response.status = RunStatus.error
                flush_in_flight_messages_on_error(run_response, locals().get("run_messages"))

                # If the content is None, set it to the error message
                if run_response.content is None:
                    run_response.content = str(e)

                log_error(f"Error in Agent run: {str(e)}")

                # Cleanup and store the run response and session
                if agent_session is not None:
                    cleanup_and_store(
                        agent,
                        run_response=run_response,
                        session=agent_session,
                        run_context=run_context,
```

源码：[libs/agno/agno/agent/_init.py:63](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_init.py#L63)。

```bash
sed -n '63,80p' libs/agno/agno/agent/_init.py
```

```output

def set_checkpoint(agent: Agent) -> None:
    """Resolve the agent's checkpoint setting.

    Constructor default is None so that OS-level inheritance can fill it. If still
    None at first run, fall back to "runs" (today's terminal-only behavior).

    "tools" is reserved for 3.0 and raises NotImplementedError if requested.
    """
    if agent.checkpoint is None:
        agent.checkpoint = "runs"
    elif agent.checkpoint == "tools":
        raise NotImplementedError(
            'checkpoint="tools" is reserved for the 3.0 runs-table split and not available yet. Use "tool-batch" or "runs".'
        )
    elif agent.checkpoint not in ("runs", "tool-batch"):
        raise ValueError(
            f'Invalid checkpoint level: {agent.checkpoint!r}. Expected one of: "runs", "tool-batch", "tools".'
```

源码：[libs/agno/agno/agent/_run.py:6451](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L6451)。

```bash
sed -n '6451,6472p' libs/agno/agno/agent/_run.py
```

```output
def checkpoint_run(
    agent: Agent,
    run_response: RunOutput,
    session: AgentSession,
    run_context: Optional[RunContext] = None,
) -> None:
    """Persist a mid-run checkpoint when ``agent.checkpoint == "tool-batch"``.

    Sets ``RunStatus.running`` and ``last_checkpoint_at_message_index``, then
    persists the run into the session. No-op when checkpointing is not "tool-batch".
    Idempotent — calling twice in a row writes the same state twice.

    Callers are responsible for ensuring ``run_response.messages`` and
    ``run_response.tools`` reflect the state to persist (see
    :func:`_sync_run_response_with_model_response`).
    """
    if agent.checkpoint != "tool-batch":
        return
    run_response.status = RunStatus.running
    run_response.last_checkpoint_at_message_index = len(run_response.messages or [])
    _mark_checkpoint_message(run_response)
    persist_run_in_session(agent, run_response, session, run_context)
```

源码：[libs/agno/agno/agent/_run.py:6490](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_run.py#L6490)。

```bash
sed -n '6490,6513p' libs/agno/agno/agent/_run.py
```

```output
def build_after_tool_results_callback(
    agent: Agent,
    run_response: RunOutput,
    session: AgentSession,
    run_messages: RunMessages,
    run_context: Optional[RunContext] = None,
) -> Optional[Any]:
    """Build the sync ``after_tool_results`` callback for ``checkpoint="tool-batch"``.

    Returns ``None`` when checkpointing is not enabled — the caller passes the
    result directly to the model's ``after_tool_results=`` kwarg, and the
    zero-cost path is taken when the callback is None.

    The returned callback receives the current ``ModelResponse``, syncs
    ``run_response`` with the in-flight messages/tools, and writes a checkpoint.
    """
    if agent.checkpoint != "tool-batch":
        return None

    def _callback(model_response: ModelResponse) -> None:
        _sync_run_response_with_model_response(run_response, run_messages, model_response)
        checkpoint_run(agent, run_response, session, run_context)

    return _callback
```

取消分支构造 cancelled 输出并尝试保存；InputCheckError/OutputCheckError 在运行内部转为 error。一般异常按 agent.retries 重试，最后 flush_in_flight_messages_on_error 尽可能挽救消息，保存 ERROR 输出。重试包住了较大段执行流程，因此已执行的有副作用工具有重复执行风险，不能把 retries 当作 exactly-once 保证。

checkpoint="tool-batch" 通过 after_tool_results 回调，把累积的 messages/tools 同步到 RunOutput，记录 last_checkpoint_at_message_index 并以 RUNNING 状态保存。默认 "runs" 不建立这个回调。回调异常在 Model 层记录后继续运行，所以运行完成不代表所有中途检查点都成功。continue_from/fork/regenerate 可选择恢复边界或创建分支，但不能撤销外部副作用；本次未模拟进程崩溃恢复。

## 15 · Knowledge 与记忆怎样接入主循环

阅读目的：说明检索为什么可以复用工具闭环，同时把显式会话状态和长期记忆分开。

源码：[libs/agno/agno/agent/_tools.py:215](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_tools.py#L215)。

```bash
sed -n '215,234p' libs/agno/agno/agent/_tools.py
```

```output
            )
        )

    # Add tools for accessing knowledge
    # Single unified path through get_relevant_docs_from_knowledge(),
    # which checks knowledge_retriever first, then falls back to knowledge.search().
    if (resolved_knowledge is not None or agent.knowledge_retriever is not None) and agent.search_knowledge:
        agent_tools.append(
            _default_tools.create_knowledge_search_tool(
                agent,
                run_response=run_response,
                run_context=run_context,
                knowledge_filters=run_context.knowledge_filters,
                enable_agentic_filters=agent.enable_agentic_knowledge_filters,
                async_mode=False,
            )
        )

    if resolved_knowledge is not None and agent.update_knowledge:
        agent_tools.append(_default_tools.create_add_to_knowledge_tool(agent, run_context=run_context))
```

源码：[libs/agno/agno/agent/_messages.py:1787](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/agent/_messages.py#L1787)。

```bash
sed -n '1787,1815p' libs/agno/agno/agent/_messages.py
```

```output
                )
            )
            return agent.knowledge_retriever(**knowledge_retriever_kwargs)
        except Exception as e:
            log_warning(f"Knowledge retriever failed: {str(e)}")
            raise e

    # Use knowledge protocol's retrieve method
    try:
        if resolved_knowledge is None:
            return None

        # Use protocol retrieve() method if available
        retrieve_fn = getattr(resolved_knowledge, "retrieve", None)
        if not callable(retrieve_fn):
            log_debug("Knowledge does not implement retrieve()")
            return None

        if num_documents is None:
            num_documents = getattr(resolved_knowledge, "max_results", 10)

        log_debug(f"Retrieving from knowledge base with filters: {filters}")
        retrieve_kwargs: Dict[str, Any] = {
            "query": query,
            "max_results": num_documents,
            "filters": filters,
        }
        retrieve_kwargs.update(get_user_id_kwarg(retrieve_fn, run_context.user_id if run_context else agent.user_id))
        relevant_docs: List[Document] = retrieve_fn(**retrieve_kwargs)
```

源码：[libs/agno/agno/knowledge/knowledge.py:5444](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/knowledge/knowledge.py#L5444)。

```bash
sed -n '5444,5467p' libs/agno/agno/knowledge/knowledge.py
```

```output
    def retrieve(
        self,
        query: str,
        max_results: Optional[int] = None,
        filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None,
        user_id: Optional[str] = None,
        **kwargs,
    ) -> List[Document]:
        """Retrieve documents for context injection.

        Used by the add_knowledge_to_context feature to pre-fetch
        relevant documents into the user message.

        Args:
            query: The query string.
            max_results: Maximum number of results.
            filters: Filters to apply.
            user_id: Owner scope forwarded to ``search``. ``None`` returns everything.
            **kwargs: Additional parameters.

        Returns:
            List of Document objects.
        """
        return self.search(query=query, max_results=max_results, filters=filters, user_id=user_id)
```

源码：[libs/agno/agno/knowledge/knowledge.py:935](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/knowledge/knowledge.py#L935)。

```bash
sed -n '935,978p' libs/agno/agno/knowledge/knowledge.py
```

```output
    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None,
        search_type: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Document]:
        """Returns relevant documents matching a query.

        Args:
            user_id: Owner scope forwarded to ``vector_db.search()``. ``None`` searches everything.
        """
        if self.page_store is not None:
            if filters:
                raise ValueError("Page knowledge does not support filters")
            return self._page_documents(
                self.search_pages(query, limit=max_results if max_results is not None else self.max_results)
            )
        from agno.vectordb import VectorDb
        from agno.vectordb.search import SearchType

        self.vector_db = cast(VectorDb, self.vector_db)

        if (
            hasattr(self.vector_db, "search_type")
            and isinstance(self.vector_db.search_type, SearchType)
            and search_type
        ):
            self.vector_db.search_type = SearchType(search_type)
        try:
            if self.vector_db is None:
                log_warning("No vector db provided")
                return []

            search_filters = self._inject_instance_scope_filter(filters)

            _max_results = max_results or self.max_results
            log_debug(f"Getting {_max_results} relevant documents for query: {query}")
            return self.vector_db.search(
                query=query,
                limit=_max_results,
                filters=search_filters,
                **strict_user_id_kwarg(self.vector_db.search, user_id),
```

get_tools 可加入知识检索工具；消息构建也有检索上下文的接入点。当前 get_relevant_docs_from_knowledge 在自定义 knowledge_retriever 之外，实际调用 resolved_knowledge.retrieve 协议。不要被附近“fallback to knowledge.search”的旧式注释误导为直接调用关系。Knowledge 的具体搜索实现支持 page_store 分支或 vector_db.search，并转交过滤条件与用户作用域。

购物清单属于显式 session_state；memory/learning 是可选的提取与检索子系统，触发条件、生命周期和用途均不同。本次只展开接入点，没有逐项审计文档摄入、切块、向量适配器、学习策略或其性能。

## 16 · Team、Workflow 与 AgentOS 的三种组合方式

阅读目的：从单 Agent 的闭环向外看，定位多 Agent 协作、代码流程编排与 HTTP 服务的不同职责。

源码：[libs/agno/agno/team/_tools.py:309](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/team/_tools.py#L309)。

```bash
sed -n '309,332p' libs/agno/agno/team/_tools.py
```

```output
        delegate_task_func = _get_delegate_task_function(
            team,
            run_response=run_response,
            run_context=run_context,
            session=session,
            team_run_context=team_run_context,
            input=user_message_content,
            # Members run as the user_id resolved on run_context, not the caller's argument
            user_id=run_context.user_id if run_context else user_id,
            stream=stream or False,
            stream_events=stream_events or False,
            async_mode=async_mode,
            images=images,  # type: ignore
            videos=videos,  # type: ignore
            audio=audio,  # type: ignore
            files=files,  # type: ignore
            add_history_to_context=add_history_to_context,
            add_dependencies_to_context=add_dependencies_to_context,
            add_session_state_to_context=add_session_state_to_context,
            debug_mode=debug_mode,
        )

        _tools.append(delegate_task_func)
        if team.get_member_information_tool:
```

源码：[libs/agno/agno/team/_default_tools.py:777](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/team/_default_tools.py#L777)。

```bash
sed -n '777,809p' libs/agno/agno/team/_default_tools.py
```

```output
            else:
                member_run_id = str(uuid4())
                if run_response.run_id is not None:
                    register_member_run(run_response.run_id, member_run_id)
                member_agent_run_response = member_agent.run(  # type: ignore
                    input=member_agent_task if not history else history,  # type: ignore
                    user_id=user_id,
                    # All members have the same session_id
                    session_id=session.session_id,
                    session_state=member_session_state_copy,  # Send a copy to the agent
                    images=images,
                    videos=videos,
                    audio=audio,
                    files=files,
                    stream=False,
                    debug_mode=debug_mode,
                    dependencies=run_context.dependencies,
                    add_dependencies_to_context=add_dependencies_to_context,
                    add_session_state_to_context=add_session_state_to_context,
                    metadata=run_context.metadata,
                    knowledge_filters=run_context.knowledge_filters
                    if not member_agent.knowledge_filters and member_agent.knowledge
                    else None,
                    run_id=member_run_id,
                )

                check_if_run_cancelled(member_agent_run_response)  # type: ignore
                # Also check if the parent team's run was cancelled while the member was executing
                if run_response.run_id is not None:
                    raise_if_cancelled(run_response.run_id)
        except RunCancelledException:
            use_team_logger()
            _process_delegate_task_to_member(
```

源码：[libs/agno/agno/workflow/step.py:1218](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/workflow/step.py#L1218)。

```bash
sed -n '1218,1246p' libs/agno/agno/workflow/step.py
```

```output

                        executor_run_id = str(uuid4())
                        if workflow_run_response is not None and workflow_run_response.run_id:
                            register_member_run(workflow_run_response.run_id, executor_run_id)
                        response = self.active_executor.run(  # type: ignore
                            input=final_message,  # type: ignore
                            images=images,
                            videos=videos,
                            audio=audios,
                            files=step_input.files,
                            session_id=session_id,
                            user_id=run_context.user_id if run_context is not None else user_id,
                            session_state=session_state_copy,  # Send a copy to the executor
                            run_context=run_context,
                            run_id=executor_run_id,
                            add_dependencies_to_context=add_dependencies_to_context,
                            add_session_state_to_context=add_session_state_to_context,
                            **kwargs,
                        )

                        # Update workflow session state
                        if run_context is None and session_state is not None:
                            merge_dictionaries(session_state, session_state_copy)

                        if store_executor_outputs and workflow_run_response is not None:
                            self._store_executor_response(workflow_run_response, response)  # type: ignore

                        # Check if agent/team response is paused (e.g., due to tool HITL)
                        # Propagate the pause to the workflow level
```

源码：[libs/agno/agno/os/routers/agents/router.py:126](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/os/routers/agents/router.py#L126)。

```bash
sed -n '126,152p' libs/agno/agno/os/routers/agents/router.py
```

```output
        if background_tasks is not None:
            kwargs["background_tasks"] = background_tasks

        if "stream_events" in kwargs:
            stream_events = kwargs.pop("stream_events")
        else:
            stream_events = True

        if auth_token and isinstance(agent, RemoteAgent):
            kwargs["auth_token"] = auth_token

        run_response = agent.arun(
            input=message,
            session_id=session_id,
            user_id=user_id,
            images=images,
            audio=audio,
            videos=videos,
            files=files,
            stream=True,
            stream_events=stream_events,
            **kwargs,
        )
        async for run_response_chunk in run_response:  # type: ignore[union-attr]
            yield format_sse_event(run_response_chunk)  # type: ignore
    except (InputCheckError, OutputCheckError) as e:
        error_response = RunErrorEvent(
```

源码：[libs/agno/agno/os/routers/agents/router.py:685](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/agno/os/routers/agents/router.py#L685)。

```bash
sed -n '685,703p' libs/agno/agno/os/routers/agents/router.py
```

```output
        # Scoped non-admin callers always get their JWT sub as user_id.
        # Admins and unscoped callers fall through to middleware/form values.
        scoped_user_id = get_scoped_user_id(request)
        state_user_id = getattr(request.state, "user_id", None)
        if scoped_user_id is not None:
            user_id = scoped_user_id
        elif state_user_id == INTERNAL_SCHEDULER_USER_ID:
            # The sentinel identifies the caller, not the owner: keep the form-field ``user_id``
            # the executor wrote, which is None for an unowned schedule.
            pass
        elif state_user_id is not None:
            if user_id and user_id != state_user_id:
                log_warning("User ID parameter passed in both request state and kwargs, using request state")
            user_id = state_user_id
        if hasattr(request.state, "session_id") and request.state.session_id is not None:
            if session_id and session_id != request.state.session_id:
                log_warning("Session ID parameter passed in both request state and kwargs, using request state")
            session_id = request.state.session_id
        if hasattr(request.state, "session_state") and request.state.session_state is not None:
```

| 组合层 | 实现交接 | 谁决定下一步 |
| --- | --- | --- |
| Team | 将 delegate_task_to_member 作为工具，内部调用 member_agent.run/arun | 在所读委派分支中由 leader 模型选择；其他 Team 模式另有路径 |
| Workflow | Step.execute 调用 active_executor.run，传递 StepInput/状态并生成 StepOutput | Workflow/Step 与 Condition、Loop、Parallel 等代码结构 |
| AgentOS | FastAPI router 调用 agent.arun，并将事件格式化为 SSE | HTTP 请求及服务运行策略 |

Team 传入成员状态副本、共享 session_id，并登记父子取消关系；Workflow 同样持有步骤执行边界，成员暂停会向上传播。AgentOS.get_app 组合应用和资源 lifespan；router 中 scoped caller 的身份优先来自认证上下文，不只是信任表单 user_id。这里只证明所列交接，不宣称已全面验证多租户隔离、调度队列、MCP 或部署环境。

## 17 · 用测试阅读边界，不把摘录校验当测试通过

阅读目的：把前述关键不变量对应到可定位的测试预期。

源码：[libs/agno/tests/unit/agent/test_history_copy_on_write.py:79](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/tests/unit/agent/test_history_copy_on_write.py#L79)。

```bash
sed -n '79,95p' libs/agno/tests/unit/agent/test_history_copy_on_write.py
```

```output


def test_history_tagging_does_not_mutate_cached_messages():
    agent = _make_agent()
    first = agent.run("first turn", session_id="s1")
    assert all(m.from_history is False for m in first.messages)

    second = agent.run("second turn", session_id="s1")

    # The originals from run 1 (aliased into the cached session) are untouched
    assert all(m.from_history is False for m in first.messages)

    # Run 2's context contains tagged copies of run 1's messages, not the originals
    history = [m for m in second.messages if m.from_history]
    assert len(history) >= 2
    first_ids = {id(m) for m in first.messages}
    assert all(id(m) not in first_ids for m in history)
```

源码：[libs/agno/tests/unit/models/test_generator_session_state.py:39](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/tests/unit/models/test_generator_session_state.py#L39)。

```bash
sed -n '39,61p' libs/agno/tests/unit/models/test_generator_session_state.py
```

```output
    func._run_context = run_context

    func.process_entrypoint()
    fc = FunctionCall(function=func, arguments={})

    # Execute - this returns a FunctionExecutionResult
    result = fc.execute()

    # For generators, updated_session_state should be None
    # (since the generator hasn't been consumed yet)
    assert result.status == "success"
    assert result.updated_session_state is None

    # The result should be a generator
    assert hasattr(result.result, "__iter__")

    # Consume the generator
    output = list(result.result)
    assert output == ["first", "second"]

    # After consumption, session_state should have the modifications
    assert session_state["modified_during_yield"] is True
    assert session_state["second_modification"] == "done"
```

源码：[libs/agno/tests/unit/agent/test_checkpoint_steps.py:638](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/tests/unit/agent/test_checkpoint_steps.py#L638)。

```bash
sed -n '638,648p' libs/agno/tests/unit/agent/test_checkpoint_steps.py
```

```output
    def test_k_plus_one_writes_with_checkpoint_steps_sync(self):
        K = 3
        db = InMemoryDb()
        agent = _make_agent(checkpoint="tool-batch", db=db)

        with patch.object(agent.model, "response", side_effect=_make_fake_response(K)):
            write_count = _wrap_db_for_counting(db)
            agent.run(input="hi")

        assert write_count[0] == K + 1

```

源码：[libs/agno/tests/unit/db/test_sqlite_session_storage_format.py:60](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/tests/unit/db/test_sqlite_session_storage_format.py#L60)。

```bash
sed -n '60,68p' libs/agno/tests/unit/db/test_sqlite_session_storage_format.py
```

```output


def _assert_stored_as_plain_json(db_file: str, session_id: str) -> None:
    for field, raw in _raw_row(db_file, session_id).items():
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), (
            f"{field} is double-encoded: one parse of the stored bytes yielded "
            f"{type(parsed).__name__} instead of dict ({raw!r})"
        )
```

源码：[libs/agno/tests/unit/agent/test_error_message_flush.py:30](https://github.com/agno-agi/agno/blob/4253dedc95250afddfcea05dfc28f26a4833a117/libs/agno/tests/unit/agent/test_error_message_flush.py#L30)。

```bash
sed -n '30,44p' libs/agno/tests/unit/agent/test_error_message_flush.py
```

```output
        rm = RunMessages()
        rm.messages = [
            Message(role="system", content="sys"),
            Message(role="user", content="hi"),
        ]
        flush_in_flight_messages_on_error(rr, rm)
        assert rr.messages is not None
        assert len(rr.messages) == 2
        assert rr.messages[1].content == "hi"

    def test_flushes_when_run_response_messages_is_empty_list(self):
        rr = RunOutput(run_id="r1", messages=[])
        rm = RunMessages()
        rm.messages = [Message(role="user", content="hi")]
        flush_in_flight_messages_on_error(rr, rm)
```

这些断言分别要求：历史标记不修改原消息；生成器消费前不提前捕获状态；K 个工具批次产生 K 次中途保存加一次终态保存；SQLite JSON 一次解码即可得到对象；模型循环提前失败时尽量保留进行中的消息。测试名称与 mock 场景表达预期，不等于生产场景的证明。

本次未运行 Agno 应用测试：当前 Python 环境缺少 pytest、pydantic、sqlalchemy、openai，购物清单例子还需要模型凭据。Showboat verify 只重放只读摘录并核对输出。未验证供应商 API、真实工具效果、数据库并发、进程崩溃、完整异步取消与部署链路。

## 18 · 收束调用链与后续阅读范围

阅读目的：用一条可复述的路径把各章连回最初的购物清单操作。

Agent.run → run_dispatch → _run → get_tools / determine_tools_for_model → get_run_messages → call_model_with_fallback → Model.response → OpenAIResponses.invoke → FunctionCall.execute → tool messages → 下一轮模型回复 → update_run_response → cleanup_and_store → persist_run_in_session → save_session + save_run。

核心设计是把运行上下文、模型工具闭环和持久化边界分开，并让 Team、Workflow、AgentOS 复用执行接口。主要阅读成本来自同步/异步、流式/非流式、继续运行等多条路径，以及 Model 基类集中承担的工具执行职责；这是一项基于本次阅读的设计判断，不是性能基准。

未覆盖：全部模型/DB/tool adapter，agnoctl 与 agno_infra 内部实现，完整知识摄入与学习系统，Workflow 每种控制结构，AgentOS durable queue、scheduler、registry、全部鉴权与外部 interfaces。图表刻意保留核心依赖，省略这些扩展的内部细节。

图表入口：[架构概览](architecture.html) · [核心执行流程](flow.html) · [暂停与检查点恢复](recovery.html)。
