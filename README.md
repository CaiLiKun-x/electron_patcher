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
| 新版 Chrome / Edge | **Launcher 方案**（保留原始 bundle，Keychain 不受影响） | Google Chrome、Microsoft Edge 及其 Beta/Canary 版本 |
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

### Launcher 方案（Chrome / Edge）

Chrome / Edge 会在启动时验证整个 `.app` bundle 的代码签名。任何对 bundle 的修改都会导致 Chrome 废弃 Keychain 中的 "Chrome Safe Storage" 密钥并强制账号重新验证。Launcher 方案通过完全不修改原始 bundle 来彻底规避此问题。

1. 将原始 `.app` **整体重命名**为 `<应用名>_original.app`（完整保留 Google 签名）
2. 在原路径创建最小 launcher `.app`，从原始 bundle 复制 `Info.plist` 和图标：
   ```
   Google Chrome.app/          ← launcher（ad-hoc 签名）
     Contents/
       Info.plist               ← 从原始 bundle 复制，保留 bundle ID / URL scheme
       Resources/*.icns         ← 图标（保持 Dock 外观）
       MacOS/Google Chrome      ← shell 脚本
   Google Chrome_original.app/ ← 原始 bundle（Google 签名完整保留）
   ```
3. launcher shell 脚本内容：
   ```bash
   #!/bin/bash
   # patched-by-electron_patcher
   exec "/Applications/Google Chrome_original.app/Contents/MacOS/Google Chrome" --use-angle=gl "$@"
   ```
4. 对 launcher bundle 进行 ad-hoc 签名

Chrome 进程实际运行的是 `Google Chrome_original.app` 内的原始 binary，`NSBundle.mainBundle` 解析到的也是 `Google Chrome_original.app`，Google 签名始终有效，Keychain 和账号登录完全不受影响。

### Binary 注入方式

用于无 Keychain 顾虑的其他 Electron 应用（如汽水音乐）。

1. 将原始二进制重命名为 `<名称>_original`（备份）
2. 在原位置创建 Shell 包装脚本，注入 `--use-angle=gl`
3. 提取原始 binary 的 entitlements，对最外层 `.app` 进行 ad-hoc 重签名（不使用 `--deep`）

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
| Launcher | 删除 launcher `.app`，将 `*_original.app` 重命名恢复到原始路径 |
| Binary 注入 | 删除包装脚本，将 `*_original` 恢复为原始二进制，重新签名 |

## 注意事项

**浏览器更新后需重新 Patch**

Chrome/Edge 更新时可能覆盖原始路径下的内容（launcher 被替换为新版 Chrome）。更新后重新运行本脚本即可。`*_original.app` 也可能被更新程序删除，此时需先还原再重新修补。

**ad-hoc 签名与 Gatekeeper**

Launcher `.app` 使用 ad-hoc 签名，仅代表"内容未被篡改"，不涉及开发者身份。原始 Chrome/Edge bundle 的 Google 签名完整保留，不受影响。

**Chrome/Edge Keychain 和账号登录说明**

v4.0.4 采用 Launcher 方案，Chrome 进程实际运行的是 `*_original.app` 内的原始 binary，Google 代码签名始终有效，Keychain 中的 "Chrome Safe Storage" 密钥正常访问，账号登录状态不受影响。

若已使用 v4.0.0–v4.0.3 修补过 Chrome/Edge，请先执行**还原**，再用 v4.0.4 重新**修补**。

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

### v4.0.4 (2026-04-16)

- **重构** Chrome / Edge 改用 Launcher 方案：将原始 `.app` bundle 整体重命名备份，在原路径创建最小 launcher `.app`，原始 Google 代码签名完整保留，彻底解决 Keychain 和账号重新验证问题

### v4.0.3 (2026-04-16)

- **新增** 扫描 `~/Library/Containers/*/Data/Library/Application Support/*/Local State`，覆盖 macOS 沙盒应用（企业微信等）

### v4.0.2 (2026-04-16)

- **修复** Binary 注入重签名去掉 `--deep`，内部原始 binary 保留 Google 开发者证书签名，彻底解决 Keychain 拒绝访问和账号重新验证问题

### v4.0.1 (2026-04-16)

- **新增** VS Code 家族内置列表：VS Code、VS Code Insiders、VSCodium、Cursor、Kiro、Windsurf、Trae、Void，优先以友好名称展示，不再依赖目录名
- **修复（不完整）** Binary 注入重签名时保留原始 entitlements（未能解决 Keychain 问题，已在 v4.0.2 彻底修复）

### v4.0.0 (2026-04-16)

- 初始发布
