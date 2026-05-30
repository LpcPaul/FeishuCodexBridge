# 版本管理

FeishuCodexBridge 使用语义化版本：

```text
MAJOR.MINOR.PATCH
```

## 当前版本

```text
0.3.0
```

## 发布规则

- `PATCH`：修复 bug、文档修正、不改变行为的小改动。
- `MINOR`：新增向后兼容能力，例如新的安装选项、新平台支持。
- `MAJOR`：改变安装方式、状态库兼容性或核心行为。

## 发布清单

每次发布前需要：

1. 更新 `VERSION`。
2. 更新 `CHANGELOG.md`。
3. 跑测试：

```bash
python3 -m unittest -v test_feishu_codex_bridge.py
python3 -m py_compile feishu_codex_bridge.py test_feishu_codex_bridge.py
```

4. 创建 Git tag：

```bash
git tag vX.Y.Z
git push origin main --tags
```
