# electron_patcher

修复 macOS 上 Chromium / Electron 应用的 GPU 渲染异常，强制使用 **OpenGL (ANGLE)** 后端。

## 背景

部分 Electron 应用和浏览器在 macOS 上使用 Metal 作为 ANGLE 图形后端时，会出现：

- GPU 进程崩溃、反复重启
- 界面渲染花屏、黑块、闪烁
- 视频/动画卡顿或不渲染

通过强制切换到 OpenGL 后端（`--use-angle=gl`）可解决上述问题。

## 支持的应用

| 类型 | 修补方式 | 典型应用 |
|------|----------|----------|
| VS Code 家族（内置列表） | 修改 `Local State` 配置文件 | VS Code、VS Code Insiders、VSCodium、Cursor、Kiro、Windsurf、Trae、Void |
| 其他 Electron 应用（自动扫描） | 修改 `Local State` 配置文件 | Slack、Discord、Notion 等（含 Containers 沙盒应用） |
| 新版 Chrome / Edge | 二进制包装脚本注入启动参数 | Google Chrome、Microsoft Edge 及其 Beta/Canary 版本 |
| 无 Local State 的 Electron 应用 | 二进制包装脚本注入启动参数 | 汽水音乐等 |
| 自定义应用 | 二进制包装脚本注入启动参数 | 任意 `.app` 包 |

> **为何 Chrome/Edge 不能用 Local State 方式？**
> 新版 Chrome/Edge 启动时会重置 `Local State` 中的实验性标志，必须通过命令行参数 `--use-angle=gl` 才能生效。

## 环境要求

- macOS（Intel / Apple Silicon 均支持）
- Python 3.10+（系统自带，无需额外安装）
- 修改 `/Applications` 内的应用需要管理员权限

## 使用方法

```bash
# 普通 Electron 应用（不需要 sudo）
python3 electron_patcher.py

# 修补 Chrome / Edge 等系统应用（需要 sudo）
sudo python3 electron_patcher.py
```

### 操作流程

**第一步：选择模式**

```
============================================================
  electron_patcher  —  强制使用 OpenGL (ANGLE) 渲染
============================================================

  请选择操作模式：
  1. Patch   — 强制应用使用 OpenGL (ANGLE) 渲染
  2. 还原    — 移除修补，恢复原始状态
  q. 退出

>>> 1
```

**第二步：从扫描列表中选择应用**

```
  #   应用名称                          类型            状态
  -----------------------------------------------------------------
  1   Discord                            Local State
  2   Slack                              Local State     [已 patch]
  3   Google Chrome                      Binary 注入
  4   Microsoft Edge                     Binary 注入     [已 patch]
  5   汽水音乐                           Binary 注入
  c   自定义应用（Binary 注入）

输入序号选择要修补的应用（1,3,5 或 2-4；a 全选；c 自定义；q 退出）：
>>> 1,3
```

**第三步：确认后执行**

```
即将修补 2 个应用：
  - Discord
  - Google Chrome

确认修补？[y/N] y
```

### 选择语法

| 输入 | 含义 |
|------|------|
| `3` | 只选第 3 个 |
| `1,3,5` | 选第 1、3、5 个 |
| `2-4` | 选第 2 到 4 个（含） |
| `a` / `all` | 全选 |
| `c` | 自定义输入 .app 路径（仅 Patch 模式） |
| `q` | 退出 |

### 自定义应用

在 Patch 模式输入 `c`，然后输入任意 `.app` 的路径：

```
路径 >>> /Applications/网易云音乐.app

  检测到 3 个可执行文件，请选择主二进制：
    1. 网易云音乐
    2. 网易云音乐 Helper (GPU)
    3. 网易云音乐 Helper (Renderer)
  序号 >>> 1
```

支持带空格的路径，可以加引号或不加：

```
路径 >>> '/Applications/App With Spaces.app'
路径 >>> ~/Applications/MyApp.app
```

## 修补原理

### Local State 方式

在应用的 `Local State` JSON 文件中，向 `browser.enabled_labs_experiments` 数组写入 `use-angle@1`。

脚本按以下顺序扫描：

| 优先级 | 扫描范围 | 路径模式 |
|--------|----------|----------|
| 1 | VS Code 家族固定列表 | `~/Library/Application Support/<已知应用>/Local State` |
| 2 | Application Support 一级子目录 | `~/Library/Application Support/*/Local State` |
| 3 | Google 子目录 | `~/Library/Application Support/Google/*/Local State` |
| 4 | Containers 沙盒目录 | `~/Library/Containers/*/Data/Library/Application Support/*/Local State` |

最终写入的内容：

```json
{
  "browser": {
    "enabled_labs_experiments": [
      "use-angle@1"
    ]
  }
}
```

### Binary 注入方式

1. 将原始二进制重命名为 `<名称>_original`（备份）
2. 在原位置创建 Shell 包装脚本：
   ```bash
   #!/bin/bash
   # patched-by-electron_patcher
   exec "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome_original" --use-angle=gl "$@"
   ```
3. 提取原始 binary 的 entitlements，使用 `codesign --force --sign -`（不加 `--deep`）携带原始 entitlements 对最外层 `.app` 进行 ad-hoc 重签名，内部二进制（含原始 Chrome binary）保留原有签名

## 还原

运行脚本，选择模式 `2`（还原）。还原模式只会列出已修补的应用：

```
>>> 2

发现 3 个已修补的应用：
  1   Google Chrome      Binary 注入     [已 patch]
  2   Discord            Local State     [已 patch]
  3   汽水音乐           Binary 注入     [已 patch]

输入序号选择要还原的应用（1,3,5 或 2-4；a 全选；q 退出）：
>>> a
```

| 类型 | 还原操作 |
|------|----------|
| Local State | 从 `enabled_labs_experiments` 中移除 `use-angle@*` 条目 |
| Binary 注入 | 删除包装脚本，将 `*_original` 恢复为原始二进制，重新签名 |

## 注意事项

**浏览器更新后需重新 Patch**

Chrome/Edge 更新时会覆盖 `Contents/MacOS/` 下的文件，包装脚本会被还原为原始二进制。更新后重新运行本脚本即可。

**ad-hoc 签名与 Gatekeeper**

Binary 注入会破坏原有的苹果代码签名，脚本会自动以 ad-hoc 方式重签名。ad-hoc 签名仅表示"此文件未被篡改"，不代表开发者身份，属于本地可信范围，不影响正常使用。

**Binary 注入后 Chrome/Edge 要求重新登录 / 无法访问密码串**

Chrome/Edge 将密码加密密钥存储在 macOS Keychain 中，密钥的 ACL 与代码签名中的 entitlements 绑定。v4.0.0 的 ad-hoc 重签名会丢弃原始 entitlements，导致 Keychain 拒绝访问、Google 账号触发安全验证。v4.0.1 已修复：重签名前会先从原始 binary 提取 entitlements 并在签名时一并传入，Keychain 访问权限得以保留。

v4.0.1 的修复（仅保留 entitlements）仍不够——Keychain ACL 校验的是**签名证书**（`OU=EQHXZ8M8AV`），而非 entitlements。v4.0.2 去掉了 `--deep`，内部原始 binary 不再被重签，Chrome 进程运行时的代码身份保持 Google 开发者证书，Keychain 验证通过。

若已使用 v4.0.0 / v4.0.1 修补过 Chrome/Edge，请先执行**还原**，再用 v4.0.2 重新**修补**。

**扫描不到的情况**

| 原因 | 示例 |
|------|------|
| Application Support 下嵌套超过一级 | `Application Support/公司名/产品名/Local State` |
| Containers 内路径不符合标准四层结构 | 非常规沙盒路径 |
| `Local State` 文件名不同 | 极少数魔改 Electron |

遇到扫描不到的应用，可在 `LOCAL_STATE_CANDIDATES` 里手动补一行，或用菜单 `c` 选项走 Binary 注入。

**权限**

修改 `/Applications` 内的应用需要管理员权限，建议加 `sudo` 运行。修改 `~/Library` 内的 Local State 文件不需要额外权限。

## 更新日志

### v4.0.3 (2026-04-16)

- **新增** 扫描 `~/Library/Containers/*/Data/Library/Application Support/*/Local State`，覆盖 macOS 沙盒应用（企业微信等）

### v4.0.2 (2026-04-16)

- **修复** Binary 注入重签名去掉 `--deep`，内部原始 binary 保留 Google 开发者证书签名，彻底解决 Keychain 拒绝访问和账号重新验证问题

### v4.0.1 (2026-04-16)

- **新增** VS Code 家族内置列表：VS Code、VS Code Insiders、VSCodium、Cursor、Kiro、Windsurf、Trae、Void，优先以友好名称展示，不再依赖目录名
- **修复（不完整）** Binary 注入重签名时保留原始 entitlements（未能解决 Keychain 问题，已在 v4.0.2 彻底修复）

### v4.0.0 (2026-04-16)

- 初始发布
