# 验证记录

源码基线：`8d3d66db49ffed1b053faea9481f46e63b9ccc47`，版本 0.22.0。验证日期：2026-09-09。运行环境 Node.js v22.23.2、npm 10.9.8。

## 源码与依赖隔离

运行时验证使用 `/tmp/teamai-cli-analysis-test` 的源码副本，不在上游 checkout 安装依赖或生成构建文件。

首次 `npm ci --ignore-scripts --no-audit --no-fund` 因锁文件引用的 `mirrors.tencent.com/npm/` 出现 `ERR_SSL_WRONG_VERSION_NUMBER` 而受阻。随后仅在临时副本将 442 个下载地址替换为 `registry.npmjs.org/`；锁定版本和 integrity 不变。再次安装成功，安装了 400 个包。没有修改上游 package-lock.json。

## 已执行的程序验证

| 检查 | 结果 |
| --- | --- |
| `npm run build` | 通过，生成 ESM bundle |
| `npm run typecheck` | 通过，退出码 0 |
| 6 个重点 Vitest 文件 | 107 个测试通过，退出码 0 |
| `node dist/index.js --version` | 输出 0.22.0 |
| `node dist/index.js codebase --help` | 成功显示命令帮助 |

测试命令（在临时源码副本中）：

```bash
./node_modules/.bin/vitest run \
  src/__tests__/hook-dispatch.test.ts \
  src/__tests__/recall-scope-isolation.test.ts \
  src/__tests__/partition.test.ts \
  src/__tests__/pending-learnings.test.ts \
  src/__tests__/pull-skip-sync.test.ts \
  src/__tests__/search-index.test.ts \
  --maxWorkers=2 --minWorkers=1
```

| 文件 | 通过数 | 验证重点 |
| --- | --- | --- |
| search-index.test.ts | 53 | 分词、检索、索引与过滤 |
| hook-dispatch.test.ts | 17 | 事件匹配、隔离、输出合并、前后台和超时 |
| pull-skip-sync.test.ts | 9 | revision 快路径和同步目标变化 |
| pending-learnings.test.ts | 10 | 离线经验持久化及重试 |
| recall-scope-isolation.test.ts | 10 | 作用域隔离、继承遮蔽、质量信号 |
| partition.test.ts | 8 | 分区身份与哈希长度 |

这是针对主流程的局部验证，没有运行完整测试集、全部真实 AI 工具 E2E、远程 Git 提交、HTTP 管理服务或模型调用。

## 文档与图表验证

- Showboat 从真实源码执行摘录，重放 verify 通过。
- 两张 HTML 的 `verify-geometry.py` 均为 0 个问题。
- 技能原版 `self_check.py` **不是全绿**：每张图报告 10 项，全部是 `<a>` 上的 GitHub 导航链接。脚本把导航链接与远程资源一起拒绝；分析仓库 AGENTS.md 则明确要求源码固定 commit 链接和 walkthrough 的 GitHub 链接。保留这些导航链接并记录例外，没有修改技能检查器。
- `check-diagrams.py` 调用原检查器，对上述两类明确的 HTTPS GitHub 导航链接逐条分类；其余检查仍保留。结果每图 10 个已说明链接例外，其他问题 0 个；SVG 无障碍、脚本及其他外部引用未报告问题。
- 环境没有可用浏览器工具或 Chromium，因此未做真实浏览器渲染、字体加载和视觉截图验证；几何检查不能替代它们。

在分析目录运行（将环境中的实际技能路径传入）：

```bash
python3 check-diagrams.py /实际技能目录/scripts/self_check.py architecture.html flow.html
```

Pages 构建从分析仓库根运行 `python3 tools/build_pages.py`，仅收集被 Git 跟踪的静态资源。本次两张 HTML 加入索引后完成构建验证；实际构建成功，共 3 张 HTML（已有 LayerFS 1 张、TeamAI 2 张）；逐字节核对 TeamAI 两张生成页面与交付产物一致。推送到 `master` 后由 GitHub Actions 部署；线上结果以工作流状态为准。

## 结论的适用范围

模块调用关系、作用域规则和写路径来自上述 commit 的实现；并发 contribute 风险、跨来源排序偏差和后台同步时序在讲解中明确标注为待验证或设计边界。没有把局部单元测试通过当作生产正确性或完整安全证明。
