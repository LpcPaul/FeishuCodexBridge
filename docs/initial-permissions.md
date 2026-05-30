# 初始权限清单

建议在第一次配置飞书应用时，把消息、卡片、文档和建群相关权限一次性申请好。这样后续开启卡片按钮、飞书文档输出、自动创建群组时，不需要反复回到开放平台补权限、重新发布版本。

## 建议导入 JSON

在飞书开放平台的权限管理页面，如果支持批量导入权限，可以尝试使用下面这段 JSON：

```json
{
  "scopes": {
    "tenant": [
      "im:message.p2p_msg:readonly",
      "im:message.group_at_msg:readonly",
      "im:message:send_as_bot",
      "im:chat:create",
      "im:chat",
      "cardkit:card:write",
      "docx:document",
      "docx:document:create"
    ],
    "user": []
  },
  "events": [
    "im.message.receive_v1",
    "card.action.trigger"
  ]
}
```

如果后台不接受 JSON 导入，就把 `tenant` 里的 scope 逐个复制到权限搜索框里搜索并申请；`events` 里的 `im.message.receive_v1` 到「已订阅的事件」里订阅，`card.action.trigger` 到「已订阅的回调」里订阅。

卡片按钮只申请权限还不够。应用还必须在「事件与回调」里配置回调订阅方式。FeishuCodexBridge 使用长连接接收卡片回调，所以不需要公网 URL，但需要把 `card.action.trigger` 添加到已订阅回调并发布新版应用。否则点击按钮会在客户端报 `code: 200340`。

## 每个权限解决什么

| 权限或事件 | 用途 |
| --- | --- |
| `im:message.p2p_msg:readonly` | 接收用户发给机器人的单聊消息 |
| `im:message.group_at_msg:readonly` | 接收群聊里 @ 机器人的消息 |
| `im:message:send_as_bot` | 让机器人回复文本消息和普通交互卡片 |
| `im:chat:create` | 允许应用创建群组 |
| `im:chat` | 允许应用获取与更新群组信息，配合建群和后续群管理 |
| `cardkit:card:write` | 使用 CardKit 2.0 创建和更新卡片实体 |
| `docx:document` | 创建及编辑新版文档 |
| `docx:document:create` | 创建新版文档 |
| `im.message.receive_v1` | 接收飞书消息事件 |
| `card.action.trigger` | 接收用户点击卡片按钮的回调 |

## 可选敏感权限

默认不建议一开始申请 `im:message.group_msg`，它会让应用接收群内所有消息。只有当你明确需要机器人不被 @ 也读取群聊内容时，再单独申请。
