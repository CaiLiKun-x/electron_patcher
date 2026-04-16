"""
electron_patcher.py: Enforce 'use-angle@1' in Chrome and Electron applications

Version 4.0.3 (2026-04-16)

策略：
  - Electron 应用：修改 Local State 文件
  - Chrome / Edge：通过二进制包装脚本注入 --use-angle=gl 命令行参数

注意：
  - 修改 Chrome/Edge 会破坏代码签名，脚本会自动进行 ad-hoc 重签名
  - 浏览器更新后包装脚本会被覆盖，需重新运行本脚本
  - 需要对 /Applications 目录有写权限（可能需要 sudo）
"""

import enum
import json
import stat
import subprocess
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Local State 方式（Electron 应用）
# ---------------------------------------------------------------------------

class ChromiumSettingsPatcher:

    class AngleVariant(enum.Enum):
        Default = "0"
        OpenGL  = "1"
        Metal   = "2"

    def __init__(self, state_file: Path) -> None:
        self._local_state_file = Path(state_file).expanduser()

    def patch(self) -> None:
        _desired_key   = "use-angle"
        _desired_value = self.AngleVariant.OpenGL.value

        if not self._local_state_file.exists():
            print("  Local State missing, creating...")
            self._local_state_file.parent.mkdir(parents=True, exist_ok=True)
            state_data = {}
        else:
            print("  Parsing Local State file")
            state_data = json.loads(self._local_state_file.read_bytes())

        if "browser" not in state_data:
            state_data["browser"] = {}
        if "enabled_labs_experiments" not in state_data["browser"]:
            state_data["browser"]["enabled_labs_experiments"] = []

        for key in state_data["browser"]["enabled_labs_experiments"]:
            if "@" not in key:
                continue
            key_pair = key.split("@")
            if len(key_pair) < 2:
                continue
            if key_pair[0] != _desired_key:
                continue
            if key_pair[1] == _desired_value:
                print(f"  {_desired_key}@{_desired_value} is already set")
                return
            index = state_data["browser"]["enabled_labs_experiments"].index(key)
            state_data["browser"]["enabled_labs_experiments"][index] = f"{_desired_key}@{_desired_value}"
            print(f"  Updated {_desired_key}@{_desired_value}")

        if f"{_desired_key}@{_desired_value}" not in state_data["browser"]["enabled_labs_experiments"]:
            state_data["browser"]["enabled_labs_experiments"].append(f"{_desired_key}@{_desired_value}")
            print(f"  Added {_desired_key}@{_desired_value}")

        print("  Writing to Local State file")
        self._local_state_file.write_text(json.dumps(state_data, indent=4))

    def unpatch(self) -> None:
        _desired_key = "use-angle"

        if not self._local_state_file.exists():
            print("  Local State not found, nothing to restore")
            return

        print("  Parsing Local State file")
        state_data = json.loads(self._local_state_file.read_bytes())

        experiments: list = state_data.get("browser", {}).get("enabled_labs_experiments", [])
        before = len(experiments)
        experiments = [e for e in experiments if not e.startswith(f"{_desired_key}@")]

        if len(experiments) == before:
            print(f"  {_desired_key} not found, nothing to remove")
            return

        state_data["browser"]["enabled_labs_experiments"] = experiments
        print(f"  Removed {_desired_key} entry")
        print("  Writing to Local State file")
        self._local_state_file.write_text(json.dumps(state_data, indent=4))


# ---------------------------------------------------------------------------
# 二进制包装方式（Chrome / Edge）
# ---------------------------------------------------------------------------

class ChromiumBinaryPatcher:

    ANGLE_FLAG = "--use-angle=gl"
    MARKER     = "# patched-by-electron_patcher"

    def __init__(self, app_path: Path, binary_name: str) -> None:
        self._app_path    = Path(app_path)
        self._binary_name = binary_name
        self._binary_path = self._app_path / "Contents" / "MacOS" / binary_name
        self._backup_path = self._app_path / "Contents" / "MacOS" / f"{binary_name}_original"

    def is_already_patched(self) -> bool:
        if not self._binary_path.exists():
            return False
        try:
            return self.MARKER in self._binary_path.read_text(errors="ignore")
        except Exception:
            return False

    def _resign(self) -> None:
        print(f"  Re-signing {self._app_path.name} with ad-hoc signature...")

        # 从原始 Mach-O binary 提取 entitlements，保留 Keychain 等系统权限。
        # patch 阶段：原始 binary 在 _backup_path；
        # unpatch 阶段：原始 binary 已恢复到 _binary_path。
        source = self._backup_path if self._backup_path.exists() else self._binary_path
        ents_file: str | None = None
        with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tmp:
            tmp_path = tmp.name

        ents_result = subprocess.run(
            ["codesign", "-d", "--entitlements", tmp_path, str(source)],
            capture_output=True, text=True,
        )
        if ents_result.returncode == 0 and Path(tmp_path).stat().st_size > 0:
            ents_file = tmp_path
            print("  Preserving original entitlements")
        else:
            Path(tmp_path).unlink(missing_ok=True)

        # 注意：不使用 --deep。
        # --deep 会递归重签名所有内部二进制（包括 *_original 备份），
        # 导致 Chrome_original 的签名从 Google 开发者证书变为 ad-hoc 匿名身份。
        # Shell wrapper exec Chrome_original 后，运行进程的代码身份即为 Chrome_original，
        # Keychain ACL 要求 Google 开发者证书（OU=EQHXZ8M8AV），ad-hoc 无法通过验证。
        # 只签名最外层 .app bundle，内部二进制保留原始 Google 签名，Keychain 访问正常。
        cmd = ["codesign", "--force", "--sign", "-"]
        if ents_file:
            cmd += ["--entitlements", ents_file]
        cmd.append(str(self._app_path))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  WARNING: codesign failed: {result.stderr.strip()}")
            else:
                print("  Re-sign successful")
        finally:
            if ents_file:
                Path(ents_file).unlink(missing_ok=True)

    def patch(self) -> None:
        if not self._binary_path.exists():
            print(f"  Binary not found: {self._binary_path}")
            return

        if self.is_already_patched():
            print(f"  Already patched: {self._binary_name}")
            return

        if not self._backup_path.exists():
            print(f"  Backing up original binary -> {self._backup_path.name}")
            self._binary_path.rename(self._backup_path)

        wrapper_script = (
            f"#!/bin/bash\n"
            f"{self.MARKER}\n"
            f'exec "{self._backup_path}" {self.ANGLE_FLAG} "$@"\n'
        )

        print(f"  Writing wrapper script")
        self._binary_path.write_text(wrapper_script)
        original_mode = self._backup_path.stat().st_mode
        self._binary_path.chmod(original_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self._resign()
        print(f"  Done: will now start with {self.ANGLE_FLAG}")

    def unpatch(self) -> None:
        if not self._backup_path.exists():
            print(f"  No backup found, nothing to restore: {self._binary_name}")
            return
        print(f"  Restoring original binary: {self._binary_name}")
        if self._binary_path.exists():
            self._binary_path.unlink()
        self._backup_path.rename(self._binary_path)
        self._resign()
        print(f"  Restored: {self._binary_name}")


# ---------------------------------------------------------------------------
# 扫描：发现所有可 patch 的应用
# ---------------------------------------------------------------------------

# VS Code 家族固定列表（Local State 方式）
LOCAL_STATE_CANDIDATES = [
    ("VS Code",           "~/Library/Application Support/Code"),
    ("VS Code Insiders",  "~/Library/Application Support/Code - Insiders"),
    ("VSCodium",          "~/Library/Application Support/VSCodium"),
    ("Cursor",            "~/Library/Application Support/Cursor"),
    ("Kiro",              "~/Library/Application Support/Kiro"),
    ("Windsurf",          "~/Library/Application Support/Windsurf"),
    ("Trae",              "~/Library/Application Support/Trae"),
    ("Void",              "~/Library/Application Support/Void"),
]

# Chrome / Edge 固定列表（二进制方式）
BINARY_PATCH_CANDIDATES = [
    # Chrome 系列
    ("/Applications/Google Chrome.app",         "Google Chrome"),
    ("/Applications/Google Chrome Beta.app",    "Google Chrome Beta"),
    ("/Applications/Google Chrome Canary.app",  "Google Chrome Canary"),
    # Edge 系列
    ("/Applications/Microsoft Edge.app",        "Microsoft Edge"),
    ("/Applications/Microsoft Edge Beta.app",   "Microsoft Edge Beta"),
    ("/Applications/Microsoft Edge Dev.app",    "Microsoft Edge Dev"),
    ("/Applications/Microsoft Edge Canary.app", "Microsoft Edge Canary"),
    # 无 Local State 的 Electron 应用
    ("/Applications/汽水音乐.app",               "汽水音乐"),
]


def scan_apps() -> list[dict]:
    """
    扫描所有可 patch 的应用，返回候选列表。
    每项格式：
      { "name": str, "type": "local_state" | "binary",
        "patched": bool, "patcher": ChromiumSettingsPatcher | ChromiumBinaryPatcher }
    """
    apps = []
    seen_state_files: set[Path] = set()

    # 1. VS Code 家族（固定列表，优先展示）
    for display_name, app_dir_str in LOCAL_STATE_CANDIDATES:
        state_file = Path(app_dir_str).expanduser() / "Local State"
        if not state_file.exists():
            continue
        seen_state_files.add(state_file)
        patcher = ChromiumSettingsPatcher(state_file)
        try:
            data = json.loads(state_file.read_bytes())
            experiments = data.get("browser", {}).get("enabled_labs_experiments", [])
            already = "use-angle@1" in experiments
        except Exception:
            already = False
        apps.append({
            "name":    display_name,
            "type":    "local_state",
            "patched": already,
            "patcher": patcher,
        })

    # 2. 扫描 Local State 应用（Electron / 旧版 Chromium，兜底扫描）
    scan_roots = [
        Path("~/Library/Application Support").expanduser(),
        Path("~/Library/Application Support/Google").expanduser(),
    ]

    for root in scan_roots:
        if not root.exists():
            continue
        for directory in sorted(root.iterdir()):
            if not directory.is_dir():
                continue
            state_file = directory / "Local State"
            if not state_file.exists() or state_file in seen_state_files:
                continue
            seen_state_files.add(state_file)

            # 跳过已知由二进制方式处理的浏览器（避免重复出现）
            skip_names = {"Google", "Microsoft Edge", "Microsoft Edge Beta",
                          "Microsoft Edge Dev", "Microsoft Edge Canary"}
            if directory.name in skip_names:
                continue

            patcher = ChromiumSettingsPatcher(state_file)
            # 检查是否已经 patch
            try:
                data = json.loads(state_file.read_bytes())
                experiments = data.get("browser", {}).get("enabled_labs_experiments", [])
                already = "use-angle@1" in experiments
            except Exception:
                already = False

            apps.append({
                "name":    directory.name,
                "type":    "local_state",
                "patched": already,
                "patcher": patcher,
            })

    # 3. 扫描 Containers 沙盒目录（路径：Containers/<bundle-id>/Data/Library/Application Support/<AppName>/Local State）
    containers_root = Path("~/Library/Containers").expanduser()
    if containers_root.exists():
        for state_file in sorted(containers_root.glob(
            "*/Data/Library/Application Support/*/Local State"
        )):
            if state_file in seen_state_files:
                continue
            seen_state_files.add(state_file)
            app_name = state_file.parent.name
            patcher = ChromiumSettingsPatcher(state_file)
            try:
                data = json.loads(state_file.read_bytes())
                experiments = data.get("browser", {}).get("enabled_labs_experiments", [])
                already = "use-angle@1" in experiments
            except Exception:
                already = False
            apps.append({
                "name":    app_name,
                "type":    "local_state",
                "patched": already,
                "patcher": patcher,
            })

    # 4. 扫描 Chrome / Edge（二进制方式）
    for app_path_str, binary_name in BINARY_PATCH_CANDIDATES:
        app_path = Path(app_path_str)
        if not app_path.exists():
            continue
        patcher = ChromiumBinaryPatcher(app_path, binary_name)
        apps.append({
            "name":    binary_name,
            "type":    "binary",
            "patched": patcher.is_already_patched(),
            "patcher": patcher,
        })

    return apps


# ---------------------------------------------------------------------------
# 自定义应用：检测 .app 包内的可用二进制
# ---------------------------------------------------------------------------

def detect_binaries(app_path: Path) -> list[str]:
    """
    列出 .app/Contents/MacOS/ 下所有可执行文件（排除已备份的 _original）。
    """
    macos_dir = app_path / "Contents" / "MacOS"
    if not macos_dir.exists():
        return []
    return [
        f.name for f in sorted(macos_dir.iterdir())
        if f.is_file()
        and not f.name.endswith("_original")
        and (f.stat().st_mode & stat.S_IXUSR)
    ]


def prompt_custom_app() -> dict | None:
    """
    引导用户输入自定义 .app 路径，自动检测可执行二进制，
    返回与 scan_apps() 格式相同的条目，或 None（用户取消）。
    """
    print()
    print("── 自定义应用 Binary 注入 ──────────────────────────────")
    print("请输入 .app 文件的完整路径（例如 /Applications/MyApp.app）")
    print("输入 q 返回主菜单")
    raw = input("路径 >>> ").strip()

    if raw.lower() in ("q", "quit", "exit", ""):
        return None

    # 展开 ~ 和引号
    raw = raw.strip("'\"")
    app_path = Path(raw).expanduser().resolve()

    if not app_path.exists():
        print(f"  错误：路径不存在 -> {app_path}")
        return None
    if not app_path.suffix == ".app":
        print(f"  错误：不是 .app 包 -> {app_path}")
        return None

    binaries = detect_binaries(app_path)
    if not binaries:
        print(f"  错误：在 Contents/MacOS/ 下未找到可执行文件")
        return None

    # 若只有一个二进制，自动选择
    if len(binaries) == 1:
        binary_name = binaries[0]
        print(f"  检测到二进制：{binary_name}（自动选择）")
    else:
        print(f"  检测到 {len(binaries)} 个可执行文件，请选择主二进制：")
        for i, name in enumerate(binaries, 1):
            print(f"    {i}. {name}")
        sel = input("  序号 >>> ").strip()
        try:
            idx = int(sel) - 1
            if not (0 <= idx < len(binaries)):
                raise ValueError
            binary_name = binaries[idx]
        except ValueError:
            print("  无效序号，已取消。")
            return None

    patcher = ChromiumBinaryPatcher(app_path, binary_name)
    return {
        "name":    f"{app_path.stem} ({binary_name})",
        "type":    "binary",
        "patched": patcher.is_already_patched(),
        "patcher": patcher,
    }


# ---------------------------------------------------------------------------
# 交互式选择界面
# ---------------------------------------------------------------------------

def print_menu(apps: list[dict], mode: str) -> None:
    """
    mode: "patch" | "unpatch"
    patch   模式：显示全部应用，已 patch 的标注状态
    unpatch 模式：只显示已 patch 的应用
    """
    print()
    print("  #   应用名称                          类型            状态")
    print("  " + "-" * 65)
    for i, app in enumerate(apps, 1):
        kind   = "Binary 注入" if app["type"] == "binary" else "Local State"
        status = "[已 patch]" if app["patched"] else ""
        print(f"  {i:<3} {app['name']:<36} {kind:<15} {status}")
    if mode == "patch":
        print(f"  {'c':<3} {'自定义应用（Binary 注入）':<36}")
    print()


def parse_indices(raw: str, total: int) -> list[int]:
    """将 '1,3,5' 或 '2-4' 等输入解析为有效序号列表（1-based）。"""
    indices: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if "-" in token:
            parts = token.split("-", 1)
            try:
                lo, hi = int(parts[0]), int(parts[1])
                indices.update(range(lo, hi + 1))
            except ValueError:
                print(f"  忽略无效输入: {token}")
        else:
            try:
                indices.add(int(token))
            except ValueError:
                print(f"  忽略无效输入: {token}")
    result = []
    for idx in sorted(indices):
        if 1 <= idx <= total:
            result.append(idx)
        else:
            print(f"  序号超出范围，已忽略: {idx}")
    return result


def prompt_selection(apps: list[dict], mode: str) -> list[dict]:
    """
    支持：
      - 单个序号：  3
      - 逗号分隔：  1,3,5
      - 范围：      2-4
      - 全选：      a / all
      - 自定义：    c（仅 patch 模式）
      - 退出：      q
    """
    if mode == "patch":
        print("输入序号选择要修补的应用（1,3,5 或 2-4；a 全选；c 自定义；q 退出）：")
    else:
        print("输入序号选择要还原的应用（1,3,5 或 2-4；a 全选；q 退出）：")

    raw = input(">>> ").strip().lower()

    if raw in ("q", "quit", "exit"):
        return []
    if raw in ("a", "all"):
        return apps
    if raw == "c" and mode == "patch":
        entry = prompt_custom_app()
        return [entry] if entry else []

    chosen = []
    for idx in parse_indices(raw, len(apps)):
        chosen.append(apps[idx - 1])
    return chosen


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def prompt_mode() -> str:
    """让用户选择 patch 或还原模式，返回 'patch' | 'unpatch' | 'quit'。"""
    print()
    print("  请选择操作模式：")
    print("  1. Patch   — 强制应用使用 OpenGL (ANGLE) 渲染")
    print("  2. 还原    — 移除修补，恢复原始状态")
    print("  q. 退出")
    print()
    raw = input(">>> ").strip().lower()
    if raw in ("1", "patch"):
        return "patch"
    if raw in ("2", "unpatch", "restore", "还原"):
        return "unpatch"
    return "quit"


def main():
    print("=" * 60)
    print("  electron_patcher  —  强制使用 OpenGL (ANGLE) 渲染")
    print("=" * 60)

    mode = prompt_mode()
    if mode == "quit":
        print("已退出。")
        return

    print("\n正在扫描应用...")
    all_apps = scan_apps()

    if not all_apps:
        print("未发现任何可 patch 的应用。")
        return

    # 还原模式只显示已 patch 的应用
    if mode == "unpatch":
        apps = [a for a in all_apps if a["patched"]]
        if not apps:
            print("未发现任何已修补的应用，无需还原。")
            return
        print(f"发现 {len(apps)} 个已修补的应用：")
    else:
        apps = all_apps
        print(f"发现 {len(apps)} 个应用：")

    print_menu(apps, mode)

    chosen = prompt_selection(apps, mode)

    if not chosen:
        print("未选择任何应用，退出。")
        return

    action_label = "还原" if mode == "unpatch" else "修补"
    print(f"\n即将{action_label} {len(chosen)} 个应用：")
    for app in chosen:
        print(f"  - {app['name']}")

    confirm = input(f"\n确认{action_label}？[y/N] ").strip().lower()
    if confirm not in ("y", "yes"):
        print("已取消。")
        return

    print()
    for app in chosen:
        if mode == "unpatch":
            print(f"Restoring: {app['name']}")
            app["patcher"].unpatch()
        else:
            print(f"Patching: {app['name']}")
            app["patcher"].patch()
        print()

    print("全部完成。")


if __name__ == "__main__":
    main()
