# 飞书机器人创建与权限配置

FeishuCodexBridge 不能替你自动创建飞书应用。你需要先在飞书开放平台创建一个企业自建应用，开启机器人能力，然后把 `App ID` 和 `App Secret` 提供给安装脚本。

官方入口：

- 飞书开放平台：https://open.feishu.cn/
- 接收消息事件：https://open.feishu.cn/document/server-docs/im-v1/message/events/receive?lang=zh-CN
- 回复消息接口：https://open.feishu.cn/document/server-docs/im-v1/message/reply?lang=zh-CN
- 长连接接收事件：https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case?lang=zh-CN
- 创建群组：https://open.feishu.cn/document/server-docs/im-v1/chat/create?lang=zh-CN
- 创建新版文档：https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/create?lang=zh-CN

## 1. 创建企业自建应用

1. 打开飞书开放平台。
2. 进入「开发者后台」。
3. 创建「企业自建应用」。
4. 进入应用详情页。
5. 在「凭证与基础信息」里找到：
   - `App ID`
   - `App Secret`

安装 FeishuCodexBridge 时只需要你手动提供这两个值。

## 2. 开启机器人能力

进入应用后台的「应用能力」或「机器人」相关页面，开启机器人能力。

如果没有开启机器人能力，Bridge 即使连接成功，也无法作为机器人收发消息。

## 3. 配置事件订阅

FeishuCodexBridge 使用飞书开放平台的长连接 WebSocket 接收事件，不需要公网回调地址。

在应用后台里：

1. 打开「事件与回调」。
2. 选择使用长连接方式接收事件。
3. 订阅事件：
   - `im.message.receive_v1`
   - `card.action.trigger`（如果要使用卡片按钮回调）

这些事件分别用于接收用户发给机器人的消息，以及接收用户点击卡片按钮后的回调。

## 4. 申请初始权限

建议第一次就把消息、卡片、文档和创建群组相关权限一起申请，避免后续每开一个能力都要重新发布应用版本。完整 JSON 见 [初始权限清单](initial-permissions.md)。

基础必需权限：

| 权限 | 用途 |
| --- | --- |
| `im:message.p2p_msg:readonly` | 接收用户和机器人的单聊消息 |
| `im:message.group_at_msg:readonly` | 接收群聊里 @ 机器人的消息 |
| `im:message:send_as_bot` | 让机器人回复消息 |

如果你希望 Codex 能发带按钮的飞书卡片，并让点击继续回到 Codex 会话，需要确保卡片回调事件已经订阅。

建议一开始同时申请：

| 权限或事件 | 用途 |
| --- | --- |
| `card.action.trigger` | 接收交互卡片按钮点击 |
| `cardkit:card:write` | 以后启用 CardKit 2.0 卡片创建和更新 |
| `docx:document` 或 `docx:document:create` | 以后启用飞书文档创建/编辑 |
| `im:chat:create` | 以后支持一条命令创建群组 |
| `im:chat` | 配合建群后的群组信息获取和更新 |

如果你希望 Codex 能创建飞书文档，需要额外申请新版文档相关权限。建议在权限管理页面搜索：

| 权限方向 | 用途 |
| --- | --- |
| 创建新版文档 | 让 Bridge 调用 `docx/v1/documents` 创建文档 |
| 查看、评论和编辑新版文档 | 后续写入或编辑文档内容 |
| 云空间文件夹读写 | 当你配置 `FEISHU_DOCS_FOLDER_TOKEN`，把文档放入指定文件夹时需要 |

文档能力不是默认开启。权限生效后，还需要在 `.env.feishu` 里设置：

```bash
FEISHU_DOCS_ENABLED=1
FEISHU_DOCS_FOLDER_TOKEN=可选的目标文件夹 token
```

如果你希望机器人读取群里所有消息，而不只是 @ 机器人的消息，再考虑申请：

| 权限 | 用途 |
| --- | --- |
| `im:message.group_msg` | 接收群组中所有消息 |

两人小群自动回复还需要应用能读取群成员列表，否则 Bridge 收到未 @ 消息后无法判断这个群是不是只有你和机器人。

这个权限更敏感，不建议作为默认安装要求。

## 5. 发布应用版本

飞书权限修改后，通常需要发布应用版本，或者让企业管理员审批后生效。

如果 Bridge 已经启动但收不到消息，优先检查：

- 应用是否已经发布到可用范围。
- 机器人是否在目标群里。
- `im.message.receive_v1` 是否已经订阅。
- 上面的最小权限是否已经申请并生效。

## 6. 安装 Bridge

准备好 `App ID` 和 `App Secret` 后，回到本项目运行：

```bash
./install.sh
```

或使用远程安装：

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/LpcPaul/FeishuCodexBridge/main/remote-install.sh)"
```
