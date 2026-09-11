# WeKnora 阅读规划

源码：`../WeKnora/` · commit `5db13a131e10e8ee2105211f665412ebc13bd98e` · upstream `git@github.com:Tencent/WeKnora.git`。

开始写作前固定如下顺序，以普通 Markdown 文档上传后提问为贯穿操作。源码工作区初始干净，保留独立 checkout。

| 章节 | 解决的问题 | 文件与符号入口 |
| --- | --- | --- |
| 01 版本与贯穿示例 | 先固定分析对象：本次阅读的是 WeKnora 的本地源码快照 | `go.mod`<br>`frontend/package.json`<br>`docreader/pyproject.toml` |
| 02 从服务启动到请求路由 | 先找到运行中的对象从哪里来，再进入业务方法 | `cmd/server/main.go`<br>`internal/container/container.go`<br>`internal/router/router.go` |
| 03 上传接口与权限边界 | 跟随浏览器提交的一个文件，确定请求格式、权限检查和返回值 | `frontend/src/api/knowledge-base/index.ts`<br>`internal/router/routes_knowledge.go`<br>`internal/handler/knowledge.go` |
| 04 知识条目与检索分块 | 明确被保存的对象，避免把文件、知识库、chunk 和 embedding 当作同一个实体 | `internal/types/knowledgebase.go`<br>`internal/types/knowledge.go`<br>`internal/types/chunk.go` |
| 05 保存文件后提交后台任务 | 这里确定上传成功的精确定义，并识别跨存储操作的失败窗口 | `internal/application/service/knowledge_create.go` |
| 06 Worker 与 Lite 模式 | 同一个 TaskEnqueuer 接口有两种执行模型，需要先说明主线使用哪一种 | `internal/container/container.go`<br>`internal/router/task.go`<br>`internal/router/sync_task.go` |
| 07 解析任务如何恢复文档上下文 | worker 不再拥有原始 HTTP 请求，需要从 payload 重建租户上下文并重新读取持久状态 | `internal/application/service/knowledge_process.go` |
| 08 可替换解析器与超时 | 解析的共同输出是 ReadResult/Markdown，后续分块在 Go 侧完成 | `internal/infrastructure/docparser/engine_registry.go`<br>`internal/infrastructure/docparser/engines.go`<br>`internal/application/service/knowledge_process.go` |
| 09 分块策略与位置不变量 | 分块不是任意字符串切片：检索和界面引用还需要定位回规范化后的文档 | `internal/infrastructure/chunker/splitter.go`<br>`internal/infrastructure/chunker/strategy.go`<br>`internal/application/service/knowledge_process.go` |
| 10 Chunk 持久化与索引写入 | 现在从文本转到可检索的数据；区分 Chunk 数据库与检索引擎 | `internal/application/service/knowledge_process.go`<br>`internal/application/service/retriever/keywords_vector_hybrid_indexer.go` |
| 11 后处理完成与取消竞争 | 解释 processing、finalizing、completed 为什么是三个阶段，以及状态保护具体在哪实现 | `internal/application/service/knowledge_process.go`<br>`internal/application/service/knowledge_post_process.go`<br>`internal/application/repository/knowledge.go` |
| 12 一轮问答的请求与消息生命周期 | 文档处理完后，从 session 的 KnowledgeQA 进入第二条主线 | `internal/router/routes_chat.go`<br>`internal/handler/session/qa.go`<br>`internal/logger/logger.go` |
| 13 动态组装的问答流水线 | 查看 query 如何在阶段之间变成搜索结果、上下文和最终回答 | `internal/types/chat_manage.go`<br>`internal/application/service/session_knowledge_qa.go`<br>`internal/application/service/chat_pipeline/query_understand.go`<br>`internal/application/service/chat_pipeline/chat_pipeline.go` |
| 14 并行搜索与检索范围 | 向量检索不是一次无范围的全库相似度搜索：先解析调用者可访问的目标，再分组查询 | `internal/application/service/chat_pipeline/search_parallel.go`<br>`internal/application/service/chat_pipeline/search.go`<br>`internal/application/service/knowledgebase_search.go`<br>`internal/application/service/knowledgebase_search_fanout.go` |
| 15 从检索适配器到 RRF 融合 | 下钻一个具体 PostgreSQL/ParadeDB 适配器，证明向量和关键词检索实际如何发生，再回到后端无关的融合步骤 | `internal/application/service/retriever/composite.go`<br>`internal/application/repository/retriever/postgres/repository.go`<br>`internal/application/service/knowledgebase_search_fusion.go` |
| 16 重排与上下文还原 | 从候选列表到 prompt，关注相关性、父块/邻块扩展，以及编辑后的旧索引 | `internal/application/service/knowledgebase_search_results.go`<br>`internal/application/service/chat_pipeline/rerank.go`<br>`internal/application/service/chat_pipeline/merge.go`<br>`internal/application/service/chat_pipeline/into_chat_message.go` |
| 17 模型流如何变成回答事件 | 追踪 ChatStream 到事件总线，说明为什么 KnowledgeQA 返回不代表回答已结束 | `internal/application/service/chat_pipeline/chat_completion_stream.go`<br>`internal/models/chat/remote_api.go` |
| 18 持久化与断线续读的边界 | 将用户看到的流、数据库保存的消息和显式停止操作闭合起来 | `internal/handler/session/qa.go`<br>`internal/handler/session/agent_stream_handler.go`<br>`internal/stream/factory.go`<br>`internal/handler/session/stream.go`<br>`internal/handler/session/helpers.go`<br>`internal/application/service/session_knowledge_qa.go` |
| 19 Agent 是另一种编排方式 | 主线结束后用最小证据定位 ReAct 扩展：复用模型、检索与流事件，但控制循环不同 | `internal/handler/session/qa.go`<br>`internal/application/service/session_agent_qa.go`<br>`internal/agent/engine.go` |
| 20 测试证据与覆盖边界 | 以下展示测试表达的预期，不宣称测试已运行 | `internal/application/service/knowledge_create_test.go`<br>`internal/infrastructure/chunker/strategy_test.go`<br>`internal/application/service/knowledgebase_search_results_edit_test.go` |

完成标准：上传→任务分派→读取→分块→索引→后处理，以及问答→搜索→融合→上下文→模型→事件→消息/续读的每个交接有可重放摘录。代码事实、分析推断和测试预期分别说明；非主线能力只定位边界。

图表：architecture（组件依赖）、flow（入库与失败分支）、qa-flow（RAG 与空检索回退），均为 doc-wide 1280×720 静态 HTML。未匹配必须采用的专门语义模式，分别直接采用 Architecture / Flowchart。
