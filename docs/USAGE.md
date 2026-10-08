# 使用与原文读取

以下命令从源码仓库根目录执行。`$HOME/ai-memory-data` 是示例数据目录；实际
使用时替换为自己的绝对路径。工具拒绝带符号链接的源路径或数据路径，请使用
实际路径。初始化不会更改全局 AI 配置，也不会注册任何定时任务。

## 精简记忆

```sh
python3 -B tools/workspace.py init --workspace "$HOME/ai-memory-data" --project demo
python3 -B tools/workspace.py add-project --workspace "$HOME/ai-memory-data" --project second-project
python3 -B tools/workspace.py startup --workspace "$HOME/ai-memory-data" --project demo
```

`init` 要求目标尚不存在，`add-project` 要求项目尚不存在。启动只读取
`global/startup-core.md`、项目 manifest、index、handoff 和 working-agreement。
不会读取归档正文、决策全集、历史日志或其他项目。

在获准更新指定项目记忆后，按模板中的 `AI_MEMORY_CONTRACT.md` 保存受影响文件的
私有备份、编辑必要字段，再校验结果。此版本不提供自动记忆总结或自动提升规则。
初始化写入失败时可能保留新建的部分目录供诊断，重试不会覆盖它。

## 导入与查找

```sh
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" import-transcript --project demo --provider example --conversation-id synthetic-design-001 --title "Synthetic notebook design" --source examples/synthetic-conversation.json
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" list --project demo
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" verify --project demo
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" verify-source --project demo --conversation synthetic-design-001
```

`list` 返回元数据；`verify` 检查存储内容的完整性；`verify-source` 重新解析保存的
可见来源并检查映射。后两项会由程序处理正文，但不会将正文作为结果输出给模型。
来源回放不能证明平台中不存在更多历史。

导入时省略 `--project` 可暂存待关联对话。随后使用 `list --unassigned` 查看，
再用 `link --project <名称> --conversation <ID或完整key>` 明确关联。
不同平台若使用相同 ID，必须使用返回的完整 `conversation_key` 区分。

## 读取指定原文

```sh
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" read --project demo --conversation synthetic-design-001 --start 1 --end 3
```

区间从 1 开始，含首尾。默认只在可见内容和结构能证明版本覆盖关系时选择版本。
若出现不可比较的编辑或分支，先查看 `list`，再明确指定 `--revision <完整哈希>`。
不要用导入时间替代版本判断。

默认输出会简化内联媒体的显示。`--include-media` 返回存储的内联载荷，不会下载
外部附件，也不表示已查看图片或听过音频。

## 全文对齐与阅读进度

```sh
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" review-create --project demo --mode full
```

保存结果的 `review_id`；计划固定当时的对话及版本。对于冲突，可提供 `--versions`
文件，内容为 `conversation_key` 到完整版本哈希的 JSON 映射。新增对话或版本需要
新建计划，旧计划不会自动扩展。

```sh
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" review-fetch --review REVIEW_ID --conversation synthetic-design-001 --start 1 --end 3
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" review-report --review REVIEW_ID
```

将 `REVIEW_ID` 替换为实际值。完整阅读返回内容后，另行确认：

```sh
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" review-ack --review REVIEW_ID --conversation synthetic-design-001 --start 1 --end 3 --note "Selected text was read in full."
```

输出截断时应缩小区间重新取回。`review-ack` 是调用者的阅读声明，不是软件对认知
状态的证明。报告区分已取回、已确认阅读、未读区间、后续版本与来源限制。

主题追溯可使用 `review-create --mode topic --topic "主题"`，配合可选
`--conversation` 和 `--revision`。主题文字只是任务范围标签，不是自动全文搜索器。
调用者仍需寻找相关原文、上下文及后续修订，不能把一个命中片段当作完整口径。

给 AI 的自然语言示例：“加载 demo 的精简记忆”“回溯 demo 中这个决定的原文”
“按固定版本完整读取 demo 的已关联对话，并报告未读范围”。AI 客户端需要自行
接入这些 CLI 和协作规则，本仓库不会自动安装宿主技能。
