# MOSS 公开预发布版

MOSS 将项目采用的协作协议与精简记忆一起交给 AI。人决定目标和重要取舍；AI 检查当前状态，比较方法，执行已授权任务，再核对结果。原文补充库继续按需读取。

工具包版本为 `0.2.0-rc.2`。MOSS 配置格式使用系统版本 `1.0.0`，本候选协议为 `ai-navigation/1.0.0-rc.2`。这些版本分别标识软件包、配置与行为协议，不能互相代替。

公开版不规定使用者的回复语言、语气、篇幅或风格。每位使用者可在自己的客户端或项目约定中设置偏好；来源、权限和验证状态仍按协议记录。

## 独立试用

当前支持与验证范围限定为已测试的 macOS 环境和 Python 3.12.14，运行代码只依赖标准库。其他 Python 版本及其他 macOS 环境尚未单独验证；Linux 尚未验收，Windows 不支持当前 POSIX 文件锁和权限实现。不需要账号、模型 API、个人配置或既有记忆库。

在仓库根目录运行：

```sh
python3 -B tools/demo.py
python3 -B tools/moss_demo.py
python3 -B -m unittest discover -s tests -q
```

两个演示只使用临时目录内的合成数据，结束后删除自己的临时目录。

## 安装与接续

新用户先创建空的私有工作区；已使用本仓库工作区的用户跳过初始化。目标父目录需已存在，数据目录必须在 Git 仓库之外。

```sh
DATA="$HOME/ai-memory-data"
python3 -B tools/workspace.py init --workspace "$DATA" --project demo
python3 -B tools/moss.py install --workspace "$DATA" \
  --user-request 'Enable MOSS for all registered and future projects in this workspace'
python3 -B tools/workspace.py startup --workspace "$DATA" --project demo
```

`install` 是使用者明确采用协议的动作：记录所提供的授权说明，并启用已有和未来登记项目的默认导航。授权说明应对应实际请求。已存在的导航采用不会被覆盖。安装保留项目记录与原文快照，不修改采集和诊断配置。

正常启动返回真实正文及哈希。启用 MOSS 时包含精简记录、项目简介、固定协议与采用依据；关闭时返回原来的五份精简文件。详细决定和发现仍按任务读取，原文库不进入日常启动。

skill 在 `skills/moss`。宿主安装后应将项目入口指向上述启动命令，或使用：

```sh
python3 -B skills/moss/scripts/moss.py --repo "$PWD" \
  bootstrap --workspace "$DATA" --project demo
```

本仓库提供读取流程；客户端是否自动触发，取决于使用者另外配置的入口。安装 skill 本身不能证明客户端已自动加载。

## 启用与停用

先获取目标当前版本，再修改同一目标：

```sh
python3 -B tools/moss.py status --workspace "$DATA" --project demo
python3 -B tools/moss.py disable --workspace "$DATA" --project demo \
  --expect-sha256 '<sha256 returned by status>'
```

重新启用用 `enable`，并先重新执行 `status`。无项目覆盖时 status 返回 `absent`，首次覆盖使用这个值。全局开关使用 `--global` 替代 `--project demo`。全局关闭优先于项目启用；关闭仍保留已登记事务检查。

协议内容与采用记录通过哈希绑定。发现版本不符、缺少必要记录、锁忙或未完成的相关事务时，读取器返回错误且不交付部分正文。仅继续不依赖缺失资料的工作。可以全局关闭导航来处理损坏的协议，但关闭不能绕过未完成事务。

## 多 Agent 交接

```sh
python3 -B tools/moss.py export --workspace "$DATA" --project demo \
  --output "$HOME/demo-moss-handoff.json"
```

输出必须位于仓库之外，不能覆盖已有文件；权限为仅当前用户可读写。快照包含协议、项目状态、权限、源文件哈希、交付文本哈希和时间。源文件含 BOM 时，两种哈希可能不同。文件不会自动发送，也不含原文归档。

接手的 Agent 应读完已交付正文，检查范围和时间，并将快照中的历史授权与当前任务权限区分开。快照适合 ChatGPT 等无法直接读取本机文件的接收端；状态改变后需重新导出。它不建立实时访问或跨会话自动刷新。

独立来源的原文归档使用 `archive-project-context` skill。共享记录、独立来源和明确交接支持多 Agent 接续；本候选不提供自动任务调度或并发写入合并。

## 升级和事务边界

从旧公开版升级代码后，未启用 MOSS 的工作区继续使用精简启动。`install` 是首次采用的独立动作。已有导航安装不会自动升级协议；本轮不提供原位替换协议或从私人部署自动迁移的命令。先备份配置，核对具体版本，再制定受控迁移。

读取器使用同一工作区内的 `.ai-memory-locks` 协调遵守该锁协议的写入者。默认登记 `moss-runtime` 的 `evidence/runtime-memory-operations` 日志目录；它只检查已登记且采用支持日志格式的写入器。本仓库没有通用事务写入器或自动恢复功能，不能检测所有手工编辑和其他工具的中断。

恢复交接应提供操作标识、预期旧版本、当前版本、失败日志、所需提交前内容、恢复权限及完成检查。缺少证据时保持未解决状态；不要自动回滚或覆盖后来改动。

## 默认关闭的后台功能

新工作区自动采集关闭、来源为空；异常诊断也未启用。MOSS 安装不更改这些设置。使用者可另外配置定时器执行文件同步；只有主动启用模型诊断后，符合条件的新事件才调用模型。模型供应商、费用和可执行工具由使用者自己的环境决定。

## 验证范围

合成测试检查启动正文、版本冲突、停用回退、未来项目继承、受控事务中断、独立交接和无个人配置运行。它们不证明所有客户端自动加载、长期效率提升、完整平台历史或附件可得性。公开发布的当前验收结果见 [VALIDATION.md](VALIDATION.md)。
