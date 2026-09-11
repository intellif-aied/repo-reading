# WeKnora 校验记录

分析源码：`5db13a131e10e8ee2105211f665412ebc13bd98e`，应用版本标识 0.8.0。所有验证针对该快照；源码初始与最终均无修改。

## Showboat

先运行 `uvx showboat --help` 确认命令语法。默认 uv 缓存位于只读路径，改用 `/tmp/weknora-uv-cache` 与 `/tmp/weknora-uv-tools` 后，在允许的联网执行环境下载 Showboat 0.6.1 并成功读取帮助。后续调用该 uvx 安装包内的原生 Showboat 可执行文件。

从本分析目录执行 `init`、`note` 与 `--workdir ../WeKnora exec`；讲解与链接全部由 note 写入，89 段摘录全部由 exec 执行只读 git/sed 命令捕获。未手工生成输出块。完成后在同一源码版本执行 `--workdir ../WeKnora verify walkthrough.md`，退出码 0。

## 图表

绘图使用发现的 diagram-design 技能，并阅读默认 style guide、Architecture、Flowchart、semantic patterns、output spec 和 HTML 模板。按仓库长期授权采用默认浅灰底/橙色，不启用 onboarding。三图都是 doc-wide 1280×720 静态 HTML，内嵌 SVG/CSS，无 JavaScript；Google Fonts 不可用时回退本地字体，简体中文使用 SC 字体栈。

| 页面 | 节点 / 箭头 | 原始 self_check | 导航例外外的问题 | 几何检查 |
| --- | --- | --- | --- | --- |
| architecture.html | 9 / 10 | 12 个远端 a 链接告警 | 0 | 0 findings |
| flow.html | 7 / 6 | 11 个远端 a 链接告警 | 0 | 0 findings |
| qa-flow.html | 8 / 8 | 11 个远端 a 链接告警 | 0 | 0 findings |

原始 `scripts/self_check.py` 并非零告警：它将远端 `<a href>` 导航和远端资源一并拒绝。根 AGENTS.md 明确要求“HTML 中引用 walkthrough 应使用本分析仓库的 GitHub blob URL，引用源码应使用 upstream 的 commit 固定链接”，因此保留这些导航。34 项告警均为上述允许范围内的链接，没有远程脚本、图片或其他外部资源例外。原检查器未被修改。

`check-diagrams.py` 调用原检查逻辑，逐条匹配 HTTPS GitHub 固定源码 commit、revision 页面或本 walkthrough 的导航告警；其余检查仍生效。额外核对源码文件存在且行号有效、walkthrough 章节锚点存在、相对 HTML/Markdown 目标存在。也校验 walkthrough 的讲解链接。未验证 GitHub HTTP 可达性；本分析的 GitHub blob 链接需提交并推送后才会在远端存在。

几何检查器 `scripts/verify-geometry.py` 三图共 0 findings。人工审查了主方向、分支标签、圆角正交连线、单独端口、标签遮罩间隔、图例、复杂度与省略范围。SVG 均有首子元素 title、非空 desc、带页面前缀的 ID 和对应 aria-labelledby。

环境未发现 Chromium、Chrome、Firefox 或 Python Playwright；没有浏览器工具可用于此处的实际渲染。未验证截图、实际字体宽度/getComputedStyle、移动端滚动、打印及屏幕阅读器表现。几何与结构检查不能替代这些验证。

## 应用与测试

未发现 Go 工具链；未安装源码项目依赖、未运行 Go/Python/前端测试，也未启动数据库、Redis、DocReader、对象存储或调用模型。walkthrough 第 20 节中的测试摘录仅说明预期不变量，不能解读为测试通过。没有运行性能、并发竞态、故障注入、安全或部署测试。

## Git 与 Pages

本次只添加 `WeKnora-analysis/`、根 `.gitignore` 的 `/WeKnora/` 与 README 的单个 Pages 入口。保留先前 Agno 的暂存内容和独立 `symphony/`。为 Pages 收集新产物，检查范围后暂存；不提交或推送。

`python3 tools/build_pages.py` 成功：4 个仓库入口、9 张分析页面。逐字节核对 `_site/WeKnora-analysis/` 下三张图与分析源文件一致；站点首页恰好一个 WeKnora 入口；仓库目录页包含三张图和正确的 GitHub walkthrough 链接。生成的目录页加三张图共 4 个 HTML。`_site/` 保持忽略。

`git diff --cached --check` 通过；已有 Agno 暂存文件保留。没有 commit、push 或远端部署操作。本地构建通过不代表 GitHub Pages 已发布。
