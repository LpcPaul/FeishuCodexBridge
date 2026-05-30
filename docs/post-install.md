# 安装后必做检查

安装脚本只负责把 Bridge 装到本机并启动 launchd 服务。飞书开放平台里的应用配置仍然需要你手动确认一次，否则消息能收到，但卡片按钮可能会报 `code: 200340`。

## 1. 确认事件和回调走长连接

打开飞书开放平台应用后台：

1. 进入「开发配置」>「事件与回调」。
2. 在「回调配置」或「订阅方式」中选择使用长连接接收事件和回调。
3. 在「已订阅的事件」中确认有：
   - `im.message.receive_v1`
4. 在「已订阅的回调」中确认有：
   - `card.action.trigger`
5. 保存后发布新版应用。

如果 `card.action.trigger` 放在事件里而不是回调里，卡片按钮仍可能不可用。

## 2. 确认权限已经发布

至少需要：

```text
im:message.p2p_msg:readonly
im:message.group_at_msg:readonly
im:message:send_as_bot
```

建议第一次就按 [初始权限清单](initial-permissions.md) 把卡片、文档、建群相关权限一起申请并发布。

## 3. 本机验收

在项目目录运行：

```bash
./bridge status
./bridge logs 30
```

日志里出现类似下面的内容，表示本机长连接已连上飞书：

```text
connected to wss://msg-frontier.feishu.cn
```

## 4. 飞书验收

1. 给机器人发一句普通消息，确认能收到文本回复。
2. 等 Bridge 发出话题卡片后，点击「继续上个话题」。
3. 如果仍提示 `code: 200340`，优先回到第 1 步检查 `card.action.trigger` 是否在「已订阅的回调」里，并确认应用已经发布新版。
