"""行情数据源：akshare 为主通道，pytdx 为备用通道，主通道失败自动切换。

两个通道的差别：
- akshare：走东方财富网页接口，一次请求拿到全市场快照，覆盖任意数量自选股；
  但它是爬虫封装，可能被反爬拦截。
- pytdx：直连通达信行情服务器，走 TCP 协议，抗反爬能力强，但只返回价格不返回股票名称。
"""
from __future__ import annotations

import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

# akshare 内部用 tqdm 打进度条，桌面小组件不需要这些东西刷屏
os.environ.setdefault("TQDM_DISABLE", "1")


@dataclass
class Quote:
    code: str
    name: str = ""
    price: float = 0.0
    prev_close: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    source: str = ""
    found: bool = True          # False = 数据源里压根没这只（比如 ETF 进了 A 股快照）

    @property
    def missing(self) -> bool:
        """数据源里没有这只代码，跟"停牌"是两回事。"""
        return not self.found

    @property
    def decimals(self) -> int:
        """报价该显示几位小数：ETF/基金习惯 3 位，股票 2 位。"""
        return 3 if _is_fund_code(self.code) else 2

    @property
    def suspended(self) -> bool:
        """停牌或没有有效报价。"""
        return self.found and self.price <= 0

    @property
    def direction(self) -> int:
        if self.suspended:
            return 0
        if self.change > 0:
            return 1
        if self.change < 0:
            return -1
        return 0


def _num(value, default: float = 0.0) -> float:
    """把 '-'、''、None、NaN 之类统一成 0。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result:  # NaN
        return default
    return result


def _pick(row: dict, *keys):
    """按顺序取第一个存在的字段，兼容不同版本的字段名差异。"""
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _digits(value) -> str:
    """从 'sh600519'、'600519.SH' 这类写法里取出 6 位代码。"""
    text = "".join(ch for ch in str(value) if ch.isdigit())
    return text[-6:] if len(text) >= 6 else text


# ---------- 交易时段判断 ----------

SESSIONS = ((9 * 60 + 15, 11 * 60 + 30), (13 * 60, 15 * 60))   # 含 09:15 起的集合竞价
_trading_days: set[str] | None = None


def _load_trading_days() -> set[str] | None:
    """全量交易日（含节假日排除），只查一次；查不到返回 None，退化成"只按周末判断"。"""
    global _trading_days
    if _trading_days is None:
        try:
            import akshare as ak

            frame = ak.tool_trade_date_hist_sina()
            _trading_days = {str(day)[:10] for day in frame["trade_date"]}
        except Exception:
            _trading_days = set()
    return _trading_days or None


def market_state(now: "datetime | None" = None) -> str:
    """当前市场状态：交易中 / 午间休盘 / 已收盘 / 未开盘 / 周末休市 / 节假日休市。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return "周末休市"
    days = _load_trading_days()
    if days is not None and now.strftime("%Y-%m-%d") not in days:
        return "节假日休市"

    minutes = now.hour * 60 + now.minute
    if minutes < SESSIONS[0][0]:
        return "未开盘"
    if minutes > SESSIONS[-1][1]:
        return "已收盘"
    if any(start <= minutes <= end for start, end in SESSIONS):
        return "交易中"
    return "午间休盘"


def _sina_symbol(code: str) -> str:
    """新浪接口用的代码：指数（sh000001/sz399006）已自带前缀，原样返回；其余按沪市 sh、深市 sz 拼。"""
    text = str(code).strip()
    if _is_index_code(text):
        return text
    return ("sh" if text[:1] in ("5", "6", "9") else "sz") + text


def _is_bj_code(code: str) -> bool:
    """北交所代码：4xxxxx / 8xxxxx / 920xxx。

    新浪日线接口不认北交所，这几只只能走整份快照，所以别用轻量路线。
    """
    code = str(code).strip()
    return code[:1] in ("4", "8") or code.startswith("920")


def _friendly_error(exc: Exception) -> str:
    """把"接口返回网页内容"这种看不懂的报错翻译成人话。"""
    text = str(exc)
    if "<" in text and ("decode" in text.lower() or "json" in text.lower()):
        return "接口返回的是网页而不是数据（多半被限流/拦截了）"
    if "RemoteDisconnected" in text or "Connection aborted" in text:
        return "连接被对方断开（多半被反爬拦截了）"
    return text


def _is_fund_code(code: str) -> bool:
    """是不是基金代码（ETF/LOF 之类）。

    A 股实时行情快照只含股票，基金要另找接口：
    - 沪市基金：5 开头（51xxxx / 56xxxx / 588xxx 等）
    - 深市基金：15xxxx / 16xxxx / 18xxxx
    """
    code = str(code).strip()
    return code[:1] == "5" or code[:2] in ("15", "16", "18")


def _build_quote(code, name, price, prev_close, change, change_pct, found: bool = True) -> Quote:
    """统一成 Quote，并补齐缺的涨跌额/涨跌幅（有的接口只给其中一部分）。"""
    if change == 0.0 and price > 0 and prev_close > 0:
        change = price - prev_close
    if change_pct == 0.0 and change and prev_close > 0:
        change_pct = change / prev_close * 100.0
    return Quote(
        code=code,
        name=str(name or "").strip(),
        price=price,
        prev_close=prev_close,
        change=change,
        change_pct=change_pct,
        source=AkshareProvider.name,
        found=found,
    )


def _is_index_code(code: str) -> bool:
    """是不是指数代码：sh/sz 前缀 + 6 位数字（sh000001 上证指数、sz399001 深证成指）。

    指数不在 A 股快照、基金行情里，要单独走指数接口；带前缀是为了
    和 6 位数字的股票代码区分开（000001 既是平安银行也是上证指数）。
    """
    text = str(code).strip().lower()
    return len(text) == 8 and text[:2] in ("sh", "sz") and text[2:].isdigit()


def market_of(code: str) -> int:
    """通达信市场号：1=上海，0=深圳/北京；指数按前缀判断。"""
    text = str(code).strip().lower()
    if _is_index_code(text):
        return 1 if text[:2] == "sh" else 0
    return 1 if text[:1] in ("5", "6", "9") else 0


def _parse_host(text: str | None) -> tuple[str, int] | None:
    """把 config 里的 "ip:port" 解析成 (ip, port)，非法则返回 None。"""
    if not text:
        return None
    ip, _, port = str(text).partition(":")
    if not ip.strip():
        return None
    try:
        return (ip.strip(), int(port))
    except ValueError:
        return None


def _tdx_hosts() -> list[tuple[str, int]]:
    """内置通达信行情服务器列表（按 pytdx 推荐顺序，去重）。"""
    try:
        from pytdx.config.hosts import hq_hosts
    except Exception:
        return []
    hosts, seen = [], set()
    for item in hq_hosts:
        try:
            ip, port = str(item[1]).strip(), int(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        if not ip or (ip, port) in seen:
            continue
        seen.add((ip, port))
        hosts.append((ip, port))
    return hosts


class AkshareProvider:
    """主通道：akshare 里能拿到实时价的接口不止一个，谁通用谁。

    核心原则是**请求数少的优先**（整份快照一次要发 70 个请求，发多了会被限流，
    表现就是"取不到数据"），所以盘中的默认路线是"一只一个请求"的逐只接口；
    一轮里还会按后端逐个补齐缺的代码，某个后端拿不到的交给下一个。

    1. minute   新浪分钟线 stock_zh_a_minute —— 1 请求/只，盘中也有当天数据，最省
    2. daily    新浪日线   stock_zh_a_daily  —— 1 请求/只，但当天 K 线要收盘后才更新
    3. bid_ask  东财单只盘口 stock_bid_ask_em —— 1 请求/只，但东财时好时坏
    4. sina     新浪全市场快照 stock_zh_a_spot —— 70 请求，自带股票名称
    5. spot     东财全市场快照 stock_zh_a_spot_em —— 兜底

    指数（sh/sz 前缀的代码）不在上面任何一条线路里，单独走一次新浪指数快照。

    名称是快照才有的，所以自选股里出现没见过的代码时，会先走一次快照把名字补齐。

    另外：A 股快照里只有股票，ETF/LOF/基金代码要单独去基金行情里找
    （新浪 fund_etf_category_sina / 东财 fund_etf_spot_em），所以自选股里
    有 5xxxxx、15/16/18xxxx 这类代码时会额外补一次请求；整份自选股都是
    基金时连 A 股快照都不拉，直接取基金行情。
    """

    name = "akshare"
    BACKENDS = ("minute", "daily", "bid_ask", "sina", "spot")
    BACKEND_LABELS = {
        "minute": "新浪分钟",
        "daily": "新浪日线",
        "bid_ask": "东财盘口",
        "sina": "新浪快照",
        "spot": "东财快照",
    }
    PER_CODE_BUDGET = 12.0      # 逐只取数的总时长上限（秒）
    MAX_PER_CODE = 12           # 自选股多过这个数就别逐只取了，直接上快照
    LIGHT_LIMIT = 12            # 不超过这个只数就走"逐只"的轻量路线，请求数少得多
    MAX_WORKERS = 4             # 逐只取数时的并发数
    COOLDOWN = 300.0            # 某个轻量通道拿不到数据后，多久内不再优先它（秒）
    ROUND_BUDGET = 45.0         # 一轮取数的总时长上界（秒）；已经拿到数据时会提前收工
    MINUTE_BUDGET = 12.0        # 逐只拉分钟线的总时长上限（秒）

    def __init__(self, known_names: dict | None = None):
        self._backend: str | None = None
        self._names: dict[str, str] = dict(known_names or {})   # 代码 -> 名称（会一直累积）
        self._minute_blocked_until = 0.0    # 分钟线通道的冷却截止时间
        self._daily_blocked_until = 0.0     # 日线通道的冷却截止时间
        self._index_blocked_until = 0.0     # 指数快照通道的冷却截止时间
        self._minute_bars_blocked_until = 0.0   # 分时图用的分钟线通道冷却截止时间
        self._unavailable: set[str] = set()  # 所有通道都查不到的代码，别再为它走重通道
        # akshare 不少接口没带 timeout，万一碰上黑洞式的网络会一直挂着；
        # 这里兜一层默认超时，取数线程才不会卡死。
        socket.setdefaulttimeout(12.0)

    @property
    def backend_label(self) -> str:
        return self.BACKEND_LABELS.get(self._backend or "", "")

    def fetch(self, codes: list[str]) -> list[Quote]:
        """按后端逐个"补齐"：每个后端只请求还没拿到的代码，拿到的就留下。

        不能要求"整批一次性成功"——盘中新浪日线没有当天的 K 线，但快照是有的；
        如果把日线这次"部分成功"（只拿到基金）当成成功直接返回，股票就永远补不上了。
        """
        result: dict[str, Quote] = {}
        errors: list[str] = []
        last_good: str | None = None
        pending = [code for code in codes if not _is_index_code(code)]
        deadline = time.monotonic() + self.ROUND_BUDGET

        # 指数不在 A 股/基金行情里，单独走指数快照（一次请求覆盖全部指数）
        index_codes = [code for code in codes if _is_index_code(code)]
        if index_codes:
            try:
                for quote in self._fetch_indexes(index_codes):
                    if not quote.missing:
                        result[quote.code] = quote
            except Exception as exc:
                errors.append(f"指数 {_friendly_error(exc)}")
                self._index_blocked_until = time.monotonic() + self.COOLDOWN

        for backend in self._order(codes):
            if not pending or time.monotonic() > deadline:
                # 剩下的代码这一轮就不追了（下一轮还会再试），别让个别查不到的代码拖住整轮刷新
                break
            if backend in ("minute", "daily", "bid_ask") and not self._stock_targets(pending):
                continue        # 只剩基金时这几条逐只线路没活干，直接跳过
            try:
                quotes = self._fetch(backend, pending)
            except Exception as exc:
                errors.append(f"{self.BACKEND_LABELS[backend]} {_friendly_error(exc)}")
                if self._backend == backend:
                    self._backend = None       # 这个后端用不动了，别再优先它
                continue
            got = [quote for quote in quotes if not quote.missing]
            if got:
                last_good = backend
            for quote in got:
                result[quote.code] = quote
            pending = [code for code in codes if code not in result]

            if not pending:
                break

            # 整市场快照都查不到的代码，再换另一家整市场快照也一样查不到，
            # 没必要为它把慢吞吞的东财快照再跑一遍（那要一分钟，会把整轮刷新拖死）
            if backend in ("sina", "spot") and result:
                break

        if not result:
            self._backend = None
            raise RuntimeError("；".join(errors) or "所有通道都没有取到数据")

        # 全通道都查不到的代码记下来：以后不再为它去拉整份快照（每次都要十几秒），
        # 逐只线路照旧每轮都会试它，哪天真能取到了自然会补上。
        self._unavailable.update(pending)
        self._backend = last_good
        return [
            result.get(code) or Quote(code=code, source=self.name, found=False) for code in codes
        ]

    def close(self) -> None:
        pass

    # ---------- 内部 ----------

    def _order(self, codes: list[str]) -> list[str]:
        """挑先走哪条路，原则是"请求数少的优先"。

        - 名称齐了 -> 逐只的轻量线路打头：新浪分钟（盘中也有数据，0.5s/只）
          -> 新浪日线（收盘后才更新当天 K 线，便宜，留着当兜底）
          -> 东财盘口（1 请求/只，但东财时好时坏）
        - 还缺名称 -> 先走新浪快照：快照自带名称，一次把名字补齐，之后就走轻量线路
        - 只数很多 -> 也先试逐只，超时了再退到整份快照
        - 刚失败过的轻量通道进冷却，排到最后，别每轮都白试一遍
        """
        if self._needs_names(codes):
            plan = ["sina", "minute", "daily", "bid_ask", "spot"]
        else:
            plan = ["minute", "daily", "bid_ask", "sina", "spot"]

        now = time.monotonic()
        for name, until in (
            ("minute", self._minute_blocked_until),
            ("daily", self._daily_blocked_until),
        ):
            if now < until and name in plan:
                plan.remove(name)
                plan.append(name)
        return plan

    def _needs_names(self, codes: list[str]) -> bool:
        """股票的代码有没有还没拿到名称的（基金/指数的名称由各自接口顺带带回来）。

        已知查不到的代码不算，否则一个写错的代码会让每一轮都去拉整份快照。
        """
        return any(
            code not in self._names and code not in self._unavailable
            for code in codes
            if not _is_fund_code(code) and not _is_index_code(code)
        )

    def _fetch(self, backend: str, codes: list[str]) -> list[Quote]:
        import akshare as ak

        if backend == "bid_ask":
            if len(codes) > self.MAX_PER_CODE:
                raise RuntimeError(f"自选股 {len(codes)} 只，改用快照接口更合适")
            if any(code.startswith("5") for code in codes):
                # akshare 的盘口接口按"6 开头才算沪市"拼 secid，5 开头的沪市基金会
                # 被当成深市查，拿不到数据，这条后端直接让给日线/快照
                raise RuntimeError("含 5 开头的沪市基金，盘口接口不支持")
            return self._fetch_one_by_one(ak, codes)

        if backend == "minute":
            table: dict = self._minute_rows(ak, codes)
        elif backend == "daily":
            table = self._daily_rows(ak, codes)
        else:
            table = {}
            if not all(_is_fund_code(code) for code in codes):
                frame = ak.stock_zh_a_spot() if backend == "sina" else ak.stock_zh_a_spot_em()
                table = self._index_snapshot(frame)

        self._fill_funds(ak, codes, table)
        if not table:
            raise RuntimeError("没有匹配到任何代码")

        quotes = []
        for code in codes:
            row = table.get(code)
            if row is None:
                quotes.append(Quote(code=code, source=self.name, found=False))
                continue
            quote = self._row_to_quote(code, row)
            if quote.name:
                self._names[code] = quote.name     # 攒着，下轮就能走轻量路线了
            quotes.append(quote)
        return quotes

    # ---------- 轻量路线：逐只取（新浪分钟 / 新浪日线） ----------

    def _per_code(self, targets: list[str], worker, budget: float) -> tuple[dict, list[str]]:
        """把"一只一个请求"的活并发跑一遍，整体受时间上限约束，返回 (拿到的行, 错误)。"""
        rows: dict = {}
        errors: list[str] = []
        deadline = time.monotonic() + budget
        pool = ThreadPoolExecutor(max_workers=min(self.MAX_WORKERS, len(targets)))
        try:
            jobs = [(pool.submit(worker, code), code) for code in targets]
            for job, code in jobs:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    errors.append(f"{code} 超过时间上限没等到")
                    continue
                try:
                    row = job.result(timeout=remaining)
                except Exception as exc:
                    errors.append(f"{code} {exc}")
                    continue
                if row is not None:
                    rows[code] = row
        finally:
            # 超时的请求可能还在路上，不等它们，让它们在后台自己结束
            pool.shutdown(wait=False, cancel_futures=True)
        return rows, errors

    @staticmethod
    def _stock_targets(codes: list[str]) -> list[str]:
        """逐只线路能处理的代码：排除基金、指数和北交所（日线/分钟线都不支持）。"""
        return [code for code in codes if not _is_fund_code(code) and not _is_bj_code(code) and not _is_index_code(code)]

    def _minute_rows(self, ak, codes: list[str]) -> dict:
        """新浪分钟线：一只一个请求，盘中也能拿到当天数据（日线要等收盘）。

        最后一根 K 线的收盘价就是最新价，比昨收算涨跌。
        """
        targets = self._stock_targets(codes)
        if not targets:
            return {}
        today = datetime.now().strftime("%Y-%m-%d")
        rows, errors = self._per_code(
            targets,
            lambda code: self._minute_row(ak, code, today, self._names),
            self.PER_CODE_BUDGET,
        )
        if not rows:
            self._minute_blocked_until = time.monotonic() + self.COOLDOWN
            if errors and not any(_is_fund_code(code) for code in codes):
                raise RuntimeError("；".join(errors[:2]))
        return rows

    @staticmethod
    def _minute_row(ak, code: str, today: str, names: dict):
        frame = ak.stock_zh_a_minute(symbol=_sina_symbol(code), period="1", adjust="")
        if frame is None or len(frame) == 0:
            return None
        days = frame["day"].astype(str).str[:10]
        today_bars = frame[days == today]
        if len(today_bars) == 0:
            return None                      # 今天还没有成交数据（停牌/未开盘）
        price = _num(today_bars.iloc[-1].get("close"))
        earlier = frame[days < today]
        prev_close = _num(earlier.iloc[-1].get("close")) if len(earlier) else 0.0
        return {
            "名称": names.get(code, ""),
            "最新价": price,
            "昨收": prev_close,
            "涨跌额": price - prev_close if prev_close > 0 else 0.0,
            "涨跌幅": (price - prev_close) / prev_close * 100.0 if prev_close > 0 else 0.0,
        }

    def _daily_rows(self, ak, codes: list[str]) -> dict:
        """新浪日线：一只一个请求（只要最近十几天，很小）。

        只认"最后一根 K 线是今天"的数据，免得把昨天的收盘价当成现价显示；
        盘中它拿不到当天数据，所以排在分钟线后面，收盘后才是最省的那条。
        """
        targets = self._stock_targets(codes)
        if not targets:
            return {}
        today = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=12)).strftime("%Y%m%d")
        rows, errors = self._per_code(
            targets,
            lambda code: self._daily_row(ak, code, start, today, self._names),
            self.PER_CODE_BUDGET,
        )
        if not rows:
            self._daily_blocked_until = time.monotonic() + self.COOLDOWN
            if errors and not any(_is_fund_code(code) for code in codes):
                raise RuntimeError("；".join(errors[:2]))
        return rows

    @staticmethod
    def _daily_row(ak, code: str, start: str, today: str, names: dict):
        frame = ak.stock_zh_a_daily(symbol=_sina_symbol(code), start_date=start)
        if frame is None or len(frame) == 0:
            return None
        last = frame.iloc[-1]
        if str(last.get("date"))[:10] != today:
            return None                      # 今天没有成交数据（停牌/未开盘）
        price = _num(last.get("close"))
        prev_close = _num(frame.iloc[-2].get("close")) if len(frame) >= 2 else 0.0
        return {
            "名称": names.get(code, ""),
            "最新价": price,
            "昨收": prev_close,
            "涨跌额": price - prev_close if prev_close > 0 else 0.0,
            "涨跌幅": (price - prev_close) / prev_close * 100.0 if prev_close > 0 else 0.0,
        }

    @staticmethod
    def _index_snapshot(frame) -> dict:
        """把快照 DataFrame 转成 代码 -> 行 的表。"""
        if frame is None or len(frame) == 0:
            raise RuntimeError("返回空快照")
        for column in ("代码", "最新价"):
            if column not in frame.columns:
                raise RuntimeError(f"快照缺少字段「{column}」，接口可能已变更")
        table = {}
        for row in frame.to_dict("records"):
            code = _digits(row.get("代码"))
            if code:
                table.setdefault(code, row)
        return table

    @staticmethod
    def _row_to_quote(code: str, row) -> Quote:
        return _build_quote(
            code=code,
            name=row.get("名称"),
            price=_num(row.get("最新价")),
            prev_close=_num(_pick(row, "昨收", "昨收价")),
            change=_num(_pick(row, "涨跌额", "涨跌")),
            change_pct=_num(_pick(row, "涨跌幅", "涨幅")),
        )

    def fetch_minute_bars(self, codes: list[str]) -> dict:
        """逐只拉当天分钟线，返回 代码 -> 原始 bar 记录（一次约 8 个交易日，按天过滤交给上层）。

        与行情通道一样是"一只一个请求"，复用同一套并发与时间预算；一只都没拿到
        就进冷却，避免被限流时每轮都白跑一遍。北交所新浪分钟线不支持，直接跳过。
        """
        import akshare as ak

        if time.monotonic() < self._minute_bars_blocked_until:
            raise RuntimeError("分钟线通道冷却中")
        targets = [code for code in codes if not _is_bj_code(code)]
        if not targets:
            return {}
        rows, errors = self._per_code(targets, lambda code: self._minute_bars(ak, code), self.MINUTE_BUDGET)
        if not rows:
            self._minute_bars_blocked_until = time.monotonic() + self.COOLDOWN
            raise RuntimeError("；".join(errors[:2]) or "分钟线没有取到数据")
        return rows

    @staticmethod
    def _minute_bars(ak, code: str) -> list[dict] | None:
        frame = ak.stock_zh_a_minute(symbol=_sina_symbol(code), period="1", adjust="")
        if frame is None or len(frame) == 0:
            return None
        return frame.to_dict("records")

    def _fetch_indexes(self, codes: list[str]) -> list[Quote]:
        """指数快照：新浪指数行情，一次请求覆盖沪深全部指数，代码就是 sh000001 这种形式。"""
        import akshare as ak

        if time.monotonic() < self._index_blocked_until:
            raise RuntimeError("指数快照通道冷却中")
        frame = ak.stock_zh_index_spot_sina()
        if frame is None or len(frame) == 0 or "代码" not in frame.columns:
            raise RuntimeError("指数快照返回空数据，接口可能已变更")
        table = {}
        for row in frame.to_dict("records"):
            code = str(row.get("代码", "")).strip().lower()
            if code:
                table.setdefault(code, row)
        quotes = []
        for code in codes:
            row = table.get(code)
            if row is None:
                quotes.append(Quote(code=code, source=self.name, found=False))
                continue
            quote = self._row_to_quote(code, row)
            if quote.name:
                self._names[code] = quote.name     # 攒着，下轮就不用再查名称了
            quotes.append(quote)
        return quotes

    def _fill_funds(self, ak, codes: list[str], table: dict) -> None:
        """ETF/LOF 不在 A 股快照里，缺的那些再单独去基金行情里找一遍。

        字段和 A 股快照一致（代码/名称/最新价/涨跌额/涨跌幅/昨收），可以走同一套解析。
        """
        pending = [code for code in codes if code not in table and _is_fund_code(code)]
        for loader in (self._sina_fund_etf, self._sina_fund_lof, self._em_fund_etf):
            if not pending:
                return
            try:
                funds = loader(ak)
            except Exception:
                continue
            table.update({code: funds[code] for code in pending if code in funds})
            pending = [code for code in pending if code not in table]

    @staticmethod
    def _sina_fund_etf(ak) -> dict:
        return AkshareProvider._fund_table(ak.fund_etf_category_sina(symbol="ETF基金"))

    @staticmethod
    def _sina_fund_lof(ak) -> dict:
        return AkshareProvider._fund_table(ak.fund_etf_category_sina(symbol="LOF基金"))

    @staticmethod
    def _em_fund_etf(ak) -> dict:
        return AkshareProvider._fund_table(ak.fund_etf_spot_em())

    @staticmethod
    def _fund_table(frame) -> dict:
        if frame is None or len(frame) == 0 or "代码" not in frame.columns:
            raise RuntimeError("基金行情返回空数据")
        return AkshareProvider._index_snapshot(frame)

    def _fetch_one_by_one(self, ak, codes: list[str]) -> list[Quote]:
        """一只一个请求，几路并发，整体受时间上限约束。"""
        deadline = time.monotonic() + self.PER_CODE_BUDGET
        pool = ThreadPoolExecutor(max_workers=min(self.MAX_WORKERS, len(codes)))
        try:
            jobs = [(pool.submit(ak.stock_bid_ask_em, symbol=code), code) for code in codes]
            quotes = []
            for job, code in jobs:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"逐只取数超过 {self.PER_CODE_BUDGET:.0f} 秒")
                quotes.append(self._from_bid_ask(job.result(timeout=remaining), code))
            return quotes
        finally:
            # 超时的请求可能还在路上，不等它们，让它们在后台自己结束
            pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _from_bid_ask(frame, code: str) -> Quote:
        if frame is None or len(frame) == 0:
            raise RuntimeError("盘口返回空数据")
        if "item" not in frame.columns or "value" not in frame.columns:
            raise RuntimeError("盘口字段异常，接口可能已变更")
        data = {str(key): value for key, value in zip(frame["item"], frame["value"])}
        return _build_quote(
            code=code,
            name=_pick(data, "名称", "股票名称"),
            price=_num(_pick(data, "最新", "最新价")),
            prev_close=_num(_pick(data, "昨收", "昨收价")),
            change=_num(_pick(data, "涨跌", "涨跌额")),
            change_pct=_num(_pick(data, "涨幅", "涨跌幅")),
        )

    def _from_snapshot(self, frame, codes: list[str]) -> list[Quote]:
        if frame is None or len(frame) == 0:
            raise RuntimeError("返回空快照")
        for column in ("代码", "最新价"):
            if column not in frame.columns:
                raise RuntimeError(f"快照缺少字段「{column}」，接口可能已变更")

        wanted = set(codes)
        table = {}
        for row in frame.to_dict("records"):
            code = _digits(row.get("代码"))
            if code in wanted:
                table[code] = row

        quotes = []
        for code in codes:
            row = table.get(code)
            if row is None:
                quotes.append(Quote(code=code, source=AkshareProvider.name))
                continue
            quotes.append(
                _build_quote(
                    code=code,
                    name=row.get("名称"),
                    price=_num(row.get("最新价")),
                    prev_close=_num(_pick(row, "昨收", "昨收价")),
                    change=_num(_pick(row, "涨跌额", "涨跌")),
                    change_pct=_num(_pick(row, "涨跌幅", "涨幅")),
                )
            )
        return quotes


class PytdxProvider:
    """备用通道：通达信行情服务器，长连接复用，断了自动重连。

    通达信的公开服务器质量参差不齐：有的连不上，有的连得上却拿不到行情。
    所以这里不把"连上"当成功，而是以"真的返回了行情"为准；坏服务器进冷却名单，
    避免每轮刷新都在死主机上白等；用通的那台记进 config，下次启动直连。
    """

    name = "pytdx"
    COOLDOWN = 600.0          # 坏服务器多久之后再试一次（秒）
    TIME_BUDGET = 8.0         # 单轮刷新最多花几秒找服务器
    MAX_QUERY = 5             # 最多在几台连得上的服务器上试查行情
    CONNECT_TIMEOUT = 1.5     # 单台服务器连接超时（秒），短一点才能多试几台
    NEVER = float("-inf")

    def __init__(self, preferred: str | None = None):
        self._api = None
        self._host: tuple[str, int] | None = None
        self._dead: dict[tuple[str, int], float] = {}
        self._last_good = _parse_host(preferred)
        self._lock = threading.Lock()

    @property
    def host_label(self) -> str:
        """当前用通的服务器，写进 config 供下次直连（冷却中的不写回）。"""
        with self._lock:
            host = self._host
            if host is None and self._last_good not in self._dead:
                host = self._last_good
            return f"{host[0]}:{host[1]}" if host else ""

    def fetch(self, codes: list[str]) -> list[Quote]:
        request = [(market_of(code), code[-6:]) for code in codes]
        deadline = time.monotonic() + self.TIME_BUDGET
        with self._lock:
            errors: list[str] = []
            queried = 0
            for host in self._candidates_unlocked():
                if queried >= self.MAX_QUERY or time.monotonic() > deadline:
                    break
                if self._api is None or self._host != host:
                    self._connect_unlocked(host)
                if self._api is None:
                    errors.append(f"{host[0]} 连接失败")
                    continue
                queried += 1
                try:
                    payload = self._api.get_security_quotes(request)
                except Exception as exc:      # 长连接可能已经失效
                    errors.append(f"{host[0]} 查询失败：{exc}")
                    self._drop_unlocked(host, cooldown=True)
                    continue
                quotes = self._build(payload, codes)
                if not any(quote.price > 0 for quote in quotes):
                    errors.append(f"{host[0]} 未返回行情")
                    self._drop_unlocked(host, cooldown=True)
                    continue
                self._last_good = host
                return quotes
            self._drop_unlocked(None, cooldown=False)
            raise RuntimeError("pytdx 取数失败：" + ("；".join(errors[:3]) or "没有可用的行情服务器"))

    def close(self) -> None:
        with self._lock:
            self._drop_unlocked(None, cooldown=False)

    # ---------- 内部：连接管理 ----------

    def _drop_unlocked(self, host: tuple[str, int] | None, cooldown: bool) -> None:
        if self._api is not None:
            try:
                self._api.disconnect()
            except Exception:
                pass
        self._api = None
        self._host = None
        if cooldown and host is not None:
            self._dead[host] = time.monotonic()

    def _connect_unlocked(self, host: tuple[str, int]) -> None:
        from pytdx.hq import TdxHq_API

        self._drop_unlocked(None, cooldown=False)
        api = TdxHq_API(heartbeat=False)
        try:
            if not api.connect(host[0], host[1], time_out=self.CONNECT_TIMEOUT):
                raise RuntimeError("连接被拒绝")
        except Exception:
            try:
                api.disconnect()
            except Exception:
                pass
            self._dead[host] = time.monotonic()
            return
        self._api, self._host = api, host

    def _candidates_unlocked(self) -> list[tuple[str, int]]:
        """优先复用已有连接，其次上次用通的服务器，最后按内置列表顺序找新的。"""
        now = time.monotonic()
        hosts: list[tuple[str, int]] = []
        for host in (self._host, self._last_good):
            if host is None or host in hosts:
                continue
            if now - self._dead.get(host, self.NEVER) > self.COOLDOWN:
                hosts.append(host)
        for host in _tdx_hosts():
            if host in hosts or now - self._dead.get(host, self.NEVER) <= self.COOLDOWN:
                continue
            hosts.append(host)
        return hosts

    def _build(self, payload, codes: list[str]) -> list[Quote]:
        table = {}
        for row in payload or []:
            table[str(row.get("code", "")).strip().zfill(6)] = row

        quotes = []
        for code in codes:
            row = table.get(code[-6:])     # 指数代码带 sh/sz 前缀，按 6 位数字部分查
            if row is None:
                quotes.append(Quote(code=code, source=self.name))
                continue
            price = _num(_pick(row, "price", "close"))
            prev_close = _num(_pick(row, "last_close", "pre_close", "lastclose"))
            change = price - prev_close if price > 0 and prev_close > 0 else 0.0
            quotes.append(
                Quote(
                    code=code,
                    price=price,
                    prev_close=prev_close,
                    change=change,
                    change_pct=(change / prev_close * 100.0) if prev_close > 0 else 0.0,
                    source=self.name,
                )
            )
        return quotes

class QuoteService:
    """主备自动切换：优先用主通道，抛异常就落到备用通道。"""

    def __init__(self, primary: str = "akshare", tdx_host: str = "", known_names=None):
        self._providers = {
            "akshare": AkshareProvider(known_names),
            "pytdx": PytdxProvider(tdx_host),
        }
        self.primary = primary if primary in self._providers else "akshare"
        self.override: str | None = None   # None = 自动切换
        self.last_source = ""
        self.last_warning = ""

    @property
    def tdx_host(self) -> str:
        """pytdx 当前用通的服务器，交给界面写回 config。"""
        return self._providers["pytdx"].host_label

    @property
    def source_label(self) -> str:
        """给界面显示的名字，akshare 会带上具体子接口，方便判断走的哪条路。"""
        if self.last_source != "akshare":
            return self.last_source
        backend = self._providers["akshare"].backend_label
        return f"akshare·{backend}" if backend else "akshare"

    def set_primary(self, name: str) -> None:
        if name in self._providers:
            self.primary = name

    def set_override(self, name: str | None) -> None:
        self.override = name if name in self._providers else None

    def _order(self) -> list[str]:
        if self.override:
            return [self.override]
        backup = "pytdx" if self.primary == "akshare" else "akshare"
        return [self.primary, backup]

    def fetch(self, codes: list[str]) -> tuple[list[Quote], str, str]:
        """返回 (行情列表, 实际生效的数据源, 告警信息)。"""
        order = self._order()
        errors = []
        for name in order:
            try:
                quotes = self._providers[name].fetch(codes)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue
            self.last_source = name
            if name != order[0]:
                self.last_warning = "；".join(errors) + "，已自动切到备用通道。"
            else:
                self.last_warning = ""
            return quotes, name, self.last_warning
        self.last_source = ""
        self.last_warning = "；".join(errors) or "没有可用的行情数据源"
        raise RuntimeError(self.last_warning)

    def fetch_minute_bars(self, codes: list[str]) -> dict:
        """分时图用的分钟线：只走 akshare 主通道（pytdx 不提供分钟线），失败由上层冷却并保留旧数据。"""
        return self._providers["akshare"].fetch_minute_bars(codes)

    def close(self) -> None:
        for provider in self._providers.values():
            provider.close()