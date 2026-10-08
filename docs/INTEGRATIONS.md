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
维护尾部，避免空闲维护无限生成归档版本；下一次普通对话会重新包含中间历史。
提示前缀只是维护约定，不是用户身份认证机制。

纯 Python 采集不调用模型。AI 调度的唤醒、上下文和工具结果可能消耗 token；
降低检查频率减少调用机会，但不能直接等同于固定比例的 token 节省。调度器、
电脑、源文件或宿主不可用时，不能保证按时更新。本地公开版不预设任何已启用的任务。
