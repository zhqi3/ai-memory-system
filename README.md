# AI Memory System

面向长期项目协作的本地 AI 记忆工具包：**用精简记忆保存当前口径，用独立的上下文补充库保存可获得的对话原文，在需要时回溯依据。**

A local toolkit for compact project memory and on-demand conversation provenance.
See the [English quick start](docs/QUICKSTART.en.md).

这是一套文件约定和 Python CLI。它不训练模型，也不把每次对话全部塞回提示词。

```mermaid
flowchart LR
    A[授权的可读对话来源] --> B[上下文补充库\n原文、版本、来源、缺口]
    B -->|用户要求追溯或对齐| C[固定版本并按区间读取]
    C -->|经授权整理结论| D[精简项目记忆]
    D -->|日常启动| E[当前状态与协作规则]
```

## 能做什么

- 初始化独立的私有数据目录和通用记忆模板；日常启动只读取 5 个精简文件。
- 从已支持的 Codex 日志、规范化 JSON 或单份文本导入可见对话。保留来源、缺口和不可变版本，同一对话可关联多个项目。
- 校验存储完整性，并从保留的可见来源重新解析，检查来源与导出记录是否对应。
- 建立固定版本的全文或主题阅读计划，分别记录“已取回”和调用者确认的“已阅读”。
- 对显式配置的来源做增量同步。默认关闭；调度器由使用者另外配置。

## 快速试用

需要 Python 3.10+，运行代码只依赖标准库。当前已在 macOS 验证；Linux 尚未验证，Windows 暂不支持文件锁和权限实现。

在仓库根目录执行，无需安装第三方依赖：

```sh
python3 -B tools/demo.py
python3 -B -m unittest discover -s tests -q
python3 -B tools/audit_public_tree.py
```

演示仅使用合成对话，在临时目录完成初始化、导入、来源回放校验和阅读取回，然后删除临时数据。它不会确认自己“已经读懂”示例。

创建自己的空数据目录（目标必须不存在，父目录需已存在，且不能位于 Git 仓库内）：

```sh
python3 -B tools/workspace.py init --workspace "$HOME/ai-memory-data" --project demo
python3 -B tools/workspace.py startup --workspace "$HOME/ai-memory-data" --project demo
```

这两个命令不会读取 AI 平台的配置或历史。已有项目数据不会被覆盖。

导入仓库中的合成示例，再按需读取：

```sh
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" import-transcript --project demo --provider example --conversation-id synthetic-design-001 --title "Synthetic notebook design" --source examples/synthetic-conversation.json
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" list --project demo
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" read --project demo --conversation synthetic-design-001 --start 1 --end 3
```

## 目录与进一步使用

| 目录 | 内容 |
| --- | --- |
| `tools/` | 初始化、加载、导入、版本选择、来源校验、阅读和同步工具 |
| `templates/` | 不含个人信息的记忆、协作约定与关闭状态的同步配置 |
| `examples/` | 明确标注的合成对话 |
| `tests/` | 在临时目录运行的合成测试 |
| `docs/` | 设计、CLI 使用、平台接入、来源与本地验收记录 |

- [日常使用与原文读取](docs/USAGE.md)
- [两层结构与数据边界](docs/ARCHITECTURE.md)
- [来源格式与自动同步](docs/INTEGRATIONS.md)
- [代码来源及公开范围](docs/PROVENANCE.md)
- [验证结果](docs/VALIDATION.md)
- [贡献说明](CONTRIBUTING.md)

## 当前边界

“原文”指来源快照中能获得的可见内容。被删消息、缺失分支、截断输出和未取得的附件不会被恢复；常见凭据会被标记后遮蔽。原始日志不会被修改。

ChatGPT 的 `read_thread` 导入接受宿主工具保存的实际响应；本仓库不提供 ChatGPT 登录、网页抓取或通用账户导出客户端。其他平台的原生导出包需要单独核实格式，目前可使用规范化 JSON 或单份文本。

归档可以包含隐私；自动遮蔽仅识别部分凭据格式，不能把真实对话变成可公开数据。私有数据应存放在仓库之外。文件在本地存储，模型是否在云端运行由使用者的 AI 客户端决定。

MIT 许可证，见 [LICENSE](LICENSE)。
