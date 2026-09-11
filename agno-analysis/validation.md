# 验证记录

日期：2026-09-09。源码：Agno 3.0.9，`4253dedc95250afddfcea05dfc28f26a4833a117`。

## 源码与讲解

- 分析前后 `git -C agno status --porcelain` 均为空；源码目录按 `/agno/` 加入根 `.gitignore`，未修改 upstream checkout。
- 使用 Showboat 0.6.1 创建 18 节 walkthrough；67 个命令包含版本、工作区状态及真实源码/测试摘录。
- 从 `agno-analysis/` 以 `--workdir ../agno` 执行 Showboat `verify walkthrough.md`，退出码 0。
- Showboat 是从 uvx 下载的官方包中调用的原生可执行文件。缓存和工具下载都放在 `/tmp`；未修改用户工具安装。原始 uvx 沙箱启动方式输出后未退出，因此改为直接调用同包二进制。
- 关键调用关系来自当前实现。特别核对了 Knowledge 的 `retrieve → search` 转发，以及 SQLite `upsert_run` 缺省 run_index 的 SQL 内回填，未照搬已不完全匹配实现的邻近注释。

## 图表与链接

三张图均为静态 `doc-wide` HTML，内嵌 SVG/CSS；架构图 9 节点，主流程 7 节点，恢复流程 8 节点，均未超出技能复杂度预算。恢复流程按检查点回调先于未决需求判断的实际顺序绘制。

| 检查 | architecture | flow | recovery |
| --- | --- | --- | --- |
| 原版 self_check 报告数 | 12 | 10 | 11 |
| 其中符合仓库要求的 GitHub 导航链接 | 12 | 10 | 11 |
| 其余自检及链接问题 | 0 | 0 | 0 |
| verify-geometry 几何问题 | 0 | 0 | 0 |

**原版 self_check 不是全绿。** 该脚本把 `<a href="https://github.com/...">` 导航与远程资产一并拒绝；仓库规范则要求 commit 固定源码链接和 GitHub walkthrough 链接。保留导航，未修改已安装技能检查器。[check-diagrams.py](check-diagrams.py) 调用原检查器，逐项识别仅限此 commit、此 walkthrough 和 revision 页的 HTTPS GitHub `<a>` 例外；其他报告继续使检查失败。

该脚本还验证源码文件存在、`#L` 行号有效、walkthrough 章节锚点存在，以及本地 HTML 互链存在。检查器不验证 GitHub HTTP 响应，链接可达性需发布后另验。原版自检未报告 SVG 无障碍、可执行属性、脚本或外部资产问题；字体是允许的 Google Fonts 样式表。

复查时先发现已安装技能目录，不依赖特定机器版本路径：

```bash
# 从 agno-analysis/ 执行，将技能目录换成当前环境发现的实际路径。
python3 /实际技能目录/scripts/self_check.py architecture.html flow.html recovery.html
python3 check-diagrams.py /实际技能目录/scripts/self_check.py architecture.html flow.html recovery.html
# verify-geometry.py 位于本次插件包的顶层 scripts/ 中。
python3 /实际插件包/scripts/verify-geometry.py architecture.html flow.html recovery.html
```

人工检查了连接线正交走向、8px 圆角、连接端口、标签与线间距、底部图例及节点宽度。自动几何检查主要识别标签遮挡，不能代替完整视觉验证。当前环境没有浏览器工具、Chromium/Chrome/Firefox 或 Playwright，未做浏览器渲染、截图、实际字体测量和屏幕阅读器测试。

## Pages 构建

仅将本次分析产物、根索引与 `.gitignore` 加入 Git 索引。此前暂存区为空；未纳入其他未跟踪源码。运行根目录 `python3 tools/build_pages.py` 成功，生成 3 个仓库入口、6 张分析页面。已核对本次构建包含：

- `_site/agno-analysis/index.html`：生成的仓库目录页。
- `_site/agno-analysis/architecture.html`、`flow.html`、`recovery.html`：与交付文件逐字节一致。
- 站点首页仅含一个 Agno 目录入口；walkthrough 指向 GitHub，Markdown 不转换为网页。

三张生成页面均已逐字节核对一致；站点首页 Agno 入口计数为 1，仓库目录页包含三图及 GitHub walkthrough。`git diff --cached --check` 通过。本次未执行 git commit 或 git push；构建不证明线上已经部署。

## 未验证范围

未运行 Agno 应用测试或购物清单示例。当前 Python 环境缺少 pytest、pydantic、sqlalchemy、openai；真实模型调用另需凭据。正文的测试章节是测试源码预期，不是“测试通过”报告。

未验证供应商调用、外部工具效果、SQLite 并发与崩溃一致性、完整异步取消、多租户隔离及部署链路。agnoctl、agno_infra、所有适配器、完整知识摄入/学习、Workflow 每种控制结构和 AgentOS 队列/调度/registry 的实现不在此次详读范围。
