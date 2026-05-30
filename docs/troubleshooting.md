# 故障排查

## 服务是否启动

```bash
./bridge status
```

查看日志：

```bash
./bridge logs
./bridge follow-logs
```

## 收不到飞书消息

优先检查：

- 飞书应用是否开启机器人能力。
- 是否订阅了 `im.message.receive_v1`。
- 是否申请了单聊或群聊 @ 消息权限。
- 应用权限是否已经发布并生效。
- 机器人是否在目标群里。

## 能收到消息但不能回复

检查是否申请了：

```text
im:message:send_as_bot
```

同时确认应用已经发布到当前用户或群所在的可用范围。

## 点击卡片按钮提示 code: 200340

`200340` 是飞书客户端返回的卡片回调配置错误。它通常表示应用没有配置卡片回调地址，或者没有把卡片回调切到长连接订阅方式。

FeishuCodexBridge 使用长连接接收卡片回调，不需要公网 URL。需要在飞书开放平台检查：

1. 打开应用后台的「开发配置」>「事件与回调」。
2. 在「回调配置」或「订阅方式」中选择使用长连接接收事件和回调。
3. 在「已订阅的回调」中添加 `card.action.trigger`。
4. 保存后发布新版应用，让配置生效。

如果本地日志里没有出现卡片点击相关记录，说明回调还没有到达本机服务，优先检查以上飞书后台配置。

## 日志里提示缺少 App ID 或 App Secret

检查配置文件：

```text
~/Library/Application Support/FeishuCodexBridge/app/.env.feishu
```

需要包含：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

## Codex 找不到或 Node 找不到

运行：

```bash
./bridge doctor
```

如果 `codex` 或 `node` 为空，先在本机安装并登录 Codex CLI，然后重新运行：

```bash
./install.sh
```

## 回复很慢

Bridge 收到消息后会先发确认文案：

```text
收到，我要开始干活了，稍等我
```

正式回复耗时主要取决于 Codex CLI 和模型执行时间。Bridge 不会本地伪造结果。

## 状态库损坏或想重置

停止服务后，可以备份并移除：

```text
~/Library/Application Support/FeishuCodexBridge/state.sqlite
```

重新启动后会自动创建新的状态库。
