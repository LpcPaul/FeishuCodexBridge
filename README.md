# FeishuCodexBridge

FeishuCodexBridge 是一个本机常驻服务，用飞书机器人把消息转给本机 Codex CLI，再把 Codex 的文本结果回到飞书。

它适合已经在本机使用 Codex、同时希望通过飞书私聊或群聊交代任务的人。

## 它解决什么

- 在飞书里和本机 Codex 对话。
- 私聊主 Bot 自动管理长期上下文和话题切换。
- 群聊里 @ 机器人后启动一个独立 Codex 会话。
- Codex 长任务执行时，Bridge 保持任务上下文，不会因为 2 小时空闲规则切走话题。
- 当前没有任务运行且空闲达到 2 小时后，Bridge 主动发送话题边界卡片，后续消息默认进入新话题。
- 回复默认按手机通信软件可读格式约束：先给结论/摘要/判断，短段落，内容长时先给第一层摘要。
- Codex 可以声明飞书交互卡片，Bridge 默认用 CardKit 2.0 发送可提交表单卡片，并把按钮点击续回同一个 Codex 会话。
- 可选开启飞书文档能力：复杂长内容可以由 Bridge 创建成飞书文档，再把链接发回聊天。

## 它不是什么

- 不是飞书版终端。
- 不是飞书工作流平台。
- 不做 Codex 执行过程可视化。
- 不把所有结果强制套成固定卡片模板；卡片由 Codex 按需声明。
- 不做完整飞书文档协作平台；文档能力只负责创建文档、写入正文和回传链接。
- 不内置飞书表格、多维表格或任务能力；这些由 Codex 自己通过正常工具路线处理。

## 安装前准备

你需要先准备两样东西：

1. 本机已经安装并登录 Codex CLI。
2. 一个飞书自建应用机器人的 `App ID` 和 `App Secret`。

飞书机器人创建步骤见 [docs/feishu-app-setup.md](docs/feishu-app-setup.md)。

如果你希望后续直接使用卡片按钮、飞书文档或一条命令创建群组，建议第一次配置应用时就按 [docs/initial-permissions.md](docs/initial-permissions.md) 把权限一次性申请好。

安装完成后还需要按 [docs/post-install.md](docs/post-install.md) 做一次飞书后台验收：尤其要把 `card.action.trigger` 放到「已订阅的回调」并发布新版应用，否则点击卡片按钮会报 `code: 200340`。

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
FEISHU_TOPIC_NOTICE_POLL_SECONDS=60
FEISHU_TASK_PROGRESS_SECONDS=7200
FEISHU_ACK_TEXT="收到，我要开始干活了，稍等我"
FEISHU_GROUP_AUTO_REPLY_ENABLED=1
FEISHU_GROUP_AUTO_REPLY_MAX_HUMAN_MEMBERS=1
FEISHU_GROUP_AUTO_REPLY_CHAT_IDS=
FEISHU_GROUP_MEMBER_CACHE_SECONDS=600
FEISHU_CODEX_CARDS_ENABLED=1
FEISHU_CARDKIT_ENABLED=1
FEISHU_DOCS_ENABLED=0
FEISHU_DOCS_FOLDER_TOKEN=
FEISHU_DOCS_AUTO_MIN_CHARS=4500
```

## 卡片与文档能力

卡片能力默认开启，且默认使用 CardKit 2.0。Codex 如果需要发交互卡片，会在最终回复中输出 `feishu-card` JSON 块；Bridge 会把 JSON 2.0 卡片创建成卡片实体，再按 `card_id` 发送，并把按钮点击或表单提交转回同一个 Codex 会话。

需要多选、表单和提交反馈时，Codex 应使用 JSON 2.0 的 `form` 容器和 `multi_select_static` 组件。Bridge 会把旧式 `checkbox_group` 测试卡片自动转换成 CardKit 2.0 表单，避免飞书接口返回 `unsupported type of block`。

卡片提交后，Bridge 会在聊天里额外发一条可见回执。默认模式会继续把提交内容交给 Codex 处理，并在完成后返回结果；如果某张卡片只需要确认收到，回调 `value` 可以设置 `requires_codex:false` 或 `feedback_mode:"ack"`，Bridge 就只发默认回执，不启动 Codex。

飞书文档能力默认关闭。开启前需要先在飞书开放平台给应用申请新版文档创建/编辑权限，然后配置：

```bash
FEISHU_DOCS_ENABLED=1
FEISHU_DOCS_FOLDER_TOKEN=可选的目标文件夹 token
```

开启后，Codex 可以输出 `<feishu_doc title="标题">正文</feishu_doc>`，Bridge 会创建文档并把链接发回飞书。你也可以在飞书里发送 `/docs` 查看当前文档能力状态。

## 文档

- [飞书机器人创建与权限配置](docs/feishu-app-setup.md)
- [初始权限清单](docs/initial-permissions.md)
- [安装后必做检查](docs/post-install.md)
- [安装与卸载](docs/install.md)
- [权限说明](docs/permissions.md)
- [架构说明](docs/architecture.md)
- [故障排查](docs/troubleshooting.md)
- [版本管理](docs/versioning.md)

## 版本

当前版本：`0.4.0`

版本记录见 [CHANGELOG.md](CHANGELOG.md)。
