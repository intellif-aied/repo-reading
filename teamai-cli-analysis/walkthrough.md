# TeamAI CLI：从团队配置同步到知识反馈的线性源码讲解

*2026-09-09T07:07:07Z by Showboat 0.6.1*
<!-- showboat-id: 71d0fe79-21d3-43f8-9d9d-a8b5c1b28b9d -->

本文分析本地源码快照，不代表上游最新版。上游为 Tencent/teamai-cli，版本 `0.22.0`，commit 为 `8d3d66db49ffed1b053faea9481f46e63b9ccc47`；开始分析时源码工作区干净。分析只读取源码，运行验证使用 `/tmp` 的独立副本。

TeamAI 是团队 AI 编码环境的配置与知识分发工具。它把 Git 中的 skills、rules、docs、agents、hooks、MCP、env 转换为不同工具的本地配置，并提供经验检索、代码知识图谱和使用反馈。模型调用出现在部分知识加工功能中；日常资源同步和词法检索的主流程不依赖模型推理。

贯穿示例：开发者在业务项目中初始化一个独立团队 Git 仓库；之后开启编码会话，后台同步团队资源，按需检索调试经验，完成任务后提交新的学习记录。

配套图：[架构图](architecture.html)、[同步流程图](flow.html)。图表与本文使用同一源码版本。

```bash
git rev-parse HEAD; git status --short; node -p 'JSON.parse(require("fs").readFileSync("package.json","utf8")).version'
```

```output
8d3d66db49ffed1b053faea9481f46e63b9ccc47
0.22.0
```

## 阅读路线

| 顺序 | 文件与符号 | 本章回答的问题 |
| --- | --- | --- |
| 1 | `index.ts`、`init.resolveInitScope` | 命令如何进入系统，默认写到哪里？ |
| 2 | `types.ts`、`config.ts`、`partition.projectSlug` | 团队规则、机器配置、同步状态如何分开？ |
| 3 | `init.init`、`providers/types.ts` | 团队仓库怎样接入，有哪些远程副作用？ |
| 4 | `hook-dispatch-cli.ts`、`createDispatcher` | 会话启动如何触发后台任务？ |
| 5 | `pull.pull`、`pullForScope` | 作用域、并发锁和增量同步如何协作？ |
| 6 | `resources/base.ts`、`SkillsHandler` | 共享资源如何变成工具实际读取的文件？ |
| 7 | `recall.recall`、`search-index.search` | 什么内容会命中，跨作用域如何去重？ |
| 8 | `codebase-extract.extractCodebase`、`code-knowledge-recall.ts` | 代码如何变为可检索的知识？ |
| 9 | `contribute`、`saveSession`、`pending-learnings.ts` | 新经验、会话摘要和统计怎样持久化？ |
| 10 | `local-agent.reportAndSyncLocalAgent` | HTTP 集成与 Git 模式有什么区别？ |
| 11 | 相关测试与实现边界 | 哪些性质已验证，哪些风险需要继续验证？ |

## 1. 入口与生命周期：先解析命令，再加载实现

先看 `index.ts` 的全局入口。Commander 负责命令树和选项，具体操作通过动态 import 加载。`preAction` 只对 `init/pull/push` 触发历史数据迁移，hook 高频路径直接跳过；迁移失败阻止后续写入。这使不同命令的生命周期成本和副作用不同。

```bash
sed -n '10,46p' src/index.ts; sed -n '168,212p' src/init.ts
```

```output
// on the double-read fallback, and hook-dispatch is excluded outright (see below).
const MIGRATION_TRIGGER_COMMANDS = new Set(['init', 'pull', 'push']);

const require = createRequire(import.meta.url);
const { version } = require('../package.json');

const program = new Command();

program
  .name('teamai')
  .description('TeamAI — Make Every Team AI Native')
  .version(version)
  .option('--dry-run', 'Preview mode, no changes made')
  .option('-v, --verbose', 'Verbose output')
  .hook('preAction', async (thisCommand, actionCommand) => {
    const opts = thisCommand.opts();
    if (opts.verbose) setVerbose(true);

    // Auto-migrate a legacy `<repo>/.teamai/` into the partition before the
    // command runs, so init/pull/push (and every path resolver they call) see
    // the migrated layout. Narrowed twice: hook-dispatch is a high-frequency
    // silent path that must never move 12MB, and only write commands trigger a
    // move (read-only commands use the double-read fallback). Dry-run previews.
    const name = actionCommand.name();
    if (TEAMAI_HOOK_SUBCOMMANDS.includes(name as (typeof TEAMAI_HOOK_SUBCOMMANDS)[number])) return;
    if (!MIGRATION_TRIGGER_COMMANDS.has(name)) return;
    const { maybeMigrate } = await import('./migrate.js');
    try {
      await maybeMigrate({ dryRun: !!opts.dryRun });
    } catch (e) {
      // A failed migration must not proceed into the command on stale/partial
      // state. Surface a clean message and exit — the copy→verify→rename design
      // leaves the source intact, so a rerun retries safely. (Without this the
      // async-hook rejection would surface as a raw unhandled-rejection stack.)
      log.error(`Auto-migration failed: ${(e as Error).message}`);
      log.error('Your original .teamai data is unchanged. Re-run the command to retry.');
      process.exit(1);
export function resolveInitScope(
  rawScope: string | undefined,
  cwd: string,
  homeDir: string,
): { scope: Scope; projectRoot?: string; explicit: boolean; fallbackReason?: string } {
  const cwdResolved = resolveRealPath(cwd);
  const homeResolved = resolveRealPath(homeDir);
  const atHome = cwdResolved === homeResolved;

  if (rawScope !== undefined && rawScope !== '') {
    if (rawScope !== 'user' && rawScope !== 'project') {
      throw new Error(`Invalid scope "${rawScope}". Use "project" (default) or "user".`);
    }
    if (rawScope === 'project' && atHome) {
      throw new Error(
        'Cannot use --scope project in your home directory (paths would collide with user scope). ' +
        'cd to a project directory first, or omit --scope / use --scope user.',
      );
    }
    return {
      scope: rawScope,
      projectRoot: rawScope === 'project' ? cwdResolved : undefined,
      explicit: true,
    };
  }

  // Implicit default: project, with E1 fallback when cwd is $HOME
  if (atHome) {
    return {
      scope: 'user',
      projectRoot: undefined,
      explicit: false,
      fallbackReason:
        'cwd is your home directory; using user scope to avoid path collision with ~/.teamai',
    };
  }

  return {
    scope: 'project',
    projectRoot: cwdResolved,
    explicit: false,
  };
}

/**
```

`init` 的默认 scope 是 project；唯独当前目录就是用户主目录时，隐式回落 user，避免路径碰撞。显式在主目录要求 project 则报错。注意 `LocalConfigSchema` 对历史配置的默认值仍是 user，这服务于旧配置兼容，不是新初始化的行为。

入口的价值是路由与生命周期控制，真正的同步不在 Commander 回调中完成。接下来要先弄清所有下游模块共享的数据模型。

## 2. 数据模型：团队内容、机器状态、工具文件

系统有三份不同职责的数据：团队仓库中的 `teamai.yaml` 和资源目录是共享输入；机器上的 `config.yaml/state.json` 描述连接、角色、过滤和上次同步；`.claude` 等目录中的文件是交付给编码工具的结果。`dataHome` 只在运行时计算，序列化时删除，避免机器绝对路径成为可传播配置。

```bash
sed -n '322,367p' src/types.ts; sed -n '436,454p' src/types.ts; sed -n '87,96p' src/config.ts; sed -n '109,134p' src/utils/partition.ts
```

```output
export const LocalConfigSchema = z.object({
  repo: z.object({
    localPath: z.string(),
    remote: z.string(),
    /**
     * Team repo backend. Defaults to 'git' for backward compatibility.
     * - 'git':  a standalone team repo cloned to <home>/team-repo.
     * - 'http': a git-free HTTP team repo (read-only consumer).
     * - 'self': single-repo mode — the business repo IS the team repo.
     *           Knowledge lives on main under <businessRepoRoot>/.teamai/;
     *           reports (members/sessions/votes/stats) live on the
     *           `teamai-reports` orphan branch. localPath = <businessRepoRoot>/.teamai.
     */
    kind: z.enum(['git', 'http', 'self']).optional(),
    /** Base URL of the HTTP team repo (only when kind === 'http'). */
    url: z.string().optional(),
    /**
     * Git root of the business repo (only when kind === 'self').
     * Equals the parent directory of localPath. All git write operations
     * (knowledge PRs, reports orphan branch) run in isolated worktrees under
     * this repo so the user's active working tree is never touched.
     */
    businessRepoRoot: z.string().optional(),
  }),
  username: z.string(),
  updatePolicy: z.enum(['auto', 'prompt', 'skip']).optional(),
  // Read-compat default for historical configs that omit `scope` (pre-project era).
  // NOT the write default for `teamai init` — init defaults to project (issue #250).
  scope: ScopeEnum.default('user'),
  primaryRole: z.string().min(1).optional(),
  additionalRoles: z.array(z.string()).default([]),
  /**
   * Logical projects (manifest ids from projects.yaml) active in THIS directory.
   * Overwrite semantics: the directory syncs exactly these projects' resources.
   * Distinct from #374's path-slug "project" (which decides where data lives).
   * Empty/absent means no project partitioning — role namespaces + shared
   * learnings root only. Optional (not defaulted) so existing configs and test
   * fixtures without the field remain valid; consumers treat absent as [].
   */
  projects: z.array(z.string()).optional(),
  resourceProfileVersion: z.number().int().positive().optional(),
  /** Absolute path to project root; required when scope is 'project'. */
  projectRoot: z.string().optional(),
  /** Opt-in: include safe user-scope resources and knowledge while in project scope. */
  inheritUserScope: z.boolean().optional(),
  /** Tags the user has subscribed to. If empty/undefined, pull all resources. */
export const StateSchema = z.object({
  lastPush: z.string().nullable().default(null),
  lastPull: z.string().nullable().default(null),
  /** Git commit hash (short) of the team repo at the time of last successful pull. */
  lastPullRev: z.string().nullable().default(null),
  /** Installed, enabled tool targets that completed the last full pull. */
  lastPullTargets: z.array(z.string()).optional(),
  /** Git commit hash synchronized through the safe user-resource inheritance channel. */
  lastInheritedPullRev: z.string().nullable().optional(),
  /** Tool targets that completed the last inherited user-resource pull. */
  lastInheritedPullTargets: z.array(z.string()).optional(),
  pushedRules: z.array(z.string()).default([]),
  pushedSkills: z.array(z.string()).default([]),
  pushedEnvVars: z.array(z.string()).default([]),
  /** Push branches whose PR is still open — see PendingPushSchema. */
  pendingPushes: z.array(PendingPushSchema).default([]),
  /**
   * Last co-author intent teamai actually wrote to tool configs, per tool file.
   * Key = absolute config path, value = the boolean we last applied. Lets the

/**
 * Serialize a LocalConfig for on-disk storage, dropping runtime-only fields.
 * `dataHome` is derived from the projectAnchor at runtime and the config file
 * lives inside that directory, so it must never be persisted (a stale absolute
 * path would defeat the anchor-derived design and break on another machine).
 */
function serializeLocalConfig(config: LocalConfig): string {
  const { dataHome: _dataHome, ...persisted } = config;
  return YAML.stringify(persisted);
  const norm = normalizeAnchor(anchor);
  const hash = createHash('sha256').update(norm).digest('hex').slice(0, 16);
  return `${safeBasename(norm)}-${hash}`;
}

/**
 * Absolute path of a project's machine-data partition:
 * `~/.teamai/projects/<slug(anchor)>`. `anchor` MUST be the shared projectAnchor
 * (the main checkout), so all worktrees of one repo share the partition.
 */
export function projectDataHome(anchor: string): string {
  return path.join(getUserHome(), '.teamai', 'projects', projectSlug(anchor));
}
```

项目机器数据位于 `~/.teamai/projects/<slug>/`。`projectSlug` 使用规范化路径的 SHA-256 前 **16 个十六进制字符**；文件开头仍有“8 hex”的旧注释，分析以函数实现和测试为准。共享 `projectAnchor` 用来让同一仓库的 linked worktrees 共享数据分区；实际工具资源仍需区分当前工作区。

不要把三个概念混为一谈：scope 决定项目安装或用户安装；磁盘 project partition 决定机器数据放哪里；`LocalConfig.projects` 是团队 manifest 中的逻辑项目选择，决定资源和经验命名空间。它们分别解决安装范围、存储身份和内容选择。

`lastPullRev` 与 `lastPullTargets` 合起来才构成同步缓存；继承用户资源还使用独立的 `lastInheritedPullRev/Targets`，防止只同步部分资源后误认为完成了完整用户同步。

## 3. 初始化与 Provider：三种后端，三种写入边界

`init()` 先将 `--http` 和 `--self`/`.` 分流，普通路径再识别 Git Provider、认证、克隆团队仓库、注册成员和保存本地配置。Provider 接口承担地址解析、认证、克隆、仓库创建和 PR/MR 等差异；资源处理器不需要知道 GitHub 与 GitLab 的 API 细节。

```bash
sed -n '954,978p' src/init.ts; sed -n '1049,1088p' src/init.ts; sed -n '1235,1266p' src/init.ts; sed -n '1386,1419p' src/init.ts
```

```output
export async function init(options: GlobalOptions & {
  repo?: string;
  repoPositional?: string;
  scope?: string;
  role?: string;
  project?: string;
  agent?: string | string[];
  force?: boolean;
  http?: string;
  token?: string;
  inheritUserScope?: boolean;
  self?: boolean;
}): Promise<void> {
  if (options.http) {
    return initHttp(options.http, options);
  }
  // Single-repo mode: `teamai init .` or `teamai init --self`. The current git
  // repo IS the team repo; knowledge lives on main under .teamai/, reports go to
  // the teamai-reports orphan branch. No separate team repo is cloned.
  const repoArg = (options.repoPositional ?? options.repo ?? '').trim();
  if (options.self || repoArg === '.') {
    return initSelfRepo(options);
  }
  log.info('Initializing teamai...');

    process.exit(1);
  }

  // Step 1b: Detect and initialize provider from URL
  let providerName: string;
  try {
    providerName = await detectProviderForInit(repoInput);
  } catch (e) {
    log.error((e as Error).message);
    process.exit(1);
    return;
  }
  const provider = getProvider(providerName);
  log.debug(`Detected provider: ${providerName}`);

  let repoInfo;
  try {
    repoInfo = provider.parseRepoInput(repoInput);
  } catch (e) {
    log.error((e as Error).message);
    process.exit(1);
  }

  // Step 2: Ensure provider tools are installed and authenticate
  await provider.ensureInstalled();

  const isGenericGit = provider.name === 'git';
  const authSpin = spinner(isGenericGit ? 'Checking Git identity...' : 'Checking authentication...').start();
  let username: string;
  try {
    if (provider.isAuthenticated()) {
      username = await provider.authenticate();
      authSpin.succeed(isGenericGit ? `Using Git identity ${username}` : `Authenticated as ${username}`);
    } else {
      authSpin.info(isGenericGit ? 'Resolving Git identity' : 'Not logged in — starting authentication');
      username = await provider.authenticate();
      log.success(isGenericGit ? `Using Git identity ${username}` : `Authenticated as ${username}`);
    }
  } catch (e) {
    authSpin.fail(`Authentication failed: ${(e as Error).message}`);

  // Step 5: Create member file
  const memberPath = path.join(localPath, 'members', `${username}.yaml`);
  const isNewMember = !await pathExists(memberPath);
  if (isNewMember) {
    const memberYaml = YAML.stringify({
      username,
      displayName: username,
      registeredAt: new Date().toISOString(),
    });
    await writeFile(memberPath, memberYaml);
    log.success(`Registered as team member: ${username}`);

    if (!options.dryRun) {
      try {
        await pushRepoDirectly(localPath, `[teamai] Register member: ${username}`, [
          'members/',
          'teamai.yaml',
          'skills/.gitkeep',
          'rules/.gitkeep',
          'docs/.gitkeep',
          'env/.gitkeep',
        ]);
        log.success('Member registration pushed to team repo');
      } catch (e) {
        log.warn(`Push failed (you can push manually later): ${(e as Error).message}`);
      }
    }
  } else {
    log.info(`Member ${username} already registered`);
  }

    state.lastPullRev = null;
    await saveStateForScope(state, localConfig);
  } catch {
    // Non-critical: state file may not exist yet on first init
  }

  // Step 7: Inject built-in + team hooks into AI tools
  const reloadedTeamConfig = await loadTeamConfig(localPath);
  if (reloadedTeamConfig) {
    const filterAgents = requestedAgents.length > 0 ? requestedAgents : undefined;
    await reconcileTeamHooksForConfig(reloadedTeamConfig, localConfig, { filterAgents });

    // Step 7.5: Deploy CLI built-in skills immediately so team-wiki-codebase
    // is available in the IDE right after init, without waiting for first pull.
    try {
      const { deployBuiltinSkills } = await import('./builtin-skills.js');
      const skipRecall = !isRecallEnabled(localConfig, reloadedTeamConfig);
      const deployed = await deployBuiltinSkills(reloadedTeamConfig, localConfig, { skipRecall });
      if (deployed > 0) {
        log.debug(`Deployed ${deployed} built-in skill(s)`);
      }
    } catch (e) {
      log.debug(`Built-in skills deployment skipped: ${(e as Error).message}`);
    }
  }

  log.success('teamai initialized successfully!');
  log.info('Built-in skills (e.g. team-wiki-codebase) are ready to use in your IDE now.');
  log.info('Skills, rules, env and docs will auto-sync on each session start (via hooks).');
  log.info('Run `teamai status` to check current config.');

  // Close the readline singleton so the process can exit cleanly.
  closePrompt();
}
```

普通 Git 初始化可以直接向团队仓库推送成员注册，因此不是纯本地安装操作。末尾部署内置技能和钩子，并提示团队资源在后续会话中同步；不能把“初始化完成”理解为所有团队资源已同步。

| 模式 | 内容来源 | 关键边界 |
| --- | --- | --- |
| `git` | 独立的团队仓库 clone | clone 被视为可重建缓存；资源发布与经验贡献采用不同路径 |
| `self` | 业务仓库的 `.teamai/` | 内容随业务 Git 更新；知识写入通过隔离 worktree 创建 PR，报告写入 `teamai-reports` 孤儿分支 |
| `http` | 服务端 report/sync 协议 | 不执行团队 Git 推送；服务端仍可下发本地资源安装等命令 |

## 4. 会话钩子：前台返回提示，后台完成同步

安装后的钩子调用统一的 `teamai hook-dispatch`。CLI 读取一次 stdin，规范化事件和 cwd，然后按配置筛选处理器。纯副作用任务可以在 detached 子进程执行；可能返回上下文的任务留在前台。

```bash
sed -n '186,233p' src/hook-dispatch-cli.ts; sed -n '450,469p' src/hook-handlers.ts; sed -n '147,193p' src/hook-dispatch.ts
```

```output
    const handlers = filterHandlersForConfig(buildHandlerRegistry(), localConfig);
    const dispatcher = createDispatcher({ handlers });

    // Detached child: run the fire-and-forget handlers, then exit. No output is
    // wired back to the host (the parent already returned).
    if (bgOnly) {
      await runDispatch(dispatcher, event, matcher, stdin, tool, 'background');
      return;
    }

    // Parent: kick off background handlers in a detached process first so they
    // start working while we run the inline (foreground) pass.
    if (dispatcher.hasBackground(event, matcher)) {
      // Preserve one fallback ID across the parent and detached child. Without
      // this, hosts that omit session_id produce different PID-based IDs and
      // the foreground and post-pull paths can claim the same hint twice.
      if (typeof stdin.session_id !== 'string' || !stdin.session_id) {
        stdin.session_id = deriveSessionId(stdin, { includeCwd: true });
      }
      spawnBackground(event, tool, matcher, JSON.stringify(stdin), cwd);
    }

    const output = await runDispatch(dispatcher, event, matcher, stdin, tool, 'foreground');

    if (output) {
      await new Promise<void>((resolve) => process.stdout.write(output, () => resolve()));
    }
  } catch (e) {
    log.warn(`hook-dispatch: unexpected error: ${e instanceof Error ? e.message : String(e)}`);
  }
}
export function buildHandlerRegistry(): HandlerRegistration[] {
  return [
    // ─── SessionStart ─────────────────────────────────
    // pull does not produce output the host needs; run detached so git fetch
    // on a slow network cannot delay session startup. Reuses LOCAL_AGENT_TIMEOUT_MS
    // (15s) — ample for a background git pull that is not awaited by the host.
    { event: 'session-start', matcher: '*', handler: pullHandler, timeoutMs: LOCAL_AGENT_TIMEOUT_MS, background: true },
    { event: 'session-start', matcher: '*', handler: dashboardReportHandler, timeoutMs: FOREGROUND_HOOK_TIMEOUT_MS },
    { event: 'session-start', matcher: '*', handler: mrHintHandler, timeoutMs: FOREGROUND_HOOK_TIMEOUT_MS, gitOnly: true },
    { event: 'session-start', matcher: '*', handler: packageHintHandler, timeoutMs: FOREGROUND_HOOK_TIMEOUT_MS },
    { event: 'session-start', matcher: '*', handler: localAgentHandler, timeoutMs: FOREGROUND_HOOK_TIMEOUT_MS },

    // ─── Stop ─────────────────────────────────────────
    // votes-sync and contribute-check may return a hint the host injects back
    // into the session, so they run inline (capped at FOREGROUND_HOOK_TIMEOUT_MS).
    // The rest are pure side effects — the update check in particular shells out
    // to the npm registry — so they run detached to avoid pushing the Stop hook
    // past the host's hook timeout (CodeBuddy kills hooks at ~10s regardless of
    // the declared timeout).
    { event: 'stop', matcher: '*', handler: updateHandler, timeoutMs: UPDATE_TIMEOUT_MS, background: true },
export function createDispatcher(config: DispatcherConfig): Dispatcher {
  const matchedFor = (event: string, matcher: string) =>
    config.handlers.filter((reg) => reg.event === event && reg.matcher === matcher);

  return {
    hasBackground(event, matcher): boolean {
      return matchedFor(event, matcher).some((reg) => reg.background === true);
    },

    async dispatch(event, matcher, stdin, tool, mode = 'all'): Promise<DispatchResult> {
      // Find all handlers that should fire for this event+matcher, then narrow
      // to the requested mode so the inline pass and the detached background
      // pass each run their own subset.
      const matched = matchedFor(event, matcher).filter((reg) => {
        if (mode === 'foreground') return reg.background !== true;
        if (mode === 'background') return reg.background === true;
        return true;
      });

      // Execute all matched handlers concurrently with isolation + per-handler timeout
      const settled = await Promise.allSettled(
        matched.map((reg) => {
          const timeoutMs = reg.timeoutMs ?? DEFAULT_TIMEOUT_MS;
          return withTimeout(reg.handler.execute(stdin, tool), timeoutMs, reg.handler.name);
        }),
      );

      // Collect results
      const outputs: string[] = [];
      const errors: DispatchError[] = [];

      for (let i = 0; i < settled.length; i++) {
        const result = settled[i];
        const handlerName = matched[i].handler.name;

        if (result.status === 'rejected') {
          errors.push({
            handlerName,
            error: result.reason instanceof Error ? result.reason : new Error(String(result.reason)),
          });
        } else if (result.status === 'fulfilled' && result.value != null) {
          outputs.push(result.value);
        }
      }

      return { output: mergeHookOutputs(outputs), errors };
    },
```

匹配条件是 event 与 matcher **完全相等**，不是通配符匹配扩展：注入的 `*` 调用和 `Skill` 调用各自触发一次，避免重复计数。匹配后的处理器通过 `Promise.allSettled` 并发运行，单个失败不会让其他处理器失败。兼容的 JSON 上下文会合并；不认识的输出格式回退第一条。

SessionStart 的 pull 是后台任务。这提升会话启动速度，但没有“首个模型请求之前一定同步完成”的保证——这是后台结构带来的时序取舍。处理器超时包装只让等待失败，并不自动取消已经启动的底层 Promise；不能把超时测试的通过解释为所有 I/O 已停止。

## 5. 核心同步：作用域优先级 → 锁 → 刷新 → 增量分支

`pull()` 先检测项目。项目存在且没有显式继承时跳过用户安装；启用继承也只拉取用户的 skills/rules/docs/agents，hooks/MCP 等控制行为仍由当前作用域负责。

Git 团队 clone 被 worktrees 共享，因此锁覆盖的不只是 `git pull`，还包括扫描、部署、reconcile、来源同步和报告。锁竞争时将整个 scope 放入 contended 集合，后续也不再读取那个 clone；这是此处最重要的并发不变量。

```bash
sed -n '1344,1377p' src/pull.ts; sed -n '1391,1412p' src/pull.ts; sed -n '57,78p' src/pull.ts; sed -n '99,110p' src/pull.ts
```

```output
  // whose lock is held by another process is added to `contended` and excluded
  // from every clone-consuming stage (idempotent — the next pull syncs it).
  const contended = new Set<LocalConfig>();
  const heldLocks = new Map<LocalConfig, string>();
  const lockScope = async (config: LocalConfig): Promise<boolean> => {
    // Only git-mode has a shared team clone to guard. http has no clone; self
    // mode writes the reports orphan-branch worktree under its own reports-lock.
    if (config.repo.kind && config.repo.kind !== 'git') return true;
    const lock = path.join(getDataHome(config), SYNC_LOCK_FILENAME);
    if (await acquireLock(lock)) {
      heldLocks.set(config, lock);
      return true;
    }
    // User-visible: this scope is skipped wholesale (no fetch/deploy/reconcile),
    // so a plain success line would be misleading. Idempotent — the next pull
    // once the other process finishes syncs it normally.
    log.info(`[${config.scope}] sync in progress elsewhere — skipped (another pull/push holds the lock)`);
    contended.add(config);
    return false;
  };

  try {

  // 1. Detect project scope first. Its presence decides whether user scope is
  //    processed at all (issue #73: project install isolates from user).
  let projectConfig: LocalConfig | null = null;
  try {
    projectConfig = await detectProjectConfig();
  } catch (e) {
    log.warn(`Project-scope detection error: ${(e as Error).message}`);
  }
  const projectMode = projectConfig !== null;
  const inheritUserScope = projectConfig?.inheritUserScope === true;

          if (await lockScope(inheritedUserConfig)) {
            await pullForScope(inheritedUserConfig, options, {
              resourceTypes: ['skills', 'rules', 'docs', 'agents'],
              revisionField: 'lastInheritedPullRev',
            });
          }
        } else {
          activeUserConfig = loadedUserConfig;
          if (await lockScope(activeUserConfig)) {
            await pullForScope(activeUserConfig, options);
          }
        }
      } else if (inheritUserScope) {
        log.warn('user-scope inheritance is enabled, but user scope is not initialized');
      } else {
        log.debug('No user-scope config found, skipping user pull');
      }
    } catch (e) {
      log.warn(`User-scope pull error: ${(e as Error).message}`);
    }
  }

async function refreshTeamRepo(
  localConfig: LocalConfig,
): Promise<{ label: string; version: string | null; reportingOnly: boolean }> {
  if (localConfig.repo.kind === 'http') {
    const { resolveApiKey } = await import('./api-key.js');
    const apiKey = resolveApiKey();
    if (!apiKey) {
      throw new Error('No API key configured. Re-run `teamai init --http <url> --token <key>` or set TEAMAI_API_TOKEN.');
    }
    // HTTP backends deliver resources through report/sync (own hook handler),
    // so there is no repo tree to pull here.
    return { label: 'HTTP (report/sync delivery)', version: null, reportingOnly: true };
  }

  if (localConfig.repo.kind === 'self') {
    // Single-repo mode: knowledge lives under <business-repo>/.teamai on main and
    // arrives with the business repo's own `git clone`/`git pull`. teamai must NOT
    // run `git pull` on localPath here — that would operate on the business repo
    // root and touch the user's active working tree. Just read the current HEAD as
    // the cache version and let the deploy step inject from the on-disk .teamai/.
    //
    // Self-heal an older .teamai/.gitignore that still ignores `env` (pre-beta.5),
  // the reconcile/source/report stages — so there is no unlocked window in which
  // another writer could reset/checkout the tree. We must NOT lock here: the lock
  // is non-reentrant, so re-acquiring it in the same process would fail.
  const result = await pullRepo(localConfig.repo.localPath);

  // Retry any learnings whose push previously failed (see savePendingLearning).
  // Best-effort: never let a flush error block the pull.
  try {
    await flushPendingLearnings(localConfig.repo.localPath, localConfig.username);
  } catch (e) {
    log.debug(`pending-learnings flush skipped: ${(e as Error).message}`);
  }
```

刷新分三条路径：Git 执行拉取并重试待提交经验；self 读取当前 HEAD，不替开发者拉取业务仓库；HTTP 返回 report/sync 标记而不是下载 Git 树。对于普通 Git，先尝试 `--ff-only`，失败后在专用仓库根上可以 fetch + reset 到远程，所以不能把该 clone 当作手工修改的唯一保存处。

接下来查看缓存命中条件。只有版本相同且目标工具集合相同才跳过资源同步；新装工具仍能收到已有团队内容。

```bash
sed -n '423,465p' src/pull.ts; sed -n '1448,1470p' src/pull.ts; sed -n '1532,1541p' src/pull.ts
```

```output
  let currentTargets: string[] | null = null;
  if (!options.force && !options.dryRun) {
    try {
      const state = await loadStateForScope(localConfig);
      if (currentRev && state[revisionField] && state[revisionField] === currentRev) {
        currentTargets = await getInstalledResourceTargets(teamConfig, localConfig);
        const previousTargets = state[targetsField];
        const syncedTargets = new Set(previousTargets ?? []);
        const targetSetMatches = previousTargets !== undefined
          && previousTargets.length === currentTargets.length
          && currentTargets.every((target) => syncedTargets.has(target));

        if (targetSetMatches) {
          log.success(`[${scopeLabel}] Already synced at ${currentRev}, skipping`);
          // 即使 repo 未变化，仍部署 CLI 内置资源（确保 CLI 升级后新版本 agent/rules 生效）
          if (!options.dryRun) {
            const cfg = await loadTeamConfig(localConfig.repo.localPath);
            if (cfg) {
              const skipRecall = !isRecallEnabled(localConfig, cfg);
              try { const { deployBuiltinAgents } = await import('./builtin-agents.js'); await deployBuiltinAgents(cfg, localConfig, { skipRecall }); } catch {}
              try { const { deployBuiltinRules } = await import('./builtin-rules.js'); await deployBuiltinRules(cfg, localConfig, { skipRecall }); } catch {}
              try { const { deployBuiltinSkills } = await import('./builtin-skills.js'); await deployBuiltinSkills(cfg, localConfig, { reportingOnly, skipRecall }); } catch {}
              // Also refresh the CLAUDE.md recall block so a CLI upgrade that ships
              // a new block reaches CLAUDE.md even when the repo HEAD is unchanged.
              await injectRecallBlockIntoTools(cfg, localConfig, scopeLabel);
            }
          }
          return;
        }

        log.debug(`[${scopeLabel}] Repo unchanged; resource target set changed, syncing`);
      }
    } catch {
      // If rev check fails, proceed with full sync
      log.debug(`[${scopeLabel}] Rev check failed, proceeding with full sync`);
    }
  }

  // Reload team config after pull (might have changed)
  const freshConfig = await loadTeamConfig(localConfig.repo.localPath);
  if (!freshConfig) {
    log.warn(`[${scopeLabel}] Team config disappeared after pull. Skipping.`);
    return;
  // 3.5. Reconcile built-in + team hooks for the active scope only. Runs OUTSIDE
  // pullForScope so it bypasses the "Already synced" rev fast-path — this is
  // what self-heals new built-in hooks and applies hooks.yaml changes on every
  // session start. In project mode user is null, even when safe resources are
  // inherited, so executable hook configuration is never composed implicitly.
  await reconcileHooksAllScopes(reconcileUser, reconcileProject, options);

  // 3.6. Reconcile team MCP servers. Outside pullForScope for the same reason as
  // hooks. User-scope MCP remains isolated in project mode.
  await reconcileMcpAllScopes(reconcileUser, reconcileProject, options);

  // 3.7. Reconcile the team co-author policy (does an AI tool stamp a
  // Co-Authored-By / attribution trailer on its commits?). Outside pullForScope
  // for the same reason as hooks/MCP; write-only, so it self-heals but never
  // strips a trailer once the team drops the policy.
  await reconcileCoAuthorAllScopes(reconcileUser, reconcileProject, options);

  // 4. Auto-report usage data to all active scopes. Events live in a single
  //    shared file (~/.teamai/usage.jsonl), so we report to each repo with
  //    skipTruncate=true first, then truncate once at the end.
  //    Scope filtering: project scope only gets sessions whose cwd is under
  //    projectRoot; user scope excludes those sessions.
  if (!options.dryRun) {
  } finally {
    // Release every partition sync-lock this pull held, now that all
    // shared-clone reads/writes for every scope are done.
    for (const lock of heldLocks.values()) {
      await releaseLock(lock);
    }
  }
}

/**
```

即便命中缓存，CLI 内置 agents/rules/skills 和 recall 指令仍有自愈部署；hooks、MCP、co-author reconcile 则放在 `pullForScope` 外，避免被版本快路径挡住。最后 finally 释放所有已持有的锁。

它是多个文件操作组成的收敛过程，不是事务：某些知识索引或内置资源阶段失败后只记录日志，而后续仍可能保存 revision。分析时应区分“流程走完”和“所有组件都成功”，不能只依据一条成功日志判断整体健康。

## 6. 资源适配：统一处理器，保留工具差异

`ResourceHandler` 规定扫描、推送、拉取和删除的接口；注册表绑定七种资源处理器。以技能为例，目标目录由 scope 和 toolPaths 决定，禁用工具及未安装工具会跳过，实际以目录复制部署。

```bash
sed -n '32,84p' src/resources/base.ts; sed -n '11,28p' src/resources/index.ts; sed -n '468,502p' src/resources/skills.ts; sed -n '525,552p' src/pull.ts
```

```output
  abstract readonly type: ResourceType;

  /**
   * Scan local sources for items that could be pushed to the team repo.
   * Returns items found locally that are not yet in the team repo.
   */
  abstract scanLocalForPush(
    teamConfig: TeamaiConfig,
    localConfig: LocalConfig,
  ): Promise<ResourceItem[]>;

  /**
   * Scan team repo for items that should be pulled to local.
   * Returns items from the team repo.
   */
  abstract scanTeamForPull(
    teamConfig: TeamaiConfig,
    localConfig: LocalConfig,
  ): Promise<ResourceItem[]>;

  /**
   * Copy a resource item from local to the team repo directory.
   */
  abstract pushItem(
    item: ResourceItem,
    teamConfig: TeamaiConfig,
    localConfig: LocalConfig,
  ): Promise<void>;

  /**
   * Pull a resource item from the team repo and inject into local AI tool directories.
   */
  abstract pullItem(
    item: ResourceItem,
    teamConfig: TeamaiConfig,
    localConfig: LocalConfig,
  ): Promise<void>;

  /**
   * Remove a resource from the team repo and all local AI tool directories.
   * Returns the list of paths that were removed.
   */
  abstract removeItem(
    name: string,
    teamConfig: TeamaiConfig,
    localConfig: LocalConfig,
  ): Promise<string[]>;

  /**
   * Check if an AI tool is installed by verifying its root directory exists.
   * e.g. for toolPath ".codebuddy/skills", checks if ~/.codebuddy/ exists.
   * This prevents creating directories for tools the user hasn't installed.
   * @param baseDir - Override base directory (defaults to HOME). Used for project scope.
const handlers: Record<ResourceType, ResourceHandler> = {
  skills: new SkillsHandler(),
  rules: new RulesHandler(),
  docs: new DocsHandler(),
  env: new EnvHandler(),
  agents: new AgentsHandler(),
  hooks: new HooksHandler(),
  mcp: new McpHandler(),
};

export function getHandler(type: ResourceType): ResourceHandler {
  return handlers[type];
}

export function getAllHandlers(): ResourceHandler[] {
  return Object.values(handlers);
}

  async pullItem(item: ResourceItem, teamConfig: TeamaiConfig, localConfig: LocalConfig): Promise<void> {
    const baseDir = resolveBaseDir(localConfig);

    for (const [tool, toolPath] of Object.entries(scopedToolPaths(teamConfig, localConfig))) {
      if (isAgentDisabled(localConfig, tool)) continue;
      if (!toolPath.skills) continue;

      let dest: string;
      if (tool === 'openclaw') {
        const wsDir = await resolveOpenclawWorkspaceDir();
        if (!wsDir) {
          log.debug(`Skipping skill sync for openclaw: workspace dir not found`);
          continue;
        }
        dest = path.join(wsDir, 'skills', item.name);
      } else if (tool === 'hermes') {
        dest = path.join(getHermesHome(), 'skills', item.name);
      } else {
        if (!await ResourceHandler.isToolInstalled(toolPath.skills, baseDir)) {
          log.debug(`Skipping skill sync for ${tool}: tool not installed`);
          continue;
        }
        dest = await resolveSkillDestination(tool, toolPath.skills, baseDir, item.name, item.sourcePath);
      }

      try {
        await copyDir(item.sourcePath, dest);
        await ensureSkillFrontmatter(dest, item.name);
        log.debug(`Synced skill ${item.name} → ${tool}`);
      } catch (e) {
        log.warn(`Failed to sync skill ${item.name} to ${tool}: ${(e as Error).message}`);
      }
    }
  }

      const allTeamSkills = await handler.scanTeamForPull(freshConfig, localConfig);

      // Tag channel: only augment when subscriptions are actually active
      const hasActiveTagSubscriptions = tagsConfig != null
        && subscribedTags != null
        && subscribedTags.length > 0;

      let tagIncluded: ResourceItem[] = [];
      if (hasActiveTagSubscriptions) {
        const tagResult = filterByTags(allTeamSkills, tagsConfig, subscribedTags, 'skills');
        const subscribedTagSet = new Set(subscribedTags);
        tagIncluded = tagResult.included.filter((item) => {
          const itemTags = tagsConfig.skills[item.name];
          return itemTags?.some((tag) => subscribedTagSet.has(tag));
        });
        skippedByTags = tagResult.skipped.length;
      }

      // Union: merge directory items with tag-matched items
      const merged = new Map<string, ResourceItem>();
      for (const item of directoryItems) merged.set(item.name, item);
      for (const item of tagIncluded) {
        if (!merged.has(item.name)) merged.set(item.name, item);
      }
      items = [...merged.values()];
      if (excludedSkills.size > 0) {
        items = items.filter((item) => !excludedSkills.has(item.name));
      }
```

skills 的选择不是简单逐文件复制：先计算角色命名空间，再与主动订阅标签命中的技能取并集，最后应用 excludedSkills。规则、MCP、agents 各有格式转换需求；规则还要清理旧格式和过期的受管文件。

显式删除使用 `.removed` 墓碑文件传播。清理未选技能时只处理已知团队技能，并保留内置技能，防止误删纯本地自建技能。这比“远程少了一个文件就清空本地同名目录”的推断更精确。

## 7. 检索：可解释的词法排序与作用域覆盖

`recall` 汇集 learnings/docs/rules/skills 的本地索引，还能查询代码知识图谱。普通内容检索是带 IDF 的词项匹配，不是向量数据库：标题、标签、正文分别加权，按查询长度归一化，附加票数，再乘领域和资源类型权重。

```bash
sed -n '748,764p' src/utils/search-index.ts; sed -n '782,817p' src/utils/search-index.ts; sed -n '449,477p' src/recall.ts; sed -n '511,545p' src/recall.ts
```

```output
  const df = index.df ?? {};

  /**
   * IDF score for a token: log((N + 1) / (docFreq + 1)).
   * Returns 1.0 when df map is unavailable (no-op for legacy indexes).
   */
  const idf = (token: string): number => {
    if (!index.df) return 1.0;
    const docFreq = df[token] ?? 0;
    return Math.log((N + 1) / (docFreq + 1)) + 1; // +1 smoothing keeps score ≥ 1
  };

  // Query-length normalization: raw match sums grow with query length, so a
  // 15-token question outscores a 4-token one on generic words alone. That makes
  // any absolute relevance threshold meaningless across queries. Dividing by
  // sqrt(len) keeps scores comparable while still rewarding queries that match
  // on more terms. Within a single query this is a constant factor, so relative
    const entryTokens = new Set(entry.tokens);

    for (const qt of queryTokens) {
      const titleToken = `title:${qt}`;
      const tagToken = `tag:${qt}`;

      if (entryTokens.has(titleToken)) {
        score += 3 * idf(titleToken);
        hasTitleOrTagMatch = true;
      }
      if (entryTokens.has(tagToken)) {
        score += 2 * idf(tagToken);
        hasTitleOrTagMatch = true;
      }
      if (entryTokens.has(qt)) {
        score += 1 * idf(qt);
      }
    }

    // Require at least one title or tag match to filter out body-only noise.
    // Docs (type === 'docs') often lack tags and have generic titles, so allow body-only
    // matches for them — the IDF weighting naturally demotes low-relevance hits.
    const isDocsEntry = entry.type === 'docs';
    if (score > 0 && (hasTitleOrTagMatch || isDocsEntry)) {
      // Normalize the token-match sum by query length before adding absolute
      // bonuses, so cross-query scores share a scale (see lengthNorm above).
      score /= lengthNorm;
      // Vote bonus: +0.5 per vote, max 5 points (unchanged).
      score += Math.min(entry.votes * 0.5, 5);

      // Query-aware domain weight (改动 A) × type bonus (unchanged).
      // Missing domain degrades gracefully to 'neutral'.
      const domainMultiplier = domainWeightRow[entry.domain ?? 'neutral'];
      const typeMultiplier = TYPE_BONUS[entry.type];
      score *= domainMultiplier * typeMultiplier;

    log.info('No learnings available. Run `teamai pull` first to sync team knowledge.');
    return;
  }

  // Merge: search each scope index, tag results with scope, then combine & sort
  const allResults: ScopedSearchResult[] = [];
  const seenEntries = new Set<string>();
  const projectEntryKeys = new Set(
    scopeIndexes
      .filter(({ scope }) => scope === 'project')
      .flatMap(({ index }) => index.entries.map((entry) => `${entry.type}:${entry.filename}`)),
  );

  const idfBaseline = computeIdfBaseline(scopeIndexes.map((s) => s.index));

  for (const { index, scope, learningsBase } of scopeIndexes) {
    const results = search(query, index);
    for (const r of results) {
      // A project entry shadows the same logical user entry even when the
      // project version does not match this particular query. This prevents a
      // stale inherited copy from leaking through after a project override.
      const entryKey = `${r.entry.type}:${r.entry.filename}`;
      if (scope === 'user' && projectEntryKeys.has(entryKey)) continue;
      if (!seenEntries.has(entryKey)) {
        seenEntries.add(entryKey);
        allResults.push({ ...r, scope, learningsBase });
      }
    }
  }
  // Re-sort merged results by score descending, then date descending
  // TODO(cross-scale): learnings scores are unbounded TF-IDF sums that grow with
  // log(N), while codebase scores are log-compressed into [0,10]. Sorting them
  // directly compares different scales — as the corpus grows, learnings hits
  // increasingly crowd out codebase hits regardless of true relevance. Fixing
  // this properly means normalizing learnings scores against the IDF baseline
  // before the merge (related to the per-domain IDF work).
  allResults.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return (b.entry.date || '').localeCompare(a.entry.date || '');
  });

  if (options.check) {
    const top = allResults.length > 0 ? allResults[0] : undefined;
    emitCheckVerdict(top?.score ?? 0, top?.fromCodebase ?? false, idfBaseline, top);
    return;
  }

  // Limit to top 5
  const topResults = allResults.slice(0, 5);

  // Record quality signal for contribute-check's knowledge-gap detection.
  // Best-effort and independent of dry-run/verbosity — misses matter too.
  if (process.env.TEAMAI_RECALL_DISABLED !== '1') {
    recordRecallQuality(deriveSessionId({}), topResults);
  }

  if (topResults.length === 0) {
    log.info(`No matching learnings found for "${query}".`);
    return;
  }

  // Output results (STDOUT — AI reads this)
  const output = formatResults(topResults);
  process.stdout.write(output + '\n');
```

普通 learnings/rules/skills 需要标题或标签命中；docs 允许只有正文命中。领域权重依据查询内容调整，并不是永远压低运维知识。

作用域合并按 `type:filename` 去重：项目条目遮蔽同身份用户条目，即使项目版本不匹配这次查询，也不放行过期用户副本。图谱 wikiRoot 始终绑定当前项目，避免继承用户知识时串库。结果按分数与日期排序，最终最多五条，附带命中覆盖和来源信息，供 Agent 再判断。

当前源码明确保留 cross-scale TODO：普通知识分数可能随语料规模增长，而代码图谱结果被对数压缩到 0–10，再混合排序。因此“普通经验挤占代码知识结果”是有实现依据的待改进项，不是本次已测出的线上故障。

## 8. 代码知识：事实、图与可引用证据

`codebase --extract` 进入 `extractCodebase`。全量收集或通过 source-manifest 比较变更，抽取事实和接口；增量时剔除旧的变更/删除文件事实，再合并新事实。AST 与启发式提取并行作为两条信息来源，合并时优先 AST；不可用时记录缺口并降级。

```bash
sed -n '530,548p' src/codebase-extract.ts; sed -n '580,610p' src/codebase-extract.ts; sed -n '623,641p' src/codebase-extract.ts; sed -n '94,114p' src/code-knowledge-recall.ts
```

```output
export async function extractCodebase(opts: ExtractCodebaseOptions): Promise<void> {
  const root = path.resolve(opts.path || '.');
  const project = opts.project || path.basename(root);
  const maxFiles = opts.maxFiles || 200;
  const outputBase = opts.outputRoot ? path.resolve(opts.outputRoot) : root;

  const wikiRoot = path.join(outputBase, 'teamwiki');
  const evidenceDir = path.join(wikiRoot, 'evidence', 'code', project);
  const manifestPath = path.join(wikiRoot, 'source-manifest.json');

  let changedFiles: string[] | undefined;
  let deletedFiles: string[] = [];
  if (opts.incremental) {
    try {
      const changes = await detectCodeIncrementalChanges(root, manifestPath, project);
      if (changes.added.length === 0 && changes.changed.length === 0 && changes.deleted.length === 0) {
        if (opts.json) {
          console.log(JSON.stringify({ status: 'up-to-date', project }));
        } else {
  let facts: CodeFact[];
  let interfaceInventory: InterfaceInventory;
  const indicesDir = path.join(wikiRoot, '.indices');

  if (changedFiles !== undefined) {
    // 增量模式（含 changedFiles=[] 即仅删除场景）
    const oldFacts = await loadFactsCache(indicesDir);
    const oldInterfaces = await loadInterfacesCache(indicesDir);

    // 剪除已变更/删除的旧数据
    const filesToRemove = new Set([...changedFiles, ...deletedFiles]);
    const remainingFacts = pruneFactsByFiles(oldFacts, filesToRemove);

    // 合并：旧的保留 facts + 新提取的 facts
    const merged = [...remainingFacts, ...newFacts];
    // 去重（kind:name:file，同一文件中同名同类型只保留一份）
    const seen = new Set<string>();
    facts = [];
    for (const f of merged) {
      if (f.kind === 'relation') {
        facts.push(f);
      } else {
        const key = `${f.kind}:${f.name}:${f.file}`;
        if (!seen.has(key)) {
          seen.add(key);
          facts.push(f);
        }
      }
    }

    // 合并 interfaces（按 component+type 去重，新覆盖旧）
  // (e.g. TEAMAI_SKIP_AST=1) or throws, recording an AST_UNAVAILABLE gap.
  const astGaps: KnowledgeGap[] = [];
  if (files.length > 0 && astAvailable()) {
    try {
      const { facts: astFacts, result: astResult } = await extractStructuralGraphAsFacts({
        repoRoot: root,
        files,
      });
      facts = mergeCodeFacts(astFacts, facts);
      let gapSeq = 0;
      for (const gap of astResult.gaps) {
        astGaps.push({
          id: `AST-${gap.kind}-${gapSeq++}`,
          kind: gap.kind,
          description: gap.message,
          source: gap.sources.join(', '),
        });
      }
      if (!opts.json) {
  };
}

function scoreBM25(page: PageDoc, queryTokens: string[], stats: CorpusStats): number {
  let score = 0;
  const dl = page.tokenCount; // B10: use raw count, not unique count
  const { totalDocs, avgDocLength, df } = stats;

  for (const token of queryTokens) {
    const docFreq = df.get(token) ?? 0;
    const idf = Math.log((totalDocs - docFreq + 0.5) / (docFreq + 0.5) + 1);
    const tf = countOccurrences(page.content, token);
    const tfNorm = (tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgDocLength));
    const titleHit = page.title.toLowerCase().includes(token) ? TITLE_BOOST : 0;
    score += idf * (tfNorm + titleHit);
  }

  return score;
}

/**
```

输出以 `teamwiki/` 中的 evidence、索引、manifest 和图结构保存，查询以 BM25 文本得分加图邻居加权提供相关页面及源码锚点。知识内容并不等同于代码本身：提取语言、文件数上限、动态调用与 AST 缺失都影响覆盖率。默认提取最多 200 个文件，因此分析大仓库时必须说明范围。

本文只覆盖提取与召回主干；AI 深度加工、跨仓库边和各语言解析器的完整语义没有逐一审计。代码生成文档和 README 的功能清单不能替代这些实现覆盖证明。

## 9. 反馈与持久化：经验、摘要、统计不是同一条写路径

普通 Git 模式的 `contribute --file` 读取学习文档，选择作用域和命名空间，写团队 clone，重建本地索引，再直接提交并推送。self 模式改用隔离知识 worktree 和 PR。

```bash
sed -n '203,251p' src/contribute.ts; sed -n '8,40p' src/utils/pending-learnings.ts; sed -n '78,99p' src/save-session.ts; sed -n '171,181p' src/save-session.ts
```

```output
    // Write file to repo
    await fs.promises.writeFile(destPath, content, 'utf-8');

    // Pull latest (best effort — don't fail if network is down)
    try {
      await pullRepo(repoPath);
    } catch {
      log.debug('contribute: pull failed, continuing with local state');
    }

    // Rebuild the index now so recall can find this contribution immediately,
    // independent of whether the push below succeeds.
    try {
      await rebuildIndexAfterContribute(localConfig);
    } catch (e) {
      log.debug(`contribute: index rebuild skipped: ${(e as Error).message}`);
    }

    // Push directly to master with timeout. withTimeout clears its timer once
    // the push settles, so a fast push does not leave a 10s timer pinning the
    // event loop (and hanging the CLI) after the work is done.
    const commitMsg = `[teamai] Contribute session knowledge from ${username}`;
    await withTimeout(
      pushRepoDirectly(repoPath, commitMsg, [`learnings/${relPath}`]),
      10_000,
      'Push timeout (10s)',
    );

    pushSpin.succeed(`Contributed: learnings/${relPath}`);

    // Mark session as contributed (dedup for contribute-check)
    const sessionId = options.sessionId || process.env.CLAUDE_SESSION_ID || '';
    if (sessionId) {
      await markContributed(sessionId);
    }

    log.info(`Your session knowledge has been shared with the team.`);
  } catch (e) {
    // Push failed (usually offline). Persist the learning OUTSIDE the clone so a
    // later pullRepo realign (reset --hard) cannot discard it, and retry on the
    // next pull.
    try {
      await savePendingLearning(repoPath, relPath, content);
      pushSpin.warn(`Saved locally (push failed: ${(e as Error).message}). Will retry on the next pull.`);
    } catch {
      pushSpin.fail(`Contribution failed: ${(e as Error).message}`);
      log.info('You can retry with: teamai contribute --file <path>');
    }
  }
/**
 * Directory holding learnings whose push failed, persisted OUTSIDE the team-repo
 * clone so a `git reset --hard` inside the clone (pullRepo's diverged realign)
 * cannot discard them. Placed as a sibling of the clone root.
 */
export function pendingLearningsDir(repoPath: string): string {
  return path.join(path.dirname(repoPath), 'pending-learnings');
}

/**
 * Persist a learning whose push failed, so the next pull can retry it.
 *
 * @param repoPath - Team-repo clone root.
 * @param relPath - Learning path RELATIVE to `learnings/` (e.g.
 *   `alpha-notes/foo-2026-01-01-ab12cd.md` for a project-namespaced learning, or
 *   `foo-....md` for a shared-root one). The namespace subdirectory is preserved
 *   here and on retry, so a failed project contribution is never downgraded to a
 *   shared-root learning.
 * @param content - Full learning file content.
 *
 * Precondition: repoPath is a dedicated team-repo clone root, NOT a single-repo
 * `<business>/.teamai` path — callers must guard self mode (pull.ts and
 * contribute.ts already do).
 */
export async function savePendingLearning(
  repoPath: string,
  relPath: string,
  content: string,
): Promise<void> {
  const dir = pendingLearningsDir(repoPath);
  const dest = path.join(dir, relPath);
  await ensureDir(path.dirname(dest));
  await fs.promises.writeFile(dest, content, 'utf-8');
        `valuable=${summary.valuable}) to ${SESSION_LOGS_LOCAL_DIR}/${monthKey(summary)}.md`,
    );
  } else {
    // Local logs live on the user's own machine, so keep the redacted prompt line.
    const written = await appendMonthlyLog(SESSION_LOGS_LOCAL_DIR, summary, { includePrompt: true });
    if (written) {
      log.info(`Recorded session to ${written}`);
    } else {
      log.info(`Session ${sessionId.slice(0, 8)} already recorded this month.`);
    }
    await pruneMonthlyLogs(SESSION_LOGS_LOCAL_DIR, new Date()).catch(() => []);
  }

  if (!options.push) return;

  // ── Team push (opt-in) ─────────────────────────────────
  if (!summary.valuable && !options.force) {
    log.info('Session not flagged as valuable (no interventions, few tools) — skipping team push. Use --force to override.');
    return;
  }

  let localConfig: LocalConfig;
    } catch {
      log.debug('session save: pull failed, continuing with local state');
    }

    // Team upload defaults to counts + tools only; the redacted prompt line is
    // opt-in via --include-prompt, since redact() is best-effort.
    const written = await appendMonthlyLog(teamDir, summary, { includePrompt: options.includePrompt });
    if (!written) {
      spin.info('Session already present in the team log — nothing to push.');
      return;
    }
```

经验推送失败时，内容保存到 clone 旁边的 `pending-learnings`，下次 pull 重试；这样即使 clone 为对齐远程执行 reset，待提交内容仍存在。重试只有在确认远程已收到后才删除备份，不能把“本地没有新文件可 commit”误当作已推送。

会话摘要由 `session save` 写本地月度日志，远程上传需要 `--push`；团队摘要默认不含提示词，另行 `--include-prompt` 才包含脱敏首句。与此独立，pull 会自动调用 `reportUsageToTeam` 汇总使用指标、干预统计和 token 等。数据是否出机要按路径和配置判断，不能概括为“所有数据都只留本地”或“自动上传完整对话”。

共享资源发布 `push` 走分支和 PR/MR，而普通经验 contribute 是直接推送，两者审核强度不同。共享 env 的本质是 YAML 中的值以及生成的 shell 配置，显示时遮罩不等同于加密存储；这也是接入时需要明确的团队仓库权限边界。

## 10. HTTP 管理路径：配置下发与本地执行

`local-agent.ts` 表明项目已超出 Git 文件同步：启用 HTTP 本地代理后，会向服务端 report/sync，拿到命令后逐条执行、ack 成功或失败。命令包括资源安装、MCP、hook、模型配置及受限的 teamai 子命令。

```bash
sed -n '3090,3116p' src/local-agent.ts; sed -n '2934,2954p' src/local-agent.ts; sed -n '3000,3021p' src/local-agent.ts; sed -n '2530,2541p' src/local-agent.ts
```

```output
      log.debug(`${tag} report OK`);
    }

    const syncPayload = await buildSyncPayload(config, context);
    const syncResponse = await localAgentFetch<{
      ok?: boolean;
      cmds?: LocalAgentCommand[];
      commands?: LocalAgentCommand[];
    }>(
      config,
      tag,
      'sync',
      { method: 'POST', body: JSON.stringify(syncPayload) },
      { redactResponseLog: true },
    );
    // Prefer the unified cmds[] (source of truth). Fall back to the legacy
    // commands[] for older backends that do not yet emit cmds. An empty cmds[]
    // is treated as "cmds not available" and falls back too — the backend sends
    // identical data in both arrays, so this only affects old backends where
    // cmds is genuinely absent/empty while commands still carries the work.
    // TODO(jiahe, cmds-migration): drop the `commands` fallback once the backend
    // guarantees `cmds` on all sync responses (clawpro iwiki ch.7).
    const cmds = syncResponse.cmds;
    const commands = cmds && cmds.length > 0 ? cmds : (syncResponse.commands ?? []);
    if (commands.length > 0) {
      log.debug(`${tag} sync returned ${commands.length} command(s): ${commands.map((c) => `${c.type}#${c.id}`).join(', ')}`);
      const modelConfigApplied = await processCommands(config, commands, context);
async function executeCommand(
  config: LocalAgentConfig,
  command: LocalAgentCommand,
  context: LocalAgentContext,
): Promise<string | undefined> {
  if (command.type === 'apply_model_config') {
    await applyModelConfig(config, command, context);
    return;
  }
  // uninstall_teamai (clawpro three-phase: cmd = "teamai uninstall --force
  // --agent <tool>") executes its `cmd` string as a restricted teamai subcommand.
  if (command.type === 'uninstall_teamai') {
    return runCmdCommand(command, context);
  }
  if (command.type === 'install_hook_rule' || command.type === 'uninstall_hook_rule') {
    return runHookRuleCommand(config, command, context);
  }
  if (command.type === 'install_mcp' || command.type === 'uninstall_mcp') {
    return runMcpCommand(config, command, context);
  }
  const kind = commandKind(command);
    }
    try {
      const version = await executeCommand(config, command, context);
      await ackCommand(config, tag, command, 'success', version);
      if (command.type === 'apply_model_config') modelConfigApplied = true;
      log.debug(`${tag} command ${command.id} (${command.type ?? ''}) succeeded`);
      // Uninstall succeeded — skip remaining commands; the hook process exits naturally.
      if (command.type === 'uninstall_teamai') {
        log.debug(`${tag} uninstall_teamai completed — remaining commands skipped`);
        return modelConfigApplied;
      }
    } catch (e) {
      const error = (e as Error).message;
      log.error(`${tag} command ${command.id} failed: ${error}`);
      try {
        await ackCommand(config, tag, command, 'failed', undefined, error);
      } catch (ackError) {
        log.debug(`${tag} failed to ack command ${command.id}: ${(ackError as Error).message}`);
      }
    }
  }
  return modelConfigApplied;
  if (argv[0] !== 'teamai') {
    throw new Error(`Rejected cmd: only "teamai" subcommands are allowed, got "${argv[0]}"`);
  }
  return argv;
}

/**
 * Resolve the teamai entry script to run a pushed cmd. Prefers the current
 * process entry (`process.argv[1]`) so the running teamai is reused, and
 * falls back to resolving `dist/index.js` from this bundle when argv[1] is
 * unavailable (some sandboxed hook launchers). Returns null when neither
 * resolves.
```

这里的 HTTP“只读”是对团队 Git 写入命令的限制，不代表不能改变本机配置。服务端属于实际信任边界。`parseTeamaiCmd` 只接受 teamai 作为入口，普通资源安装另有路径、归属和安装清单检查；这些限制降低误操作范围，但本文不把它们宣称为完整的安全证明。

结构上，Provider 与 ResourceHandler 是相对清晰的扩展点；`pull.ts`、`init.ts` 和尤其 `local-agent.ts` 承担了大量跨模块编排。后续维护最值得优先抽出的，是统一的同步结果模型、clone 生命周期锁，以及本地代理各命令类型的执行边界，而不是继续扩充入口判断。

## 11. 测试证据与边界

先看测试表达的契约：分区哈希长度、作用域覆盖、处理器隔离和经验离线恢复均有专门测试。摘录只证明这些测试存在；实际执行结果单独记录在 `validation.md`。

```bash
rg -n 'it\(' src/__tests__/recall-scope-isolation.test.ts; sed -n '240,261p' src/__tests__/hook-dispatch.test.ts; sed -n '60,71p' src/__tests__/partition.test.ts
```

```output
116:  it('project mode: returns project results only, never consults user scope', async () => {
128:  it('user mode: returns user results only when no project scope detected', async () => {
139:  it('project mode: merges user results when inheritance is explicitly enabled', async () => {
152:  it('project mode: project entry wins when both scopes contain the same type and filename', async () => {
163:  it('keeps different resource types that share the same filename', async () => {
174:  it('shadows a matching user entry even when the project replacement does not match', async () => {
185:  it('keeps codebase lookup bound to the project when its index is empty', async () => {
202:  it('does not write inherited user votes through the active project channel', async () => {
215:  it('records recall quality (hit) for contribute-check knowledge-gap detection', async () => {
226:  it('records recall quality (miss) when nothing matches', async () => {
    it('aborts a handler that exceeds its timeout', async () => {
      const slowHandler: TestHandler = {
        name: 'slow',
        execute: vi.fn().mockImplementation(
          () => new Promise((resolve) => setTimeout(() => resolve('late'), 5000)),
        ),
      };
      const fastHandler = createHandler('fast', 'quick');

      const dispatcher = createDispatcher({
        handlers: [
          { event: 'session-start', matcher: '*', handler: slowHandler, timeoutMs: 50 },
          { event: 'session-start', matcher: '*', handler: fastHandler },
        ],
      });

      const result = await dispatcher.dispatch('session-start', '*', {}, 'claude');

      expect(result.errors).toHaveLength(1);
      expect(result.errors[0].handlerName).toBe('slow');
      expect(result.errors[0].error.message).toContain('timeout');
      expect(result.output).toBe('quick');
  });

  it('uses a 64-bit (16 hex) digest suffix, not 32-bit', () => {
    resetCaseProbeCache();
    vi.spyOn(fs, 'existsSync').mockReturnValue(false);
    const slug = projectSlug('/work/proj');
    const hex = slug.split('-').pop() ?? '';
    // 32-bit (8 hex) is cheaply collidable; require the widened suffix.
    expect(hex).toMatch(/^[0-9a-f]{16}$/);
  });

  it('projectDataHome roots under ~/.teamai/projects/<slug>', () => {
```

本次重点结论分为三类：

| 类别 | 结论 | 证据与限制 |
| --- | --- | --- |
| 实现事实 | 项目默认隔离，用户继承需显式启用；hooks/MCP 不随安全资源一起继承 | `pull()` 的作用域分支与独立 reconcile |
| 实现事实 | Git clone 上 pull/push 使用共享锁，锁竞争跳过整个作用域 | `pull.ts:1344`、`push.ts:344`；不能推广为所有写路径均受锁保护 |
| 待验证风险 | 普通 `contribute` 对共享 clone 直接写入、拉取和推送，但未采用 pull/push 的同步锁 | `contribute.ts:193–251`；本次没有复现并发数据冲突 |
| 已记录的设计欠缺 | 两类检索分数尺度不一致 | `recall.ts:511` 的 TODO 和排序实现；没有做大语料召回质量基准 |
| 实现取舍 | 后台同步不保证会话首轮读到最新资源；超时不等于底层任务取消 | `hook-handlers.ts`、`hook-dispatch.ts` |
| 可观测性欠缺 | 部分阶段 best-effort 失败仍可继续记录 revision，缺少单一结构化同步结果 | `pullForScope` 多个 catch 与结尾状态写入 |

未覆盖：所有 Git 托管平台的真实认证和 PR/MR、所有编码工具版本的真实 hook 行为、HTTP 后台服务、完整语言解析器、远程 AI 加工、生产数据隐私审计。上述能力不能由局部测试通过外推。

重放讲解：从本分析目录运行 `uvx showboat --workdir ../teamai-cli verify walkthrough.md`，要求源码保持上述 commit。代码摘录保留上游原文；本分析的讲解与图表均为中文。

实际验证已完成：构建与类型检查通过，6 个重点测试文件共 107 个测试通过；未运行完整远程 E2E。Showboat 摘录重放通过。图表几何检查通过，原版自检器的 GitHub 导航链接例外及未进行浏览器视觉验证的限制详见 [验证记录](validation.md)。
