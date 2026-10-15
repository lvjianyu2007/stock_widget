#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键打包脚本 —— 以后要出 exe，跑这一个命令就行。

用法（在项目目录下）：
    .\\.venv\\Scripts\\python.exe build.py              # 目录版（推荐，双击 build.bat 同效）
    .\\.venv\\Scripts\\python.exe build.py onefile      # 单文件版
    .\\.venv\\Scripts\\python.exe build.py both         # 两个都打
    .\\.venv\\Scripts\\python.exe build.py dir --no-clean   # 不删 build/ 缓存（更快，偶发脏缓存）
    .\\.venv\\Scripts\\python.exe build.py dir --no-open    # 结束后不自动打开 dist 目录

它比手敲 PyInstaller 命令多做的几件事（都是踩过坑的）：
1. 先结束正在运行的 StockWidget —— 否则 exe 被占用，打包会莫名失败
2. 备份并还原 dist 里的 config.json —— 重新打包不会丢掉你调好的样式/自选股/窗口位置
3. 清理旧产物：删不掉（被占用/权限不足）就自动改名让开，保证打包能继续
4. 校验产物是否真的生成，失败时直接打印日志尾部
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "build.spec"
DIST = ROOT / "dist"
BUILD_DIR = ROOT / "build"
LOG = ROOT / "build_log.txt"
APP = "StockWidget"

# 模式 -> 产出的中文名
MODES = {
    "dir": ("目录版",),
    "onefile": ("单文件版",),
    "both": ("目录版", "单文件版"),
}
USAGE = "用法：build.py [dir|onefile|both] [--no-clean] [--no-open]"

TAIL_LINES = 25


def say(message: str = "") -> None:
    print(message, flush=True)


def step(index: int, message: str) -> None:
    say(f"[{index}] {message}")


def human_size(path: Path) -> str:
    if path.is_dir():
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    else:
        total = path.stat().st_size
    return f"{total / 1024 / 1024:.1f} MB"


def stamp(path: Path) -> str:
    return time.strftime("%H:%M:%S", time.localtime(path.stat().st_mtime))


# ---------- 各步骤 ----------

def ensure_pyinstaller() -> bool:
    """确认 PyInstaller 装在当前这个解释器里，没有就装一个。"""
    probe = subprocess.run(
        [sys.executable, "-c", "import PyInstaller"], capture_output=True
    )
    if probe.returncode == 0:
        version = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True, text=True,
        ).stdout.strip()
        say(f"     PyInstaller {version}（{sys.executable}）")
        return True

    say("     没装 PyInstaller，正在安装 ...")
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller", "--disable-pip-version-check"]
    )
    if install.returncode != 0:
        say("     [错误] PyInstaller 安装失败，请检查网络或 pip 源")
        return False
    return True


def kill_running() -> None:
    """打包前先关掉在跑的 exe，否则文件被锁会让打包失败。"""
    result = subprocess.run(
        ["taskkill", "/IM", f"{APP}.exe", "/F"], capture_output=True, text=True
    )
    if result.returncode == 0:
        say(f"     已结束正在运行的 {APP}.exe")
    else:
        say("     没有正在运行的实例")


def backup_configs() -> dict[Path, bytes]:
    """把 dist 里的 config.json 存下来，打包完再放回去。"""
    saved = {}
    for path in (DIST / APP / "config.json", DIST / "config.json"):
        if path.is_file():
            saved[path] = path.read_bytes()
            say(f"     已备份 {path.relative_to(ROOT)}")
    if not saved:
        say("     没有需要备份的 config.json")
    return saved


def remove_target(path: Path, is_dir: bool) -> str:
    """删掉旧产物；删不掉就改名让开，绝不因为清理失败而中断打包。"""
    if not path.exists():
        return ""
    try:
        if is_dir:
            shutil.rmtree(path)
        else:
            path.unlink()
        say(f"     已删除 {path.relative_to(ROOT)}")
        return ""
    except Exception as exc:
        backup = path.with_name(f"{path.name}_old-{time.strftime('%m%d-%H%M')}")
        index = 1
        while backup.exists():
            index += 1
            backup = path.with_name(f"{path.name}_old-{time.strftime('%m%d-%H%M')}-{index}")
        try:
            path.rename(backup)
            say(f"     删不掉（{type(exc).__name__}），已改名为 {backup.name}，可手动删除")
            return backup.name
        except Exception as exc2:
            say(f"     [警告] {path.name} 无法删除也无法改名：{exc2}")
            return ""


def clean_targets(mode: str, clean_cache: bool) -> list[str]:
    """清理上一轮的产物，返回被改名让开的目录名。"""
    leftovers = []
    if mode in ("dir", "both"):
        name = remove_target(DIST / APP, is_dir=True)
        if name:
            leftovers.append(name)
    if mode in ("onefile", "both"):
        name = remove_target(DIST / f"{APP}.exe", is_dir=False)
        if name:
            leftovers.append(name)
    if clean_cache:
        remove_target(BUILD_DIR, is_dir=True)
    else:
        say("     （--no-clean：保留 build/ 缓存）")
    return leftovers


def run_pyinstaller(onefile: bool, clean_cache: bool) -> bool:
    label = "单文件版" if onefile else "目录版"
    env = dict(os.environ)
    if onefile:
        env["STOCK_WIDGET_ONEFILE"] = "1"
    else:
        env.pop("STOCK_WIDGET_ONEFILE", None)

    command = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"]
    if clean_cache:
        command.append("--clean")
    say(f"     {label}：{' '.join(command[1:])}")

    start = time.time()
    with LOG.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"=== {label} ===\n" + " ".join(command) + "\n\n")
        log.flush()
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=ROOT)

    if result.returncode != 0:
        say(f"     [错误] {label} 打包失败（{time.time() - start:.0f}s），日志尾部：")
        for line in tail_log():
            say(f"       {line}")
        return False

    target = DIST / f"{APP}.exe" if onefile else DIST / APP / f"{APP}.exe"
    if not target.exists():
        say(f"     [错误] {label} 跑完了但没找到 {target.relative_to(ROOT)}，看日志：{LOG.name}")
        return False
    say(f"     完成 {label}：{target.relative_to(ROOT)}  {human_size(target.parent if not onefile else target)}  "
        f"耗时 {time.time() - start:.0f}s")
    return True


def tail_log(lines: int = TAIL_LINES) -> list[str]:
    if not LOG.is_file():
        return ["（没有日志）"]
    content = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return content[-lines:]


def restore_configs(saved: dict[Path, bytes]) -> None:
    for path, data in saved.items():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            say(f"     已还原 {path.relative_to(ROOT)}")
        except Exception as exc:
            say(f"     [警告] 还原 {path.name} 失败：{exc}")


def show_result(mode: str) -> None:
    say()
    say("=" * 56)
    say(" 打包完成")
    for label in MODES[mode]:
        if label == "目录版":
            target = DIST / APP / f"{APP}.exe"
            folder = DIST / APP
            if target.exists():
                say(f"  目录版：{target}   {human_size(folder)}   修改于 {stamp(target)}")
        else:
            target = DIST / f"{APP}.exe"
            if target.exists():
                say(f"  单文件：{target}   {human_size(target)}   修改于 {stamp(target)}")
    say(f"  日志：  {LOG}")
    say("=" * 56)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    mode = (args[0].lower() if args else "dir")

    if mode not in MODES or flags - {"--no-clean", "--no-open"}:
        say(USAGE)
        return 2

    say(f"项目目录：{ROOT}")
    say(f"打包模式：{MODES[mode][0]}{'（含单文件）' if mode == 'both' else ''}"
        f"{'，保留缓存' if '--no-clean' in flags else ''}")

    step(1, "检查环境")
    if not SPEC.is_file():
        say("     [错误] 找不到 build.spec")
        return 1
    if not ensure_pyinstaller():
        return 1

    step(2, "结束正在运行的实例")
    kill_running()

    step(3, "备份 dist 里的配置")
    saved = backup_configs()

    step(4, "清理旧产物")
    clean_cache = "--no-clean" not in flags
    leftovers = clean_targets(mode, clean_cache)

    step(5, "开始打包（第一次会比较慢）")
    ok = True
    if mode in ("dir", "both"):
        ok = run_pyinstaller(onefile=False, clean_cache=clean_cache) and ok
    if mode in ("onefile", "both"):
        ok = run_pyinstaller(onefile=True, clean_cache=False) and ok

    step(6, "还原配置")
    restore_configs(saved)

    if not ok:
        say()
        say(f"[失败] 完整日志：{LOG}（把最后几行发我即可）")
        return 1

    if leftovers:
        say()
        say(f"提醒：以下旧目录被改名让开了，确认新版没问题后可以删掉：{'、'.join(leftovers)}")

    show_result(mode)
    if "--no-open" not in flags:
        subprocess.run(["explorer", str(DIST)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
