# 0.2.0-rc.2 预发布说明与复核

本版将精简项目记忆、按需原文追溯和 MOSS 导航整理为一个可独立试用的工具包。所有私有数据、账号配置和本机部署回执均不包含在发布文件中。

[0.2.0-rc.2](https://github.com/zhqi3/ai-memory-system/releases/tag/v0.2.0-rc.2) 已于 2026-10-10 在现有 GitHub 仓库公开预发布，尚非正式版。以下步骤用于复核本版及准备后续发行；文档修改不替换已发布的标签或附件。

## 发布前检查

```sh
python3 -B -m unittest discover -s tests -q
python3 -B tools/demo.py
python3 -B tools/moss_demo.py
python3 -B tools/audit_public_tree.py
git diff --check
```

暂存明确选定的公开文件后，检查实际暂存内容：

```sh
python3 -B tools/audit_public_tree.py --tracked
git diff --cached --check
```

在仅复制 `PUBLIC_FILES.txt` 中文件的干净目录中，以空 HOME、无个人 AI 配置、无网络请求的环境重复测试和演示。发布包也只包含此清单，并逐文件核对哈希。签名扫描不证明所有隐私风险都已消除；人工检查新增协议、路径、示例和来源说明。

## 建议试用范围

先在新的私有工作区试用 MOSS，再对原有工作区进行显式首次采用。检查目标、权限和协议正文是否实际交付；关闭导航应回到五份精简记录；交接快照需检查时间和状态变化。

当前支持与验证范围限定为已测试的 macOS 环境和 Python 3.12.14。其他 Python 版本及其他 macOS 环境尚未单独验证；Linux 尚未验证，Windows 不支持当前 POSIX 文件锁和权限实现。定时器、客户端自动触发、模型诊断启用和外部消息发送由使用者单独设置。协议首次采用和后台功能启用是独立动作。

后续候选版本仍应使用预发布标识。正式版需要按其声明范围完成验收并另行发布；本地准备完成不会自动推送或创建 GitHub Release。
