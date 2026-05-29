# 飞书机器人创建与权限配置

FeishuCodexBridge 不能替你自动创建飞书应用。你需要先在飞书开放平台创建一个企业自建应用，开启机器人能力，然后把 `App ID` 和 `App Secret` 提供给安装脚本。

官方入口：

- 飞书开放平台：https://open.feishu.cn/
- 接收消息事件：https://open.feishu.cn/document/server-docs/im-v1/message/events/receive?lang=zh-CN
- 回复消息接口：https://open.feishu.cn/document/server-docs/im-v1/message/reply?lang=zh-CN
- 长连接接收事件：https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case?lang=zh-CN

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

这个事件用于接收用户发给机器人的消息。

## 4. 申请最小权限

基础聊天场景建议只申请这些权限：

| 权限 | 用途 |
| --- | --- |
| `im:message.p2p_msg:readonly` | 接收用户和机器人的单聊消息 |
| `im:message.group_at_msg:readonly` | 接收群聊里 @ 机器人的消息 |
| `im:message:send_as_bot` | 让机器人回复消息 |

如果你希望机器人读取群里所有消息，而不只是 @ 机器人的消息，再考虑申请：

| 权限 | 用途 |
| --- | --- |
| `im:message.group_msg` | 接收群组中所有消息 |

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
