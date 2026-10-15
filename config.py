"""配置持久化：所有可调项都保存在同目录的 config.json 里。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _base_dir() -> Path:
    """决定 config.json 放哪。

    源码运行时就是本文件所在目录；打包成 exe 之后 __file__ 指向临时解包目录
    （每次启动都不一样、退出还会被删），必须改用 exe 自己所在的目录，
    否则窗口位置、颜色、透明度每次重启都会丢。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
CONFIG_PATH = BASE_DIR / "config.json"

UI_FONT = "Microsoft YaHei UI"   # 中文正文
NUM_FONT = "Consolas"            # 数字用等宽字体，价格列才对得齐

DEFAULTS = {
    "codes": ["600519", "000001", "300750"],
    "refresh_seconds": 10,
    "font_size": 14,
    "font_color": "#E8EEF7",   # 名称、底部提示等的文字色
    "up_color": "#F05A4F",     # 国内习惯：红涨
    "down_color": "#2BC48A",   # 绿跌
    "flat_color": "#FFFFFF",   # 白平（含停牌/无数据的占位文字）
    "bg_color": "#0E1420",
    "opacity": 0.85,           # 面板（背景）不透明度，0 = 只剩文字飘在桌面上
    "font_opacity": 1.0,       # 字体不透明度，0 = 文字完全看不见
    "width": 350,
    "height": 240,
    "always_on_top": True,
    "show_source": True,
    "show_sparkline": True,    # 行内迷你分时缩略图（关掉就只留独立分时窗口）
    "primary_source": "akshare",
    "names": {},               # 代码 -> 名称，pytdx 不返回名称，缓存下来下次直接用
    "tdx_host": "",            # 上次用通了的通达信服务器 "ip:port"，下次启动直连
    "pos_x": None,
    "pos_y": None,
}


def normalize_codes(raw) -> list[str]:
    """把用户输入统一成去重后的代码列表。

    6 位数字 = 股票/基金（如 600519）；sh/sz + 6 位数字 = 指数（如 sh000001 上证指数）。
    指数必须带前缀，否则 000001 会和深市平安银行撞代码。
    """
    if isinstance(raw, str):
        raw = raw.replace(",", " ").replace("，", " ").replace(";", " ").split()
    codes, seen = [], set()
    for item in raw or []:
        text = str(item).strip().lower()
        if len(text) >= 8 and text[:2] in ("sh", "sz"):
            digits = "".join(ch for ch in text[2:] if ch.isdigit())
            code = text[:2] + digits if len(digits) == 6 else ""
        else:
            digits = "".join(ch for ch in text if ch.isdigit())
            code = digits if len(digits) == 6 else ""
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def normalize_names(raw) -> dict[str, str]:
    """清理"代码 -> 名称"缓存，丢掉不合法或空的条目。"""
    if not isinstance(raw, dict):
        return {}
    names = {}
    for code, name in raw.items():
        text = str(code).strip().lower()
        if len(text) == 8 and text[:2] in ("sh", "sz") and text[2:].isdigit():
            clean = text
        else:
            clean = "".join(ch for ch in text if ch.isdigit())
            if len(clean) != 6:
                continue
        if str(name).strip():
            names[clean] = str(name).strip()
    return names


class Config:
    def __init__(self, data: dict | None = None):
        self._data = dict(DEFAULTS)
        if data:
            for key in DEFAULTS:
                if key in data and data[key] is not None:
                    self._data[key] = data[key]
        self._data["codes"] = normalize_codes(self._data["codes"]) or list(DEFAULTS["codes"])
        self._data["names"] = normalize_names(self._data["names"])

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if key in DEFAULTS and value is not None:
                self._data[key] = value

    def as_dict(self) -> dict:
        return dict(self._data)

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                return cls(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass  # 配置损坏时直接用默认值，不让程序起不来
        return cls()

    def save(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )