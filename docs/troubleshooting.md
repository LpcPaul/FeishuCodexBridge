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

## 卡片发送失败，日志提示 unsupported type of block

如果日志里出现：

```text
Failed to create card content
unsupported type of block
ErrorValue: checkbox_group
```

说明 Codex 生成了普通飞书卡片不支持的旧式组件。当前 Bridge 默认启用 CardKit 2.0，多选表单应使用 JSON 2.0 的 `form` 容器和 `multi_select_static` 组件。

Bridge 会自动把旧式 `checkbox_group` 测试卡片转换成 CardKit 2.0 表单。若仍失败，优先检查：

1. 配置文件中 `FEISHU_CARDKIT_ENABLED=1`。
2. 飞书应用已申请并发布 `cardkit:card:write` 权限。
3. 卡片 JSON 已声明 `"schema": "2.0"`，且表单提交按钮在 `form` 容器内。

## 卡片提交后飞书提示已提交，但聊天里没有回复

飞书客户端的“已提交”只代表卡片回调已经被客户端接受，不代表 Bridge 已经把处理结果发回聊天。

Bridge 会在收到 Codex 卡片提交后额外发送一条聊天内回执。默认回执是“已收到你的提交，正在继续处理。”，随后继续把提交内容交给 Codex；如果卡片设置了 `requires_codex:false` 或 `feedback_mode:"ack"`，Bridge 只发送“已收到你的提交。”，不启动 Codex。

如果日志里出现 `Invalid ids`，通常说明这张卡片不是作为某条真实飞书消息的回复发出，或者回调里带的是测试用临时消息编号。Bridge 会优先使用卡片回调里的真实消息编号，必要时改为按 `chat_id` 主动发消息。

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
