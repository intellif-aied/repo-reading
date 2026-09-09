# 仓库分析工作流

本仓库保存开源项目的源码阅读分析。源码放在 `./<repo>/`，分析产物放在 `./<repo>-analysis/`，沿用 `layerfs-analysis/` 的组织方式。默认用中文讲解，保留源码中的标识符。

## 触发与交付

用户添加新源码并要求「分析」「linear walkthrough」或「架构图/流程图」时，默认完成下面的完整流程。若用户只指定其中一项，则按指定范围执行。文件出现本身不会启动后台任务；用户可说：`分析 ./<repo>，完成 linear walkthrough 和 diagram-design 架构图、核心流程图。`

1. 确定源码目录与分析版本。
2. 阅读实现，规划一条有先后依赖的讲解路线。
3. 用 Showboat 生成 `<repo>-analysis/walkthrough.md`。
4. 使用 `diagram-design:diagram-design` 生成架构图和核心流程图。
5. 校验产物，更新分析目录说明和根 README 索引。

源码目录作为独立 checkout 保留；在根 `.gitignore` 中按目录添加 `/<repo>/`，避免将上游源码混入分析仓库。不要忽略 `<repo>-analysis/`。更新已有分析时先阅读现有产物，并保留用户改动。

## 1. 阅读与规划

- 检查目标源码的说明、构建清单、入口、公开接口、核心模块和相关测试；沿实际调用链追踪，不能只依据 README 或目录名解释架构。
- 记录 upstream URL、commit、版本及工作区是否有改动。没有 Git 元数据时，说明源码快照来源和版本未知项。
- 选择一个贯穿全文的典型操作，按「入口与生命周期 → 数据模型 → 核心算法/存储 → 模块协作 → 错误与恢复 → 测试和边界」规划路线；按项目实际结构调整。
- 开始写作前列出章节顺序、对应文件/符号以及每章解决的问题。完成标准是主流程的每个关键交接都有实现证据，未覆盖部分明确列出。

## 2. Showboat 线性讲解

先运行 `uvx showboat --help`，再根据当前帮助确认所用子命令参数。若找不到 `uvx`，先检查 `~/.local/bin/uvx`；仍不可用时说明缺少 uv，安装后继续。

统一从分析目录调用 Showboat，以 `--workdir ../<repo>` 设置摘录命令的源码工作目录。下面的 `<repo>`、标题及文件范围需要替换为真实值；子命令语法以帮助输出为准：

```sh
uvx showboat --help
mkdir -p <repo>-analysis
cd <repo>-analysis
uvx showboat init walkthrough.md '<repo>：线性源码讲解'
uvx showboat note walkthrough.md '说明版本、阅读路线和贯穿示例。'
uvx showboat --workdir ../<repo> exec walkthrough.md bash 'git rev-parse HEAD'
uvx showboat note walkthrough.md '解释接下来这段代码的输入、输出和作用。'
uvx showboat --workdir ../<repo> exec walkthrough.md bash "sed -n '1,80p' path/to/source"
uvx showboat --workdir ../<repo> verify walkthrough.md
```

- 所有讲解、章节标题和图表链接通过 `showboat note` 写入；代码摘录通过 `showboat exec` 执行 `sed`、`rg`、`grep`、`cat` 等只读命令捕获。不要手工编造输出块或复制源码代替执行。
- 每节先说明阅读目的，再展示必要片段，随后解释控制流、数据变化、设计取舍及与下一节的衔接。长函数拆成有意义的片段，避免整文件堆砌。
- 关键结论关联具体文件和符号；区分实现事实、推断及测试所表达的预期。说明主要错误路径、状态不变量、并发或资源生命周期（适用时）。
- 摘录命令保持确定性、可重放；如需运行应用或测试，先检查依赖及副作用，并与源码摘录验证分别记录。
- 完成后在相同源码版本上运行 `showboat verify`；失败时排查工作目录、源码版本和命令输出，使用 Showboat 修正后重验。

## 3. Diagram Design

绘图前加载已安装的 `diagram-design:diagram-design` 技能及所选图型的 reference，按技能执行风格、布局和输出检查。使用技能发现的路径，不在本仓库固定某台机器的插件版本路径。

- 在本分析仓库根目录检查 `.diagram-design`，按技能的 profiles 规则解析。首次绘图的风格选择遵循技能 onboarding；仅在用户已选择后持久化 profile/default 标记。
- 默认交付 `architecture.html`（组件、职责与依赖）和 `flow.html`（贯穿示例的核心执行流程）。如有多条独立流程，以有意义的文件名拆图；流程按内容选用 flowchart、sequence 或 data flow。
- 绘制前简短说明图型、语义模式（适用时）、尺寸及拆分范围。默认静态 HTML、`doc-wide` 尺寸，具体布局按所选 reference 执行。
- 图中节点与关系必须能追溯到本次阅读的源码；概览和细节分开，遵守技能复杂度预算。为关键组件/步骤提供源码链接，并与 walkthrough 的章节互相引用。
- 输出独立 HTML，内嵌 SVG/CSS，满足技能的无障碍要求与连线规则。PNG/SVG 导出仅在用户要求时生成。
- 运行技能提供的 `scripts/self_check.py`；如安装包提供几何检查器，也运行几何检查。按技能 checklist 检查布局，有浏览器工具时检查实际渲染；缺失工具时明确记录未完成的视觉验证。

## 4. 索引与 GitHub Pages

`<repo>-analysis/README.md` 记录源码版本、walkthrough 和图表入口、Showboat 重放命令、校验结果与限制。根 `README.md` 只保留已完成分析的仓库列表及 GitHub Pages URL，操作步骤统一维护在本文件。

- Pages 首页：`https://intellif-aied.github.io/repo-reading/`。
- 图表页面路径：`https://intellif-aied.github.io/repo-reading/<repo>-analysis/<file>.html`。
- `tools/build_pages.py` 只收集 Git 已跟踪的 HTML 和静态资源，保留相对路径并生成首页；Markdown 不会转换为网页。
- HTML 中引用 walkthrough 应使用本分析仓库的 GitHub blob URL，引用源码应使用 upstream 的 commit 固定链接。相对的源码 checkout 路径无法在 Pages 上访问。

从仓库根目录预览：

```sh
python3 tools/build_pages.py
python3 -m http.server 8000 --directory _site
```

打开 `http://localhost:8000`。新 HTML/静态资源需先加入 Git 索引才会被构建；暂存前检查本次产物范围，并保留用户已有暂存内容。`_site/` 为忽略的生成目录，构建脚本会重建它。

发布由 `.github/workflows/pages.yml` 管理：推送到 `master` 后自动部署，也可从 Actions 手动运行 **Deploy GitHub Pages**。仓库设置需为 **Settings → Pages → Source → GitHub Actions**。完成本地分析不意味着要求提交或推送；发布操作按用户指令执行。

## 完成检查

- walkthrough 按规划贯穿关键实现，摘录由 Showboat 捕获且 verify 通过。
- 架构图、核心流程图与源码版本一致，技能检查通过，链接指向可访问的产物或固定版本源码。
- 分析目录说明和根索引已更新；Pages 本地构建包含预期页面。
- 汇报产物路径、验证结果及未验证事项；仅在确实运行过应用测试时声明测试通过。
