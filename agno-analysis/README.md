# Agno 源码分析

分析基线：[agno-agi/agno](https://github.com/agno-agi/agno)，包版本 **3.0.9**，commit `4253dedc95250afddfcea05dfc28f26a4833a117`。分析日期：2026-09-09；源码 checkout 位于 `../agno/`，分析前后均无改动。

主线是购物清单工具 `add_item`：从 `Agent.run` 进入运行编排，追踪上下文注入、模型与工具循环、状态修改、会话与运行分表保存，再向外解释 Team、Workflow 和 AgentOS。

| 产物 | 阅读内容 |
| --- | --- |
| [线性源码讲解](walkthrough.md) | 18 节、67 个可重放的只读摘录命令 |
| [阅读路线](reading-plan.md) | 章节顺序、对应文件及每章问题 |
| [架构图](architecture.html) | 运行编排、模型、工具、存储与外层组合 |
| [核心执行流程图](flow.html) | 初始化 → 模型/工具往返 → 保存输出 |
| [暂停与恢复流程图](recovery.html) | HITL、检查点、错误终态及显式继续 |
| [验证记录](validation.md) | 重放、链接、图表检查及适用限制 |

值得抓住的三个边界：`Agent` 负责运行生命周期，`Model.response` 负责工具往返；内存 `AgentSession.runs` 与数据库分表存储是不同层次；重试、检查点与恢复不构成外部工具副作用的 exactly-once 保证。正文给出实现证据，并区分设计解读和测试预期。

从此目录重放（源码需保持上述 commit）：

```bash
uvx showboat --workdir ../agno verify walkthrough.md
```

本次先运行 `uvx showboat --help` 确认 CLI；下载到 `/tmp` 后，沙箱内 uvx 包装进程在输出后未退出，改用该安装包自带的 **Showboat 0.6.1** 可执行文件生成及验证。所有讲解均经 `note`，所有摘录均经 `exec`，没有手工写入输出块。

Showboat verify 通过；三图几何检查通过，源码文件/行号、章节锚点和本地页面链接检查通过。技能原版自检有 33 项 GitHub 导航链接报告，按本仓库要求保留并逐项记录例外，其他问题为 0。未运行 Agno 应用测试，未做真实浏览器渲染或线上可达性验证。

图表采用默认浅灰底与橙色重点，静态 HTML 内嵌 SVG/CSS，画布均为 1280×720。在线字体不可用时回退到系统字体。架构图合并 Team/Workflow 入口；主流程省略的异常与暂停分支在恢复图及正文说明。源码链接固定 commit，walkthrough 回链指向分析仓库 GitHub。

Pages 本地构建已包含三张图和本目录页。预期发布入口：[Agno 分析目录](https://intellif-aied.github.io/repo-reading/agno-analysis/)。本次仅完成本地产物和构建所需暂存，未提交或推送，线上发布状态尚未验证。预览及发布的统一操作见根 `AGENTS.md`。
