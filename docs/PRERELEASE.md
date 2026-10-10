# 0.2.0-rc.2 预发布准备

本候选将精简项目记忆、按需原文追溯和 MOSS 导航整理为一个可独立试用的工具包。所有私有数据、账号配置和本机部署回执均不包含在发布文件中。

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

本候选只在 macOS 上验证。定时器、客户端自动触发、模型诊断启用和外部消息发送由使用者单独设置。协议首次采用和后台功能启用是独立动作。

GitHub 发布应使用预发布标识。公开发行前需要用户的发布指令；本地准备完成不会自动推送或创建 GitHub Release。
