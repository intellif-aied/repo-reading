# WeKnora 源码分析

以“上传 Markdown 文档，再基于该知识库提问”为主线，追踪文档处理与 RAG 问答的实际调用链。

- [线性源码讲解](walkthrough.md)：20 节、89 段 Showboat 可重放摘录。
- [组件架构图](architecture.html)：接口、任务、解析、索引、模型与存储。
- [文档入库流程图](flow.html)：保存、入队、解析、索引与后处理。
- [RAG 问答流程图](qa-flow.html)：检索融合、上下文构造、流式回答与消息保存。
- [阅读规划](reading-plan.md) · [校验记录和限制](validation.md)。

源码位于独立 checkout `../WeKnora/`，已在根 `.gitignore` 中排除；分析产物不被忽略。

| 项目 | 本次分析值 |
| --- | --- |
| Upstream | `git@github.com:Tencent/WeKnora.git` |
| Commit | `5db13a131e10e8ee2105211f665412ebc13bd98e` |
| 应用版本标识 | 0.8.0（README / frontend package），HEAD 未显示 release tag |
| 构建要求 | Go 1.26.0；DocReader Python >=3.10.18 |
| 源码工作区 | 分析前后干净，未修改源码 |
| 分析日期 | 2026-09-11 |

核心发现：上传 HTTP 200 不代表解析/索引完成；Chunk 与检索投影分开保存；混合搜索校验权限和 embedding 空间，并按存储实例与所属租户分组；断线续读依赖事件缓冲，不能代替任务恢复；消息 complete 事件也不是持久化成功凭据。

在上述源码版本上，从本目录重放：

```sh
uvx showboat --workdir ../WeKnora verify walkthrough.md
python3 check-diagrams.py
```

`check-diagrams.py` 从已安装的 diagram-design 技能发现自检器；多个安装或不同位置可用 `--skill-checker <发现的技能目录>/scripts/self_check.py` 指定。它调用原自检器，仅对仓库明确要求的固定源码/章节 GitHub 导航链接记例外，并额外验证源文件、行号、章节锚点及本地链接；不修改技能。

Showboat 重放和几何检查通过；技能自检的导航例外详见校验记录。未运行应用测试、真实模型/存储调用或浏览器渲染。GitHub Pages 本地构建及发布状态见校验记录；本地分析不代表已部署。
