# TeamAI CLI 源码分析

分析基线：Tencent/teamai-cli，版本 **0.22.0**，commit `8d3d66db49ffed1b053faea9481f46e63b9ccc47`。分析前后上游 checkout 均无改动。分析日期：2026-09-09。

| 产物 | 内容 |
| --- | --- |
| [中文线性源码讲解](walkthrough.md) | 11 章：入口、数据、初始化、钩子、同步、适配、检索、代码知识、反馈、HTTP、测试边界 |
| [中文架构图](architecture.html) | 配置与知识如何连接 Git、CLI、AI 工具与 HTTP 管理 |
| [中文同步流程图](flow.html) | 普通 Git 模式下 pull 的作用域、锁、增量分支与释放 |
| [验证记录](validation.md) | 构建、107 个测试、图表检查与明确限制 |

核心判断：TeamAI 将团队 AI 配置分发、知识检索和使用反馈放在同一 CLI 中。Provider 与 ResourceHandler 是清晰的扩展点；同步协调、本地代理命令和跨来源检索排序是后续维护重点。详见 walkthrough 的证据与待验证风险表。

从此目录重放源码摘录：

```bash
uvx showboat --workdir ../teamai-cli verify walkthrough.md
```

当前环境 uvx 的缓存写入受沙箱限制，生成与校验使用已有缓存中的同一 Showboat 可执行文件；未手工编造输出。

图表使用默认浅灰底、橙色重点，静态 HTML 内嵌 SVG/CSS，画布 1280×720。中文字体离线时可能回退系统字体。架构图将同类工具、资源及 Provider 合并；流程图聚焦普通 Git，同步异常、self/HTTP 分支在文字中说明。

发布方式：推送到分析仓库 `master` 后，由 `Deploy GitHub Pages` 工作流自动部署。页面地址：

- [架构图](https://intellif-aied.github.io/repo-reading/teamai-cli-analysis/architecture.html)
- [同步流程图](https://intellif-aied.github.io/repo-reading/teamai-cli-analysis/flow.html)

部署状态以 GitHub Actions 的 `Deploy GitHub Pages` 运行结果为准。HTML 内的 walkthrough 链接指向分析仓库中的中文讲解。
