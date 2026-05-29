# FeishuCodexBridge

FeishuCodexBridge 是一个本机常驻服务，用飞书机器人把消息转给本机 Codex CLI，再把 Codex 的文本结果回到飞书。

它适合已经在本机使用 Codex、同时希望通过飞书私聊或群聊交代任务的人。

## 它解决什么

- 在飞书里和本机 Codex 对话。
- 私聊主 Bot 自动管理长期上下文和话题切换。
- 群聊里 @ 机器人后启动一个独立 Codex 会话。
- Codex 长任务执行时，Bridge 保持任务上下文，不会因为 2 小时空闲规则切走话题。
- 回复默认按手机通信软件可读格式约束：先给结论/摘要/判断，短段落，内容长时先给第一层摘要。

## 它不是什么

- 不是飞书版终端。
- 不是飞书工作流平台。
- 不做 Codex 执行过程可视化。
- 不做结果卡片模板；Codex 最终返回什么，就按文本转发什么。
- 不内置飞书文档、表格、多维表格或任务能力；这些由 Codex 自己通过正常工具路线处理。

## 安装前准备

你需要先准备两样东西：

1. 本机已经安装并登录 Codex CLI。
2. 一个飞书自建应用机器人的 `App ID` 和 `App Secret`。

飞书机器人创建步骤见 [docs/feishu-app-setup.md](docs/feishu-app-setup.md)。

## 一键安装

macOS 用户可以运行：

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/LpcPaul/FeishuCodexBridge/main/remote-install.sh)"
```

脚本会提示你输入：

- `Feishu App ID`
- `Feishu App Secret`

然后自动完成：

- 安装 Python 依赖。
- 探测 `codex` 和 `node` 路径。
- 写入本机 `.env.feishu`。
- 安装 launchd 后台服务。
- 启动 Bridge。

如果你不想使用远程安装脚本，也可以手动安装：

```bash
git clone https://github.com/LpcPaul/FeishuCodexBridge.git
cd FeishuCodexBridge
./install.sh
```

## 服务管理

```bash
./bridge status
./bridge restart
./bridge logs
./bridge follow-logs
./bridge uninstall
```

默认运行目录：

```text
~/Library/Application Support/FeishuCodexBridge
```

状态数据库：

```text
~/Library/Application Support/FeishuCodexBridge/state.sqlite
```

## 飞书使用方式

- 私聊机器人：进入长期主 Bot 入口，由 Bridge 管理话题切分。
- 群聊 @ 机器人：启动一个新的 Codex 对话。
- 群聊消息回复串：继续同一个 Codex 对话。
- `/new` 或 `新会话`：当前飞书容器重新开始一个 Codex 对话。
- `/clear` 或 `清空上下文`：清空当前飞书容器绑定的 Codex 对话。
- `/status` 或 `当前会话`：查看当前飞书容器绑定的 Codex 会话。
- `继续上个话题`：私聊主 Bot 切回上一个话题。

## 核心配置

安装后配置文件在：

```text
~/Library/Application Support/FeishuCodexBridge/app/.env.feishu
```

常用变量：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CODEX_WORKDIR="$HOME"
PYTHON_BIN=/path/to/python3
NODE_BIN=/path/to/node
CODEX_BIN=/path/to/codex
FEISHU_TOPIC_IDLE_SECONDS=7200
FEISHU_TASK_PROGRESS_SECONDS=7200
FEISHU_ACK_TEXT="收到，我要开始干活了，稍等我"
```

## 文档

- [飞书机器人创建与权限配置](docs/feishu-app-setup.md)
- [安装与卸载](docs/install.md)
- [权限说明](docs/permissions.md)
- [架构说明](docs/architecture.md)
- [故障排查](docs/troubleshooting.md)
- [版本管理](docs/versioning.md)

## 版本

当前版本：`0.1.0`

版本记录见 [CHANGELOG.md](CHANGELOG.md)。
