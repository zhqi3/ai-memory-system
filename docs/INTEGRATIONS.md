# 来源格式与自动同步

## 来源支持范围

| 来源 | 导入入口 | 当前范围 |
| --- | --- | --- |
| Codex | `import-codex --source FILE --title TITLE` | 已观察格式的 JSONL 可见记录；不会读取数据库或登录信息 |
| ChatGPT 宿主响应 | `import-read-thread --source FILE` | 已保存的真实 `read_thread` 分页响应 |
| 任意平台规范化 JSON | `import-transcript` | `ai-context-transcript/1.0` 或 `/2.0` |
| 单份 TXT、MD、HTML | `import-transcript` | 保留完整文本，发言角色不作结构化推断；HTML 不执行 |
| 平台账户原生导出包 | 尚无通用入口 | 必须先验证具体格式；不能把 ZIP 或任意 JSON 当作规范化输入 |

所有入口都需要 `--root`，项目可选；真实使用前先确定来源及归属授权。
`examples/synthetic-conversation.json` 给出了规范化 JSON 的最小可运行示例。
未知字段可作为公开扩展数据保存；调用者负责证明它属于可见来源，不能将摘要
伪装成逐字原文或把内部引擎上下文混入输入。

ChatGPT bundle 的外层为：

```json
{"schema_version":"ai-context-read-thread/1.0","pages":[]}
```

这里只展示外层形状；空 `pages` 不是有效导入数据。必须填入宿主工具的实际 JSON
响应，按工具返回的 cursor 继续翻页，不得用预览或 AI 重述替代。分页结束仅描述
已提供页面的元数据，不能证明没有缺页、删除记录或隐藏分支。原生账户导出 JSON
与这种宿主响应不是同一格式。本仓库不包含该宿主工具或相关登录客户端。

## 显式配置增量同步

初始化生成关闭状态的 `AI-Context/sync-config.json`。确认来源授权后，在自己的
私有目录中设置 `enabled: true`，并加入来源，例如：

```json
{
  "schema_version": "ai-context-auto-sync/1.0",
  "enabled": true,
  "interval_minutes": 180,
  "sources": [{
    "kind": "transcript",
    "provider": "example",
    "conversation_id": "synthetic-design-001",
    "path": "/absolute/private/source/synthetic-conversation.json",
    "title": "Synthetic notebook design",
    "projects": ["demo"],
    "enabled": true
  }],
  "codex_discovery_roots": [],
  "workspace_routes": [],
  "heartbeat_conversation_id": null
}
```

将 `path` 换成已存在的实际源文件路径，保持配置文件 mode 0600。

```sh
python3 -B tools/auto_sync.py --root "$HOME/ai-memory-data/AI-Context" status
python3 -B tools/auto_sync.py --root "$HOME/ai-memory-data/AI-Context" sync-files
```

`interval_minutes: 180` 是配置元数据，**不会自行创建每三小时任务**。使用者可在
自己的调度器中按该频率调用 CLI。工具每次只执行一轮，采用文件锁防止重入，
检查变化、保留旧版本、验证新增记录后更新检查点，并保存运行回执。
单个来源失败不会删除良好快照；应修复后重试并检查回执。

适配器或脱敏策略升级后，旧检查点不能仅凭“文件未变”跳过检查。若旧快照文件
完整但已无法回放，工具会保留它，并尝试从当前获准来源生成新的可验证快照。
存储哈希不一致等完整性错误仍会报错，不会被当作可忽略的旧格式问题。

仅有新增内部事件或内容未变化时，不产生新的可见内容版本。快速路径依赖文件
元数据；需要重新检查内容时使用 `sync-files --force`。这是本地增量采集，不是
对恶意源文件修改的持续监控保证。

## 可选 Codex 发现与 AI 调度

默认不扫描任何会话目录。显式配置 `codex_discovery_roots` 和
`workspace_routes: [{"path": "/absolute/project/workspace", "project": "demo"}]`
后，可扫描主会话的首行元数据。只有已知交互来源、唯一最长路径匹配的项目才
自动注册；歧义条目只记为待关联候选。来源发生格式变化需检查回执与适配器测试。

注册的 ChatGPT 来源使用 `kind: "read_thread"`、`provider: "chatgpt"`，另附
`conversation_id`、`title`、`projects` 和 `enabled`；没有本地 `path` 字段。
`sync-files` 会跳过它。宿主获取分页并保存 bundle 后，通过
`auto_sync.py --root ROOT import-read-thread --conversation ID --source BUNDLE`
完成检查与存储。工具不会主动请求远程平台。

如用 AI 对话自动化调用同步，维护提示以 `AI_CONTEXT_AUTO_SYNC_V1` 开头，并显式
设置 `heartbeat_conversation_id`。匹配会话会暂缓采集最后一个普通用户消息之后的
维护尾部，避免空闲维护无限生成归档版本；同时识别应用的完整 `<heartbeat>`
封装，独立 `<environment_context>` 消息不会重新开放维护尾部。引用、工具输出、
助手消息和仅包含相似标记的普通请求不会触发过滤。下一次普通对话会重新包含中间历史。
提示前缀只是维护约定，不是用户身份认证机制。

本地文件扫描可以由用户授权的系统调度器直接运行 Python `sync-files`，不需要
模型参与。macOS 可使用用户级 LaunchAgent，直接传入 Python、脚本和显式库路径；
将标准输出、错误日志保存在私有位置，保留程序锁与回执。若同时保留 AI 自动化，
应从其提示中移除本地文件扫描，仅保留必要的 `read_thread` 接口采集及异常检查。
定时器安装和启用必须由使用者自行授权；仓库不会自动修改个人系统配置。

每次同步还会用纯 Python 从运行回执生成 `sync/health/pending.json`，不读取
归档正文、不调用模型。临时文件或权限错误先等下轮重试，连续两轮失败才升级；
核验错误立即列入诊断，版本分歧等待审查，未知项目只请求用户确定归属。
同一问题保留一个条目；来源成功后清除故障，未变化的文件不会自动消除版本分歧。
可运行 `python3 -B tools/sync_health.py --root ROOT` 重新整理历史回执。它只生成
待处理清单，不启动 AI，也不意味着用户已阅读或异常已解决。

可选的 `auto_diagnose.py` 仅在使用者明确启用
`exception_diagnosis: {"mode": "automatic", "automatic_ai_enabled": true,
"codex_executable": "/absolute/path/to/codex", "max_attempts_per_24h": 4,
"timeout_seconds": 120}` 后生效。`auto_sync.py` 的 CLI 在释放写锁后检查待处理清单，
无异常、等待重试或仅待用户确定项目时不调用模型；可用 `model` 和
`reasoning_effort` 保留宿主所选模型，否则由 CLI 使用其默认值。

每轮最多处理一项新诊断，每个异常阶段先登记再调用，默认滚动 24 小时最多四次。
重复问题、诊断失败和中途退出均不会自动反复调用；故障恢复后再次出现，或错误类型/
冲突版本改变，才视为新的诊断阶段。失败后需用户按需排查；积压条目留待后续轮次。
AI 只接收有长度限制的元数据与文件可读性观察，使用临时、只读且禁用工具的
Codex CLI 上下文，保存原因假设、检查建议和可取得的 token 用量，不自动修复或分配项目。
报告在 `sync/diagnostics/reports/`，最近结果在 `sync/diagnostics/latest.json`。
它不恢复 ChatGPT 周期采集，不添加新调度器，也不发送通知或加载历史对话。

非交互执行与临时会话依据 [Codex 官方说明](https://learn.chatgpt.com/docs/non-interactive-mode)。
CLI 版本、模型访问、现有登录和主机权限必须实测；某台主机成功不代表其他环境已接通。

纯 Python 采集不调用模型。AI 调度的唤醒、上下文和工具结果可能消耗 token；
降低检查频率减少调用机会，但不能直接等同于固定比例的 token 节省。调度器、
电脑、源文件或宿主不可用时，不能保证按时更新。本地公开版不预设任何已启用的任务。

## Hermes、Claude Code 与 ChatGPT skill

[`archive-project-context`](../skills/archive-project-context/SKILL.md) 提供与
Codex 无关的归档流程。其 `scripts/capture.py` 需要显式指定受信任源码目录、
私有库、已存在项目、来源文件和会话身份；不读取宿主设置，不直接访问平台数据库。

- Hermes：接收指定单个会话的 JSON 导出，按 profile/实例 ID 与原生会话 ID
  共同区分来源。需要宿主先取得真实导出；静态旧导出不能反映后续对话。
- Claude Code：接收选定主会话或子 Agent 的 JSONL，保留可见文本、工具内容和
  可获得的父消息关系。主会话与子 Agent 独立归档，不能互相替代。
- ChatGPT：用 `--format chatgpt-read-thread` 导入宿主实际取得的完整分页 bundle，
  `--session-id` 传对话 ID，不传 `--agent-id`。保留原有 `chatgpt + 对话ID` 身份，
  与已有 `import-read-thread` 导入结果共用会话。获取步骤见
  [ChatGPT skill 接入说明](../skills/archive-project-context/references/chatgpt.md)。

Hermes 和 Claude Code 先生成可见记录投影，再使用原有导入和来源回放工具；回放核验覆盖保存的
标准化投影，不等于对原生日志所有字段的独立回放或全量历史验收。过滤已识别的
内部推理及系统字段，未知内容块/事件报错。外部附件不下载。

ChatGPT 直接复用原有响应适配器和来源组装映射，保留真实分页响应，必要时进行
有标记的凭据遮蔽。skill 入口拒绝未结束的分页、已知游标矛盾和已识别的截断信号。
未解析的公开项目保留在源响应中，并在回执报告缺口；不冒充已完整解析。
翻到末页不能证明没有缺页、隐藏分支或删除记录。宿主原文工具需要另外提供，
任意平台账户导出 JSON 不能直接当成 `read_thread` bundle。

助手使用 `sync/run.lock`，与现有定时同步避免同时写库。回执在
`sync/agent-capture-runs/`，与定时同步的 `sync/runs/` 分开；每轮只输出元数据。
相同输入去重，新增内容生成不可变版本。它不修改同步配置或精简记忆。

持续运行需要另行接入宿主 hook/协调器，并明确新会话、profile 和子 Agent 的
项目路由。现有 `kind=transcript` 不直接解析这些原生格式。当前只交付 skill 和
文件导入助手，未自动安装平台钩子或启用任何真实来源。
