# WeKnora：从文档上传到流式问答的线性源码讲解

*2026-09-11T03:45:18Z by Showboat 0.6.1*
<!-- showboat-id: 9703d7fa-0c17-4ae3-89cd-52eeb0705747 -->

分析日期：2026-09-11。Upstream：`git@github.com:Tencent/WeKnora.git`；commit：`5db13a131e10e8ee2105211f665412ebc13bd98e`；应用版本标识：0.8.0（README / frontend package）。本地源码初始无改动；分析过程不修改上游 checkout。

[阅读规划](reading-plan.md) · [架构图](architecture.html) · [入库流程](flow.html) · [问答流程](qa-flow.html)。摘录均由 Showboat 执行只读命令捕获；分析是针对固定快照的实现阅读。

```bash
git rev-parse HEAD
```

```output
5db13a131e10e8ee2105211f665412ebc13bd98e
```

```bash
git status --porcelain
```

```output
```

## 01 版本与贯穿示例

先固定分析对象：本次阅读的是 WeKnora 的本地源码快照。贯穿操作是：在已有普通文档知识库中上传一份带标题的 Markdown 文档，等待处理，再在已有 session 中基于该知识库提问。假定已配置存储、embedding 和聊天模型；关键词索引、rerank、父子分块等按配置讨论，关闭 Web Search、Wiki 和多模态以便看清主线。示例是阅读场景，没有实际调用应用或模型。

先看后端构建入口。 [`go.mod:1`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/go.mod#L1)

```bash
sed -n '1,5p' go.mod
```

```output
module github.com/Tencent/WeKnora

go 1.26.0

require (
```

Go 模块是 `github.com/Tencent/WeKnora`，声明 Go 1.26.0。不能把 Swagger 中的 API version 1.0 当作仓库发布版本。

用前端构建清单核对应用版本。 [`frontend/package.json:1`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/frontend/package.json#L1)

```bash
sed -n '1,20p' frontend/package.json
```

```output
{
  "name": "knowledage-base",
  "version": "0.8.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "build-with-types": "run-p type-check \"build-only {@}\" --",
    "preview": "vite preview",
    "build-only": "vite build",
    "type-check": "vue-tsc --build",
    "test": "tsx --test",
    "check-i18n": "tsx --test src/i18n/localeKeyAudit.test.ts",
    "scan-i18n-gaps": "tsx src/i18n/localeGapScan.ts",
    "regenerate-i18n-locales": "tsx src/i18n/regeneratePrunedLocales.ts"
  },
  "dependencies": {
    "@microsoft/fetch-event-source": "^2.0.1",
    "@types/dompurify": "^3.2.0",
```

前端声明 0.8.0，README 的徽章也标注 0.8.0；它是本次源码的版本标识，并不意味着该 commit 恰好是 release tag。

辨认独立解析服务的构建边界。 [`docreader/pyproject.toml:1`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/docreader/pyproject.toml#L1)

```bash
sed -n '1,13p' docreader/pyproject.toml
```

```output
[project]
name = "docreader"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.10.18"
dependencies = [
    "beautifulsoup4>=4.14.2",
    "ebooklib>=0.18",
    "grpcio>=1.78.0",
    "grpcio-health-checking>=1.78.0",
    "grpcio-tools>=1.78.0",
    "lxml>=6.1.0",
```

DocReader 是 Python 子项目，自己的版本为 0.1.0、Python 要求为 >=3.10.18。主应用、前端和解析服务不共享一个包版本。

本次以 commit 为准，不评价上游当前最新状态。阅读重点是实现如何交接，以及哪些成功信号不能互相替代。

## 02 从服务启动到请求路由

先找到运行中的对象从哪里来，再进入业务方法。架构概览见 [architecture.html](architecture.html)。

从 main 找到依赖注入与 HTTP server。 [`cmd/server/main.go:59`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/cmd/server/main.go#L59)

```bash
sed -n '59,82p' cmd/server/main.go
```

```output

	// One-shot bootstrap hooks (e.g. promote env-named user to system
	// admin). Best-effort: never aborts startup — see bootstrap.go.
	runStartupBootstrap(c)

	// Run application
	err := c.Invoke(func(
		cfg *config.Config,
		router *gin.Engine,
		resourceCleaner interfaces.ResourceCleaner,
		systemSettingSvc interfaces.SystemSettingService,
	) error {
		// Create HTTP server
		server := &http.Server{
			Handler: router,
		}

		addr := fmt.Sprintf("%s:%d", cfg.Server.Host, cfg.Server.Port)
		listener, err := listenWithRetry(addr, 10, 300*time.Millisecond)
		if err != nil {
			return fmt.Errorf("failed to start server: %v", err)
		}

		ctx, done := context.WithCancel(context.Background())
```

`BuildContainer` 注册实现，`Invoke` 取出配置、Gin 路由与资源清理器，随后创建 listener。服务并非只实例化一个 RAG 对象。

看基础设施如何注入。 [`internal/container/container.go:113`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/container/container.go#L113)

```bash
sed -n '113,139p' internal/container/container.go
```

```output
	must(container.Provide(NewResourceCleaner, dig.As(new(interfaces.ResourceCleaner))))

	// Core infrastructure configuration
	logger.Debugf(ctx, "[Container] Registering core infrastructure...")
	must(container.Provide(config.LoadConfig))
	must(container.Provide(initLangfuse))
	must(container.Provide(initDatabase))
	must(container.Provide(initFileService))
	must(container.Provide(initRedisClient))
	must(container.Provide(initAntsPool))

	must(container.Invoke(registerLangfuseCleanup))

	// Register goroutine pool cleanup handler
	must(container.Invoke(registerPoolCleanup))

	// Initialize retrieval engine registry for search capabilities
	logger.Debugf(ctx, "[Container] Registering retrieval engine registry...")
	must(container.Provide(initRetrieveEngineRegistry))

	// External service clients
	logger.Debugf(ctx, "[Container] Registering external service clients...")
	must(container.Provide(initDocReaderClient))
	must(container.Provide(docparser.NewImageResolver))
	must(container.Provide(initOllamaService))
	must(container.Provide(initNeo4jClient))
	must(container.Provide(stream.NewStreamManager))
```

数据库、文件存储、Redis、检索引擎注册表和 DocReader 都由容器装配；选择具体适配器的职责在服务之外。

确认主业务路由经过的认证位置。 [`internal/router/router.go:180`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/router/router.go#L180)

```bash
sed -n '180,193p' internal/router/router.go
```

```output
	// query ticket — see RegisterSandboxTerminalRoutes; browsers cannot set
	// auth headers on the WS handshake, so this must precede the global Auth
	// middleware). The ticket is minted by an authenticated POST.
	RegisterSandboxTerminalRoutes(r, params.SessionHandler)

	// 认证中间件
	r.Use(middleware.Auth(params.TenantService, params.UserService, params.TenantMemberService, params.TenantAPIKeyService, params.Config))

	// 文件服务：统一代理本地/MinIO/COS/TOS存储后端（需要认证）
	serveFilesWithResources(r, params.FileService, params.StorageBackendResolver, params.ResourceCatalog)

	// Presigned file access: no auth required, signature-verified.
	servePresignedFiles(r, params.TenantService, params.StorageBackendResolver)

```

`middleware.Auth` 挂到路由器；源码在这之前也注册了公开或自行认证的入口，因此不能推广为“所有端点共用同一个认证链”。

最后看进程退出时如何释放资源。 [`cmd/server/main.go:95`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/cmd/server/main.go#L95)

```bash
sed -n '95,128p' cmd/server/main.go
```

```output
		go func() {
			sig := <-signals
			logger.Infof(context.Background(), "Received signal: %v, starting server shutdown...", sig)

			// Close listener first to release port immediately,
			// so the next process can bind during our graceful drain.
			listener.Close()

			shutdownTimeout := cfg.Server.ShutdownTimeout
			if shutdownTimeout == 0 {
				shutdownTimeout = 30 * time.Second
			}
			shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), shutdownTimeout)
			defer shutdownCancel()

			// Second signal → force close all connections immediately
			go func() {
				sig := <-signals
				logger.Warnf(context.Background(), "Received second signal: %v, forcing shutdown...", sig)
				server.Close()
			}()

			if err := server.Shutdown(shutdownCtx); err != nil {
				logger.Errorf(context.Background(), "Server forced to shutdown: %v", err)
				server.Close()
			}

			logger.Info(context.Background(), "Cleaning up resources...")
			errs := resourceCleaner.Cleanup(shutdownCtx)
			if len(errs) > 0 {
				logger.Errorf(context.Background(), "Errors occurred during resource cleanup: %v", errs)
			}
			logger.Info(context.Background(), "Server has exited")
			done()
```

第一次信号关闭 listener、限时 Shutdown 并调用 Cleanup；第二次信号强制关闭连接。后台任务还有自己的队列/取消生命周期，不能仅凭 HTTP Shutdown 推断全部已持久化。

入口、业务服务和基础设施被接口隔开，但大量功能仍在同一 Go 应用中装配；源码目录的分层不等于分别部署的微服务。

## 03 上传接口与权限边界

跟随浏览器提交的一个文件，确定请求格式、权限检查和返回值。

从前端 uploadKnowledgeFile 发起上传。 [`frontend/src/api/knowledge-base/index.ts:207`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/frontend/src/api/knowledge-base/index.ts#L207)

```bash
sed -n '207,231p' frontend/src/api/knowledge-base/index.ts
```

```output
export function uploadKnowledgeFile(
  kbId: string,
  data: {
    file: File
    tag_ids?: string[]
    fileName?: string
    process_config?: KnowledgeProcessOverrides | string
    [key: string]: any
  } = { file: new File([], '') },
  onProgress?: (progressEvent: any) => void,
) {
  const formData = new FormData();
  Object.keys(data).forEach(key => {
    const value = data[key];
    if (value === undefined) return;
    if (key === 'tag_ids' && Array.isArray(value)) {
      formData.append(key, value.join(','));
    } else if (key === 'process_config' && value && typeof value !== 'string') {
      formData.append(key, JSON.stringify(value));
    } else {
      formData.append(key, value);
    }
  });
  return postUpload(`/api/v1/knowledge-bases/${kbId}/knowledge/file`, formData, onProgress);
}
```

文件和标签、process_config 被编码为 multipart FormData，然后 POST 到知识库下的 `/knowledge/file`。

追到精确路由。 [`internal/router/routes_knowledge.go:67`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/router/routes_knowledge.go#L67)

```bash
sed -n '67,80p' internal/router/routes_knowledge.go
```

```output
func RegisterKnowledgeRoutes(r *gin.RouterGroup, handler *handler.KnowledgeHandler, g *rbacGuards) {
	// 知识库下的知识路由组（URL :id is the KB id）。Scoped API key 需要
	// ingest 能力才能写内容，且仍受 KB 范围限制；清空 KB 只允许 full-access key。
	kb := g.apiKeyGroup(r.Group("/knowledge-bases/:id/knowledge"), apiKeyIngest(apiKeyFullAccess()))
	kbRead := kb.With(apiKeyRetrieve(apiKeyFullAccess()))
	{
		kb.POST("/file", g.OwnedKBOrAdmin(), g.KBAccessWrite("id"), handler.CreateKnowledgeFromFile)
		kb.POST("/url", g.OwnedKBOrAdmin(), g.KBAccessWrite("id"), handler.CreateKnowledgeFromURL)
		kb.POST("/manual", g.OwnedKBOrAdmin(), g.KBAccessWrite("id"), handler.CreateManualKnowledge)
		kbRead.GET("", g.Viewer(), g.KBAccessRead("id"), handler.ListKnowledge)
		kbRead.GET("/folders", g.Viewer(), g.KBAccessRead("id"), handler.ListKnowledgeFolders)
		kb.PUT("/folders", g.OwnedKBOrAdmin(), g.KBAccessWrite("id"), handler.RenameKnowledgeFolder)
		// Clearing all contents under a KB is a destructive op; gate
		// behind Admin instead of Contributor.
```

路由声明 Contributor、API key ingest 能力与知识库写权限等守卫。角色守卫是否强制还受 RBAC 配置影响；这里只证明主线的检查位置。

进入 CreateKnowledgeFromFile 的 handler。 [`internal/handler/knowledge.go:254`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/knowledge.go#L254)

```bash
sed -n '254,282p' internal/handler/knowledge.go
```

```output
func (h *KnowledgeHandler) CreateKnowledgeFromFile(c *gin.Context) {
	ctx := c.Request.Context()
	logger.Info(ctx, "Start creating knowledge from file")

	// Validate access to the knowledge base (only owner or admin/editor can create)
	_, kbID, effectiveTenantID, permission, err := h.validateKnowledgeBaseAccess(c)
	if err != nil {
		c.Error(err)
		return
	}
	ctx = types.WithExecutionTenant(c.Request.Context(), effectiveTenantID)

	// Check write permission
	if permission != types.OrgRoleAdmin && permission != types.OrgRoleEditor {
		c.Error(errors.NewForbiddenError("No permission to create knowledge"))
		return
	}

	// Validate file size — read MAX_FILE_SIZE_MB env (50MB default).
	// Deliberately not a runtime system_setting; see filesize.go for the
	// rationale (nginx / docreader / browser bundle all cache this at
	// container startup, so a UI knob would silently mismatch).
	maxSizeMB := utils.GetMaxFileSizeMB()
	maxSize := maxSizeMB * 1024 * 1024
	// Capped before the multipart parse, not after: FormFile buffers the whole
	// body first, so the size check below only ever sees an upload we already
	// accepted. nginx location /api/ still enforces MAX_FILE_SIZE; this is the
	// same cap for requests that reach the app without that proxy.
	limitUploadBody(c, maxSize)
```

handler 验证知识库访问和写权限，并把执行租户切换到实际资源所属租户。文件体积限制在 multipart 解析前设置。身份与执行租户不能简单混为一个 ID。

跟踪 service 返回到 HTTP 响应。 [`internal/handler/knowledge.go:359`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/knowledge.go#L359)

```bash
sed -n '359,388p' internal/handler/knowledge.go
```

```output
	channel := c.PostForm("channel")

	// Create knowledge entry from the file
	knowledge, err := h.kgService.CreateKnowledgeFromFile(ctx, kbID, file, metadata, enableMultimodel, customFileName, tagIDs, channel, processOverrides)
	// Check for duplicate knowledge error
	if err != nil {
		if h.handleDuplicateKnowledgeError(c, err, knowledge, "file") {
			return
		}
		if appErr, ok := errors.IsAppError(err); ok {
			c.Error(appErr)
			return
		}
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(errors.NewInternalServerError(err.Error()))
		return
	}

	logger.Infof(
		ctx,
		"Knowledge created successfully, ID: %s, title: %s",
		secutils.SanitizeForLog(knowledge.ID),
		secutils.SanitizeForLog(knowledge.Title),
	)
	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"data":    knowledge,
	})
}

```

handler 对重复文件和业务错误单独处理；无 error 时返回 HTTP 200 与 Knowledge 对象。接下来要看 service 是否保证此时已完成解析。

上传响应携带业务状态。调用方应检查 `parse_status`，并在后续读取知识条目状态，而不是看到 200 就开始假定检索可用。

## 04 知识条目与检索分块

明确被保存的对象，避免把文件、知识库、chunk 和 embedding 当作同一个实体。

先看 KnowledgeBase 持有的配置。 [`internal/types/knowledgebase.go:59`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/types/knowledgebase.go#L59)

```bash
sed -n '59,87p' internal/types/knowledgebase.go
```

```output
type KnowledgeBase struct {
	// Unique identifier of the knowledge base
	ID string `yaml:"id"                      json:"id"                      gorm:"type:varchar(36);primaryKey"`
	// Name of the knowledge base
	Name string `yaml:"name"                    json:"name"`
	// Type of the knowledge base (document, faq, etc.)
	Type string `yaml:"type"                    json:"type"                    gorm:"type:varchar(32);default:'document'"`
	// Whether this knowledge base is temporary (ephemeral) and should be hidden from UI
	IsTemporary bool `yaml:"is_temporary"            json:"is_temporary"            gorm:"default:false"`
	// Description of the knowledge base
	Description string `yaml:"description"             json:"description"`
	// Workspace ID
	TenantID uint64 `yaml:"tenant_id"               json:"tenant_id"`
	// CreatorID records the user ID of whoever originally created the KB.
	// Used by the workspace-level RBAC middleware to let Contributors edit
	// their own KBs without granting them access to everyone else's.
	// Nullable for backward compatibility with rows created before the
	// RBAC migration backfilled the column to the workspace Owner.
	CreatorID string `yaml:"creator_id"              json:"creator_id"              gorm:"type:varchar(36);index"`
	// Chunking configuration
	ChunkingConfig ChunkingConfig `yaml:"chunking_config"         json:"chunking_config"         gorm:"type:json"`
	// Image processing configuration
	ImageProcessingConfig ImageProcessingConfig `yaml:"image_processing_config" json:"image_processing_config" gorm:"type:json"`
	// ID of the embedding model
	EmbeddingModelID string `yaml:"embedding_model_id"      json:"embedding_model_id"`
	// Summary model ID
	SummaryModelID string `yaml:"summary_model_id"        json:"summary_model_id"`
	// VLM config
	VLMConfig VLMConfig `yaml:"vlm_config"              json:"vlm_config"              gorm:"type:json"`
```

知识库组织文档并选择 embedding、summary 等模型；知识库是检索与处理配置的边界。

再看单份 Knowledge 的身份与状态。 [`internal/types/knowledge.go:124`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/types/knowledge.go#L124)

```bash
sed -n '124,156p' internal/types/knowledge.go
```

```output
type Knowledge struct {
	// Unique identifier of the knowledge
	ID string `json:"id"                 gorm:"type:varchar(36);primaryKey"`
	// Workspace ID
	TenantID uint64 `json:"tenant_id"`
	// ID of the knowledge base
	KnowledgeBaseID string `json:"knowledge_base_id"`
	// Tags holds the tags associated with this knowledge (populated on query, not persisted directly).
	Tags []*KnowledgeTag `json:"tags"               gorm:"-"`
	// Type of the knowledge
	Type string `json:"type"`
	// Title of the knowledge
	Title string `json:"title"`
	// Description of the knowledge
	Description string `json:"description"`
	// DescriptionSpecified distinguishes an explicitly supplied empty description
	// from an omitted field in partial update requests.
	DescriptionSpecified bool `json:"-" gorm:"-"`
	// Source of the knowledge (e.g. URL address for url type, "manual" for manual type)
	Source string `json:"source"             gorm:"type:varchar(2048)"`
	// Channel indicates through which channel the knowledge was ingested (web, api, browser_extension, wechat, etc.)
	Channel string `json:"channel"            gorm:"type:varchar(50);default:'web'"`
	// Parse status of the knowledge
	ParseStatus string `json:"parse_status"`
	// PendingSubtasksCount is the outstanding enrichment subtask count
	// (summary + question + graph chunks). Only meaningful while
	// ParseStatus == "finalizing"; defaults to 0 in any terminal state.
	PendingSubtasksCount int `json:"pending_subtasks_count" gorm:"type:int;not null;default:0"`
	// Summary status for async summary generation
	SummaryStatus string `json:"summary_status"     gorm:"type:varchar(32);default:none"`
	// Enable status of the knowledge
	EnableStatus string `json:"enable_status"`
	// ID of the embedding model
```

Knowledge 属于租户和知识库，保存 parse、summary、enable 三类状态及后处理计数。它表示一次导入的文档条目，不是向量本身。

检查 Chunk 的内容、位置与版本。 [`internal/types/chunk.go:113`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/types/chunk.go#L113)

```bash
sed -n '113,156p' internal/types/chunk.go
```

```output
type Chunk struct {
	// Unique identifier of the chunk, using UUID format
	ID string `json:"id"                       gorm:"type:varchar(36);primaryKey"`
	// SeqID is an auto-increment integer ID for external API usage (FAQ entries)
	SeqID int64 `json:"seq_id"                   gorm:"type:bigint;uniqueIndex;autoIncrement"`
	// Tenant ID, used for multi-tenant isolation
	TenantID uint64 `json:"tenant_id"`
	// ID of the parent knowledge, associated with the Knowledge model
	KnowledgeID string `json:"knowledge_id"`
	// ID of the knowledge base, for quick location
	KnowledgeBaseID string `json:"knowledge_base_id"`
	// Optional tag ID for categorization within a knowledge base (used for FAQ)
	TagID string `json:"tag_id"                   gorm:"type:varchar(36);index"`
	// Actual text content of the chunk
	Content string `json:"content"`
	// SourceContent is the immutable parser output. Legacy rows are lazily
	// backfilled from Content on the first manual edit.
	SourceContent string `json:"-"`
	// ContentRevision is incremented for every user edit or rollback.
	ContentRevision int `json:"content_revision" gorm:"not null;default:0"`
	// IndexStatus reports whether the current content is reflected in the
	// retrieval stores: ready | processing | failed.
	IndexStatus string `json:"index_status" gorm:"type:varchar(16);not null;default:'ready'"`
	// LastEditorID records the actor that produced the current revision.
	LastEditorID string `json:"last_editor_id" gorm:"type:varchar(64);not null;default:''"`
	// Index position of the chunk in the original document
	ChunkIndex int `json:"chunk_index"`
	// Whether the chunk is enabled, can be used to temporarily disable certain chunks
	IsEnabled bool `json:"is_enabled"               gorm:"default:true"`
	// Flags 存储多个布尔状态的位标志（如推荐状态等）
	// 默认值为 ChunkFlagRecommended (1)，表示默认可推荐
	Flags ChunkFlags `json:"flags"                    gorm:"default:1"`
	// Status of the chunk
	Status int `json:"status"                   gorm:"default:0"`
	// Starting character position in the original text
	StartAt int `json:"start_at"`
	// Ending character position in the original text
	EndAt int `json:"end_at"`
	// Previous chunk ID
	PreChunkID string `json:"pre_chunk_id"`
	// Next chunk ID
	NextChunkID string `json:"next_chunk_id"`
	// Chunk 类型，用于区分不同类型的 Chunk
	ChunkType ChunkType `json:"chunk_type"               gorm:"type:varchar(20);default:'text'"`
```

Chunk 关联 Knowledge，保存正文、源正文、编辑 revision、索引同步状态和父块/相邻块关系。元数据数据库中的内容与检索库中的索引需要同步。

核心关系是 Tenant → KnowledgeBase → Knowledge → Chunk；索引通过 ChunkID 关联回原文。Session/Message 是另一组会话实体，问答时才把检索结果接到消息上。

## 05 保存文件后提交后台任务

这里确定上传成功的精确定义，并识别跨存储操作的失败窗口。入库图见 [flow.html](flow.html)。

看重复文件检查的实际参数。 [`internal/application/service/knowledge_create.go:79`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_create.go#L79)

```bash
sed -n '79,109p' internal/application/service/knowledge_create.go
```

```output
	// Calculate file hash for deduplication
	logger.Info(ctx, "Calculating file hash")
	hash, err := calculateFileHash(file)
	if err != nil {
		logger.Errorf(ctx, "Failed to calculate file hash: %v", err)
		return nil, err
	}

	// Check if file already exists
	tenantID := ctx.Value(types.TenantIDContextKey).(uint64)
	logger.Infof(ctx, "Checking if file exists, tenant ID: %d", tenantID)
	exists, existingKnowledge, err := s.repo.CheckKnowledgeExists(ctx, tenantID, kbID, &types.KnowledgeCheckParams{
		Type:     "file",
		FileName: fileName,
		FileType: getFileType(fileName),
		FileSize: file.Size,
		FileHash: hash,
	})
	if err != nil {
		logger.Errorf(ctx, "Failed to check knowledge existence: %v", err)
		return nil, err
	}
	if exists {
		logger.Infof(ctx, "File already exists: %s", fileName)
		// Update creation time for existing knowledge
		if err := s.repo.UpdateKnowledgeColumn(ctx, existingKnowledge.ID, "created_at", time.Now()); err != nil {
			logger.Errorf(ctx, "Failed to update existing knowledge: %v", err)
			return nil, err
		}
		return existingKnowledge, types.NewDuplicateFileError(existingKnowledge)
	}
```

service 计算文件 hash，并按 tenant、KB、名称、类型、大小等参数查询重复项。命中时更新创建时间，返回已有记录和 DuplicateFileError；这段预检查本身不能证明并发上传的全局唯一性。

看 pending 初始化、文件保存和 DB 写入的顺序。 [`internal/application/service/knowledge_create.go:165`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_create.go#L165)

```bash
sed -n '165,204p' internal/application/service/knowledge_create.go
```

```output
		FileHash:         hash,
		ParseStatus:      "pending",
		EnableStatus:     "disabled",
		CreatedAt:        time.Now(),
		UpdatedAt:        time.Now(),
		EmbeddingModelID: kb.EmbeddingModelID,
		Metadata:         metadataJSON,
	}

	if processOverrides != nil {
		if err := knowledge.SetProcessOverrides(processOverrides); err != nil {
			logger.Errorf(ctx, "Failed to set process overrides: %v", err)
			return nil, err
		}
	}

	// Save the file to storage (use KB-level storage engine if configured)
	logger.Infof(ctx, "Saving file, knowledge ID: %s", knowledge.ID)
	fileSvc := s.resolveFileService(ctx, kb)
	filePath, err := fileSvc.SaveFile(ctx, file, knowledge.TenantID, knowledge.ID)
	if err != nil {
		logger.Errorf(ctx, "Failed to save file, knowledge ID: %s, error: %v", knowledge.ID, err)
		return nil, err
	}
	knowledge.FilePath = filePath

	// Save knowledge record to database after the file is safely stored.
	logger.Info(ctx, "Saving knowledge record to database")
	if err := s.repo.CreateKnowledge(ctx, knowledge); err != nil {
		logger.Errorf(ctx, "Failed to create knowledge record, ID: %s, error: %v", knowledge.ID, err)
		if deleteErr := fileSvc.DeleteFile(ctx, filePath); deleteErr != nil {
			logger.Errorf(ctx, "Failed to delete saved file after knowledge creation failed, path: %s, error: %v", filePath, deleteErr)
		}
		return nil, err
	}
	// Set tag relations
	if err := s.setAndAttachKnowledgeTags(ctx, tenantID, kbID, knowledge, tagIDs); err != nil {
		logger.Errorf(ctx, "Failed to set knowledge tags, knowledge ID: %s, error: %v", knowledge.ID, err)
		return nil, err
	}
```

初始状态为 pending/disabled。先 SaveFile，再 CreateKnowledge；数据库创建失败时尝试删除文件。标签关联失败则直接返回，说明这些跨资源步骤不是一个整体事务。

最后看队列 payload 与入队结果。 [`internal/application/service/knowledge_create.go:216`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_create.go#L216)

```bash
sed -n '216,256p' internal/application/service/knowledge_create.go
```

```output
	lang := types.LanguageFromContextOrDefault(ctx)
	taskPayload := types.DocumentProcessPayload{
		TenantID:                 tenantID,
		KnowledgeID:              knowledge.ID,
		KnowledgeBaseID:          kbID,
		FilePath:                 filePath,
		FileName:                 safeFilename,
		FileType:                 getFileType(safeFilename),
		EnableMultimodel:         enableMultimodelValue,
		EnableQuestionGeneration: enableQuestionGeneration,
		QuestionCount:            questionCount,
		Language:                 lang,
	}

	langfuse.InjectTracing(ctx, &taskPayload)
	payloadBytes, err := json.Marshal(taskPayload)
	if err != nil {
		logger.Errorf(ctx, "Failed to marshal document process task payload: %v", err)
		s.markKnowledgeEnqueueFailed(ctx, knowledge)
		recordKBActivity(ctx, s.audit, knowledge.TenantID, kbID, types.AuditActionKnowledgeCreated,
			"knowledge", knowledge.ID, types.AuditOutcomeFailed, map[string]any{
				"title": knowledge.Title, "source_type": "file", "file_type": knowledge.FileType,
				"processing_status": "failed", "failure_stage": "enqueue",
			})
		// 即使入队失败，也返回knowledge，因为文件已保存
		return knowledge, nil
	}

	task := asynq.NewTask(
		types.TypeDocumentProcess,
		payloadBytes,
		documentProcessTaskOptions(s.config, asynq.MaxRetry(3))...,
	)
	info, err := s.task.Enqueue(task)
	if err != nil {
		logger.Errorf(ctx, "Failed to enqueue document process task: %v", err)
		s.markKnowledgeEnqueueFailed(ctx, knowledge)
		recordKBActivity(ctx, s.audit, knowledge.TenantID, kbID, types.AuditActionKnowledgeCreated,
			"knowledge", knowledge.ID, types.AuditOutcomeFailed, map[string]any{
				"title": knowledge.Title, "source_type": "file", "file_type": knowledge.FileType,
				"processing_status": "failed", "failure_stage": "enqueue",
```

payload 携带 tenant、KB、Knowledge、文件路径和处理参数；Asynq task 声明 MaxRetry(3)。入队失败会标记失败，却仍返回 `knowledge, nil`，所以 handler 可能给出 HTTP 200。

确认失败状态不是只打印一条日志。 [`internal/application/service/knowledge_create.go:1142`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_create.go#L1142)

```bash
sed -n '1142,1154p' internal/application/service/knowledge_create.go
```

```output
// markKnowledgeEnqueueFailed prevents a durable knowledge row from remaining
// indefinitely "pending" when its background processing task was never
// created. The API may still return the row so callers can retry it.
func (s *knowledgeService) markKnowledgeEnqueueFailed(ctx context.Context, knowledge *types.Knowledge) {
	if knowledge == nil {
		return
	}
	knowledge.ParseStatus = "failed"
	knowledge.ErrorMessage = "Failed to enqueue processing task"
	if err := s.repo.UpdateKnowledge(ctx, knowledge); err != nil {
		logger.Errorf(ctx, "Failed to mark knowledge as failed after enqueue error: %v", err)
	}
}
```

`markKnowledgeEnqueueFailed` 写入 failed 和错误信息；状态更新本身仍可能失败并被记录。保存、入队及错误回写之间存在补偿逻辑，不能宣称 exactly-once。

典型路径到这里仅完成“文件和条目已保存，处理任务已提交”。外部文件存储、元数据 DB 与任务系统之间没有在本路径看到统一提交协议。

## 06 Worker 与 Lite 模式

同一个 TaskEnqueuer 接口有两种执行模型，需要先说明主线使用哪一种。这里以配置 REDIS_ADDR 的 Asynq 路径为主。

看容器选择队列后端。 [`internal/container/container.go:319`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/container/container.go#L319)

```bash
sed -n '319,348p' internal/container/container.go
```

```output
	logger.Debugf(ctx, "[Container] Registering task enqueuer...")
	redisAvailable := os.Getenv("REDIS_ADDR") != ""
	if redisAvailable {
		must(container.Provide(router.NewAsyncqClient, dig.As(new(interfaces.TaskEnqueuer))))
		// Dedicated pools guarantee capacity for each stage. The shared pool
		// additionally subscribes to core/enrichment queues to provide elastic
		// borrowing while post-process and maintenance remain hard-isolated.
		must(container.Provide(router.NewCoreAsynqServer, dig.Name("coreAsynqServer")))
		must(container.Provide(router.NewPostProcessAsynqServer, dig.Name("postProcessAsynqServer")))
		must(container.Provide(router.NewEnrichmentAsynqServer, dig.Name("enrichmentAsynqServer")))
		must(container.Provide(router.NewMaintenanceAsynqServer, dig.Name("maintenanceAsynqServer")))
		must(container.Provide(router.NewSharedAsynqServer, dig.Name("sharedAsynqServer")))
		must(container.Provide(router.NewWikiAsynqServer, dig.Name("wikiAsynqServer")))
		// Asynq inspector for cancel-by-knowledge-id (best-effort
		// dequeue of pending/scheduled/retry tasks + active-task cancel).
		must(container.Provide(router.NewAsynqInspector))
		must(container.Provide(router.NewAsynqTaskInspector))
		// Install the distributed per-model chat concurrency governor. Only
		// available with Redis (the shared semaphore backend); Lite mode is
		// single-process and low-volume, so it runs ungated.
		must(container.Invoke(registerModelConcurrencyLimiter))
	} else {
		syncExec := router.NewSyncTaskExecutor()
		must(container.Provide(func() interfaces.TaskEnqueuer { return syncExec }))
		must(container.Provide(func() *router.SyncTaskExecutor { return syncExec }))
		// Lite mode: no Redis means no asynq inspector. SyncTaskExecutor
		// dispatches inline goroutines that the checkpoint-based abort
		// already handles.
		must(container.Provide(router.NewNoopTaskInspector))
		// Even without Redis, background ingestion/enrichment can burst the
```

存在 REDIS_ADDR 时注册 Asynq 和分池 worker；没有时注入 SyncTaskExecutor。后者名字虽然有 Sync，但注释已提示它用 goroutine。

检查重试耗尽之后的记录位置。 [`internal/router/task.go:232`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/router/task.go#L232)

```bash
sed -n '232,244p' internal/router/task.go
```

```output
	// Install the dead-letter middleware FIRST so it sees the raw error
	// returned by the handler, before any other middleware that might
	// transform it. The middleware records one task_dead_letters row per
	// task that exhausts its retry budget — operators can then SQL-query
	// failures by task type, scope, or tenant without scraping logs.
	// Best-effort: a DB failure is logged and swallowed; the original task
	// error always propagates upstream to asynq for retry/archival.
	//
	// The callback flips Knowledge.parse_status to "failed" the moment a
	// document-related task exhausts its retry budget. Without this hook,
	// a permanently-failing task left its parent knowledge stranded in
	// "processing" until housekeeping cron caught it minutes later — the
	// UI signal users actually see.
```

Asynq mux 安装 dead-letter middleware，并提供知识失败回调；这些处理属于 Redis 路径，不能自动套到 Lite。

追到任务类型与业务处理器的连接。 [`internal/router/task.go:263`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/router/task.go#L263)

```bash
sed -n '263,271p' internal/router/task.go
```

```output
	mux.HandleFunc(types.TypeChunkExtract, params.ChunkExtractor.Handle)
	mux.HandleFunc(types.TypeDataTableSummary, params.DataTableSummary.Handle)

	// Register document processing handler
	mux.HandleFunc(types.TypeDocumentProcess, params.KnowledgeService.ProcessDocument)
	mux.HandleFunc(types.TypeTemporaryDocumentProcess, params.TemporaryDocument.Process)

	// Register manual knowledge processing handler (cleanup + re-indexing)
	mux.HandleFunc(types.TypeManualProcess, params.KnowledgeService.ProcessManualUpdate)
```

`document:process` 对应 `KnowledgeService.ProcessDocument`，形成从上传到 worker 的明确交接。

对照 Lite 的真实执行循环。 [`internal/router/sync_task.go:77`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/router/sync_task.go#L77)

```bash
sed -n '77,115p' internal/router/sync_task.go
```

```output

	go func() {
		if delay > 0 {
			time.Sleep(delay)
		}

		// Tag as a background worker execution so the per-model concurrency
		// governor throttles Lite-mode ingestion/enrichment LLM calls, mirroring
		// the asynq backgroundTaskMiddleware in the Redis path.
		ctx := types.WithBackgroundTask(context.Background())
		start := time.Now()
		logger.Infof(ctx, "[SyncTask] Executing task type=%s id=%s", task.Type(), taskID)

		var lastErr error
		for attempt := 0; attempt <= maxRetry; attempt++ {
			if attempt > 0 {
				backoff := time.Duration(attempt) * 5 * time.Second
				if backoff > 30*time.Second {
					backoff = 30 * time.Second
				}
				logger.Infof(ctx, "[SyncTask] Retrying task type=%s id=%s attempt=%d/%d backoff=%s",
					task.Type(), taskID, attempt, maxRetry, backoff)
				time.Sleep(backoff)
			}

			attemptCtx := types.WithTaskRetryMetadata(ctx, attempt, maxRetry)
			lastErr = handler(attemptCtx, task)
			if lastErr == nil {
				logger.Infof(ctx, "[SyncTask] Task completed type=%s id=%s elapsed=%v",
					task.Type(), taskID, time.Since(start))
				return
			}
		}

		logger.Errorf(ctx, "[SyncTask] Task failed (exhausted retries) type=%s id=%s elapsed=%v err=%v",
			task.Type(), taskID, time.Since(start), lastErr)
	}()

	return info, nil
```

它用 context.Background 起 goroutine，对 handler 返回的 error 做本地退避重试。没有持久队列，因此进程退出后的任务恢复语义不同。

不要把“都实现 TaskEnqueuer”理解成队列选项、故障恢复和跨进程行为完全等价。Lite 的 Enqueue 还仅遍历额外传入的 opts（源码 50–63 行），而文件上传把选项放在 NewTask；本次不声称两条路径有相同重试次数。

## 07 解析任务如何恢复文档上下文

worker 不再拥有原始 HTTP 请求，需要从 payload 重建租户上下文并重新读取持久状态。

检查 payload 解析、上下文与 retry 元数据。 [`internal/application/service/knowledge_process.go:3238`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L3238)

```bash
sed -n '3238,3265p' internal/application/service/knowledge_process.go
```

```output
func (s *knowledgeService) ProcessDocument(ctx context.Context, t *asynq.Task) error {
	var payload types.DocumentProcessPayload
	if err := json.Unmarshal(t.Payload(), &payload); err != nil {
		logger.Errorf(ctx, "failed to unmarshal document process task payload: %v", err)
		return nil
	}

	ctx = logger.WithRequestID(ctx, payload.RequestId)
	ctx = logger.WithField(ctx, "document_process", payload.KnowledgeID)
	ctx = types.WithExecutionTenant(ctx, payload.TenantID)
	if payload.Language != "" {
		ctx = context.WithValue(ctx, types.LanguageContextKey, payload.Language)
	}

	// 获取任务重试信息，用于判断是否是最后一次重试
	retryCount, _ := asynq.GetRetryCount(ctx)
	maxRetry, _ := asynq.GetMaxRetry(ctx)
	isLastRetry := retryCount >= maxRetry

	tenantInfo, err := s.tenantRepo.GetTenantByID(ctx, payload.TenantID)
	if err != nil {
		logger.Errorf(ctx, "failed to get tenant: %v", err)
		return nil
	}
	ctx = context.WithValue(ctx, types.TenantInfoContextKey, tenantInfo)

	logger.Infof(ctx, "Processing document task: knowledge_id=%s, file_path=%s, retry=%d/%d",
		payload.KnowledgeID, payload.FilePath, retryCount, maxRetry)
```

错误 payload 或读取 tenant 失败的某些分支返回 nil；worker 看到 nil 就不按失败重试。重试策略必须逐条沿 return 看，不能只看 MaxRetry。

检查已完成、删除和取消状态。 [`internal/application/service/knowledge_process.go:3279`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L3279)

```bash
sed -n '3279,3302p' internal/application/service/knowledge_process.go
```

```output
		payload.TenantID,
		payload.KnowledgeBaseID,
		payload.KnowledgeID); err != nil {
		return err
	}

	// 检查是否正在删除 / 已被用户取消 - 如果是则直接退出
	if knowledge.ParseStatus == types.ParseStatusDeleting {
		logger.Infof(ctx, "Knowledge is being deleted, aborting processing: %s", payload.KnowledgeID)
		return nil
	}
	if knowledge.ParseStatus == types.ParseStatusCancelled {
		logger.Infof(ctx, "Knowledge cancelled by user, aborting processing: %s", payload.KnowledgeID)
		return nil
	}

	// 检查任务状态 - 幂等性处理
	if knowledge.ParseStatus == types.ParseStatusCompleted {
		logger.Infof(ctx, "Document already completed, skipping: %s", payload.KnowledgeID)
		return nil // 幂等：已完成的任务直接返回
	}

	if knowledge.ParseStatus == types.ParseStatusFailed {
		// 检查是否可恢复（例如：超时、临时错误等）
```

校验资源身份后，对 deleting、cancelled、completed 直接退出。状态防线减少重复处理，但没有提供跨系统严格一次执行保证。

检查 KB 所属租户和 processing 切换。 [`internal/application/service/knowledge_process.go:3325`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L3325)

```bash
sed -n '3325,3357p' internal/application/service/knowledge_process.go
```

```output
		knowledge.ErrorMessage = fmt.Sprintf("failed to get knowledge base: %v", err)
		knowledge.UpdatedAt = time.Now()
		s.repo.UpdateKnowledge(ctx, knowledge)
		return nil
	}
	if kb == nil || kb.ID != payload.KnowledgeBaseID || kb.TenantID != payload.TenantID {
		return fmt.Errorf("processing task KB owner changed: %w", asynq.SkipRetry)
	}
	ctx, err = access.WithKBTaskWrite(ctx, kb, payload.TenantID)
	if err != nil {
		return fmt.Errorf("invalid processing scope: %v: %w", err, asynq.SkipRetry)
	}

	processOverrides, _ := knowledge.ProcessOverrides()
	eff := ResolveProcessConfig(kb, processOverrides)

	// Re-check abort status right before flipping to "processing" — closes
	// the race where the user cancels between the entry guard above and
	// this write (otherwise the worker would overwrite cancelled→processing
	// and downstream checkpoints would treat the run as live).
	if aborted, status := s.isKnowledgeAborted(ctx, knowledge.TenantID, knowledge.ID); aborted {
		logger.Infof(ctx, "Knowledge aborted (%s) before marking processing: %s", status, knowledge.ID)
		return nil
	}
	markKnowledgeProcessing(knowledge, time.Now())
	if err := s.repo.UpdateKnowledge(ctx, knowledge); err != nil {
		logger.Errorf(ctx, "failed to update knowledge status to processing: %v", err)
		return nil
	}

	// Resolve the attempt for span tracking. The enqueue site sets
	// payload.Attempt to a fresh number for the initial parse and to
	// max+1 for each user-initiated reparse. Asynq retries within a
```

后台任务重新校验 KB owner，并取得 task write scope；写 processing 前再次检查取消/删除。这个检查减少竞态，不应当把所有状态写都说成原子条件更新。

找到文件分支对 convert 的调用。 [`internal/application/service/knowledge_process.go:3514`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L3514)

```bash
sed -n '3514,3524p' internal/application/service/knowledge_process.go
```

```output
	} else {
		// File import
		convertResult, err = s.convert(ctx, payload, kb, knowledge, eff, isLastRetry)
		if err != nil {
			return err
		}
		if convertResult == nil {
			return nil
		}
	}

```

文件输入进入 convert；error 会返回给 worker，nil result 则结束此任务。其他 passage、URL、ASR 分支是独立入口差异。

到此输入从“上传请求”变成“由持久记录和任务 payload 校验过的处理上下文”，下一步才真正读取文件。

## 08 可替换解析器与超时

解析的共同输出是 ReadResult/Markdown，后续分块在 Go 侧完成。

查看 NewReader 的工厂分派。 [`internal/infrastructure/docparser/engine_registry.go:69`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/infrastructure/docparser/engine_registry.go#L69)

```bash
sed -n '69,90p' internal/infrastructure/docparser/engine_registry.go
```

```output
func NewReader(
	ctx context.Context, engine, fileType string, isURL bool, deps ReaderDeps,
) (interfaces.DocReader, error) {
	if registration, ok := lookupEngine(engine); ok {
		return registration.NewReader(ctx, deps)
	}
	if engine == "" && !isURL && IsSimpleFormat(fileType) {
		return &SimpleFormatReader{}, nil
	}
	return remoteReader(deps)
}

// remoteReader returns the docreader client, or an error when the service is
// not connected — a nil interface value here would panic at the call site.
func remoteReader(deps ReaderDeps) (interfaces.DocReader, error) {
	if deps.Remote == nil {
		return nil, errNotConnected
	}
	return deps.Remote, nil
}

// ListAllEngines returns the merged engine list: locally registered engines
```

显式引擎走注册实现；未指定且是简单格式时用 SimpleFormatReader，其他情况落到远端 DocReader。没有远端实例会报错，不会静默创建可用服务。

说明 anydoc 与 Python DocReader 的关系。 [`internal/infrastructure/docparser/engines.go:33`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/infrastructure/docparser/engines.go#L33)

```bash
sed -n '33,56p' internal/infrastructure/docparser/engines.go
```

```output
	types.SetPreferParserEngine(preferAnydocWhenAvailable)
	RegisterEngine(&builtinEngine{})
	RegisterEngine(&simpleEngine{})
	RegisterEngine(&anydocEngine{})
	RegisterEngine(&weKnoraCloudEngine{})
	RegisterEngine(&mineruEngine{})
	RegisterEngine(&mineruCloudEngine{})
	RegisterEngine(&paddleOCRVLEngine{})
	RegisterEngine(&paddleOCRVLCloudEngine{})
}

// preferAnydocWhenAvailable is the type-level default override: when the
// anydoc binding is linked and converts this file type, use it instead of
// builtin or the markitdown fallback. Simple formats stay on the Go reader.
func preferAnydocWhenAvailable(fileType string) string {
	if IsSimpleFormat(fileType) {
		return ""
	}
	if anydoc.Available() && anydoc.Supports(fileType, "") {
		return AnydocEngineName
	}
	return ""
}

```

注册引擎包括 builtin、simple、anydoc 与外部解析服务。anydoc 只有在绑定可用且支持格式时才参与默认选择；不能说每个文件都经过 Python。

看解析错误如何区分。 [`internal/application/service/knowledge_process.go:3792`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L3792)

```bash
sed -n '3792,3823p' internal/application/service/knowledge_process.go
```

```output
	}

	result, err := s.callDocReaderWithTimeout(ctx, reader, req)
	if err != nil {
		// Distinguish DocReader timeout (a knowable user-facing
		// failure) from generic read errors so the UI can suggest
		// "split this large file" specifically when relevant.
		code := werrors.ErrCodeDocReaderParseFailed
		if errors.Is(err, context.DeadlineExceeded) || strings.Contains(err.Error(), "docreader call timeout") {
			code = werrors.ErrCodeDocReaderTimeout
		}
		s.failStage(ctx, knowledge.ID, types.StageDocReader,
			code, "document read failed", err)
		return s.failKnowledge(ctx, knowledge, isLastRetry, "document read failed: %v", err)
	}
	sanitizeReadResult(result)
	if result.Error != "" {
		logger.Errorf(ctx, "[convert] parser returned error kb=%s knowledge=%s file=%q type=%s engine=%q: %s",
			kb.ID, knowledge.ID, req.FileName, fileType, parserEngine, result.Error)
		knowledge.ParseStatus = "failed"
		knowledge.ErrorMessage = result.Error
		knowledge.UpdatedAt = time.Now()
		s.repo.UpdateKnowledge(ctx, knowledge)
		s.failStage(ctx, knowledge.ID, types.StageDocReader,
			werrors.ErrCodeDocReaderParseFailed, result.Error, nil)
		return nil, nil
	}
	docOutput := types.JSONMap{
		"text_length":  len(result.MarkdownContent),
		"images_found": len(result.ImageRefs),
		"is_audio":     result.IsAudio,
	}
```

调用 error 走 failKnowledge；而 ReadResult.Error 会写 failed 后返回 nil result。两类错误虽然都表现为解析失败，对任务重试的语义不同。

看调用级 timeout 的实际传递。 [`internal/application/service/knowledge_process.go:3839`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L3839)

```bash
sed -n '3839,3855p' internal/application/service/knowledge_process.go
```

```output
func (s *knowledgeService) callDocReaderWithTimeout(
	ctx context.Context, reader interfaces.DocReader, req *types.ReadRequest,
) (*types.ReadResult, error) {
	timeout := 30 * time.Minute
	if s.config != nil && s.config.KnowledgeBase != nil && s.config.KnowledgeBase.DocReaderCallTimeout > 0 {
		timeout = s.config.KnowledgeBase.DocReaderCallTimeout
	}
	callCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	start := time.Now()
	result, err := reader.Read(callCtx, req)
	elapsed := time.Since(start)
	if err != nil {
		// Promote DeadlineExceeded into a clearer message; retain underlying
		// error via %w so errors.Is(callCtx.Err(), context.DeadlineExceeded)
		// still works for upstream classification.
```

通过子 context 把超时交给 reader.Read，默认 30 分钟并允许配置覆盖。它依赖具体 reader 尊重 context，不能凭这一层证明能强杀任何解析计算。

这条主线用普通 Markdown，通常可走 Go simple reader；复杂文件的内部 OCR/PDF 算法未在本次逐一展开。

## 09 分块策略与位置不变量

分块不是任意字符串切片：检索和界面引用还需要定位回规范化后的文档。

先看 Chunk 的位置约定。 [`internal/infrastructure/chunker/splitter.go:14`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/infrastructure/chunker/splitter.go#L14)

```bash
sed -n '14,46p' internal/infrastructure/chunker/splitter.go
```

```output
// Chunk represents a piece of split text with position tracking.
//
// Content holds exactly the text from the original document between Start
// and End (rune offsets), so End-Start == utf8.RuneCountInString(Content).
// This invariant is relied on by document-reconstruction code paths
// (knowledge.go:2278+ for summary generation, UI highlighting, etc.).
//
// ContextHeader is a separately-tracked context string (e.g. a Markdown
// heading breadcrumb) that should be prepended at embedding/retrieval time
// but is NOT part of Content. Keeping the two apart preserves the
// position invariant while still letting embedding pipelines see the
// section context.
type Chunk struct {
	Content       string
	ContextHeader string
	Seq           int
	Start         int
	End           int
}

// EmbeddingContent returns the text that should be fed to the embedding
// model — the ContextHeader prepended (when set) plus the chunk content.
// Use this where Content alone would lose semantic context (Tier-1 chunks).
//
// Content is returned verbatim from the source document (the End-Start
// rune-count invariant requires that), but for embedding we trim the
// surrounding whitespace so leading/trailing newlines from boundary slices
// don't dilute the embedded vector or waste tokens. Inner whitespace is
// preserved.
func (c Chunk) EmbeddingContent() string {
	body := strings.TrimSpace(c.Content)
	if c.ContextHeader == "" {
		return body
```

Start/End 是 rune 偏移，Content 保留对应区间；ContextHeader 单独保存标题路径，在 embedding 时拼接，避免破坏定位。它定位的是转换、换行规范化后的文本，不是 PDF 原始字节。

再看当前使用的策略入口 Split。 [`internal/infrastructure/chunker/strategy.go:34`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/infrastructure/chunker/strategy.go#L34)

```bash
sed -n '34,58p' internal/infrastructure/chunker/strategy.go
```

```output
func Split(text string, cfg SplitterConfig) []Chunk {
	if text == "" {
		return nil
	}
	cfg = ensureDefaults(cfg)
	chain, profile := resolveChainWithProfile(text, cfg)
	totalChars := len([]rune(text))

	var lastOut []Chunk
	for i, tier := range chain {
		out := runTier(tier, text, cfg, profile)
		if v := ValidateChunks(out, totalChars, cfg.ChunkSize); v.OK {
			return out
		} else {
			logger.Debugf(context.Background(), "chunker: tier %s rejected: %s", tier, v.Reason)
		}
		if tier == TierLegacy && i == len(chain)-1 {
			lastOut = out
		}
	}
	if lastOut != nil {
		return lastOut
	}
	return SplitText(text, cfg)
}
```

resolveChainWithProfile 选择策略链，逐个 runTier 并 ValidateChunks，失败时退回后续策略/legacy。注释所谓“always non-nil”与空字符串直接返回 nil 并不一致，以实现为准。

查看父子分块分支。 [`internal/application/service/knowledge_process.go:3612`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L3612)

```bash
sed -n '3612,3639p' internal/application/service/knowledge_process.go
```

```output
	convertResult.MarkdownContent = chunker.NormalizeLineEndings(convertResult.MarkdownContent)
	chunkCfg := buildSplitterConfigFromChunking(eff.ChunkingConfig)

	processOpts := ProcessChunksOptions{
		EnableQuestionGeneration: payload.EnableQuestionGeneration,
		QuestionCount:            payload.QuestionCount,
		EnableMultimodel:         payload.EnableMultimodel,
		StoredImages:             storedImages,
	}

	if convertResult != nil {
		processOpts.Metadata = convertResult.Metadata
	}

	if eff.ChunkingConfig.EnableParentChild {
		parentCfg, childCfg := buildParentChildConfigs(eff.ChunkingConfig, chunkCfg)
		pcResult := chunker.SplitParentChild(convertResult.MarkdownContent, parentCfg, childCfg)
		chunks = make([]types.ParsedChunk, len(pcResult.Children))
		for i, c := range pcResult.Children {
			chunks[i] = types.ParsedChunk{
				Content:       c.Content,
				ContextHeader: c.ContextHeader,
				Seq:           c.Seq,
				Start:         c.Start,
				End:           c.End,
				ParentIndex:   c.ParentIndex,
			}
		}
```

先规范化换行，再根据 EnableParentChild 调用 SplitParentChild，并记录 ParentIndex；关闭时调用 Split。孩子负责细粒度命中，父块用于后续上下文扩展。

把分块结果交给 processChunks。 [`internal/application/service/knowledge_process.go:3645`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L3645)

```bash
sed -n '3645,3665p' internal/application/service/knowledge_process.go
```

```output
		logger.Infof(ctx, "Split document into %d parent + %d child chunks for knowledge %s",
			len(pcResult.Parents), len(pcResult.Children), knowledge.ID)
	} else {
		splitChunks := chunker.Split(convertResult.MarkdownContent, chunkCfg)
		chunks = make([]types.ParsedChunk, len(splitChunks))
		for i, c := range splitChunks {
			chunks[i] = types.ParsedChunk{
				Content:       c.Content,
				ContextHeader: c.ContextHeader,
				Seq:           c.Seq,
				Start:         c.Start,
				End:           c.End,
			}
		}
		logger.Infof(ctx, "Split document into %d chunks for knowledge %s", len(chunks), knowledge.ID)
	}

	// Step 4: Process chunks (vectorize + index + enqueue async tasks)
	s.processChunks(ctx, kb, knowledge, chunks, processOpts)

	return nil
```

ParsedChunk 的正文、标题路径、顺序与位置被原样转交；流程继续进入数据库和索引写入。

位置不变量是跨模块协议：分块、编辑、父块展开与引用定位都依赖它。分块“更小”并非独立优化，必须同时考虑召回与上下文还原。

## 10 Chunk 持久化与索引写入

现在从文本转到可检索的数据；区分 Chunk 数据库与检索引擎。

查看处理开始的取消检查、模型选择及旧数据清理。 [`internal/application/service/knowledge_process.go:310`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L310)

```bash
sed -n '310,345p' internal/application/service/knowledge_process.go
```

```output
	// Check if knowledge is being deleted/cancelled before processing.
	// Both statuses short-circuit identically here — there's nothing to clean
	// up yet so the branch is purely "stop early".
	if aborted, status := s.isKnowledgeAborted(ctx, knowledge.TenantID, knowledge.ID); aborted {
		logger.Infof(ctx, "Knowledge aborted (%s), skipping chunk processing: %s", status, knowledge.ID)
		return
	}

	// Get embedding model for vectorization — only needed when vector/keyword indexing is enabled
	var embeddingModel embedding.Embedder
	if kb.NeedsEmbeddingModel() {
		var err error
		embeddingModel, err = s.modelService.GetEmbeddingModel(ctx, kb.EmbeddingModelID)
		if err != nil {
			logger.GetLogger(ctx).WithField("error", err).Errorf("processChunks get embedding model failed")
			return
		}
	} else {
		logger.Infof(ctx, "Vector/keyword indexing disabled for KB %s, skipping embedding model", kb.ID)
	}

	// 幂等性处理：清理旧的chunks和索引数据，避免重复数据
	logger.Infof(ctx, "Cleaning up existing chunks and index data for knowledge: %s", knowledge.ID)

	// 删除旧的chunks
	if err := s.chunkRepo.DeleteChunksByKnowledgeID(ctx, knowledge.TenantID, knowledge.ID); err != nil {
		logger.Warnf(ctx, "Failed to delete existing chunks (may not exist): %v", err)
		// 不返回错误，继续处理（可能没有旧数据）
	}

	// 删除旧的索引数据 — only when vector/keyword indexing is enabled
	tenantInfo := ctx.Value(types.TenantInfoContextKey).(*types.Tenant)
	retrieveEngine, err := retriever.CreateRetrieveEngineForKB(
		ctx, s.retrieveEngine, s.ownership, tenantInfo.ID, kb.VectorStoreID)
	if err == nil && embeddingModel != nil {
		if err := retrieveEngine.DeleteByKnowledgeIDList(ctx, []string{knowledge.ID}, embeddingModel.GetDimensions(), knowledge.Type); err != nil {
```

按索引策略加载 embedding model，并删除旧 chunks/索引以重建。删除旧数据失败有仅记录日志继续的分支，这是一种尽力重建逻辑，不是无中断原子替换。

确认是否不开向量就不保存 Chunk。 [`internal/application/service/knowledge_process.go:522`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L522)

```bash
sed -n '522,543p' internal/application/service/knowledge_process.go
```

```output
	// Check if knowledge is being deleted/cancelled before writing chunks.
	// Nothing has been persisted yet, so both branches just bail.
	if aborted, status := s.isKnowledgeAborted(ctx, knowledge.TenantID, knowledge.ID); aborted {
		logger.Infof(ctx, "Knowledge aborted (%s), skipping chunk write: %s", status, knowledge.ID)
		return
	}

	// Save chunks to database — ALWAYS, regardless of indexing strategy.
	// Chunks are needed for wiki generation, graph extraction, and summary generation
	// even when vector/keyword indexing is disabled.
	s.beginStage(ctx, knowledge.ID, types.StageChunking, types.JSONMap{
		"chunks_planned": len(insertChunks),
	})
	if err := s.chunkRepo.CreateChunks(ctx, insertChunks); err != nil {
		knowledge.ParseStatus = types.ParseStatusFailed
		knowledge.ErrorMessage = err.Error()
		knowledge.UpdatedAt = time.Now()
		s.repo.UpdateKnowledge(ctx, knowledge)
		s.failStage(ctx, knowledge.ID, types.StageChunking,
			werrors.ErrCodeChunkingFailed, "create chunks failed", err)
		return
	}
```

所有策略都先保存 Chunk，Wiki/图谱/摘要也可使用它；CreateChunks 失败会标记知识失败。

确认到底哪些块被索引。 [`internal/application/service/knowledge_process.go:564`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L564)

```bash
sed -n '564,583p' internal/application/service/knowledge_process.go
```

```output
		s.beginStage(ctx, knowledge.ID, types.StageEmbedding, embedInput)
		// Create index information — only for child/flat chunks, NOT parent chunks.
		// Parent chunks are stored for context retrieval but do not need vector embeddings.
		// Prepend the document title to improve semantic alignment between
		// question-style queries and statement-style chunk content.
		indexInfoList := make([]*types.IndexInfo, 0, len(textChunks))
		for _, chunk := range textChunks {
			// chunk.EmbeddingContent prepends ContextHeader (heading breadcrumb)
			// when the chunker populated it during Tier-1 splitting; falls back
			// to plain Content otherwise. The document title sits outermost;
			// custom metadata remains document-scoped model context.
			indexContent := buildKnowledgeIndexContent(knowledge, chunk.EmbeddingContent())
			indexInfoList = append(indexInfoList, &types.IndexInfo{
				Content:         indexContent,
				SourceID:        chunk.ID,
				SourceType:      types.ChunkSourceType,
				ChunkID:         chunk.ID,
				KnowledgeID:     knowledge.ID,
				KnowledgeBaseID: knowledge.KnowledgeBaseID,
				IsEnabled:       true,
```

仅 child/flat 文本块形成 IndexInfo；文档标题和 ContextHeader 进入 embedding 输入，ParentChunk 不生成对应向量。ChunkID 是回原文的键。

深入 BatchIndex 的实际算法。 [`internal/application/service/retriever/keywords_vector_hybrid_indexer.go:88`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/retriever/keywords_vector_hybrid_indexer.go#L88)

```bash
sed -n '88,128p' internal/application/service/retriever/keywords_vector_hybrid_indexer.go
```

```output
func (v *KeywordsVectorHybridRetrieveEngineService) BatchIndex(ctx context.Context,
	embedder embedding.Embedder, indexInfoList []*types.IndexInfo, retrieverTypes []types.RetrieverType,
) error {
	if len(indexInfoList) == 0 {
		return nil
	}

	if slices.Contains(retrieverTypes, types.VectorRetrieverType) {
		var contentList []string
		for _, indexInfo := range indexInfoList {
			contentList = append(contentList, sanitizeForEmbedding(ctx, indexInfo.Content))
		}
		embeddings, err := batchEmbedWithBackoff(ctx, embedder, contentList)
		if err != nil {
			return err
		}

		batchSize := 40
		chunks := utils.ChunkSlice(indexInfoList, batchSize)

		// Use concurrent batch saving for better performance
		// Limit concurrency to avoid overwhelming the backend
		const maxConcurrency = 5
		if len(chunks) <= maxConcurrency {
			// For small number of batches, use simple concurrency
			return v.concurrentBatchSave(ctx, chunks, embeddings, batchSize)
		}

		// For large number of batches, use bounded concurrency
		return v.boundedConcurrentBatchSave(ctx, chunks, embeddings, batchSize, maxConcurrency)
	}

	// For non-vector retrieval, use concurrent batch saving as well
	chunks := utils.ChunkSlice(indexInfoList, 10)
	const maxConcurrency = 5
	if len(chunks) <= maxConcurrency {
		return v.concurrentBatchSaveNoEmbedding(ctx, chunks)
	}
	return v.boundedConcurrentBatchSaveNoEmbedding(ctx, chunks, maxConcurrency)
}

```

向量分支先批量 embedding，再按 40 个条目分批写，最多 5 路；非向量分支按 10 个一批。retrieveEngine 再把保存操作交给选中的 repository。

看 BatchIndex 失败的补偿。 [`internal/application/service/knowledge_process.go:622`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L622)

```bash
sed -n '622,649p' internal/application/service/knowledge_process.go
```

```output
		err = retrieveEngine.BatchIndex(ctx, embeddingModel, indexInfoList)
		if err != nil {
			knowledge.ParseStatus = types.ParseStatusFailed
			knowledge.ErrorMessage = err.Error()
			knowledge.UpdatedAt = time.Now()
			s.repo.UpdateKnowledge(ctx, knowledge)

			// delete failed chunks
			if err := s.chunkRepo.DeleteChunksByKnowledgeID(ctx, knowledge.TenantID, knowledge.ID); err != nil {
				logger.Errorf(ctx, "Delete chunks failed: %v", err)
			}

			// delete index
			if err := retrieveEngine.DeleteByKnowledgeIDList(
				ctx, []string{knowledge.ID}, embeddingModel.GetDimensions(), kb.Type,
			); err != nil {
				logger.Errorf(ctx, "Delete index failed: %v", err)
			}
			// Map vector store / embedding rate-limit errors to a
			// stable code so the UI can offer "retry later" hints.
			code := werrors.ErrCodeVectorStoreWriteFailed
			if isLikelyRateLimitError(err) {
				code = werrors.ErrCodeEmbeddingRateLimit
			}
			s.failStage(ctx, knowledge.ID, types.StageEmbedding,
				code, "batch index failed", err)
			return
		}
```

失败会写 failed，尝试删除本次 chunks 与索引并记录阶段错误。`processChunks` 无返回值，后续 ProcessDocument 返回 nil，因此该失败不能仅依赖 Asynq 自动重试；补偿操作自身也可能失败。

多种检索后端由同一接口接入，代价是必须明确元数据和索引之间的一致性窗口。索引写成功也还不是所有后台任务结束。

## 11 后处理完成与取消竞争

解释 processing、finalizing、completed 为什么是三个阶段，以及状态保护具体在哪实现。

看索引后的状态写入。 [`internal/application/service/knowledge_process.go:215`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L215)

```bash
sed -n '215,240p' internal/application/service/knowledge_process.go
```

```output
func finalizeIndexedKnowledgeState(
	knowledge *types.Knowledge,
	totalStorageSize int64,
	textChunkCount int,
	hasPendingMultimodal bool,
	now time.Time,
) {
	if hasPendingMultimodal || textChunkCount > 0 {
		knowledge.ParseStatus = types.ParseStatusProcessing
		knowledge.SummaryStatus = types.SummaryStatusNone
	} else {
		// No text chunks and no pending multimodal work: there is nothing for
		// post-process to enrich, so complete immediately. This is the only
		// route to 'completed' that bypasses FinalizeSubtask, so it has to
		// clear error_message itself — otherwise a successfully indexed row
		// keeps reporting a failure from an earlier attempt.
		knowledge.ParseStatus = types.ParseStatusCompleted
		knowledge.SummaryStatus = types.SummaryStatusNone
		knowledge.ErrorMessage = ""
	}

	knowledge.EnableStatus = "enabled"
	knowledge.StorageSize = totalStorageSize
	knowledge.ProcessedAt = &now
	knowledge.UpdatedAt = now
}
```

存在文本块或待处理多模态时仍为 processing；无后续内容的路径可直接 completed。EnableStatus 已可设 enabled，因此可用性与最终完成不完全等价。

把索引主任务接到后处理任务。 [`internal/application/service/knowledge_process.go:704`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_process.go#L704)

```bash
sed -n '704,727p' internal/application/service/knowledge_process.go
```

```output
	} else {
		s.skipStage(ctx, knowledge.ID, types.StageMultimodal, "skipped")
		// If there are no multimodal tasks, enqueue the post process task immediately
		lang := types.LanguageFromContextOrDefault(ctx)
		postProcessPayload := types.KnowledgePostProcessPayload{
			TenantID:        knowledge.TenantID,
			KnowledgeID:     knowledge.ID,
			KnowledgeBaseID: knowledge.KnowledgeBaseID,
			Language:        lang,
			Attempt:         attemptFromCtx(ctx),
		}
		langfuse.InjectTracing(ctx, &postProcessPayload)
		payloadBytes, err := json.Marshal(postProcessPayload)
		if err == nil {
			task := asynq.NewTask(types.TypeKnowledgePostProcess, payloadBytes,
				knowledgePostProcessTaskOptions()...)
			if _, err := s.task.Enqueue(task); err != nil {
				logger.Errorf(ctx, "Failed to enqueue knowledge post process task: %v", err)
			} else {
				logger.Infof(ctx, "Enqueued knowledge post process task for %s", knowledge.ID)
			}
		} else {
			logger.Errorf(ctx, "Failed to marshal knowledge post process payload: %v", err)
		}
```

无多模态任务时提交 TypeKnowledgePostProcess；payload 继续携带文档身份与 attempt。该处入队失败只打印错误，可能留下尚未完成的状态；不能认为主任务返回 nil 后后台必然会继续。

看后处理计数怎么计算。 [`internal/application/service/knowledge_post_process.go:240`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_post_process.go#L240)

```bash
sed -n '240,263p' internal/application/service/knowledge_post_process.go
```

```output
	// must match exactly how many batch tasks we enqueue below.
	questionBatchCount := (len(questionChunks) + questionGenChunkBatchSize - 1) / questionGenChunkBatchSize

	graphChunkCount := 0
	if eff.GraphEnabled {
		graphChunkCount = len(graphChunks)
	}
	expectedSubtasks := 0
	if willSpawnSummary {
		expectedSubtasks++
	}
	expectedSubtasks += questionBatchCount
	if willSpawnWiki {
		expectedSubtasks++
	}
	expectedSubtasks += graphChunkCount

	// enteredFinalizing is set only when the processing-to-finalizing handoff
	// actually seeded the counter. For Wiki-enabled knowledge, that handoff
	// also persists the pending Wiki op in the same transaction.
	enteredFinalizing := false
	wikiSlotOwned := false

	switch {
```

摘要、问题生成批次、Wiki 和图谱块按实际将发出的任务计数；本例禁用 Wiki/图谱，通常只需看启用的摘要或问题生成。

看 SetFinalizing 的数据库条件。 [`internal/application/repository/knowledge.go:668`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/repository/knowledge.go#L668)

```bash
sed -n '668,687p' internal/application/repository/knowledge.go
```

```output
func (r *knowledgeRepository) SetFinalizing(
	ctx context.Context, id string, expectedSubtasks int,
) (bool, error) {
	if expectedSubtasks < 0 {
		expectedSubtasks = 0
	}
	now := time.Now()
	res := r.db.WithContext(ctx).Model(&types.Knowledge{}).
		Where("id = ? AND parse_status = ?", id, types.ParseStatusProcessing).
		Updates(map[string]interface{}{
			"parse_status":           types.ParseStatusFinalizing,
			"pending_subtasks_count": expectedSubtasks,
			"error_message":          "",
			"updated_at":             now,
		})
	if res.Error != nil {
		return false, res.Error
	}
	return res.RowsAffected > 0, nil
}
```

只有 processing 行能被更新为 finalizing，同时设置计数和清除旧错误；通过 RowsAffected 告知是否取得推进资格。

看子任务结束时如何避免旧副本读导致卡住。 [`internal/application/repository/knowledge.go:600`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/repository/knowledge.go#L600)

```bash
sed -n '600,640p' internal/application/repository/knowledge.go
```

```output
func (r *knowledgeRepository) FinalizeSubtask(
	ctx context.Context, id string,
) (int, bool, error) {
	now := time.Now()
	// 1) Atomic decrement, clamped at zero. The `pending_subtasks_count > 0`
	//    guard is purely a safety net for accounting bugs — under normal
	//    operation each subtask handler decrements at most once per task,
	//    so the counter cannot go negative.
	res := r.db.WithContext(ctx).Model(&types.Knowledge{}).
		Where("id = ? AND pending_subtasks_count > 0", id).
		Updates(map[string]interface{}{
			"pending_subtasks_count": gorm.Expr("pending_subtasks_count - 1"),
			"updated_at":             now,
		})
	if res.Error != nil {
		return 0, false, res.Error
	}

	// 2) Guarded promote. EVERY caller unconditionally attempts this after
	//    decrementing — we must NOT gate it on a separate SELECT of the
	//    counter. That read can be served by a lagging read-replica (or a
	//    stale connection snapshot) and return a non-zero value even after
	//    the counter has truly reached zero on the primary; if every caller
	//    trusts that stale read, NONE of them runs the promote and the row
	//    is stranded in `finalizing` forever (the observed "stuck
	//    pending_subtasks_count" bug). The promote is a WRITE, so it executes
	//    on the primary and its `pending_subtasks_count = 0` WHERE clause is
	//    the single authoritative, atomic check on the live row: only the
	//    caller whose decrement actually brought the counter to zero matches,
	//    and cancel/delete cannot be clobbered by a late promote.
	promoteRes := r.db.WithContext(ctx).Model(&types.Knowledge{}).
		Where("id = ? AND parse_status = ? AND pending_subtasks_count = 0",
			id, types.ParseStatusFinalizing).
		Updates(map[string]interface{}{
			"parse_status":  types.ParseStatusCompleted,
			"error_message": "",
			"processed_at":  now,
			"updated_at":    now,
		})
	if promoteRes.Error != nil {
		return 0, false, promoteRes.Error
```

先条件递减计数，再直接执行带 `finalizing AND count=0` 条件的 promote，不以另一次 SELECT 作完成判据。两次 SQL 不是一个不可分割操作，计数设计也依赖调用方对重复 finalize 的控制。

实现用状态条件和子任务计数协调并行完成；仍需区分失败终止、取消、可重试 error 与仅日志记录。特别是 expectedSubtasks==0 的快速路径另行更新（knowledge_post_process.go:294），不能笼统宣称所有完成路径都经过同一个 CAS。

## 12 一轮问答的请求与消息生命周期

文档处理完后，从 session 的 KnowledgeQA 进入第二条主线。流程图见 [qa-flow.html](qa-flow.html)。

区分快速问答与 Agent 路由。 [`internal/router/routes_chat.go:108`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/router/routes_chat.go#L108)

```bash
sed -n '108,130p' internal/router/routes_chat.go
```

```output

// RegisterChatRoutes 注册路由。Chat endpoints are tenant-member usage
// surfaces; Viewer+ is sufficient because per-session/per-agent
// authorisation is enforced inside the handlers.
func RegisterChatRoutes(r *gin.RouterGroup, handler *session.Handler, g *rbacGuards) {
	// These POST routes append messages and run generation, so a scoped key
	// needs the explicit chat capability unless it has full tenant access.
	knowledgeChat := g.apiKeyGroup(r.Group("/knowledge-chat", g.Viewer()), apiKeyChat(apiKeyFullAccess()))
	{
		knowledgeChat.POST("/:session_id", handler.KnowledgeQA)
	}

	// Agent-based chat
	agentChat := g.apiKeyGroup(r.Group("/agent-chat", g.Viewer()), apiKeyChat(apiKeyFullAccess()))
	{
		agentChat.POST("/:session_id", handler.AgentQA)
	}

	// 新增知识检索接口，不需要session_id
	knowledgeSearch := g.apiKeyGroup(r.Group("/knowledge-search", g.Viewer()), apiKeyRetrieve(apiKeyFullAccess()))
	{
		knowledgeSearch.POST("", handler.SearchKnowledge)
	}
```

`/knowledge-chat/:session_id` 与 `/agent-chat/:session_id` 是不同入口；还有无 session 的 `/knowledge-search`。此处主线是快速问答。

查看 KnowledgeQA handler 的全部核心。 [`internal/handler/session/qa.go:882`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/session/qa.go#L882)

```bash
sed -n '882,893p' internal/handler/session/qa.go
```

```output
func (h *Handler) KnowledgeQA(c *gin.Context) {
	// Parse and validate request
	reqCtx, request, err := h.parseQARequest(c, "KnowledgeQA")
	if err != nil {
		c.Error(err)
		return
	}

	// Execute normal mode QA, generate title unless disabled
	h.executeQA(reqCtx, qaModeNormal, !request.DisableTitle)
}

```

解析和验证完成后，以 qaModeNormal 调用 executeQA。选择某个 custom agent 配置不必然表示运行 ReAct。

确认生成前先保存什么。 [`internal/handler/session/qa.go:1095`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/session/qa.go#L1095)

```bash
sed -n '1095,1108p' internal/handler/session/qa.go
```

```output
	createdUser := reqCtx.userMessageID == ""
	createdAssistant := reqCtx.assistantMessage == nil || reqCtx.assistantMessage.ID == ""

	// Create user message. Include pre-uploaded document metadata so history
	// reload shows the attachments even though their content is selected later.
	if err := h.persistTurnMessages(ctx, reqCtx); err != nil {
		if reqCtx.c != nil && !reqCtx.skipSSE {
			_ = reqCtx.c.Error(errors.NewInternalServerError(err.Error()))
		} else {
			logger.ErrorWithFields(ctx, err, map[string]interface{}{"session_id": sessionID})
		}
		return
	}

```

`persistTurnMessages` 先保存 user 和未完成的 assistant 消息，失败就不进入正常生成。

解释与浏览器断连分离的 context。 [`internal/logger/logger.go:511`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/logger/logger.go#L511)

```bash
sed -n '511,530p' internal/logger/logger.go
```

```output
func CloneContext(ctx context.Context) context.Context {
	newCtx := context.Background()

	for _, k := range types.ContextKeysClonedAcrossDetach() {
		if v := ctx.Value(k); v != nil {
			newCtx = context.WithValue(newCtx, k, v)
		}
	}

	// Preserve the active OpenTelemetry span across the rebuild. The Langfuse
	// *Trace handle above carries the trace id, but span PARENTING flows through
	// the OTel span context (trace.SpanFromContext), which CloneContext would
	// otherwise drop — orphaning child spans opened after a CloneContext (e.g.
	// the agent engine's agent.execute becoming a separate trace from the HTTP
	// root). Re-inject the recording span so children stitch to the same trace.
	if sp := trace.SpanFromContext(ctx); sp.IsRecording() {
		newCtx = trace.ContextWithSpan(newCtx, sp)
	}

	return newCtx
```

CloneContext 从 Background 重建允许复制的 identity/trace 字段，保留必要上下文但脱离原始取消链。setupSSEStream 再用 WithCancel 提供显式停止生成的控制。

请求连接、生成工作和持久 Message 是三个生命周期。后续 resume 恢复的是流事件读取，并不意味着重新运行模型或恢复被杀死的进程。

## 13 动态组装的问答流水线

查看 query 如何在阶段之间变成搜索结果、上下文和最终回答。

先读 ChatManage 的跨阶段状态。 [`internal/types/chat_manage.go:113`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/types/chat_manage.go#L113)

```bash
sed -n '113,154p' internal/types/chat_manage.go
```

```output
type PipelineState struct {
	RewriteQuery string      `json:"rewrite_query,omitempty"`
	Intent       QueryIntent `json:"intent,omitempty"`
	History      []*History  `json:"history,omitempty"`

	SearchResult         []*SearchResult   `json:"-"`
	RerankResult         []*SearchResult   `json:"-"`
	MergeResult          []*SearchResult   `json:"-"`
	Entity               []string          `json:"-"`
	EntityKBIDs          []string          `json:"-"`
	EntityKnowledge      map[string]string `json:"-"`
	GraphResult          *GraphData        `json:"-"`
	UserContent          string            `json:"-"`
	RenderedContexts     string            `json:"-"`
	ChatResponse         *ChatResponse     `json:"-"`
	ImageDescription     string            `json:"-"`
	QuotedContext        string            `json:"-"` // Quoted message text, injected at LLM prompt stage
	SystemPromptOverride string            `json:"-"`
	// MemoryPrompt is the long-term memory envelope appended to the system
	// prompt for this turn, empty when memory is off or nothing matched.
	MemoryPrompt string `json:"-"`
	// UsedMemories mirrors MemoryPrompt in structured form so the answer can
	// tell the user which memories it saw.
	UsedMemories UsedMemories `json:"-"`
}

// PipelineContext holds runtime context for the current pipeline execution.
type PipelineContext struct {
	EventBus      EventBusInterface `json:"-"`
	MessageID     string            `json:"-"`
	UserMessageID string            `json:"-"`
}

// ChatManage represents the full configuration, state and runtime context
// for a chat pipeline execution. It embeds PipelineRequest (immutable config),
// PipelineState (mutable intermediate data), and PipelineContext (runtime handles).
type ChatManage struct {
	PipelineRequest
	PipelineState
	PipelineContext
}

```

PipelineRequest、PipelineState、PipelineContext 组合成 ChatManage；SearchResult → RerankResult → MergeResult 表示不同处理阶段，EventBus 是输出通道。

再读运行时阶段选择。 [`internal/application/service/session_knowledge_qa.go:162`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/session_knowledge_qa.go#L162)

```bash
sed -n '162,207p' internal/application/service/session_knowledge_qa.go
```

```output

	// Determine pipeline based on the effective knowledge retrieval scope and
	// web search setting. Tag-only mentions leave the raw KB/knowledge ID slices
	// empty but produce SearchTargets, so the unified targets must participate in
	// this decision or the request is incorrectly downgraded to pure chat.
	hasKB := types.HasKnowledgeRetrievalScope(searchTargets, knowledgeBaseIDs, knowledgeIDs)
	needsRAG := hasKB || req.WebSearchEnabled
	hasHistory := chatManage.MaxRounds > 0

	var pipeline []types.EventType
	if !needsRAG {
		// Pure chat — no retrieval needed.
		userContent := req.Query
		if req.ImageDescription != "" && !chatModelSupportsVision {
			userContent += "\n\n[用户上传图片内容]\n" + req.ImageDescription
		}
		if req.QuotedContext != "" {
			userContent += "\n\n" + req.QuotedContext
		}
		// Inject attachment content for pure-chat path (RAG path handles this in INTO_CHAT_MESSAGE).
		if len(req.Attachments) > 0 {
			userContent += req.Attachments.BuildPrompt()
		}
		chatManage.UserContent = userContent

		pipeline = types.NewPipelineBuilder().
			AddIf(hasHistory, types.LOAD_HISTORY).
			Add(types.MEMORY_RECALL).
			Add(types.CHAT_COMPLETION_STREAM).
			Build()
	} else {
		// RAG — dynamically assemble based on feature flags.
		pipeline = types.NewPipelineBuilder().
			AddIf(hasHistory, types.LOAD_HISTORY).
			Add(types.MEMORY_RECALL).
			Add(types.QUERY_UNDERSTAND).
			Add(types.CHUNK_SEARCH_PARALLEL).
			Add(types.CHUNK_RERANK).
			AddIf(req.WebSearchEnabled, types.WEB_FETCH).
			Add(types.CHUNK_MERGE).
			Add(types.FILTER_TOP_K).
			AddIf(chatManage.DataAnalysisEnabled, types.DATA_ANALYSIS).
			Add(types.INTO_CHAT_MESSAGE).
			Add(types.CHAT_COMPLETION_STREAM).
			Build()
	}
```

没有知识检索 scope 且未开网页搜索时走纯聊天；有检索需求时按顺序装配历史、memory、query understand、parallel search、rerank、merge、top K、prompt 和 streaming。部分阶段仍会按配置跳过自己的工作。

检查 query-understand 阶段是否必然调用模型。 [`internal/application/service/chat_pipeline/query_understand.go:63`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/query_understand.go#L63)

```bash
sed -n '63,77p' internal/application/service/chat_pipeline/query_understand.go
```

```output
) *PluginError {
	chatManage.RewriteQuery = chatManage.Query

	hasImages := len(chatManage.Images) > 0
	needRewrite := chatManage.EnableRewrite
	if !needRewrite && !hasImages {
		pipelineInfo(ctx, "QueryUnderstand", "skip", map[string]interface{}{
			"session_id": chatManage.SessionID,
			"reason":     "rewrite_disabled_no_images",
		})
		return next()
	}

	pipelineInfo(ctx, "QueryUnderstand", "input", map[string]interface{}{
		"session_id":     chatManage.SessionID,
```

先把 RewriteQuery 初始化为原问题；未启用 rewrite 且无图片时直接 next。启用路径会准备历史和模型，解析结构化输出；因此阶段存在不等于每次都会多调用一次 LLM。

查明 EventManager 是否是远端消息队列。 [`internal/application/service/chat_pipeline/chat_pipeline.go:54`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/chat_pipeline.go#L54)

```bash
sed -n '54,83p' internal/application/service/chat_pipeline/chat_pipeline.go
```

```output
func (e *EventManager) buildHandler(plugins []Plugin) func(
	ctx context.Context, eventType types.EventType, chatManage *types.ChatManage,
) *PluginError {
	next := func(context.Context, types.EventType, *types.ChatManage) *PluginError { return nil }
	for i := len(plugins) - 1; i >= 0; i-- {
		current := plugins[i]
		prevNext := next
		next = func(ctx context.Context, eventType types.EventType, chatManage *types.ChatManage) *PluginError {
			return current.OnEvent(ctx, eventType, chatManage, func() *PluginError {
				return prevNext(ctx, eventType, chatManage)
			})
		}
	}
	return next
}

// Trigger invokes the handler for the specified event type
func (e *EventManager) Trigger(ctx context.Context,
	eventType types.EventType, chatManage *types.ChatManage,
) *PluginError {
	if handler, ok := e.handlers[eventType]; ok {
		return handler(ctx, eventType, chatManage)
	}
	return nil
}

// PluginError represents an error in plugin execution
type PluginError struct {
	Err         error  // Original error
	Description string // Human-readable description
```

buildHandler 在同一进程中组合 next 链，Trigger 直接同步调用对应 handler；它与前文 Asynq 任务队列不是一个机制。

观察 references 与回答的先后次序。 [`internal/application/service/session_knowledge_qa.go:720`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/session_knowledge_qa.go#L720)

```bash
sed -n '720,729p' internal/application/service/session_knowledge_qa.go
```

```output
		}
		// Emit references before answer streaming so the SSE client receives
		// them while the connection is still open. Previously references were
		// emitted after the pipeline returned — by then the `complete` event had
		// already closed the stream, so the frontend only saw citations on refresh.
		if eventType == types.CHAT_COMPLETION_STREAM {
			emitKnowledgeReferencesEvent(ctx, chatManage)
		}
		err := s.eventManager.Trigger(stageCtx, eventType, chatManage)
		if understandProgress != nil && eventType == types.QUERY_UNDERSTAND {
```

启动 CHAT_COMPLETION_STREAM 前先发检索引用，避免 complete 关流后引用才到达。阶段顺序属于对用户可见的协议。

流水线编排顺序由 service 的 builder 控制；容器中注册 plugin 的顺序不是整轮 RAG 的阶段顺序。真正的流消费阶段会启动异步 goroutine。

## 14 并行搜索与检索范围

向量检索不是一次无范围的全库相似度搜索：先解析调用者可访问的目标，再分组查询。

看外层 parallel search 如何隔离结果。 [`internal/application/service/chat_pipeline/search_parallel.go:90`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/search_parallel.go#L90)

```bash
sed -n '90,113p' internal/application/service/chat_pipeline/search_parallel.go
```

```output
func (p *PluginSearchParallel) OnEvent(ctx context.Context,
	eventType types.EventType, chatManage *types.ChatManage, next func() *PluginError,
) *PluginError {
	// Intent-based skip: query-understand step determined KB retrieval is unnecessary
	if !chatManage.NeedsRetrieval() {
		pipelineInfo(ctx, "SearchParallel", "skip", map[string]interface{}{
			"session_id": chatManage.SessionID,
			"reason":     "intent_no_search",
		})
		return next()
	}

	pipelineInfo(ctx, "SearchParallel", "start", map[string]interface{}{
		"session_id":    chatManage.SessionID,
		"has_entities":  len(chatManage.Entity) > 0,
		"rewrite_query": chatManage.RewriteQuery,
	})

	// Deep-copy to avoid concurrent read/write on shared slice fields
	chunkCM := chatManage.Clone()
	chunkCM.SearchResult = nil
	entityCM := chatManage.Clone()
	entityCM.SearchResult = nil

```

对 chunk/entity 分支 Clone ChatManage，并清空各自 SearchResult，降低并发修改共享切片的风险。无实体时 entity 分支可跳过。

找到 chunk search 对 HybridSearch 的真实调用。 [`internal/application/service/chat_pipeline/search.go:463`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/search.go#L463)

```bash
sed -n '463,481p' internal/application/service/chat_pipeline/search.go
```

```output
			// Combined search: one HybridSearch call spanning all full-KB targets
			if len(fullKBIDs) > 0 {
				innerWg.Add(1)
				go func() {
					defer innerWg.Done()

					params := types.SearchParams{
						QueryText:             queryText,
						QueryEmbedding:        queryEmbedding,
						KnowledgeBaseIDs:      fullKBIDs,
						VectorThreshold:       chatManage.VectorThreshold,
						KeywordThreshold:      chatManage.KeywordThreshold,
						MatchCount:            chatManage.EmbeddingTopK,
						SkipContextEnrichment: true,
						DisableVectorMatch:    disableVector,
					}
					res, err := p.knowledgeBaseService.HybridSearch(ctx, fullKBIDs[0], params)
					if err != nil {
						pipelineWarn(ctx, "Search", "combined_kb_search_error", map[string]interface{}{
```

同模型组中的完整 KB 可以合并查询，并传 QueryEmbedding 与 SkipContextEnrichment；按文档/标签限定的目标另行处理。

进入 HybridSearch 后检查授权与模型一致性。 [`internal/application/service/knowledgebase_search.go:147`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledgebase_search.go#L147)

```bash
sed -n '147,180p' internal/application/service/knowledgebase_search.go
```

```output
	// Batch-load every KB in scope. Required for store grouping,
	// embedding-model consistency validation, and FAQ type detection.
	// GetKnowledgeBaseByIDs is intentionally tenant-agnostic at the
	// repository layer so that Organization-shared KBs (owned by a
	// different tenant) can be loaded here; authorization for each
	// returned row is enforced explicitly below.
	kbs, err := s.repo.GetKnowledgeBaseByIDs(ctx, searchKBIDs)
	if err != nil {
		logger.ErrorWithFields(ctx, err, map[string]interface{}{
			"knowledge_base_ids": searchKBIDs,
		})
		return nil, err
	}
	if len(kbs) == 0 {
		return nil, apperrors.NewNotFoundError("knowledge base not found")
	}

	// Authorize every KB for the original caller, using exact upstream
	// grants or organization permissions. Execution in a shared tenant
	// does not grant access to its other KBs. Without this guard, a
	// caller could pass arbitrary KB UUIDs in params.KnowledgeBaseIDs
	// and reach foreign tenants' bound vector stores via the per-group
	// engine resolution downstream.
	if err := s.authorizeKBAccess(ctx, kbs); err != nil {
		return nil, err
	}

	// Explicit embedding-model consistency check. Multi-KB searches that
	// span different embedding spaces would otherwise silently produce
	// meaningless cross-model scores. Same-model wiki/graph KBs are
	// tolerated — see validateSameEmbeddingModel for the carve-out.
	if err := s.validateSameEmbeddingModel(ctx, kbs); err != nil {
		return nil, err
	}
```

可以跨租户加载共享 KB，但必须逐个验证原始调用者的访问权限；执行租户切换不会自动授权该租户其他 KB。同一次混合检索还要验证 embedding 空间一致。

看向量复用与存储分组。 [`internal/application/service/knowledgebase_search.go:198`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledgebase_search.go#L198)

```bash
sed -n '198,227p' internal/application/service/knowledgebase_search.go
```

```output
	// Compute the query embedding once before fan-out and propagate via
	// params.QueryEmbedding. Without this, each storeGroup's
	// buildRetrievalParams would re-embed the same query text — for N
	// stores that means N API calls of identical input.
	//
	// Skip when params already carries an embedding (e.g. the agent
	// pre-computed it) or when the primary KB has no vector indexing
	// configured.
	if len(params.QueryEmbedding) == 0 &&
		kb.IsVectorEnabled() && kb.EmbeddingModelID != "" &&
		!params.DisableVectorMatch {
		emb, embErr := s.GetQueryEmbedding(ctx, kb.ID, params.QueryText)
		if embErr != nil {
			return nil, embErr
		}
		params.QueryEmbedding = emb
	}

	// Group KBs by (storeID, owner tenant), resolve the bound engine for
	// each group, and build the per-group base RetrieveParams once.
	groups, err := s.resolveStoreGroups(ctx, kb, kbs, params, matchCount)
	if err != nil {
		return nil, err
	}
	if len(groups) == 0 || allBaseParamsEmpty(groups) {
		// Wiki-only / graph-only fan-out: every KB is non-retrievable.
		// Preserve the existing "return empty rather than error" contract
		// so agent tools that combine multiple KB scopes degrade gracefully.
		logger.Infof(ctx, "No retrievable indexing pipelines across %d KBs", len(kbs))
		return nil, nil
```

若上层尚未传 embedding，才在需要向量时计算一次，再按 `(storeID, owner tenant)` 分组；仅 Wiki/图谱而无可检索索引时可返回空结果。

看多存储查询的并发与错误传播。 [`internal/application/service/knowledgebase_search_fanout.go:64`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledgebase_search_fanout.go#L64)

```bash
sed -n '64,106p' internal/application/service/knowledgebase_search_fanout.go
```

```output
		all []*types.RetrieveResult
	)
	g, gctx := errgroup.WithContext(ctx)
	g.SetLimit(defaultMultiStoreFanoutLimit)

	for i := range groups {
		grp := groups[i]
		g.Go(func() error {
			gcCtx, cancel := context.WithTimeout(gctx, timeout)
			defer cancel()
			res, err := grp.Engine.Retrieve(gcCtx, paramsWithTopK(grp))
			if err != nil {
				logger.WarnWithFields(gctx, logger.Fields{
					"tenant_id":  grp.OwnerTenantID,
					"kb_count":   len(grp.KBIDs),
					"store_kind": storeKindLabel(grp.StoreID),
				}, fmt.Sprintf("multi-store retrieve failed: %v", err))
				return fmt.Errorf("store group retrieve: %w", err)
			}
			mu.Lock()
			all = append(all, res...)
			mu.Unlock()
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		// Only treat an explicit client cancellation (context.Canceled)
		// as "the client gave up". A parent context whose deadline has
		// expired must still surface as a typed unavailable error so the
		// handler returns a clean 4xx instead of leaking the raw stdlib
		// DeadlineExceeded.
		if isParentCancelled(ctx) {
			return nil, ctx.Err()
		}
		// Any retrieve failure (per-group timeout, transport error,
		// upstream rejection) is collapsed into a single typed
		// unavailable error. The underlying cause is recorded in
		// structured logs above; the response body intentionally exposes
		// no internal detail.
		return nil, apperrors.NewVectorStoreUnavailableError(
			"vector retrieval failed for one or more bound stores")
	}

```

errgroup 限制并发，每组有 timeout；任意 store 查询失败会让这次多存储调用返回 unavailable，而不是悄悄只返回成功存储的内容。更外层 pipeline 仍可能有其他独立结果，不能把局部契约扩大到所有搜索层。

权限、embedding 身份和存储位置共同决定合法检索范围；这些条件比“接了哪个向量数据库”更接近应用层核心设计。

## 15 从检索适配器到 RRF 融合

下钻一个具体 PostgreSQL/ParadeDB 适配器，证明向量和关键词检索实际如何发生，再回到后端无关的融合步骤。

查复合引擎的分派。 [`internal/application/service/retriever/composite.go:33`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/retriever/composite.go#L33)

```bash
sed -n '33,60p' internal/application/service/retriever/composite.go
```

```output
	retrieveParams []types.RetrieveParams,
) ([]*types.RetrieveResult, error) {
	return concurrentRetrieve(ctx, retrieveParams,
		func(ctx context.Context, param types.RetrieveParams, results *[]*types.RetrieveResult, mu *sync.Mutex) error {
			found := false
			for _, engineInfo := range c.engineInfos {
				if engineInfo == nil {
					continue
				}
				if slices.Contains(engineInfo.retrieverType, param.RetrieverType) {
					result, err := engineInfo.retrieveEngine.Retrieve(ctx, param)
					if err != nil {
						return err
					}
					mu.Lock()
					*results = append(*results, result...)
					mu.Unlock()
					found = true
					break
				}
			}
			if !found {
				return fmt.Errorf("retriever type %s not found", param.RetrieverType)
			}
			return nil
		},
	)
}
```

Retrieve 按 retriever type 找到支持该类型的 engine，交给 engine.Retrieve；Hybrid engine 的 Retrieve 再委托 repository。

检查 repository 的类型分支。 [`internal/application/repository/retriever/postgres/repository.go:150`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/repository/retriever/postgres/repository.go#L150)

```bash
sed -n '150,161p' internal/application/repository/retriever/postgres/repository.go
```

```output
func (g *pgRepository) Retrieve(ctx context.Context, params types.RetrieveParams) ([]*types.RetrieveResult, error) {
	logger.GetLogger(ctx).Debugf("[Postgres] Processing retrieval request of type: %s", params.RetrieverType)
	switch params.RetrieverType {
	case types.KeywordsRetrieverType:
		return g.KeywordsRetrieve(ctx, params)
	case types.VectorRetrieverType:
		return g.VectorRetrieve(ctx, params)
	}
	err := errors.New("invalid retriever type")
	logger.GetLogger(ctx).Errorf("[Postgres] %v: %s", err, params.RetrieverType)
	return nil, err
}
```

同一个 Postgres repository 明确分为 KeywordsRetrieve 和 VectorRetrieve。这里展示的是一个具体后端，不代表其他适配器使用相同 SQL。

核对关键词实现而不只读注释。 [`internal/application/repository/retriever/postgres/repository.go:196`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/repository/retriever/postgres/repository.go#L196)

```bash
sed -n '196,215p' internal/application/repository/retriever/postgres/repository.go
```

```output

	// Use ParadeDB's ||| operator for matching any token
	conds = append(conds, clause.Expr{
		SQL:  "content ||| ?",
		Vars: []interface{}{params.Query},
	})

	// Filter by is_enabled = true or NULL (NULL means enabled for historical data)
	conds = append(conds, clause.Expr{
		SQL:  "(is_enabled IS NULL OR is_enabled = ?)",
		Vars: []interface{}{true},
	})
	conds = append(conds, clause.OrderBy{Columns: []clause.OrderByColumn{
		{Column: clause.Column{Name: "score"}, Desc: true},
	}})

	var embeddingDBList []pgVectorWithScore
	err := g.db.WithContext(ctx).Clauses(conds...).Debug().
		Select([]string{
			"paradedb.score(id) as score",
```

代码使用 ParadeDB 的 `content ||| ?` 和 BM25 score，不能因为函数注释写 PostgreSQL full-text 就推断成通用 tsvector/ts_rank。

看向量排序的真实 SQL。 [`internal/application/repository/retriever/postgres/repository.go:377`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/repository/retriever/postgres/repository.go#L377)

```bash
sed -n '377,397p' internal/application/repository/retriever/postgres/repository.go | sed 's/[[:blank:]]*$//'
```

```output
	querySQL := fmt.Sprintf(`
		SELECT
			id, content, source_id, source_type, chunk_id, knowledge_id, knowledge_base_id, tag_id,
			(1 - distance) as score
		FROM (
			SELECT
				id, content, source_id, source_type, chunk_id, knowledge_id, knowledge_base_id, tag_id,
				embedding::halfvec(%[1]d) <=> $1::halfvec(%[1]d) as distance
			FROM embeddings
			%[2]s
			ORDER BY embedding::halfvec(%[1]d) <=> $1::halfvec(%[1]d)
			LIMIT $%[3]d
		) AS candidates
		WHERE distance <= $%[4]d
		ORDER BY distance ASC
		LIMIT $%[5]d
	`, dimension, whereClause, subqueryLimitParam, thresholdParam, finalLimitParam)

	allVars = append(allVars, expandedTopK)       // LIMIT in subquery
	allVars = append(allVars, 1-params.Threshold) // Distance threshold
	allVars = append(allVars, params.TopK)        // Final LIMIT
```

通过 halfvec 的 `<=>` 距离排序，外层施加距离阈值和最终 LIMIT，并把 1-distance 暴露为 score。是否实际命中 HNSW 还需部署环境的执行计划验证。

最后看 RRF 为什么不直接相加原始分数。 [`internal/application/service/knowledgebase_search_fusion.go:84`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledgebase_search_fusion.go#L84)

```bash
sed -n '84,128p' internal/application/service/knowledgebase_search_fusion.go
```

```output
func fuseWithRRF(ctx context.Context, vectorResults, keywordResults []*types.IndexWithScore, retrievalCfg *types.RetrievalConfig) []*types.IndexWithScore {
	rrfK := retrievalCfg.GetEffectiveRRFK()
	vectorWeight, keywordWeight := retrievalCfg.GetEffectiveRRFWeights()

	// Build rank maps for each retriever (already sorted by score from retriever)
	vectorRanks := make(map[string]int, len(vectorResults))
	for i, r := range vectorResults {
		if _, exists := vectorRanks[r.ChunkID]; !exists {
			vectorRanks[r.ChunkID] = i + 1 // 1-indexed rank
		}
	}
	keywordRanks := make(map[string]int, len(keywordResults))
	for i, r := range keywordResults {
		if _, exists := keywordRanks[r.ChunkID]; !exists {
			keywordRanks[r.ChunkID] = i + 1
		}
	}

	// Collect all unique chunks — prefer vector result's metadata for each chunk
	chunkInfoMap := make(map[string]*types.IndexWithScore)
	for _, r := range vectorResults {
		if existing, exists := chunkInfoMap[r.ChunkID]; !exists || r.Score > existing.Score {
			chunkInfoMap[r.ChunkID] = r
		}
	}
	for _, r := range keywordResults {
		if _, exists := chunkInfoMap[r.ChunkID]; !exists {
			chunkInfoMap[r.ChunkID] = r
		}
	}

	// Compute weighted RRF scores and assign to each chunk
	result := make([]*types.IndexWithScore, 0, len(chunkInfoMap))
	for chunkID, info := range chunkInfoMap {
		rrfScore := 0.0
		if rank, ok := vectorRanks[chunkID]; ok {
			rrfScore += vectorWeight / float64(rrfK+rank)
		}
		if rank, ok := keywordRanks[chunkID]; ok {
			rrfScore += keywordWeight / float64(rrfK+rank)
		}
		info.Score = rrfScore
		result = append(result, info)
	}
	slices.SortFunc(result, sortByScoreDesc)
```

按 ChunkID 记录每路从 1 开始的排名，再计算 `vectorWeight/(k+vectorRank) + keywordWeight/(k+keywordRank)`；某块只出现一路就贡献一路分数，避免直接混用 BM25 与向量相似度量纲。若整次搜索只有一种结果，fuseOrDeduplicate（同文件 33–48 行）保留该路分数并去重，不走 RRF。

融合后的候选还要回查 Chunk/Knowledge 并经过状态过滤。检索命中向量库不等于可以直接把存储里的那段旧正文送给用户。

## 16 重排与上下文还原

从候选列表到 prompt，关注相关性、父块/邻块扩展，以及编辑后的旧索引。

先看回查正文的可搜索条件。 [`internal/application/service/knowledgebase_search_results.go:337`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledgebase_search_results.go#L337)

```bash
sed -n '337,354p' internal/application/service/knowledgebase_search_results.go
```

```output
func (s *knowledgeBaseService) isSearchableChunk(chunk *types.Chunk) bool {
	if chunk == nil || !chunk.IsEnabled {
		return false
	}
	// An edit is persisted before its retrieval artifacts are synchronized.
	// Do not hydrate stale vector hits while that synchronization is pending or
	// failed. Empty is accepted for legacy rows created before index_status.
	if chunk.IndexStatus == "processing" || chunk.IndexStatus == "failed" {
		return false
	}
	return slices.Contains([]types.ChunkType{
		types.ChunkTypeText, types.ChunkTypeSummary,
		types.ChunkTypeTableColumn, types.ChunkTypeTableSummary,
		types.ChunkTypeFAQ,
		types.ChunkTypeImageOCR, types.ChunkTypeImageCaption,
	}, chunk.ChunkType)
}
```

IsEnabled 必须为真；IndexStatus 为 processing/failed 的编辑中块被丢弃，legacy 空值仍被接受。它阻止未同步编辑的旧命中被回填到回答。

确认 rerank 是否强制。 [`internal/application/service/chat_pipeline/rerank.go:51`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/rerank.go#L51)

```bash
sed -n '51,72p' internal/application/service/chat_pipeline/rerank.go
```

```output
	if len(chatManage.SearchResult) == 0 {
		pipelineInfo(ctx, "Rerank", "skip", map[string]interface{}{
			"reason": "empty_search_result",
		})
		return next()
	}
	if chatManage.RerankModelID == "" {
		pipelineWarn(ctx, "Rerank", "skip", map[string]interface{}{
			"reason": "empty_model_id",
		})
		return next()
	}

	// Get rerank model from service
	rerankModel, err := p.modelService.GetRerankModel(ctx, chatManage.RerankModelID)
	if err != nil {
		pipelineError(ctx, "Rerank", "get_model", map[string]interface{}{
			"model_id": chatManage.RerankModelID,
			"error":    err.Error(),
		})
		return ErrGetRerankModel.WithError(err)
	}
```

空结果或未配置模型时跳过；获取已配置的 rerank 模型失败则产生明确的 PluginError。因此图中将其标成按配置执行。

看 Merge 如何把小块还原为足够的上下文。 [`internal/application/service/chat_pipeline/merge.go:76`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/merge.go#L76)

```bash
sed -n '76,96p' internal/application/service/chat_pipeline/merge.go
```

```output
	// Step 4: Resolve parent chunks
	searchResult = p.resolveParentChunks(ctx, chatManage, searchResult)

	// Step 5: Group by knowledge/chunkType and merge overlapping ranges
	mergedChunks := p.groupAndMergeCurrentContent(ctx, searchResult)

	// Step 6: Populate FAQ answers
	mergedChunks = p.populateFAQAnswers(ctx, chatManage, mergedChunks)

	// Step 7: Expand short contexts
	mergedChunks = p.expandShortContextWithNeighbors(ctx, chatManage, mergedChunks)

	// Step 7.5: Re-merge overlapping ranges introduced by expansion
	mergedChunks = p.groupAndMergeCurrentContent(ctx, mergedChunks)

	// Step 8: Final dedup — catches exact duplicates plus partial content overlaps
	mergedChunks = p.dedup(ctx, "final_dedup", mergedChunks)
	mergedChunks = removePartialOverlaps(ctx, mergedChunks)

	chatManage.MergeResult = mergedChunks
	return next()
```

先 resolve parent，再按文档和类型合并范围、填充 FAQ、扩展短上下文，之后重新合并去重。返回的是 MergeResult，不是简单截断初始 search list。

看正文被装入 prompt 的位置。 [`internal/application/service/chat_pipeline/into_chat_message.go:155`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/into_chat_message.go#L155)

```bash
sed -n '155,176p' internal/application/service/chat_pipeline/into_chat_message.go
```

```output
	} else {
		for i, result := range chatManage.MergeResult {
			passage := getEnrichedPassageForChat(ctx, result)
			if i > 0 {
				contextsBuilder.WriteString("\n")
			}
			contextsBuilder.WriteString(fmt.Sprintf("<context id=\"%d\">%s</context>", i+1, passage))
		}
	}

	chatManage.RenderedContexts = contextsBuilder.String()

	// Replace placeholders in context template
	userContent := types.RenderPromptPlaceholders(chatManage.SummaryConfig.ContextTemplate, types.PlaceholderValues{
		"query":    safeQuery,
		"contexts": chatManage.RenderedContexts,
		"language": chatManage.Language,
	})

	// Append image description as text fallback only when the chat model cannot
	// process images directly. Vision-capable models see images via MultiContent.
	if chatManage.ImageDescription != "" && !chatManage.ChatModelSupportsVision {
```

常规文档路径按 context id 包装 passage，写到 RenderedContexts，再替换 ContextTemplate 的 query/contexts/language。这一步把检索结果真正连到生成输入。

上层后续 FILTER_TOP_K 再控制送入模型的结果规模。引用标签提供可追溯输入，但并不构成模型答案一定正确的证明。

## 17 模型流如何变成回答事件

追踪 ChatStream 到事件总线，说明为什么 KnowledgeQA 返回不代表回答已结束。

准备模型消息。 [`internal/application/service/chat_pipeline/chat_completion_stream.go:49`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/chat_completion_stream.go#L49)

```bash
sed -n '49,60p' internal/application/service/chat_pipeline/chat_completion_stream.go
```

```output
	// Prepare chat model and options
	chatModel, opt, err := prepareChatModel(ctx, p.modelService, chatManage)
	if err != nil {
		return ErrGetChatModel.WithError(err)
	}

	// Prepare base messages without history

	chatMessages, modelContext := prepareMessagesWithModelContext(ctx, chatManage)
	chatMessages = modelContext.EncodeMessages(chatMessages)
	ctx = withPromptCacheMetadata(ctx, chatModel, chatMessages, opt, "knowledge_qa")
	pipelineInfo(ctx, "Stream", "messages_ready", map[string]interface{}{
```

聊天模型来自 modelService，messages 先由 modelcontext 编码并附加 prompt cache 元数据。模型适配器可以在这层更换。

启动 ChatStream 并建立消费者。 [`internal/application/service/chat_pipeline/chat_completion_stream.go:80`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/chat_completion_stream.go#L80)

```bash
sed -n '80,112p' internal/application/service/chat_pipeline/chat_completion_stream.go
```

```output
	// Initiate streaming chat model call with independent context
	pipelineInfo(ctx, "Stream", "model_call", map[string]interface{}{
		"chat_model": chatManage.ChatModelID,
	})
	responseChan, err := chatModel.ChatStream(ctx, chatMessages, opt)
	if err != nil {
		pipelineError(ctx, "Stream", "model_call", map[string]interface{}{
			"chat_model": chatManage.ChatModelID,
			"error":      err.Error(),
		})
		return ErrModelCall.WithError(err)
	}
	if responseChan == nil {
		pipelineError(ctx, "Stream", "model_call", map[string]interface{}{
			"chat_model": chatManage.ChatModelID,
			"error":      "nil_channel",
		})
		return ErrModelCall.WithError(errors.New("chat stream returned nil channel"))
	}

	pipelineInfo(ctx, "Stream", "model_started", map[string]interface{}{
		"session_id": chatManage.SessionID,
	})

	// Start goroutine to consume channel and emit events directly.
	// reasoning_content is routed to EventAgentThought (SSE response_type=thinking)
	// and plain answer text to EventAgentFinalAnswer, matching the Agent pipeline.
	// The goroutine monitors ctx.Done() to avoid leaking when the context is cancelled
	// and the upstream channel is not closed promptly.
	go func() {
		answerDecoder := modelContext.StreamDecoder()
		thinkingDecoder := modelContext.StreamDecoder()
		thinkingID := fmt.Sprintf("%s-thinking", uuid.New().String()[:8])
```

ChatStream 返回 channel，plugin 启动 goroutine 消费，因此 OnEvent/KnowledgeQA 可先返回。错误创建流则同步返回 PluginError。

查看 RemoteAPIChat 的 HTTP/SDK 分支。 [`internal/models/chat/remote_api.go:277`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/models/chat/remote_api.go#L277)

```bash
sed -n '277,292p' internal/models/chat/remote_api.go
```

```output
func (c *RemoteAPIChat) ChatStream(ctx context.Context, messages []Message, opts *ChatOptions) (<-chan types.StreamResponse, error) {
	// 仅在调用方未设置 deadline 时附加兜底超时；流式调用默认超时更长，
	// 因为带思考/推理的模型可能数十秒甚至几分钟才产出首 token。
	timeoutCtx, cancel := withLLMTimeout(ctx, defaultStreamTimeout)

	body, endpoint, useRawHTTP, err := c.buildOutbound(timeoutCtx, messages, opts, true)
	if err != nil {
		cancel()
		return nil, err
	}
	if useRawHTTP {
		ch, err := c.chatStreamWithRawHTTP(timeoutCtx, endpoint, body, opts)
		return wrapStreamCancel(ch, err, cancel)
	}

	req := *(body.(*openai.ChatCompletionRequest))
```

buildOutbound 决定走 raw HTTP 还是 SDK 路径。不能将所有 provider 都描述成同一个 OpenAI SDK 调用。

沿其中一个 SDK 分支追到网络调用。 [`internal/models/chat/remote_api.go:302`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/models/chat/remote_api.go#L302)

```bash
sed -n '302,327p' internal/models/chat/remote_api.go
```

```output
	stream, err := c.client.CreateChatCompletionStream(timeoutCtx, req)
	if err != nil {
		if isMultimodalNotSupportedError(err) {
			logger.Warnf(timeoutCtx, "[LLM Stream] Model %s does not support multimodal, retrying without images", c.modelName)
			cleaned := stripImagesFromMessages(messages)
			req = c.shapedRequest(cleaned, opts, true)
			stream, err = c.client.CreateChatCompletionStream(timeoutCtx, req)
		}
		if err != nil {
			cancel()
			close(streamChan)
			return nil, fmt.Errorf("create chat completion stream: %w", err)
		}
	}

	go func() {
		defer cancel()
		if streamDumper != nil {
			defer streamDumper.Close()
		}
		c.processStream(timeoutCtx, stream, streamChan, streamDumper)
	}()

	return streamChan, nil
}

```

SDK CreateChatCompletionStream 发出请求；特定多模态不支持错误会去掉图片重试；消费者结束时释放 timeout context。这里是实现证据，并未真的调用模型。

检查最终 answer 的 Done 标志。 [`internal/application/service/chat_pipeline/chat_completion_stream.go:218`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/chat_pipeline/chat_completion_stream.go#L218)

```bash
sed -n '218,248p' internal/application/service/chat_pipeline/chat_completion_stream.go
```

```output

				if response.ResponseType == types.ResponseTypeAnswer {
					// Providers can emit a completion once for finish_reason and again
					// for their EOF sentinel. A final answer is a terminal event for a
					// single stream, so forwarding a later duplicate would put an answer
					// after the session's complete event.
					if answerCompleted {
						continue
					}
					response.Content = answerDecoder.Feed(response.Content)
					if response.Done {
						response.Content += answerDecoder.Flush()
						answerCompleted = true
					}
					closeThinking()
					eventBus.Emit(ctx, types.Event{
						ID:        answerID,
						Type:      types.EventType(event.EventAgentFinalAnswer),
						SessionID: chatManage.SessionID,
						Data: event.AgentFinalAnswerData{
							Content: response.Content,
							Done:    response.Done,
						},
					})
				}
			}
		}
	}()

	return next()
}
```

流响应转为 EventAgentFinalAnswer；decoder flush 保留末尾内容，answerCompleted 丢弃重复终止后的 answer。模型输出事件与会话 complete 仍是两个相邻步骤。

这是一条“模型 channel → EventBus → Message/StreamManager”的桥接链，而不是 handler 一边阻塞调用模型一边直接写 socket。

## 18 持久化与断线续读的边界

将用户看到的流、数据库保存的消息和显式停止操作闭合起来。

看快速问答收到 Done 后的操作顺序。 [`internal/handler/session/qa.go:1167`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/session/qa.go#L1167)

```bash
sed -n '1167,1192p' internal/handler/session/qa.go
```

```output
		streamCtx.eventBus.On(event.EventAgentFinalAnswer, func(ctx context.Context, evt event.Event) error {
			data, ok := evt.Data.(event.AgentFinalAnswerData)
			if !ok {
				return nil
			}
			streamCtx.assistantMessage.Content += data.Content
			if data.IsFallback {
				streamCtx.assistantMessage.IsFallback = true
			}
			if data.Done {
				if completionHandled {
					return nil
				}
				completionHandled = true

				logger.Infof(streamCtx.asyncCtx, "Knowledge QA service completed for session: %s", sessionID)
				updateCtx := context.WithValue(streamCtx.asyncCtx, types.TenantIDContextKey, reqCtx.session.TenantID)
				h.completeAssistantMessage(updateCtx, streamCtx.assistantMessage, reqCtx.query, reqCtx.userMessageID)
				streamCtx.eventBus.Emit(streamCtx.asyncCtx, event.Event{
					Type:      event.EventAgentComplete,
					SessionID: sessionID,
					Data:      event.AgentCompleteData{FinalAnswer: streamCtx.assistantMessage.Content},
				})
			}
			return nil
		})
```

累加 Content，在首次 Done 时 completeAssistantMessage，再发 EventAgentComplete。局部布尔值防止重复完成事件，但不是数据库事务。

核对 completeAssistantMessage 是否保证写库成功。 [`internal/handler/session/qa.go:1665`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/session/qa.go#L1665)

```bash
sed -n '1665,1674p' internal/handler/session/qa.go
```

```output
func (h *Handler) completeAssistantMessage(
	ctx context.Context, assistantMessage *types.Message, userQuery, userMessageID string,
) {
	assistantMessage.UpdatedAt = time.Now()
	assistantMessage.IsCompleted = true
	_ = h.messageService.UpdateMessage(ctx, assistantMessage)

	// Asynchronously index the Q&A pair into the chat history knowledge base for vector search.
	// Use WithoutCancel so the goroutine survives after the HTTP request context is done.
	bgCtx := context.WithoutCancel(ctx)
```

代码将 IsCompleted 设为 true 后调用 UpdateMessage，并忽略返回 error。因此 complete 事件不是“数据库一定写入成功”的确认。

查看回答事件怎样进入流缓冲。 [`internal/handler/session/agent_stream_handler.go:551`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/session/agent_stream_handler.go#L551)

```bash
sed -n '551,568p' internal/handler/session/agent_stream_handler.go
```

```output
		}
	}
	if data.IsFallback {
		metadata["is_fallback"] = true
	}
	h.mu.Unlock()

	// Append this chunk to stream (frontend will accumulate by event ID)
	if err := h.streamManager.AppendEvent(h.ctx, h.sessionID, h.assistantMessageID, interfaces.StreamEvent{
		ID:        evt.ID,
		Type:      types.ResponseTypeAnswer,
		Content:   data.Content, // Just this chunk
		Done:      data.Done,
		Timestamp: time.Now(),
		Data:      metadata,
	}); err != nil {
		logger.GetLogger(h.ctx).Error("Append answer event to stream failed", "error", err)
	}
```

AgentStreamHandler 把回答写为 StreamEvent 并调用 StreamManager.AppendEvent；其名称含 Agent，但也为快速问答的统一事件流服务。

确认缓冲选择不是仅由 REDIS_ADDR 决定。 [`internal/stream/factory.go:18`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/stream/factory.go#L18)

```bash
sed -n '18,44p' internal/stream/factory.go
```

```output
func NewStreamManager() (interfaces.StreamManager, error) {
	switch os.Getenv("STREAM_MANAGER_TYPE") {
	case TypeRedis:
		db, err := strconv.Atoi(os.Getenv("REDIS_DB"))
		if err != nil {
			db = 0
		}
		// Default 1h. Live-run keys are refreshed while the turn is still
		// streaming (AppendEvent / GetEvents / steer writes), so a run that
		// lasts longer than this TTL does not look idle to /steer.
		ttl := time.Hour
		return NewRedisStreamManager(
			os.Getenv("REDIS_ADDR"),
			os.Getenv("REDIS_USERNAME"),
			os.Getenv("REDIS_PASSWORD"),
			db,
			os.Getenv("REDIS_PREFIX"),
			ttl,
		)
	default:
		return NewMemoryStreamManager(), nil
	}
}
```

`STREAM_MANAGER_TYPE=redis` 才选 Redis，默认内存；Redis 路径配置一小时 TTL。任务队列后端与流缓冲后端是两个开关。

看 ContinueStream 如何恢复。 [`internal/handler/session/stream.go:114`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/session/stream.go#L114)

```bash
sed -n '114,130p' internal/handler/session/stream.go
```

```output

	// Get initial events from stream (offset 0)
	events, currentOffset, err := h.streamManager.GetEvents(ctx, sessionID, messageID, 0)
	if err != nil {
		logger.ErrorWithFields(ctx, err, nil)
		c.Error(errors.NewInternalServerError(fmt.Sprintf("Failed to get stream data: %s", err.Error())))
		return
	}

	if len(events) == 0 {
		logger.Warnf(ctx, "No events found in stream, session ID: %s, message ID: %s", sessionID, messageID)
		c.JSON(http.StatusNotFound, gin.H{
			"success": false,
			"error":   "No stream events found",
		})
		return
	}
```

从 offset 0 读取该 session/message 的事件，没事件返回 404；它先回放，再继续读新事件，不重新执行 RAG。内存丢失或缓存过期后不能凭这个端点恢复进程内生成。

查看停止信号最终如何取消生成。 [`internal/handler/session/helpers.go:318`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/session/helpers.go#L318)

```bash
sed -n '318,338p' internal/handler/session/helpers.go
```

```output
func (h *Handler) setupStopEventHandler(
	eventBus *event.EventBus,
	sessionID string,
	sessionTenantID uint64,
	assistantMessage *types.Message,
	cancel context.CancelFunc,
) {
	eventBus.On(event.EventStop, func(ctx context.Context, evt event.Event) error {
		logger.Infof(ctx, "Received stop event, cancelling async operations for session: %s", sessionID)
		cancel()
		// Preserve whatever has been streamed so far; do not overwrite Content.
		// Use session's tenant for message update (ctx may have effectiveTenantID when using shared agent).
		// Use WithoutCancel so the GORM UPDATE survives the upcoming ctx.Done triggered by cancel()/client disconnect.
		updateCtx := context.WithValue(
			context.WithoutCancel(ctx),
			types.TenantIDContextKey, sessionTenantID,
		)
		h.completeAssistantMessage(updateCtx, assistantMessage, "", "") // empty query: stopped conversations are not indexed
		return nil
	})
}
```

EventStop 回调调用 cancel，并保留已经流出的正文，以 WithoutCancel 的 context 尝试完成消息写入。停止行为与网络断开分离，写库仍沿用同一个错误处理约定。

检查停止与空检索回退的次序。 [`internal/application/service/session_knowledge_qa.go:759`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/session_knowledge_qa.go#L759)

```bash
sed -n '759,787p' internal/application/service/session_knowledge_qa.go
```

```output
		// Otherwise we would persist the fixed fallback response ("Sorry, I am
		// unable to answer this question.") over the intentionally-empty stopped
		// message, and the user would see the fallback text after refreshing.
		// This is not single-machine specific: the stop arrives via the shared
		// StreamManager and cancels asyncCtx on whichever node is generating.
		if ctxErr := ctx.Err(); ctxErr != nil {
			common.PipelineWarn(ctx, "Pipeline", "stage_cancelled", map[string]interface{}{
				"event":       string(eventType),
				"duration_ms": stageDuration.Milliseconds(),
				"reason":      ctxErr.Error(),
			})
			return ctxErr
		}

		if err == chatpipeline.ErrSearchNothing {
			common.PipelineWarn(ctx, "Pipeline", "stage_fallback", map[string]interface{}{
				"event":       string(eventType),
				"duration_ms": stageDuration.Milliseconds(),
				"reason":      "search_nothing",
				"strategy":    string(chatManage.FallbackStrategy),
			})
			s.handleFallbackResponse(ctx, chatManage)
			return nil
		}

		if err != nil {
			common.PipelineError(ctx, "Pipeline", "stage_failed", map[string]interface{}{
				"event":       string(eventType),
				"duration_ms": stageDuration.Milliseconds(),
```

ctx.Err 优先于 ErrSearchNothing，避免用户停止后把空结果解释成“没找到”并写入兜底回答。真正空检索才进入固定或模型 fallback。

显式 Stop 通过 StreamManager 发停止信号（stream.go:296），独立 watcher 取消 asyncCtx（qa.go:737–748）；断连自身并不等于 Stop。持久消息、有限寿命的事件缓冲、任务恢复应分别评估。

## 19 Agent 是另一种编排方式

主线结束后用最小证据定位 ReAct 扩展：复用模型、检索与流事件，但控制循环不同。

看 Agent 入口何时仍会选择快速问答。 [`internal/handler/session/qa.go:946`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/handler/session/qa.go#L946)

```bash
sed -n '946,960p' internal/handler/session/qa.go
```

```output
		h.executeQA(reqCtx, qaModeAgent, true)
	} else {
		logger.Infof(reqCtx.ctx, "Agent mode disabled, delegating to normal mode for session: %s", reqCtx.sessionID)
		h.executeQA(reqCtx, qaModeNormal, !request.DisableTitle)
	}
}

// qaMode determines which QA execution path to use.
type qaMode int

const (
	qaModeNormal qaMode = iota // KnowledgeQA pipeline (RAG / pure chat)
	qaModeAgent                // Agent engine with tool calling
)

```

agentModeEnabled 为真才用 qaModeAgent，否则进入同一个 qaModeNormal。名称和页面配置不能替代实际分支条件。

追到 service 对引擎的调用。 [`internal/application/service/session_agent_qa.go:267`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/session_agent_qa.go#L267)

```bash
sed -n '267,276p' internal/application/service/session_agent_qa.go
```

```output

	// Execute agent with streaming (asynchronously)
	// Events will be emitted to EventBus and handled by the Handler layer
	logger.Info(ctx, "Executing agent with streaming")
	if _, err := engine.Execute(ctx, sessionID, req.AssistantMessageID, agentQuery, llmContext, agentImageURLs); err != nil {
		logger.Errorf(ctx, "Agent execution failed: %v", err)
		// Emit error event to the EventBus used by this agent
		eventBus.Emit(ctx, event.Event{
			Type:      event.EventError,
			SessionID: sessionID,
```

AgentQA 调用 engine.Execute，传 session、assistant message、query、LLM context 和图片；这是与固定 pipeline 不同的编排入口。

看 Agent 循环的边界与迭代。 [`internal/agent/engine.go:496`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/agent/engine.go#L496)

```bash
sed -n '496,529p' internal/agent/engine.go
```

```output
	for e.withinIterationBudget(state.CurrentRound) || e.allowSteerOverrun {
		e.allowSteerOverrun = false
		// Check for context cancellation (request timeout, user cancel, etc.)
		select {
		case <-ctx.Done():
			logger.Warnf(ctx, "[Agent] Context cancelled at round %d: %v",
				state.CurrentRound+1, ctx.Err())
			// Try to salvage existing results
			if totalTC := countTotalToolCalls(state.RoundSteps); totalTC > 0 {
				logger.Infof(ctx, "[Agent] Synthesizing final answer from %d existing tool results",
					totalTC)
				_ = e.streamFinalAnswerToEventBus(ctx, query, state, sessionID)
				state.IsComplete = true
			}
			return state, ctx.Err()
		default:
		}

		// A slow startup, OAuth discovery or explicit refresh may have produced
		// new definitions since the previous response. Publish them only here,
		// after all previous tool calls have finished, and rebuild the wire list.
		if e.toolRegistry != nil {
			e.toolRegistry.RefreshMCPTools(ctx)
			tools = e.buildToolsForLLM()
		}

		// Each iteration runs inside an "agent.round.<N>" Langfuse span.
		// We execute the body in a closure so `defer span.Finish()` fires at
		// every exit path (break/continue/next) without having to sprinkle
		// manual finish calls throughout the many branches below.
		outcome, iterErr := e.runReActIteration(ctx, state, &messages, tools,
			sessionID, messageID, query, &emptyRetries, &consecutiveSameContent, &lastResponseContent)
		if iterErr != nil {
			return state, iterErr
```

每轮检查 context，刷新 MCP 工具定义，并调用 runReActIteration。budget 还允许特定 steering overrun，不能简单说轮数永远严格等于 MaxIterations。

检查一轮 ReAct 的模型调用。 [`internal/agent/engine.go:665`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/agent/engine.go#L665)

```bash
sed -n '665,673p' internal/agent/engine.go
```

```output

	// 1. Think: Call LLM with function calling (includes retry + graceful degradation)
	e.lastSentMsgCount = len(*messagesPtr)
	resp, err := e.callLLMWithRetry(ctx, messagesPtr, tools, state, query, state.CurrentRound, sessionID)
	if err != nil {
		retErr = err
		return iterOutcomeNext, err
	}
	if resp == nil {
```

callLLMWithRetry 接收当前 messages 和工具描述；模型返回后才决定是否执行工具。这是工具调用结果能够改变后续模型输入的控制交接。

看工具执行后如何回到下一轮。 [`internal/agent/engine.go:832`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/agent/engine.go#L832)

```bash
sed -n '832,843p' internal/agent/engine.go
```

```output

	// 3. Act: Execute tool calls
	e.executeToolCalls(ctx, response, &step, state.CurrentRound, sessionID, assistantMessageID)
	toolCallCount = len(step.ToolCalls)

	// 4. Observe: Add tool results to messages and write to context
	state.RoundSteps = append(state.RoundSteps, step)
	*messagesPtr = e.appendToolResults(*messagesPtr, step)
	common.PipelineInfo(ctx, "Agent", "round_end", map[string]interface{}{
		"iteration":   state.CurrentRound,
		"round":       round,
		"tool_calls":  toolCallCount,
```

executeToolCalls 执行 tool calls，appendToolResults 把观察结果追加到 messages，随后继续迭代。工具注册、授权、MCP 与 sandbox 是重要扩展边界，本次不逐项审计。

固定 RAG 流水线由应用预设阶段；Agent 循环让模型决定调用哪些可用工具，并将工具结果接回上下文。Wiki 自动生成和长程记忆也有自己的任务链，不能用本图的普通文档问答流程替代解释。

## 20 测试证据与覆盖边界

以下展示测试表达的预期，不宣称测试已运行。本环境未发现 Go 工具链，未启动数据库、Redis、DocReader 或模型服务。

先核对文件保存失败时的预期。 [`internal/application/service/knowledge_create_test.go:130`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledge_create_test.go#L130)

```bash
sed -n '130,157p' internal/application/service/knowledge_create_test.go
```

```output
func TestCreateKnowledgeFromFileDoesNotPersistWhenStorageSaveFails(t *testing.T) {
	t.Parallel()

	repo := &createKnowledgeFileRepoStub{}
	fileSvc := &createKnowledgeFileServiceStub{saveErr: errors.New("storage unavailable")}
	svc := &knowledgeService{
		repo:      repo,
		kbService: &createKnowledgeFileKBServiceStub{kb: &types.KnowledgeBase{ID: "kb-1"}},
		fileSvc:   fileSvc,
	}

	knowledge, err := svc.CreateKnowledgeFromFile(
		newCreateKnowledgeFileContext(),
		"kb-1",
		newMultipartFileHeader(t, "doc.txt", "hello"),
		nil,
		nil,
		"",
		nil,
		"",
		nil,
	)

	require.Error(t, err)
	require.Nil(t, knowledge)
	require.Equal(t, 1, fileSvc.saveCalls)
	require.Zero(t, repo.createCalls)
}
```

测试要求返回 error/nil Knowledge，调用一次 SaveFile，并且不能先 CreateKnowledge；与第五节的保存顺序对应。

再核对分块算法跨策略的定位。 [`internal/infrastructure/chunker/strategy_test.go:77`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/infrastructure/chunker/strategy_test.go#L77)

```bash
sed -n '77,112p' internal/infrastructure/chunker/strategy_test.go
```

```output
func TestSplit_PreservesPositionInvariantAcrossTiers(t *testing.T) {
	cases := map[string]string{
		"heading-tier": "# Top\nintro paragraph here.\n\n## Section A\nbody A here.\n\n## Section B\nbody B here.\n\n## Section C\nbody C.",
		"heuristic-tier": strings.Repeat("Kapitel 1: Einleitung\n", 1) + strings.Repeat("Beispieltext. ", 50) +
			"\n\n" + strings.Repeat("Kapitel 2: Hauptteil\n", 1) + strings.Repeat("Mehr Text. ", 50),
		"recursive-tier": strings.Repeat("plain prose without structure. ", 100),
	}
	cfg := SplitterConfig{ChunkSize: 300, ChunkOverlap: 30, Separators: []string{"\n\n", "\n", "。", ". "}, Strategy: StrategyAuto}

	for name, doc := range cases {
		t.Run(name, func(t *testing.T) {
			runes := []rune(doc)
			chunks := Split(doc, cfg)
			if len(chunks) == 0 {
				t.Fatal("expected chunks")
			}
			for i, c := range chunks {
				contentRuneLen := len([]rune(c.Content))
				spanLen := c.End - c.Start
				if spanLen != contentRuneLen {
					t.Errorf("chunk %d: End(%d)-Start(%d)=%d but Content has %d runes:\n%q",
						i, c.End, c.Start, spanLen, contentRuneLen, c.Content)
				}
				if c.Start < 0 || c.End > len(runes) {
					t.Errorf("chunk %d: position out of range Start=%d End=%d totalRunes=%d",
						i, c.Start, c.End, len(runes))
				}
				if c.Start >= 0 && c.End <= len(runes) {
					sliced := string(runes[c.Start:c.End])
					if sliced != c.Content {
						t.Errorf("chunk %d: runes[Start:End] differs from Content", i)
					}
				}
			}
		})
	}
```

测试覆盖多个结构类型，要求 End-Start 等于 rune 长度，且规范文本对应切片与 Content 一致。这是位置协议的直接预期证据。

最后核对编辑与禁用状态的检索过滤。 [`internal/application/service/knowledgebase_search_results_edit_test.go:9`](https://github.com/Tencent/WeKnora/blob/5db13a131e10e8ee2105211f665412ebc13bd98e/internal/application/service/knowledgebase_search_results_edit_test.go#L9)

```bash
sed -n '9,36p' internal/application/service/knowledgebase_search_results_edit_test.go
```

```output
func TestIsSearchableChunkSkipsUnsynchronizedEdits(t *testing.T) {
	service := &knowledgeBaseService{}
	for _, status := range []string{"processing", "failed"} {
		chunk := &types.Chunk{ChunkType: types.ChunkTypeText, IndexStatus: status, IsEnabled: true}
		if service.isSearchableChunk(chunk) {
			t.Fatalf("chunk with index status %q should not be searchable", status)
		}
	}
	for _, status := range []string{"", "ready"} {
		chunk := &types.Chunk{ChunkType: types.ChunkTypeText, IndexStatus: status, IsEnabled: true}
		if !service.isSearchableChunk(chunk) {
			t.Fatalf("chunk with index status %q should be searchable", status)
		}
	}
}

func TestIsSearchableChunkSkipsDisabledChunk(t *testing.T) {
	service := &knowledgeBaseService{}
	chunk := &types.Chunk{
		ChunkType:   types.ChunkTypeFAQ,
		IndexStatus: "ready",
		IsEnabled:   false,
	}
	if service.isSearchableChunk(chunk) {
		t.Fatal("disabled FAQ chunk should never be searchable")
	}
}
```

processing/failed 不可检索，ready/legacy 可接受，禁用 FAQ 被拒绝。测试是局部状态不变量，不足以证明多节点下没有竞态。

实现层面的取舍：接口隔离使解析、模型和检索存储可替换；Chunk/IndexInfo 把全文内容与检索投影分开；任务状态与流缓冲让后台处理和断连续读可用。代价是跨存储补偿、并行后处理计数和多条异步生命周期都需要明确的错误约定。

未覆盖：全部 UI 组件、CLI/Desktop/小程序/外部 client；各 DocReader parser 的内部算法；所有模型和向量库适配器；Wiki 图谱生成、revision/rollback、数据源增量同步；长期记忆提取；MCP OAuth、工具审批与 sandbox 安全；完整 RBAC/API key 审计；调度治理和 housekeeping 所有恢复分支。本文不提供性能数值、不保证 exactly-once、不构成安全审计或部署验证。

图表互链：[组件架构](architecture.html) · [文档入库](flow.html) · [RAG 问答](qa-flow.html)。校验与未验证项见 [validation.md](validation.md)。
