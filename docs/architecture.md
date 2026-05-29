# 架构说明

FeishuCodexBridge 是一个本机常驻进程。

```text
Feishu 用户消息
  -> 飞书开放平台 WebSocket 事件
  -> FeishuCodexBridge
  -> codex exec / codex exec resume
  -> Codex 文本结果
  -> 飞书机器人回复
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
- 群里没有 @ 机器人，也不在已有回复串里的普通消息：忽略。

## 上下文管控

私聊主 Bot 默认继续当前活跃话题。

如果超过 2 小时无互动，并且没有飞书触发的 Codex 任务正在运行，下一条消息会自动开启新话题。

新话题开启时，Bridge 会发一张轻量卡片：

- `继续上个话题`
- `保持新话题`

用户也可以直接发：

- `继续上个话题`
- `继续刚才那个`
- `继续刚才的话题`
- `回到上个话题`

## 长任务规则

Bridge 不设置任务执行超时。

只要 Codex 任务仍在执行，Bridge 就不会因为 2 小时到了而切新话题。任务运行期间，Bridge 默认每 2 小时发一次进度提示。任务完成后，才重新计算 2 小时空闲时间。

## 移动端回复格式

每次把飞书消息转给 Codex 时，Bridge 都会附加移动端回复格式要求：

```text
你正在通过手机通信软件回复用户。请使用移动端可读格式：
先给结论/摘要/判断；短段落；根据消息类型组织内容；
不要输出大段长文；如果内容较长，只回复第一层结论/摘要/判断，用户要求详细答复时再展开。
```

这不是结果模板。最终回复仍然由 Codex 生成。
