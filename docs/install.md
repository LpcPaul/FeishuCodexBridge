# 安装与卸载

## 前置条件

- macOS。
- Python 3。
- 已安装并登录 Codex CLI。
- 已准备飞书机器人的 `App ID` 和 `App Secret`。

## 一键安装

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/LpcPaul/FeishuCodexBridge/main/remote-install.sh)"
```

安装脚本会提示输入飞书应用凭证，然后自动安装 launchd 服务。

## 手动安装

```bash
git clone https://github.com/LpcPaul/FeishuCodexBridge.git
cd FeishuCodexBridge
./install.sh
```

## 安装后位置

运行目录：

```text
~/Library/Application Support/FeishuCodexBridge
```

应用副本：

```text
~/Library/Application Support/FeishuCodexBridge/app
```

配置文件：

```text
~/Library/Application Support/FeishuCodexBridge/app/.env.feishu
```

可选能力也在这个文件里开启：

```bash
FEISHU_CODEX_CARDS_ENABLED=1
FEISHU_CARDKIT_ENABLED=0
FEISHU_DOCS_ENABLED=0
FEISHU_DOCS_FOLDER_TOKEN=
```

修改配置后运行 `./bridge restart` 让服务重新读取。

LaunchAgent：

```text
~/Library/LaunchAgents/com.codex.feishu-codex-bridge.plist
```

## 服务管理

在项目目录里运行：

```bash
./bridge status
./bridge restart
./bridge logs
./bridge follow-logs
./bridge stop
./bridge start
```

## 卸载

只移除服务，保留状态库和配置：

```bash
./uninstall.sh
```

移除服务并删除运行数据：

```bash
REMOVE_FEISHU_CODEX_BRIDGE_DATA=1 ./uninstall.sh
```
