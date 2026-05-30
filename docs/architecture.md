# 架构说明

FeishuCodexBridge 是一个本机常驻进程。

```text
Feishu 用户消息
  -> 飞书开放平台 WebSocket 事件
  -> FeishuCodexBridge
  -> codex exec / codex exec resume
  -> Codex 文本结果 / 卡片声明 / 文档声明
  -> 飞书机器人回复 / 飞书交互卡片 / 飞书文档链接
```

## 核心模块

- `feishu_codex_bridge.py`：Bridge 主程序。
- `start_feishu_codex_bridge.sh`：launchd 调用的启动脚本。
- `install.sh`：macOS 安装脚本。
- `bridge`：本地服务管理命令。
- `state.sqlite`：运行时状态库，默认在 `~/Library/Application Support/FeishuCodexBridge/state.sqlite`。

## 路由策略

- 飞书私聊主机器人：进入长期主 Bot 入口，由 Bridge 做话题切分。
- 飞书话题或回复串：映射为独立 Codex 对话。
- 飞书群里 @ 机器人：启动新的 Codex 对话。
- 两人小群中未 @ 的普通消息：如果飞书已投递群内全量消息，并且成员查询显示只有 1 个真人用户，则进入稳定的群直聊会话。
- 其他群里没有 @ 机器人，也不在已有回复串里的普通消息：忽略。

## 上下文管控

私聊主 Bot 默认继续当前活跃话题。

如果超过 2 小时无互动，并且没有飞书触发的 Codex 任务正在运行，Bridge 会由后台检查器主动发送话题边界卡片，并把后续消息默认切到新话题。

这张轻量卡片不会等到用户下一次发消息才出现。卡片提供两个动作：

- `继续上个话题`
- `保持新话题`

用户也可以直接发：

- `继续上个话题`
- `继续刚才那个`
- `继续刚才的话题`
- `回到上个话题`

## 长任务规则

Bridge 不设置任务执行超时。

只要 Codex 任务仍在执行，Bridge 就不会因为 2 小时到了而切新话题。任务运行期间，Bridge 默认每 2 小时发一次进度提示。任务完成后，才重新计算 2 小时空闲时间，并由后台检查器主动发送话题边界卡片。

## 移动端回复格式

每次把飞书消息转给 Codex 时，Bridge 都会附加移动端回复格式要求：

```text
你正在通过手机通信软件回复用户。请使用移动端可读格式：
先给结论/摘要/判断；短段落；根据消息类型组织内容；
不要输出大段长文；如果内容较长，只回复第一层结论/摘要/判断，用户要求详细答复时再展开。
```

这不是结果模板。最终回复仍然由 Codex 生成。

## Codex 卡片协议

Bridge 会在提示词里告诉 Codex：如果需要发送飞书交互卡片，可以在最终回复中输出：

````text
```feishu-card
{ "config": { "wide_screen_mode": true }, "elements": [] }
```
````

Bridge 会从普通文本里移除这段 JSON，并用飞书 `interactive` 消息发送卡片。

如果卡片按钮有 `value` 对象，Bridge 会自动补充：

- `bridge_action=codex_card`
- `session_key`
- `origin_message_id`

用户点击按钮后，Bridge 会先立即返回卡片回调响应，避免飞书客户端等待后台处理时提示目标服务超时。随后 Bridge 在后台给聊天发一条可见回执。默认情况下，Bridge 把点击 payload 转成 `[card-click] {...}`，并用同一个 `session_key` 继续 `codex exec resume`，最终结果仍回到聊天里。

如果某张卡片提交后不需要 Codex 继续处理，卡片回调 `value` 可以设置：

- `requires_codex=false`
- 或 `feedback_mode="ack"`

这种卡片只会发默认收到回执，不启动 Codex。

## 飞书文档协议

文档能力只有在 `FEISHU_DOCS_ENABLED=1` 时启用。启用后，Bridge 会在提示词里告诉 Codex：复杂内容可以输出：

```text
<feishu_doc title="标题">正文</feishu_doc>
```

Bridge 会调用飞书新版文档接口创建文档、写入正文，并把文档链接发回聊天。文档创建位置由 `FEISHU_DOCS_FOLDER_TOKEN` 控制；未配置时使用应用默认位置。
