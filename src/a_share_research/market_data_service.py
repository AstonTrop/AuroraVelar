from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any
import contextlib
import io
import json
import os
import re
import tempfile

import pandas as pd
import requests

from .portfolio import fetch_board_snapshot, normalize_board_snapshot, summarize_board_strength, summarize_technical_profile
from .utils import is_mainboard_code, is_st_name, normalize_code, parse_numeric


DEFAULT_SOURCE = "akshare/eastmoney"
EASTMONEY_DIRECT_SOURCE = "eastmoney/direct"
SINA_SOURCE = "sina/market-center"
TENCENT_SOURCE = "tencent/qt"
MIN_FULL_MARKET_ROWS = 1000


def fetched_at() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def normalize_stock_code(raw: object) -> str:
    text = str(raw).strip()
    match = re.search(r"(\d{6})", text)
    if match:
        return match.group(1)
    return normalize_code(raw)


def response_envelope(
    data: Any,
    *,
    source: str = DEFAULT_SOURCE,
    freshness: str = "live",
    fetched_time: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "fetched_at": fetched_time or fetched_at(),
        "freshness": freshness,
        "is_stale": freshness == "stale_cache",
        "data": json_safe(data),
    }


def _safe_float(value: object, default: float = float("nan")) -> float:
    value = parse_numeric(value)
    if pd.isna(value):
        return default
    return float(value)


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return round((new - old) / old * 100, 4)


def _ratio(part: float | None, whole: float | None) -> float | None:
    if part is None or whole in (None, 0):
        return None
    return round(part / whole, 4)


def _quote_col(df: pd.DataFrame, *names: str) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _first_row(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {}
    return df.iloc[0].to_dict()


def _pick(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row:
            value = row.get(name)
            try:
                if pd.isna(value):
                    continue
            except (TypeError, ValueError):
                pass
            return value
    return default


def _nullable_float(value: object) -> float | None:
    parsed = parse_numeric(value)
    if pd.isna(parsed):
        return None
    return float(parsed)


def _nullable_int(value: object) -> int | None:
    parsed = parse_numeric(value)
    if pd.isna(parsed):
        return None
    return int(float(parsed))


def stock_market(code: str) -> str | None:
    code = normalize_code(code)
    if not code:
        return None
    if code.startswith(("6", "5", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return None


def trading_phase(now: datetime | None = None) -> str:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return "non_trading_day"
    current = now.time()
    if current < time(9, 30):
        return "pre_open"
    if time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0):
        return "continuous_auction"
    if time(11, 30) < current < time(13, 0):
        return "lunch_break"
    return "after_close"


def freshness_for_phase(phase: str) -> str:
    if phase == "continuous_auction":
        return "live"
    if phase in {"lunch_break", "after_close"}:
        return "after_close"
    if phase == "pre_open":
        return "delayed"
    return "delayed"


def normalize_quotes_df(quotes_df: pd.DataFrame) -> pd.DataFrame:
    if quotes_df is None or quotes_df.empty:
        return pd.DataFrame(columns=["code", "name", "latest_price", "day_change_pct", "turnover_rate", "volume_ratio", "amount"])
    work = quotes_df.copy()
    code_col = _quote_col(work, "代码", "code", "股票代码")
    name_col = _quote_col(work, "名称", "name", "股票名称")
    price_col = _quote_col(work, "最新价", "latest_price", "last_price", "现价")
    change_col = _quote_col(work, "涨跌幅", "day_change_pct", "change_pct")
    turnover_col = _quote_col(work, "换手率", "turnover_rate")
    ratio_col = _quote_col(work, "量比", "volume_ratio")
    amount_col = _quote_col(work, "成交额", "amount")
    out = pd.DataFrame(index=work.index)
    out["code"] = work[code_col].map(normalize_code) if code_col else ""
    out["name"] = work[name_col].astype(str) if name_col else ""
    out["latest_price"] = pd.to_numeric(work[price_col], errors="coerce") if price_col else float("nan")
    out["day_change_pct"] = pd.to_numeric(work[change_col], errors="coerce") if change_col else 0.0
    out["turnover_rate"] = pd.to_numeric(work[turnover_col], errors="coerce") if turnover_col else float("nan")
    out["volume_ratio"] = pd.to_numeric(work[ratio_col], errors="coerce") if ratio_col else float("nan")
    out["amount"] = pd.to_numeric(work[amount_col], errors="coerce") if amount_col else float("nan")
    return out[out["code"].ne("")].reset_index(drop=True)


def _bid_ask_to_dict(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty or "item" not in df.columns or "value" not in df.columns:
        return {}
    return {str(row["item"]).strip(): row["value"] for _, row in df.iterrows()}


def classify_bid_ask_actionability(
    *,
    latest_price: float,
    day_change_pct: float,
    sell_1: float,
    buy_1: float,
    cash: float | None = None,
) -> dict[str, Any]:
    sell_missing = pd.isna(sell_1) or sell_1 <= 0
    buy_missing = pd.isna(buy_1) or buy_1 <= 0
    is_limit_up_sealed = latest_price > 0 and day_change_pct >= 9.7 and sell_missing and not buy_missing
    is_limit_down_sealed = latest_price > 0 and day_change_pct <= -9.7 and buy_missing and not sell_missing
    min_lot_cost = latest_price * 100.0 if latest_price > 0 else float("nan")
    cash_known = cash is not None and not pd.isna(cash)
    cash_sufficient = not cash_known or (not pd.isna(min_lot_cost) and float(cash) >= min_lot_cost)
    if is_limit_up_sealed:
        actionability = "涨停封板不可追"
    elif is_limit_down_sealed:
        actionability = "跌停风险"
    elif not cash_sufficient:
        actionability = "现金不足"
    elif sell_missing:
        actionability = "盘口缺失"
    else:
        actionability = "可买"
    return {
        "actionability": actionability,
        "is_limit_up_sealed": bool(is_limit_up_sealed),
        "is_limit_down_sealed": bool(is_limit_down_sealed),
        "min_lot_cost": min_lot_cost,
        "cash_sufficient": bool(cash_sufficient),
    }


class AkshareMarketDataProvider:
    source = DEFAULT_SOURCE

    def _ak(self):
        try:
            import akshare as ak  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("AkShare is not installed or cannot be imported") from exc
        return ak

    def quotes(self) -> pd.DataFrame:
        return self._ak().stock_zh_a_spot_em()

    def quotes_for(self, codes: list[str]) -> pd.DataFrame:
        quotes = self.quotes()
        normalized = {normalize_code(code) for code in codes}
        code_col = _quote_col(quotes, "代码", "code", "股票代码")
        if code_col is None:
            return quotes
        work = quotes.copy()
        return work[work[code_col].map(normalize_code).isin(normalized)]

    def indices(self) -> pd.DataFrame:
        return self._ak().stock_zh_index_spot_em()

    def bid_ask(self, code: str) -> pd.DataFrame:
        return self._ak().stock_bid_ask_em(symbol=normalize_code(code))

    def boards(self) -> pd.DataFrame:
        board_df, _source = fetch_board_snapshot()
        return board_df

    def board_constituents(self, board: dict[str, Any]) -> pd.DataFrame:
        board_name = str(_pick(board, "board_name", "板块名称", default="") or "")
        board_type = str(_pick(board, "board_type", default="") or "")
        if not board_name:
            return pd.DataFrame()
        ak = self._ak()
        if "概念" in board_type:
            raw = ak.stock_board_concept_cons_em(symbol=board_name)
        else:
            raw = ak.stock_board_industry_cons_em(symbol=board_name)
        if raw is None or raw.empty:
            return pd.DataFrame()
        code_col = _quote_col(raw, "代码", "code", "股票代码")
        name_col = _quote_col(raw, "名称", "name", "股票名称")
        change_col = _quote_col(raw, "涨跌幅", "change_pct", "changepercent")
        if code_col is None:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "code": raw[code_col].map(normalize_code),
                "name": raw[name_col].astype(str) if name_col else "",
                "change_pct": raw[change_col].map(parse_numeric) if change_col else 0.0,
            }
        )

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        report_date = report_date or date.today()
        start_date = report_date.replace(year=max(1990, report_date.year - 1)).strftime("%Y%m%d")
        return self._ak().stock_zh_a_hist(
            symbol=normalize_code(code),
            period="daily",
            start_date=start_date,
            end_date=report_date.strftime("%Y%m%d"),
            adjust="qfq",
        )

    def intraday_1m(self, code: str, limit: int | None = None) -> pd.DataFrame:
        raise RuntimeError("AkShare intraday method is not enabled for this service")

    def recent_trades(self, code: str, limit: int = 100) -> pd.DataFrame:
        tick_df = self._ak().stock_zh_a_tick_tx_js(symbol=_tencent_symbol(code))
        if tick_df is None or tick_df.empty:
            return pd.DataFrame()
        rows = []
        for _, row in tick_df.head(limit).iterrows():
            row_dict = row.to_dict()
            side_text = str(_pick(row_dict, "性质", "side", default="") or "")
            if "买" in side_text:
                side = "buy"
            elif "卖" in side_text:
                side = "sell"
            elif "中" in side_text:
                side = "neutral"
            else:
                side = "unknown"
            raw_volume = _nullable_float(_pick(row_dict, "成交量", "volume"))
            rows.append(
                {
                    "time": str(_pick(row_dict, "成交时间", "time", default="")),
                    "price": _nullable_float(_pick(row_dict, "成交价格", "price")),
                    "volume": raw_volume * 100 if raw_volume is not None else None,
                    "amount": _nullable_float(_pick(row_dict, "成交金额", "amount")),
                    "side": side,
                }
            )
        return pd.DataFrame.from_records(rows)

    def zt_pool(self, report_date: date | None = None) -> pd.DataFrame:
        report_date = report_date or date.today()
        return self._ak().stock_zt_pool_em(date=report_date.strftime("%Y%m%d"))


class EastmoneyDirectMarketDataProvider:
    source = EASTMONEY_DIRECT_SOURCE

    def __init__(self, fetcher: Any | None = None, page_size: int = 500) -> None:
        self.fetcher = fetcher or self._fetch_page
        self.page_size = page_size

    def _fetch_page(self, *, page: int, page_size: int) -> dict[str, Any]:
        response = requests.get(
            "https://82.push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": page,
                "pz": page_size,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f12,f14,f2,f3,f8,f10,f6",
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/center/gridlist.html",
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def _records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        total = None
        while True:
            payload = self.fetcher(page=page, page_size=self.page_size)
            data = payload.get("data") or {}
            diff = data.get("diff") or []
            total = total if total is not None else data.get("total")
            records.extend(diff)
            if not diff:
                break
            if total is not None and len(records) >= int(total):
                break
            if len(diff) < self.page_size:
                break
            page += 1
        return records

    def quotes(self) -> pd.DataFrame:
        rows = []
        for item in self._records():
            rows.append(
                {
                    "代码": normalize_code(item.get("f12")),
                    "名称": item.get("f14", ""),
                    "最新价": parse_numeric(item.get("f2")),
                    "涨跌幅": parse_numeric(item.get("f3")),
                    "换手率": parse_numeric(item.get("f8")),
                    "量比": parse_numeric(item.get("f10")),
                    "成交额": parse_numeric(item.get("f6")),
                }
            )
        return pd.DataFrame.from_records(rows)

    def quotes_for(self, codes: list[str]) -> pd.DataFrame:
        normalized = {normalize_code(code) for code in codes}
        quotes = self.quotes()
        if quotes.empty:
            return quotes
        return quotes[quotes["代码"].map(normalize_code).isin(normalized)].copy()

    def indices(self) -> pd.DataFrame:
        raise RuntimeError("Eastmoney direct fallback only provides full-market stock quotes")

    def bid_ask(self, code: str) -> pd.DataFrame:
        raise RuntimeError("Eastmoney direct fallback does not provide bid/ask depth")

    def boards(self) -> pd.DataFrame:
        raise RuntimeError("Eastmoney direct fallback does not provide board heat data")

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        raise RuntimeError("Eastmoney direct fallback does not provide historical bars")

    def intraday_1m(self, code: str, limit: int | None = None) -> pd.DataFrame:
        raise RuntimeError("Eastmoney direct fallback does not provide intraday bars")

    def recent_trades(self, code: str, limit: int = 100) -> pd.DataFrame:
        raise RuntimeError("Eastmoney direct fallback does not provide recent trades")

    def zt_pool(self, report_date: date | None = None) -> pd.DataFrame:
        raise RuntimeError("Eastmoney direct fallback does not provide limit-up pool")


class SinaMarketDataProvider:
    source = SINA_SOURCE

    def __init__(
        self,
        fetcher: Any | None = None,
        board_fetcher: Any | None = None,
        hist_fetcher: Any | None = None,
        page_size: int = 100,
    ) -> None:
        self.fetcher = fetcher or self._fetch_page
        self.board_fetcher = board_fetcher or self._fetch_board
        self.hist_fetcher = hist_fetcher or self._fetch_hist
        self.page_size = page_size

    def _fetch_page(self, *, node: str, page: int, page_size: int) -> list[dict[str, Any]]:
        response = requests.get(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
            params={
                "page": page,
                "num": page_size,
                "sort": "changepercent",
                "asc": 0,
                "node": node,
                "symbol": "",
                "_s_r_a": "page",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    def _records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for node in ("sh_a", "sz_a"):
            page = 1
            while True:
                batch = self.fetcher(node=node, page=page, page_size=self.page_size)
                if not batch:
                    break
                records.extend(batch)
                if len(batch) < self.page_size:
                    break
                page += 1
        return records

    def quotes(self) -> pd.DataFrame:
        rows = []
        seen: set[str] = set()
        for item in self._records():
            code = normalize_code(item.get("code"))
            if not code or code in seen:
                continue
            seen.add(code)
            rows.append(
                {
                    "代码": code,
                    "名称": item.get("name", ""),
                    "最新价": parse_numeric(item.get("trade")),
                    "涨跌幅": parse_numeric(item.get("changepercent")),
                    "换手率": parse_numeric(item.get("turnoverratio")),
                    "量比": float("nan"),
                    "成交额": parse_numeric(item.get("amount")),
                }
            )
        return pd.DataFrame.from_records(rows)

    def quotes_for(self, codes: list[str]) -> pd.DataFrame:
        normalized = {normalize_code(code) for code in codes}
        quotes = self.quotes()
        if quotes.empty:
            return quotes
        return quotes[quotes["代码"].map(normalize_code).isin(normalized)].copy()

    def _fetch_board(self, board_type: str) -> str:
        if board_type == "行业":
            url = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
            params = None
        else:
            url = "http://money.finance.sina.com.cn/q/view/newFLJK.php"
            params = {"param": "class"}
        response = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return response.text

    def _parse_board_text(self, text: str, board_type: str) -> pd.DataFrame:
        import json  # noqa: PLC0415

        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            return pd.DataFrame()
        payload = json.loads(text[start : end + 1])
        rows = []
        for value in payload.values():
            parts = str(value).split(",")
            if len(parts) < 13:
                continue
            rows.append(
                {
                    "board_type": board_type,
                    "label": parts[0],
                    "board_name": parts[1],
                    "change_pct": parse_numeric(parts[5]),
                    "up_count": 0.0,
                    "down_count": 0.0,
                    "leader_code": normalize_code(parts[8]),
                    "leader": parts[12],
                    "leader_price": parse_numeric(parts[10]),
                    "leader_change_pct": parse_numeric(parts[9]),
                }
            )
        out = pd.DataFrame.from_records(rows)
        if out.empty:
            return out
        out["up_ratio"] = 0.0
        out["board_action"] = out["change_pct"].map(lambda value: "只观察" if parse_numeric(value) >= 0 else "回避")
        return out

    def board_constituents(self, board: dict[str, Any]) -> pd.DataFrame:
        label = str(_pick(board, "label", default="") or "")
        if not label:
            return pd.DataFrame()
        try:
            import akshare as ak  # noqa: PLC0415

            # AkShare's Sina sector detail emits tqdm progress; keep API logs clean.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                raw = ak.stock_sector_detail(sector=label)
        except Exception:
            return pd.DataFrame()
        if raw is None or raw.empty:
            return pd.DataFrame()
        code_col = _quote_col(raw, "code", "股票代码", "代码")
        name_col = _quote_col(raw, "name", "股票名称", "名称")
        change_col = _quote_col(raw, "changepercent", "涨跌幅", "change_pct")
        if code_col is None:
            return pd.DataFrame()
        out = pd.DataFrame(
            {
                "code": raw[code_col].map(normalize_code),
                "name": raw[name_col].astype(str) if name_col else "",
                "change_pct": raw[change_col].map(parse_numeric) if change_col else 0.0,
            }
        )
        return out[out["code"].ne("")].reset_index(drop=True)

    def boards(self) -> pd.DataFrame:
        frames = []
        for board_type in ("行业", "概念"):
            text = self.board_fetcher(board_type)
            parsed = self._parse_board_text(text, board_type)
            if not parsed.empty:
                frames.append(parsed)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def indices(self) -> pd.DataFrame:
        raise RuntimeError("Sina market-center fallback only provides full-market stock quotes")

    def bid_ask(self, code: str) -> pd.DataFrame:
        raise RuntimeError("Sina market-center fallback does not provide bid/ask depth")

    def _fetch_hist(self, *, symbol: str, datalen: int) -> list[dict[str, Any]]:
        response = requests.get(
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData",
            params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": datalen},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        code = normalize_code(code)
        symbol = _tencent_symbol(code)
        rows = self.hist_fetcher(symbol=symbol, datalen=180)
        out = pd.DataFrame.from_records(rows)
        if out.empty:
            return out
        rename_map = {
            "day": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "volume": "成交量",
        }
        out = out.rename(columns=rename_map)
        for col in ["开盘", "最高", "最低", "收盘", "成交量"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        return out

    def _fetch_intraday(self, *, symbol: str, datalen: int) -> list[dict[str, Any]]:
        response = requests.get(
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData",
            params={"symbol": symbol, "scale": 1, "ma": "no", "datalen": datalen},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    def intraday_1m(self, code: str, limit: int | None = None) -> pd.DataFrame:
        code = normalize_code(code)
        symbol = _tencent_symbol(code)
        rows = self._fetch_intraday(symbol=symbol, datalen=limit or 260)
        out = pd.DataFrame.from_records(rows)
        if out.empty:
            return out
        rename_map = {
            "day": "time",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
        out = out.rename(columns=rename_map)
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        if "amount" not in out.columns:
            out["amount"] = out["close"] * out["volume"]
        if "price" not in out.columns:
            out["price"] = out["close"]
        cumulative_volume = out["volume"].cumsum().replace(0, pd.NA)
        cumulative_amount = out["amount"].cumsum()
        out["avg_price"] = cumulative_amount / cumulative_volume
        if limit:
            out = out.tail(limit)
        return out[["time", "price", "avg_price", "open", "high", "low", "close", "volume", "amount"]].copy()

    def recent_trades(self, code: str, limit: int = 100) -> pd.DataFrame:
        raise RuntimeError("Sina market-center fallback does not provide stable recent trades")

    def zt_pool(self, report_date: date | None = None) -> pd.DataFrame:
        raise RuntimeError("Sina market-center fallback does not provide limit-up pool")


def _tencent_symbol(code: str, *, compact_index: bool = False) -> str:
    code = normalize_code(code)
    if code.startswith(("6", "5", "9")):
        prefix = "sh"
    else:
        prefix = "sz"
    if compact_index:
        return f"s_{prefix}{code}"
    return f"{prefix}{code}"


def _parse_tencent_lines(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.split(";"):
        if '="' not in line:
            continue
        payload = line.split('="', 1)[1].rstrip('"')
        if payload:
            rows.append(payload.split("~"))
    return rows


class TencentMarketDataProvider:
    source = TENCENT_SOURCE

    def __init__(self, fetcher: Any | None = None) -> None:
        self.fetcher = fetcher or self._fetch

    def _fetch(self, symbols: list[str]) -> str:
        response = requests.get(
            "https://qt.gtimg.cn/q=" + ",".join(symbols),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        response.encoding = "gbk"
        return response.text

    def quotes(self) -> pd.DataFrame:
        raise RuntimeError("Tencent quote fallback requires explicit stock codes")

    def quotes_for(self, codes: list[str]) -> pd.DataFrame:
        symbols = [_tencent_symbol(code) for code in codes if normalize_code(code)]
        rows = _parse_tencent_lines(self.fetcher(symbols)) if symbols else []
        records: list[dict[str, Any]] = []
        for row in rows:
            if len(row) < 4:
                continue
            records.append(
                {
                    "代码": normalize_code(row[2]),
                    "名称": row[1],
                    "最新价": parse_numeric(row[3]),
                    "昨收": parse_numeric(row[4]) if len(row) > 4 else float("nan"),
                    "今开": parse_numeric(row[5]) if len(row) > 5 else float("nan"),
                    "成交量": parse_numeric(row[36]) if len(row) > 36 else float("nan"),
                    "涨跌额": parse_numeric(row[31]) if len(row) > 31 else float("nan"),
                    "涨跌幅": parse_numeric(row[32]) if len(row) > 32 else float("nan"),
                    "最高": parse_numeric(row[33]) if len(row) > 33 else float("nan"),
                    "最低": parse_numeric(row[34]) if len(row) > 34 else float("nan"),
                    "成交额": parse_numeric(row[37]) if len(row) > 37 else float("nan"),
                    "换手率": parse_numeric(row[38]) if len(row) > 38 else float("nan"),
                    "量比": parse_numeric(row[49]) if len(row) > 49 else float("nan"),
                    "涨停价": parse_numeric(row[47]) if len(row) > 47 else float("nan"),
                    "跌停价": parse_numeric(row[48]) if len(row) > 48 else float("nan"),
                }
            )
        return pd.DataFrame.from_records(records)

    def indices(self) -> pd.DataFrame:
        symbols = [
            "s_sh000001",
            "s_sz399001",
            "s_sz399006",
            "s_sh000300",
        ]
        rows = _parse_tencent_lines(self.fetcher(symbols))
        records: list[dict[str, Any]] = []
        for row in rows:
            if len(row) < 6:
                continue
            records.append(
                {
                    "代码": normalize_code(row[2]),
                    "名称": row[1],
                    "最新价": parse_numeric(row[3]),
                    "涨跌幅": parse_numeric(row[5]),
                }
            )
        return pd.DataFrame.from_records(records)

    def bid_ask(self, code: str) -> pd.DataFrame:
        rows = _parse_tencent_lines(self.fetcher([_tencent_symbol(code)]))
        if not rows:
            return pd.DataFrame()
        row = rows[0]
        data = {
            "最新": parse_numeric(row[3]) if len(row) > 3 else float("nan"),
            "涨幅": parse_numeric(row[32]) if len(row) > 32 else float("nan"),
            "buy_1": parse_numeric(row[9]) if len(row) > 9 else float("nan"),
            "buy_1_volume": parse_numeric(row[10]) if len(row) > 10 else float("nan"),
            "buy_2": parse_numeric(row[11]) if len(row) > 11 else float("nan"),
            "buy_2_volume": parse_numeric(row[12]) if len(row) > 12 else float("nan"),
            "buy_3": parse_numeric(row[13]) if len(row) > 13 else float("nan"),
            "buy_3_volume": parse_numeric(row[14]) if len(row) > 14 else float("nan"),
            "buy_4": parse_numeric(row[15]) if len(row) > 15 else float("nan"),
            "buy_4_volume": parse_numeric(row[16]) if len(row) > 16 else float("nan"),
            "buy_5": parse_numeric(row[17]) if len(row) > 17 else float("nan"),
            "buy_5_volume": parse_numeric(row[18]) if len(row) > 18 else float("nan"),
            "sell_1": parse_numeric(row[19]) if len(row) > 19 else float("nan"),
            "sell_1_volume": parse_numeric(row[20]) if len(row) > 20 else float("nan"),
            "sell_2": parse_numeric(row[21]) if len(row) > 21 else float("nan"),
            "sell_2_volume": parse_numeric(row[22]) if len(row) > 22 else float("nan"),
            "sell_3": parse_numeric(row[23]) if len(row) > 23 else float("nan"),
            "sell_3_volume": parse_numeric(row[24]) if len(row) > 24 else float("nan"),
            "sell_4": parse_numeric(row[25]) if len(row) > 25 else float("nan"),
            "sell_4_volume": parse_numeric(row[26]) if len(row) > 26 else float("nan"),
            "sell_5": parse_numeric(row[27]) if len(row) > 27 else float("nan"),
            "sell_5_volume": parse_numeric(row[28]) if len(row) > 28 else float("nan"),
        }
        return pd.DataFrame([{"item": key, "value": value} for key, value in data.items()])

    def boards(self) -> pd.DataFrame:
        raise RuntimeError("Tencent fallback does not provide board heat data")

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        raise RuntimeError("Tencent fallback does not provide historical bars")

    def intraday_1m(self, code: str, limit: int | None = None) -> pd.DataFrame:
        raise RuntimeError("Tencent fallback does not provide intraday bars")

    def recent_trades(self, code: str, limit: int = 100) -> pd.DataFrame:
        raise RuntimeError("Tencent fallback does not provide stable recent trades")

    def zt_pool(self, report_date: date | None = None) -> pd.DataFrame:
        raise RuntimeError("Tencent fallback does not provide limit-up pool")


class FallbackMarketDataProvider:
    def __init__(self, primary: Any | None = None, fallback: Any | None = None) -> None:
        self.primary = primary or AkshareMarketDataProvider()
        self.fallback = fallback or TencentMarketDataProvider()
        self.source = f"{getattr(self.primary, 'source', DEFAULT_SOURCE)}+{getattr(self.fallback, 'source', TENCENT_SOURCE)}"

    def _with_fallback(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            result = getattr(self.primary, method)(*args, **kwargs)
            if isinstance(result, pd.DataFrame) and result.empty:
                raise RuntimeError(f"Primary {method} returned empty data")
            return result
        except Exception:
            return getattr(self.fallback, method)(*args, **kwargs)

    def quotes(self) -> pd.DataFrame:
        try:
            quotes = self.primary.quotes()
            if isinstance(quotes, pd.DataFrame) and len(quotes) < MIN_FULL_MARKET_ROWS:
                raise RuntimeError(f"Primary full-market quotes incomplete: {len(quotes)} rows")
            return quotes
        except Exception:
            return self.fallback.quotes()

    def quotes_for(self, codes: list[str]) -> pd.DataFrame:
        if hasattr(self.primary, "quotes_for"):
            try:
                quotes = self.primary.quotes_for(codes)
                if isinstance(quotes, pd.DataFrame) and not quotes.empty:
                    return quotes
            except Exception:
                pass
        return self.fallback.quotes_for(codes)

    def indices(self) -> pd.DataFrame:
        return self._with_fallback("indices")

    def bid_ask(self, code: str) -> pd.DataFrame:
        return self._with_fallback("bid_ask", code)

    def boards(self) -> pd.DataFrame:
        return self._with_fallback("boards")

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        return self._with_fallback("hist", code, report_date=report_date)

    def intraday_1m(self, code: str, limit: int | None = None) -> pd.DataFrame:
        return self._with_fallback("intraday_1m", code, limit=limit)

    def recent_trades(self, code: str, limit: int = 100) -> pd.DataFrame:
        return self._with_fallback("recent_trades", code, limit=limit)

    def zt_pool(self, report_date: date | None = None) -> pd.DataFrame:
        return self._with_fallback("zt_pool", report_date=report_date)

    def board_constituents(self, board: dict[str, Any]) -> pd.DataFrame:
        return self._with_fallback("board_constituents", board)


class CloudMarketDataProvider:
    """Route each endpoint to the lightest reliable public data source."""

    def __init__(
        self,
        *,
        full_market_provider: Any | None = None,
        realtime_provider: Any | None = None,
        board_provider: Any | None = None,
        history_provider: Any | None = None,
        zt_provider: Any | None = None,
    ) -> None:
        akshare = AkshareMarketDataProvider()
        tencent = TencentMarketDataProvider()
        sina = SinaMarketDataProvider()
        self.full_market_provider = full_market_provider or FallbackMarketDataProvider(
            primary=EastmoneyDirectMarketDataProvider(),
            fallback=sina,
        )
        self.realtime_provider = realtime_provider or FallbackMarketDataProvider(
            primary=tencent,
            fallback=akshare,
        )
        self.board_provider = board_provider or FallbackMarketDataProvider(
            primary=sina,
            fallback=akshare,
        )
        self.history_provider = history_provider or FallbackMarketDataProvider(
            primary=sina,
            fallback=akshare,
        )
        self.zt_provider = zt_provider or akshare
        sources = [
            getattr(self.full_market_provider, "source", EASTMONEY_DIRECT_SOURCE),
            getattr(self.realtime_provider, "source", TENCENT_SOURCE),
            getattr(self.board_provider, "source", SINA_SOURCE),
            getattr(self.history_provider, "source", SINA_SOURCE),
            getattr(self.zt_provider, "source", DEFAULT_SOURCE),
        ]
        self.source = "+".join(dict.fromkeys(str(source) for source in sources))

    def quotes(self) -> pd.DataFrame:
        return self.full_market_provider.quotes()

    def quotes_for(self, codes: list[str]) -> pd.DataFrame:
        return self.realtime_provider.quotes_for(codes)

    def indices(self) -> pd.DataFrame:
        return self.realtime_provider.indices()

    def bid_ask(self, code: str) -> pd.DataFrame:
        return self.realtime_provider.bid_ask(code)

    def boards(self) -> pd.DataFrame:
        return self.board_provider.boards()

    def board_constituents(self, board: dict[str, Any]) -> pd.DataFrame:
        return self.board_provider.board_constituents(board)

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        return self.history_provider.hist(code, report_date=report_date)

    def intraday_1m(self, code: str, limit: int | None = None) -> pd.DataFrame:
        return self.history_provider.intraday_1m(code, limit=limit)

    def recent_trades(self, code: str, limit: int = 100) -> pd.DataFrame:
        return self.realtime_provider.recent_trades(code, limit=limit)

    def zt_pool(self, report_date: date | None = None) -> pd.DataFrame:
        return self.zt_provider.zt_pool(report_date=report_date)


def create_default_provider() -> CloudMarketDataProvider:
    return CloudMarketDataProvider()


class StaticMarketDataProvider:
    source: str = "static-test"

    def __init__(
        self,
        quotes: pd.DataFrame | None = None,
        indices: pd.DataFrame | None = None,
        bidasks: dict[str, pd.DataFrame] | None = None,
        boards_df: pd.DataFrame | None = None,
        hist: dict[str, pd.DataFrame] | None = None,
        intraday: dict[str, pd.DataFrame] | None = None,
        recent_trades: dict[str, pd.DataFrame] | None = None,
        zt_pool: pd.DataFrame | None = None,
    ) -> None:
        self.quotes_df = quotes.copy() if quotes is not None else pd.DataFrame()
        self.indices_df = indices.copy() if indices is not None else pd.DataFrame()
        self.bidasks = bidasks or {}
        self.boards_df = boards_df.copy() if boards_df is not None else pd.DataFrame()
        self.hist_map = hist or {}
        self.intraday_map = intraday or {}
        self.recent_trades_map = recent_trades or {}
        self.zt_pool_df = zt_pool.copy() if zt_pool is not None else pd.DataFrame()

    def quotes(self) -> pd.DataFrame:
        return self.quotes_df.copy()

    def quotes_for(self, codes: list[str]) -> pd.DataFrame:
        normalized = {normalize_code(code) for code in codes}
        quotes = self.quotes()
        code_col = _quote_col(quotes, "代码", "code", "股票代码")
        if code_col is None:
            return quotes
        return quotes[quotes[code_col].map(normalize_code).isin(normalized)].copy()

    def indices(self) -> pd.DataFrame:
        return self.indices_df.copy()

    def bid_ask(self, code: str) -> pd.DataFrame:
        return self.bidasks.get(normalize_code(code), pd.DataFrame()).copy()

    def boards(self) -> pd.DataFrame:
        return self.boards_df.copy()

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        return self.hist_map.get(normalize_code(code), pd.DataFrame()).copy()

    def intraday_1m(self, code: str, limit: int | None = None) -> pd.DataFrame:
        out = self.intraday_map.get(normalize_code(code), pd.DataFrame()).copy()
        return out.tail(limit).copy() if limit and not out.empty else out

    def recent_trades(self, code: str, limit: int = 100) -> pd.DataFrame:
        out = self.recent_trades_map.get(normalize_code(code), pd.DataFrame()).copy()
        return out.head(limit).copy() if limit and not out.empty else out

    def zt_pool(self, report_date: date | None = None) -> pd.DataFrame:
        return self.zt_pool_df.copy()


def calculate_intraday_technical_indicators(hist_df: pd.DataFrame) -> dict[str, Any]:
    if hist_df is None or hist_df.empty:
        return {key: None for key in [
            "ma5",
            "ma10",
            "ma20",
            "ma60",
            "macd_dif",
            "macd_dea",
            "macd_hist",
            "rsi6",
            "rsi12",
            "rsi24",
            "boll_upper",
            "boll_mid",
            "boll_lower",
            "recent_high_20",
            "recent_low_20",
            "volume_ma5",
            "volume_ma10",
            "atr",
            "platform_support",
            "platform_resistance",
        ]}
    work = hist_df.copy()
    close_col = _quote_col(work, "收盘", "close", "最新价")
    high_col = _quote_col(work, "最高", "high")
    low_col = _quote_col(work, "最低", "low")
    volume_col = _quote_col(work, "成交量", "volume")
    close = pd.to_numeric(work[close_col], errors="coerce") if close_col else pd.Series(dtype="float64")
    high = pd.to_numeric(work[high_col], errors="coerce") if high_col else close
    low = pd.to_numeric(work[low_col], errors="coerce") if low_col else close
    volume = pd.to_numeric(work[volume_col], errors="coerce") if volume_col else pd.Series([float("nan")] * len(close))

    def last(series: pd.Series) -> float | None:
        if series.empty:
            return None
        value = series.iloc[-1]
        if pd.isna(value):
            return None
        return round(float(value), 4)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = (dif - dea) * 2

    def rsi(period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, pd.NA)
        return 100 - (100 / (1 + rs))

    ma20 = close.rolling(20).mean()
    boll_std = close.rolling(20).std()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    recent_low_20 = low.rolling(20).min()
    recent_high_20 = high.rolling(20).max()
    return {
        "ma5": last(close.rolling(5).mean()),
        "ma10": last(close.rolling(10).mean()),
        "ma20": last(ma20),
        "ma60": last(close.rolling(60).mean()),
        "macd_dif": last(dif),
        "macd_dea": last(dea),
        "macd_hist": last(macd_hist),
        "rsi6": last(rsi(6)),
        "rsi12": last(rsi(12)),
        "rsi24": last(rsi(24)),
        "boll_upper": last(ma20 + 2 * boll_std),
        "boll_mid": last(ma20),
        "boll_lower": last(ma20 - 2 * boll_std),
        "recent_high_20": last(recent_high_20),
        "recent_low_20": last(recent_low_20),
        "volume_ma5": last(volume.rolling(5).mean()),
        "volume_ma10": last(volume.rolling(10).mean()),
        "atr": last(true_range.rolling(14).mean()),
        "platform_support": last(recent_low_20),
        "platform_resistance": last(recent_high_20),
    }


class MarketDataService:
    def __init__(self, provider: Any | None = None, review_store_path: str | Path | None = None) -> None:
        self.provider = provider or create_default_provider()
        default_review_path = Path(tempfile.gettempdir()) / "a_share_review_ledger.json"
        self.review_store_path = Path(
            review_store_path or os.getenv("A_SHARE_REVIEW_STORE_PATH") or default_review_path
        )

    @property
    def source(self) -> str:
        return str(getattr(self.provider, "source", DEFAULT_SOURCE))

    def _read_review_records(self) -> list[dict[str, Any]]:
        if not self.review_store_path.exists():
            return []
        try:
            raw = json.loads(self.review_store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _write_review_records(self, records: list[dict[str, Any]]) -> None:
        self.review_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.review_store_path.write_text(
            json.dumps(json_safe(records), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def log_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        code = normalize_code(payload.get("code", ""))
        if not code:
            return response_envelope({"error": "code is required"}, source=self.source, freshness="unavailable")
        now = fetched_at()
        records = self._read_review_records()
        review_id = f"{now.replace('-', '').replace(':', '').replace(' ', '')}-{code}-{len(records) + 1}"
        record = {
            "review_id": review_id,
            "created_at": now,
            "updated_at": now,
            "status": "open",
            "code": code,
            "name": str(payload.get("name", "") or ""),
            "decision": str(payload.get("decision", "") or ""),
            "decision_score": _nullable_float(payload.get("decision_score")),
            "freshness": payload.get("freshness"),
            "key_levels": payload.get("key_levels") if isinstance(payload.get("key_levels"), dict) else {},
            "risk_tags": payload.get("risk_tags") if isinstance(payload.get("risk_tags"), list) else [],
            "source": payload.get("source") or "manual_or_gpt",
            "evaluation": None,
        }
        records.append(record)
        self._write_review_records(records)
        return response_envelope(
            {
                "record": record,
                "next_step": "下次分析前调用getRecentReviews对比上次判断",
                "privacy_note": "仅保存股票代码、判断、关键点位和复盘标签，不保存券商账户凭据",
            },
            source=self.source,
        )

    def recent_reviews(self, code: str | None = None, limit: int = 10) -> dict[str, Any]:
        normalized_code = normalize_code(code or "")
        records = self._read_review_records()
        if normalized_code:
            records = [record for record in records if normalize_code(record.get("code", "")) == normalized_code]
        records = sorted(records, key=lambda item: str(item.get("created_at", "")), reverse=True)[: max(1, limit)]
        return response_envelope(
            {
                "records": records,
                "must_compare_with_current_analysis": True,
                "comparison_prompt": "请对比上次判断、当前价格/板块/技术结构变化，并说明上次判断是否仍成立",
            },
            source=self.source,
        )

    def evaluate_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        review_id = str(payload.get("review_id", "") or "")
        records = self._read_review_records()
        matched_index = next((index for index, item in enumerate(records) if item.get("review_id") == review_id), None)
        if matched_index is None:
            return response_envelope({"error": "review_id not found"}, source=self.source, freshness="unavailable")
        evaluation = {
            "evaluated_at": fetched_at(),
            "actual_outcome": str(payload.get("actual_outcome", "") or ""),
            "actual_action": str(payload.get("actual_action", "") or ""),
            "triggered_failure_line": bool(payload.get("triggered_failure_line", False)),
            "triggered_buy_condition": bool(payload.get("triggered_buy_condition", False)),
            "lesson_tags": payload.get("lesson_tags") if isinstance(payload.get("lesson_tags"), list) else [],
            "outcome_rating": str(payload.get("outcome_rating", "") or "未评级"),
            "outcome_note": str(payload.get("outcome_note", "") or ""),
        }
        records[matched_index]["status"] = "evaluated"
        records[matched_index]["updated_at"] = evaluation["evaluated_at"]
        records[matched_index]["evaluation"] = evaluation
        self._write_review_records(records)
        return response_envelope(
            {
                "record": records[matched_index],
                "next_step": "后续分析前调用getReviewLessons，避免重复同类错误或忽略有效经验",
            },
            source=self.source,
        )

    def review_lessons(self, limit: int = 20) -> dict[str, Any]:
        records = self._read_review_records()
        evaluated = [record for record in records if isinstance(record.get("evaluation"), dict)]
        lesson_counts: dict[str, int] = {}
        latest_lessons: list[dict[str, Any]] = []
        for record in sorted(evaluated, key=lambda item: str(item.get("updated_at", "")), reverse=True):
            evaluation = record.get("evaluation") or {}
            tags = evaluation.get("lesson_tags") if isinstance(evaluation.get("lesson_tags"), list) else []
            for tag in tags:
                lesson_counts[str(tag)] = lesson_counts.get(str(tag), 0) + 1
            latest_lessons.append(
                {
                    "code": record.get("code"),
                    "name": record.get("name"),
                    "decision": record.get("decision"),
                    "outcome_rating": evaluation.get("outcome_rating"),
                    "lesson_tags": tags,
                    "actual_outcome": evaluation.get("actual_outcome"),
                }
            )
        return response_envelope(
            {
                "lesson_counts": lesson_counts,
                "latest_lessons": latest_lessons[: max(1, limit)],
                "usage_rule": "分析前先读这些lesson；若当前情形相似，必须说明本次是否沿用或反驳历史经验",
            },
            source=self.source,
        )

    def _unavailable(self, exc: Exception) -> dict[str, Any]:
        return response_envelope({"error": f"{type(exc).__name__}: {exc}"}, source=self.source, freshness="unavailable")

    def health(self) -> dict[str, Any]:
        return response_envelope({"status": "ok", "provider": self.source}, source=self.source)

    def _module_failed(self, exc: Exception) -> dict[str, Any]:
        return {"status": "failed", "fetched_at": fetched_at(), "error": f"{type(exc).__name__}: {exc}"}

    def _quote_detail(self, code: str) -> dict[str, Any]:
        fetched_time = fetched_at()
        quote_df = self.provider.quotes_for([code]) if hasattr(self.provider, "quotes_for") else self.provider.quotes()
        row = _first_row(quote_df)
        if not row:
            raise RuntimeError("No quote data returned")
        latest_price = _nullable_float(_pick(row, "最新价", "latest_price", "last_price", "现价"))
        pre_close = _nullable_float(_pick(row, "昨收", "pre_close", "昨收价"))
        change = _nullable_float(_pick(row, "涨跌额", "change"))
        if change is None and latest_price is not None and pre_close is not None:
            change = round(latest_price - pre_close, 4)
        return {
            "status": "ok",
            "fetched_at": fetched_time,
            "code": normalize_code(_pick(row, "代码", "code", default=code)),
            "name": str(_pick(row, "名称", "name", default="") or ""),
            "market": stock_market(code),
            "latest_price": latest_price,
            "change": change,
            "change_pct": _nullable_float(_pick(row, "涨跌幅", "change_pct", "day_change_pct")),
            "pre_close": pre_close,
            "open": _nullable_float(_pick(row, "今开", "开盘", "open")),
            "high": _nullable_float(_pick(row, "最高", "high")),
            "low": _nullable_float(_pick(row, "最低", "low")),
            "turnover_rate": _nullable_float(_pick(row, "换手率", "turnover_rate")),
            "volume_ratio": _nullable_float(_pick(row, "量比", "volume_ratio")),
            "volume": _nullable_float(_pick(row, "成交量", "volume")),
            "volume_unit": "股",
            "amount": _nullable_float(_pick(row, "成交额", "amount")),
            "amount_unit": "元",
            "total_market_cap": _nullable_float(_pick(row, "总市值", "total_market_cap")),
            "circulating_market_cap": _nullable_float(_pick(row, "流通市值", "circulating_market_cap")),
            "limit_up_price": _nullable_float(_pick(row, "涨停价", "limit_up_price")),
            "limit_down_price": _nullable_float(_pick(row, "跌停价", "limit_down_price")),
            "amplitude": _nullable_float(_pick(row, "振幅", "amplitude")),
        }

    def _intraday_rows(self, code: str, limit: int | None = None) -> dict[str, Any]:
        fetched_time = fetched_at()
        intraday_df = self.provider.intraday_1m(code, limit=limit)
        if intraday_df is None or intraday_df.empty:
            return {"status": "failed", "fetched_at": fetched_time, "rows": [], "error": "No intraday data returned"}
        rows = []
        for _, row in intraday_df.iterrows():
            rows.append(
                {
                    "time": str(_pick(row.to_dict(), "time", "时间", default="")),
                    "price": _nullable_float(_pick(row.to_dict(), "price", "close", "收盘")),
                    "avg_price": _nullable_float(_pick(row.to_dict(), "avg_price", "vwap", "均价")),
                    "vwap": _nullable_float(_pick(row.to_dict(), "vwap", "avg_price", "均价")),
                    "open": _nullable_float(_pick(row.to_dict(), "open", "开盘")),
                    "high": _nullable_float(_pick(row.to_dict(), "high", "最高")),
                    "low": _nullable_float(_pick(row.to_dict(), "low", "最低")),
                    "close": _nullable_float(_pick(row.to_dict(), "close", "收盘", "price")),
                    "volume": _nullable_float(_pick(row.to_dict(), "volume", "成交量")),
                    "volume_unit": "股",
                    "amount": _nullable_float(_pick(row.to_dict(), "amount", "成交额")),
                    "amount_unit": "元",
                }
            )
        return {"status": "ok", "fetched_at": fetched_time, "rows": rows}

    def _order_book_5(self, code: str, cash: float | None = None) -> dict[str, Any]:
        fetched_time = fetched_at()
        raw = _bid_ask_to_dict(self.provider.bid_ask(code))
        if not raw:
            return {"status": "failed", "fetched_at": fetched_time, "bid": [], "ask": [], "error": "No bid/ask data returned"}
        bid = []
        ask = []
        for level in range(1, 6):
            bid_price = _nullable_float(raw.get(f"buy_{level}"))
            ask_price = _nullable_float(raw.get(f"sell_{level}"))
            if bid_price is not None:
                bid.append({"price": bid_price, "volume": _nullable_float(raw.get(f"buy_{level}_volume"))})
            if ask_price is not None:
                ask.append({"price": ask_price, "volume": _nullable_float(raw.get(f"sell_{level}_volume"))})
        latest_price = _safe_float(raw.get("最新"))
        day_change_pct = _safe_float(raw.get("涨幅"), 0.0)
        sell_1 = _safe_float(raw.get("sell_1"))
        buy_1 = _safe_float(raw.get("buy_1"))
        action = classify_bid_ask_actionability(
            latest_price=latest_price,
            day_change_pct=day_change_pct,
            sell_1=sell_1,
            buy_1=buy_1,
            cash=cash,
        )
        spread = None
        if bid and ask and bid[0]["price"] is not None and ask[0]["price"] is not None:
            spread = round(float(ask[0]["price"]) - float(bid[0]["price"]), 4)
        seal_amount = None
        if action["is_limit_up_sealed"] and bid:
            seal_amount = (bid[0]["price"] or 0) * (bid[0]["volume"] or 0)
        return {
            "status": "ok",
            "fetched_at": fetched_time,
            "time": fetched_time.split(" ")[1],
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "volume_unit": "股",
            "is_limit_up": bool(day_change_pct >= 9.7),
            "is_limit_down": bool(day_change_pct <= -9.7),
            "is_limit_up_sealed": action["is_limit_up_sealed"],
            "seal_amount": seal_amount,
            "seal_ratio_to_amount": None,
            "seal_ratio_to_float_mcap": None,
            "actionability": action["actionability"],
            "min_lot_cost": action["min_lot_cost"],
        }

    def _recent_trade_rows(self, code: str, limit: int = 100) -> dict[str, Any]:
        fetched_time = fetched_at()
        trade_df = self.provider.recent_trades(code, limit=limit)
        if trade_df is None or trade_df.empty:
            return {"status": "failed", "fetched_at": fetched_time, "rows": [], "error": "No recent trade data returned"}
        rows = []
        for _, row in trade_df.iterrows():
            row_dict = row.to_dict()
            amount = _nullable_float(_pick(row_dict, "amount", "成交额"))
            price = _nullable_float(_pick(row_dict, "price", "成交价"))
            volume = _nullable_float(_pick(row_dict, "volume", "成交量"))
            if amount is None and price is not None and volume is not None:
                amount = price * volume
            rows.append(
                {
                    "time": str(_pick(row_dict, "time", "成交时间", default="")),
                    "price": price,
                    "volume": volume,
                    "volume_unit": "股",
                    "amount": amount,
                    "amount_unit": "元",
                    "side": str(_pick(row_dict, "side", "方向", default="unknown") or "unknown"),
                    "large_order_flag": bool(amount is not None and amount >= 500000),
                }
            )
        return {"status": "ok", "fetched_at": fetched_time, "rows": rows}

    def _technical_indicators(self, code: str) -> dict[str, Any]:
        fetched_time = fetched_at()
        hist_df = self.provider.hist(code)
        if hist_df is None or hist_df.empty:
            return {"status": "failed", "fetched_at": fetched_time, "error": "No daily history returned"}
        return {"status": "ok", "fetched_at": fetched_time, **calculate_intraday_technical_indicators(hist_df)}

    def _history_rows(self, code: str) -> dict[str, Any]:
        fetched_time = fetched_at()
        hist_df = self.provider.hist(code)
        if hist_df is None or hist_df.empty:
            return {"status": "failed", "fetched_at": fetched_time, "rows": [], "error": "No daily history returned"}
        rows = []
        for _, row in hist_df.tail(120).iterrows():
            row_dict = row.to_dict()
            rows.append(
                {
                    "date": str(_pick(row_dict, "日期", "date", default="")),
                    "open": _nullable_float(_pick(row_dict, "开盘", "open")),
                    "high": _nullable_float(_pick(row_dict, "最高", "high")),
                    "low": _nullable_float(_pick(row_dict, "最低", "low")),
                    "close": _nullable_float(_pick(row_dict, "收盘", "close", "最新价")),
                    "volume": _nullable_float(_pick(row_dict, "成交量", "volume")),
                    "amount": _nullable_float(_pick(row_dict, "成交额", "amount")),
                }
            )
        return {"status": "ok", "fetched_at": fetched_time, "rows": rows}

    def _board_context(self, code: str) -> dict[str, Any]:
        fetched_time = fetched_at()
        board_df = self.provider.boards()
        if board_df is None or board_df.empty:
            return {"status": "failed", "fetched_at": fetched_time, "industry": None, "concepts": [], "core_stocks": [], "error": "No board data returned"}
        work = board_df.copy()
        if "board_name" not in work.columns:
            work = normalize_board_snapshot(work, "board", "provider")
        code = normalize_code(code)
        leader_col = _quote_col(work, "leader_code", "领涨股代码")
        matched = pd.DataFrame()
        if leader_col:
            matched = work[work[leader_col].map(normalize_code).eq(code)]
            if not matched.empty:
                work = pd.concat([matched, work.drop(matched.index)], ignore_index=True)
        industry_rows = work[work.get("board_type", "").astype(str).str.contains("行业", na=False)] if "board_type" in work.columns else pd.DataFrame()
        concept_rows = work[work.get("board_type", "").astype(str).str.contains("概念", na=False)] if "board_type" in work.columns else pd.DataFrame()

        def constituent_match(row: dict[str, Any]) -> dict[str, Any] | None:
            if not hasattr(self.provider, "board_constituents"):
                return None
            try:
                constituents = self.provider.board_constituents(row)
            except Exception:
                return None
            if constituents is None or constituents.empty:
                return None
            code_col = _quote_col(constituents, "code", "代码", "股票代码")
            if code_col is None:
                return None
            matched_constituent = constituents[constituents[code_col].map(normalize_code).eq(code)]
            if matched_constituent.empty:
                return None
            item = matched_constituent.iloc[0].to_dict()
            return {
                "code": normalize_code(_pick(item, "code", "代码", "股票代码", default=code)),
                "name": str(_pick(item, "name", "名称", "股票名称", default="") or ""),
                "change_pct": _nullable_float(_pick(item, "change_pct", "涨跌幅", "changepercent")),
            }

        def find_constituent_board(rows: pd.DataFrame) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            for row in rows.to_dict(orient="records"):
                matched_stock = constituent_match(row)
                if matched_stock:
                    row = dict(row)
                    row["matched_by"] = "constituent"
                    row["matched_stock"] = matched_stock
                    return row, matched_stock
            return None, None

        industry_match, industry_stock = find_constituent_board(industry_rows) if not industry_rows.empty else (None, None)
        concept_matches: list[dict[str, Any]] = []
        for row in concept_rows.head(20).to_dict(orient="records"):
            matched_stock = constituent_match(row)
            if matched_stock:
                row = dict(row)
                row["matched_by"] = "constituent"
                row["matched_stock"] = matched_stock
                concept_matches.append(row)
        if industry_match:
            work = pd.concat(
                [
                    pd.DataFrame([industry_match]),
                    pd.DataFrame(concept_matches),
                    work,
                ],
                ignore_index=True,
            )
            industry_rows = work[work.get("board_type", "").astype(str).str.contains("行业", na=False)] if "board_type" in work.columns else pd.DataFrame()
            concept_rows = work[work.get("board_type", "").astype(str).str.contains("概念", na=False)] if "board_type" in work.columns else pd.DataFrame()
        elif leader_col and matched.empty:
            return {
                "status": "partial",
                "fetched_at": fetched_time,
                "industry": None,
                "concepts": [],
                "core_stocks": [],
                "error": "Stock-to-board mapping is unavailable from current public board source",
            }

        industry_row = _first_row(industry_rows if not industry_rows.empty else work)

        def board_item(row: dict[str, Any]) -> dict[str, Any]:
            up_count = _nullable_float(_pick(row, "up_count", "上涨家数"))
            down_count = _nullable_float(_pick(row, "down_count", "下跌家数"))
            up_ratio = None
            if up_count is not None and down_count is not None and up_count + down_count > 0:
                up_ratio = round(up_count / (up_count + down_count), 4)
            return {
                "name": str(_pick(row, "board_name", "板块名称", default="") or ""),
                "change_pct": _nullable_float(_pick(row, "change_pct", "涨跌幅")),
                "rank": _nullable_int(_pick(row, "rank", "排名")),
                "amount": _nullable_float(_pick(row, "amount", "成交额")),
                "turnover_rate": _nullable_float(_pick(row, "turnover_rate", "换手率")),
                "main_net_inflow": _nullable_float(_pick(row, "main_net_inflow", "主力净流入")),
                "up_count": up_count,
                "down_count": down_count,
                "up_ratio": up_ratio,
                "leader_code": normalize_code(_pick(row, "leader_code", "领涨股代码", default="")),
                "leader_name": str(_pick(row, "leader", "leader_name", "领涨股", default="") or ""),
                "leader_change_pct": _nullable_float(_pick(row, "leader_change_pct", "领涨股涨跌幅")),
                "limit_up_count": _nullable_int(_pick(row, "limit_up_count", "涨停家数")),
                "matched_by": _pick(row, "matched_by", default="leader" if normalize_code(_pick(row, "leader_code", "领涨股代码", default="")) == code else None),
                "matched_stock": _pick(row, "matched_stock", default=None),
            }

        concepts = [board_item(row.to_dict()) for _, row in concept_rows.head(5).iterrows()]
        core_stocks = []
        for row in work.head(5).to_dict(orient="records"):
            leader_code = normalize_code(_pick(row, "leader_code", "领涨股代码", default=""))
            leader_name = str(_pick(row, "leader", "leader_name", "领涨股", default="") or "")
            if leader_code or leader_name:
                core_stocks.append({"code": leader_code, "name": leader_name, "role": "leader"})
        return {
            "status": "ok",
            "fetched_at": fetched_time,
            "industry": board_item(industry_row) if industry_row else None,
            "concepts": concepts,
            "core_stocks": core_stocks,
        }

    def _market_context(self, *, include_breadth: bool = False) -> dict[str, Any]:
        fetched_time = fetched_at()
        phase = trading_phase()
        if not include_breadth:
            try:
                indices = normalize_quotes_df(self.provider.indices()).head(20).to_dict(orient="records")
            except Exception as exc:  # noqa: BLE001
                indices = []
                index_error = f"{type(exc).__name__}: {exc}"
            else:
                index_error = None
            index_map = {item.get("code"): item for item in indices}
            return {
                "status": "partial" if index_error else "ok",
                "fetched_at": fetched_time,
                "trade_date": datetime.now().strftime("%Y-%m-%d"),
                "trading_phase": phase,
                "index": {
                    "shanghai_change_pct": index_map.get("000001", {}).get("day_change_pct"),
                    "shenzhen_change_pct": index_map.get("399001", {}).get("day_change_pct"),
                    "chinext_change_pct": index_map.get("399006", {}).get("day_change_pct"),
                    "beijing_change_pct": index_map.get("899050", {}).get("day_change_pct"),
                },
                "breadth": {
                    "up_count": None,
                    "down_count": None,
                    "flat_count": None,
                    "limit_up_count": None,
                    "limit_down_count": None,
                    "real_limit_up_count": None,
                    "open_board_count": None,
                },
                "amount": {
                    "total_market_amount": None,
                    "shanghai_amount": None,
                    "shenzhen_amount": None,
                },
                "risk_mode": "unknown",
                "error": index_error,
            }
        snapshot = self.market_snapshot()
        data = snapshot.get("data", {})
        indices = {item.get("code"): item for item in data.get("indices", [])}
        up_count = data.get("up_count") or 0
        down_count = data.get("down_count") or 0
        total = up_count + down_count
        up_ratio = up_count / total if total else 0
        risk_mode = "attack" if up_ratio >= 0.62 else "neutral" if up_ratio >= 0.45 else "defense" if total else "unknown"
        return {
            "status": "ok" if snapshot.get("freshness") != "unavailable" else "partial",
            "fetched_at": fetched_time,
            "trade_date": datetime.now().strftime("%Y-%m-%d"),
            "trading_phase": phase,
            "index": {
                "shanghai_change_pct": indices.get("000001", {}).get("day_change_pct"),
                "shenzhen_change_pct": indices.get("399001", {}).get("day_change_pct"),
                "chinext_change_pct": indices.get("399006", {}).get("day_change_pct"),
                "beijing_change_pct": indices.get("899050", {}).get("day_change_pct"),
            },
            "breadth": {
                "up_count": data.get("up_count"),
                "down_count": data.get("down_count"),
                "flat_count": data.get("flat_count"),
                "limit_up_count": data.get("limit_up_count"),
                "limit_down_count": data.get("limit_down_count"),
                "real_limit_up_count": data.get("real_limit_up_count"),
                "open_board_count": data.get("open_board_count"),
            },
            "amount": {
                "total_market_amount": data.get("total_market_amount"),
                "shanghai_amount": data.get("shanghai_amount"),
                "shenzhen_amount": data.get("shenzhen_amount"),
            },
            "risk_mode": risk_mode,
        }

    def _zt_pool_related(self, code: str) -> dict[str, Any]:
        fetched_time = fetched_at()
        try:
            zt_df = self.provider.zt_pool()
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "fetched_at": fetched_time, "related": [], "error": f"{type(exc).__name__}: {exc}"}
        if zt_df is None or zt_df.empty:
            return {"status": "partial", "fetched_at": fetched_time, "related": []}
        code_col = _quote_col(zt_df, "code", "代码")
        work = zt_df.copy()
        if code_col:
            work = work[work[code_col].map(normalize_code).eq(normalize_code(code))]
        related = []
        for _, row in work.head(20).iterrows():
            row_dict = row.to_dict()
            related.append(
                {
                    "code": normalize_code(_pick(row_dict, "code", "代码", default="")),
                    "name": str(_pick(row_dict, "name", "名称", default="") or ""),
                    "change_pct": _nullable_float(_pick(row_dict, "change_pct", "涨跌幅")),
                    "latest_price": _nullable_float(_pick(row_dict, "latest_price", "最新价")),
                    "limit_up_price": _nullable_float(_pick(row_dict, "limit_up_price", "涨停价")),
                    "seal_amount": _nullable_float(_pick(row_dict, "seal_amount", "封单资金")),
                    "first_limit_up_time": _pick(row_dict, "first_limit_up_time", "首次封板时间"),
                    "last_limit_up_time": _pick(row_dict, "last_limit_up_time", "最后封板时间"),
                    "open_board_count": _nullable_int(_pick(row_dict, "open_board_count", "炸板次数")),
                    "continuous_limit_up_count": _nullable_int(_pick(row_dict, "continuous_limit_up_count", "连板数")),
                    "industry": _pick(row_dict, "industry", "所属行业"),
                    "concepts": _pick(row_dict, "concepts", default=[]),
                }
            )
        return {"status": "ok", "fetched_at": fetched_time, "related": related}

    def _account_context(self, account: dict[str, Any], quote: dict[str, Any], board: dict[str, Any]) -> dict[str, Any]:
        fetched_time = fetched_at()
        positions = []
        latest = quote.get("latest_price")
        industry_name = None
        if isinstance(board.get("industry"), dict):
            industry_name = board["industry"].get("name")
        for item in account.get("positions", []) or []:
            shares = int(float(item.get("shares", 0) or 0))
            available = int(float(item.get("available", 0) or 0))
            cost = _nullable_float(item.get("cost"))
            market_value = latest * shares if latest is not None else None
            pnl = (latest - cost) * shares if latest is not None and cost is not None else None
            pnl_pct = ((latest - cost) / cost * 100) if latest is not None and cost not in (None, 0) else None
            positions.append(
                {
                    "code": normalize_code(item.get("code", "")),
                    "name": item.get("name", ""),
                    "shares": shares,
                    "available": available,
                    "cost": cost,
                    "latest_price": latest,
                    "market_value": market_value,
                    "profit_loss": pnl,
                    "profit_loss_pct": pnl_pct,
                    "today_buy_flag": bool(shares > 0 and available < shares),
                    "sector": item.get("sector") or industry_name,
                    "concepts": item.get("concepts", []),
                }
            )
        exposures: dict[str, float] = {}
        total_asset = float(account.get("total_asset", 0) or 0)
        for item in positions:
            sector = item.get("sector") or "unknown"
            exposures[sector] = exposures.get(sector, 0.0) + float(item.get("market_value") or 0)
        return {
            "status": "ok",
            "fetched_at": fetched_time,
            "cash": _nullable_float(account.get("cash")),
            "total_asset": total_asset,
            "positions": positions,
            "sector_exposure": [
                {"sector": sector, "market_value": value, "ratio": value / total_asset if total_asset else None}
                for sector, value in exposures.items()
            ],
        }

    def _decision_score(
        self,
        *,
        quote: dict[str, Any],
        intraday: dict[str, Any],
        order_book: dict[str, Any],
        recent_trades: dict[str, Any],
        technical: dict[str, Any],
        board: dict[str, Any],
        market: dict[str, Any],
        account: dict[str, Any],
        data_quality: dict[str, Any],
    ) -> dict[str, Any]:
        risk_flags: list[str] = []
        positive_flags: list[str] = []
        latest_price = _nullable_float(quote.get("latest_price"))
        change_pct = _nullable_float(quote.get("change_pct"))
        cash = _nullable_float(account.get("cash"))
        min_lot_cost = latest_price * 100 if latest_price is not None else None

        if data_quality.get("quote_status") != "ok":
            risk_flags.append("实时报价不可确认")
        if data_quality.get("intraday_status") != "ok" or data_quality.get("order_book_status") != "ok":
            risk_flags.append("缺少分时或盘口关键数据")
        if data_quality.get("recent_trades_status") != "ok":
            risk_flags.append("逐笔成交不可确认")
        if data_quality.get("board_status") not in {"ok", "partial"}:
            risk_flags.append("板块匹配不足")
        if latest_price is not None and cash is not None and min_lot_cost is not None and cash < min_lot_cost:
            risk_flags.append("现金不足买一手")
        if order_book.get("actionability") not in {None, "可买"}:
            risk_flags.append(str(order_book.get("actionability")))
        if change_pct is not None and change_pct >= 8:
            risk_flags.append("日内涨幅偏高，防止追高")

        market_score = 8
        if market.get("risk_mode") == "attack":
            market_score = 15
            positive_flags.append("市场广度支持进攻")
        elif market.get("risk_mode") == "neutral":
            market_score = 11
        elif market.get("risk_mode") == "defense":
            market_score = 5
            risk_flags.append("市场风险模式偏防守")
        elif market.get("trading_phase") in {"continuous_auction", "lunch_break", "after_close"}:
            market_score = 9

        board_score = 6
        industry = board.get("industry") if isinstance(board.get("industry"), dict) else {}
        concepts = board.get("concepts") if isinstance(board.get("concepts"), list) else []
        industry_change = _nullable_float(industry.get("change_pct")) if industry else None
        industry_rank = _nullable_int(industry.get("rank")) if industry else None
        if board.get("status") == "ok":
            board_score = 12
            if industry_change is not None and industry_change > 0:
                board_score += 3
            if industry_rank is not None and industry_rank <= 10:
                board_score += 3
            industry_inflow = _nullable_float(industry.get("main_net_inflow")) if industry else None
            if industry_inflow is not None and industry_inflow > 0:
                board_score += 2
            if concepts:
                board_score += 1
            positive_flags.append("板块数据可用于方向确认")
        elif board.get("status") == "partial":
            board_score = 8
            risk_flags.append("个股所属板块只能部分确认")
        board_score = min(board_score, 20)

        technical_score = 5
        if technical.get("status") == "ok":
            technical_score = 8
            ma5 = _nullable_float(technical.get("ma5"))
            ma20 = _nullable_float(technical.get("ma20"))
            macd_hist = _nullable_float(technical.get("macd_hist"))
            if latest_price is not None and ma20 is not None and latest_price >= ma20:
                technical_score += 3
                positive_flags.append("价格站上MA20")
            if latest_price is not None and ma5 is not None and latest_price >= ma5:
                technical_score += 2
            if macd_hist is not None and macd_hist > 0:
                technical_score += 2
            if latest_price is not None and ma20 is not None and latest_price < ma20:
                risk_flags.append("尚未重新站稳MA20")
        else:
            risk_flags.append("日K技术指标不可确认")
        technical_score = min(technical_score, 15)

        intraday_score = 2
        rows = intraday.get("rows") if isinstance(intraday.get("rows"), list) else []
        if intraday.get("status") == "ok" and rows:
            last_row = rows[-1]
            last_price = _nullable_float(last_row.get("close") or last_row.get("price"))
            avg_price = _nullable_float(last_row.get("avg_price") or last_row.get("vwap"))
            intraday_score = 9
            if last_price is not None and avg_price is not None and last_price >= avg_price:
                intraday_score = 14
                positive_flags.append("分时站上均价")
            elif last_price is not None and avg_price is not None:
                risk_flags.append("分时暂未站回均价")
        else:
            risk_flags.append("缺少分时均价，不能判断低吸或追高")

        order_book_score = 2
        if order_book.get("status") == "ok":
            order_book_score = 6
            if order_book.get("actionability") == "可买":
                order_book_score = 10
                positive_flags.append("盘口可买且未触发硬否决")
            if order_book.get("is_limit_up_sealed"):
                order_book_score = 1
        else:
            risk_flags.append("盘口承接不可确认")

        trade_score = 3
        trades = recent_trades.get("rows") if isinstance(recent_trades.get("rows"), list) else []
        if recent_trades.get("status") == "ok" and trades:
            trade_score = 6
            buy_large = sum(1 for item in trades if item.get("side") == "buy" and item.get("large_order_flag"))
            sell_large = sum(1 for item in trades if item.get("side") == "sell" and item.get("large_order_flag"))
            if buy_large > sell_large:
                trade_score = 8
                positive_flags.append("最近成交有主动大单迹象")
            elif sell_large > buy_large:
                risk_flags.append("最近成交卖出大单偏多")

        account_score = 7
        if latest_price is not None and cash is not None and min_lot_cost is not None and cash >= min_lot_cost:
            account_score = 12
            positive_flags.append("现金可支持一手交易")
        positions = account.get("positions") if isinstance(account.get("positions"), list) else []
        locked_positions = [item for item in positions if int(item.get("shares") or 0) > int(item.get("available") or 0)]
        if len(locked_positions) >= 2:
            account_score -= 3
            risk_flags.append("锁仓股较多，新开仓需控制隔夜风险")
        exposure_ratios = [
            _nullable_float(item.get("ratio"))
            for item in account.get("sector_exposure", [])
            if isinstance(item, dict) and _nullable_float(item.get("ratio")) is not None
        ]
        if exposure_ratios and max(exposure_ratios) > 0.45:
            account_score -= 2
            risk_flags.append("同板块暴露偏高")
        account_score = max(0, min(account_score, 14))

        total_score = round(
            market_score
            + board_score
            + technical_score
            + intraday_score
            + order_book_score
            + trade_score
            + account_score,
            1,
        )
        if total_score >= 80:
            probability_band = "高胜率"
            suggested_action = "可进攻跟踪，等待买点触发后按计划执行"
            target_attack_position_pct = 75
        elif total_score >= 68:
            probability_band = "中高胜率"
            suggested_action = "可按偏进攻思路试仓或加仓，但必须贴近触发点"
            target_attack_position_pct = 70
        elif total_score >= 55:
            probability_band = "中性"
            suggested_action = "只做小仓试错或等待二次确认"
            target_attack_position_pct = 65
        elif total_score >= 40:
            probability_band = "低胜率"
            suggested_action = "只观察，不建议主动新开仓"
            target_attack_position_pct = 45
        else:
            probability_band = "不适合交易"
            suggested_action = "不建议买入，等待数据或结构改善"
            target_attack_position_pct = 25

        if "缺少分时或盘口关键数据" in risk_flags and total_score >= 65:
            total_score = 64.0
            probability_band = "中性"
            suggested_action = "关键盘中数据不足，只观察，等分时和盘口恢复后再判断"
            target_attack_position_pct = min(target_attack_position_pct, 60)

        return {
            "style": "aggressive_growth",
            "style_note": "偏进攻、弱保守；分数用于纪律化筛选，不替代盘面解释",
            "total_score": total_score,
            "probability_band": probability_band,
            "suggested_action": suggested_action,
            "target_attack_position_pct": target_attack_position_pct,
            "factor_scores": {
                "market": market_score,
                "board": board_score,
                "technical": technical_score,
                "intraday": intraday_score,
                "order_book": order_book_score,
                "recent_trades": trade_score,
                "account": account_score,
            },
            "positive_flags": list(dict.fromkeys(positive_flags)),
            "risk_flags": list(dict.fromkeys(risk_flags)),
            "score_usage": "用于判断是否值得进入交易计划；不是收益预测，也不是自动下单信号",
        }

    def _trading_plan(
        self,
        *,
        quote: dict[str, Any],
        intraday: dict[str, Any],
        order_book: dict[str, Any],
        technical: dict[str, Any],
        account: dict[str, Any],
        decision_score: dict[str, Any],
    ) -> dict[str, Any]:
        latest_price = _nullable_float(quote.get("latest_price"))
        rows = intraday.get("rows") if isinstance(intraday.get("rows"), list) else []
        last_row = rows[-1] if rows else {}
        avg_price = _nullable_float(last_row.get("avg_price") or last_row.get("vwap"))
        platform_support = _nullable_float(technical.get("platform_support") or technical.get("recent_low_20"))
        platform_resistance = _nullable_float(technical.get("platform_resistance") or technical.get("recent_high_20"))
        boll_upper = _nullable_float(technical.get("boll_upper"))
        atr = _nullable_float(technical.get("atr"))
        low = _nullable_float(quote.get("low"))
        high = _nullable_float(quote.get("high"))

        if avg_price is not None and latest_price is not None:
            buy_condition = f"回踩分时均价{avg_price:.2f}附近不破，重新放量站回{latest_price:.2f}上方再考虑"
        elif latest_price is not None:
            buy_condition = f"等待分时均价和盘口恢复；若价格不追高、重新站稳{latest_price:.2f}附近再评估"
        else:
            buy_condition = "等待实时价格、分时均价和盘口恢复后再判断买点"

        raw_supports = [value for value in [platform_support, low, latest_price - atr if latest_price is not None and atr is not None else None] if value is not None and value > 0]
        lower_supports = [value for value in raw_supports if latest_price is None or value < latest_price]
        if lower_supports:
            failure_line_value = max(lower_supports)
            failure_line = f"跌破{failure_line_value:.2f}且5-10分钟收不回，视为买入逻辑失败"
        elif latest_price is not None and atr is not None:
            failure_line = f"跌破{latest_price - atr:.2f}且反抽弱，视为买入逻辑失败"
        else:
            failure_line = "缺少技术支撑位，不能给精确失败线；先不做强买入"

        raw_resistances = [value for value in [platform_resistance, boll_upper, high] if value is not None and value > 0]
        upper_resistances = [value for value in raw_resistances if latest_price is None or value > latest_price]
        if upper_resistances:
            resistance_value = min(upper_resistances)
            take_profit_line = f"接近{resistance_value:.2f}或放量滞涨时分批止盈/减仓"
        elif latest_price is not None:
            take_profit_line = "若从买点快速拉升5%-8%但量能跟不上，优先考虑减仓而非追高"
        else:
            take_profit_line = "等待技术压力位恢复后再设定止盈位"

        cash = _nullable_float(account.get("cash"))
        min_lot_cost = _nullable_float(order_book.get("min_lot_cost"))
        max_lots = int(cash // min_lot_cost) if cash is not None and min_lot_cost not in (None, 0) else 0
        if decision_score.get("total_score", 0) >= 68 and max_lots > 0:
            position_plan = f"满足触发条件后可偏进攻，首笔1手起；若组合风险允许，目标进攻仓位可向{decision_score.get('target_attack_position_pct')}%靠近"
        elif max_lots > 0:
            position_plan = "当前只适合小仓或观察，不因有现金而强行买入"
        else:
            position_plan = "现金不足或一手成本不可确认，不能执行买入"

        return {
            "style_note": "偏进攻、弱保守；允许进攻仓位在65%以上，但必须有失败线",
            "buy_condition": buy_condition,
            "failure_line": failure_line,
            "take_profit_line": take_profit_line,
            "position_plan": position_plan,
            "next_day_plan": "若今日买入，必须按T+1隔夜处理；明天低开跌破失败线且反抽弱，优先处理风险",
            "point_sources": [
                "分时均价" if avg_price is not None else "分时均价缺失",
                "平台支撑/近20日低点" if platform_support is not None else "平台支撑缺失",
                "平台压力/近20日高点" if platform_resistance is not None else "平台压力缺失",
                "五档盘口可执行性",
                "账户现金与T+1约束",
            ],
        }

    def _technical_interpretation(
        self,
        *,
        quote: dict[str, Any],
        intraday: dict[str, Any],
        order_book: dict[str, Any],
        technical: dict[str, Any],
        trading_plan: dict[str, Any],
    ) -> dict[str, Any]:
        latest_price = _nullable_float(quote.get("latest_price"))
        ma5 = _nullable_float(technical.get("ma5"))
        ma10 = _nullable_float(technical.get("ma10"))
        ma20 = _nullable_float(technical.get("ma20"))
        ma60 = _nullable_float(technical.get("ma60"))
        recent_high_20 = _nullable_float(technical.get("recent_high_20"))
        recent_low_20 = _nullable_float(technical.get("recent_low_20"))
        boll_upper = _nullable_float(technical.get("boll_upper"))
        boll_mid = _nullable_float(technical.get("boll_mid"))
        boll_lower = _nullable_float(technical.get("boll_lower"))
        atr = _nullable_float(technical.get("atr"))

        rows = intraday.get("rows") if isinstance(intraday.get("rows"), list) else []
        last_row = rows[-1] if rows else {}
        previous_row = rows[-2] if len(rows) >= 2 else {}
        last_intraday_price = _nullable_float(last_row.get("close") or last_row.get("price"))
        avg_price = _nullable_float(last_row.get("avg_price") or last_row.get("vwap"))
        last_volume = _nullable_float(last_row.get("volume"))
        previous_volume = _nullable_float(previous_row.get("volume"))

        risk_tags: list[str] = []
        point_sources: list[str] = []

        if latest_price is None or technical.get("status") != "ok":
            trend_state = "日线不可确认"
            risk_tags.append("日K缺失")
        elif ma20 is not None and latest_price >= ma20 and (ma60 is None or latest_price >= ma60):
            trend_state = "日线偏强"
            point_sources.extend(["MA20", "MA60"])
        elif ma20 is not None and latest_price >= ma20:
            trend_state = "日线修复中"
            point_sources.append("MA20")
            if ma60 is not None:
                point_sources.append("MA60压力")
        elif ma20 is not None:
            trend_state = "日线偏弱"
            point_sources.append("MA20")
            risk_tags.append("未站稳MA20")
        else:
            trend_state = "日线结构不足"
            risk_tags.append("均线缺失")

        if intraday.get("status") != "ok" or not rows:
            intraday_state = "分时不可确认"
            risk_tags.append("分时缺失")
        elif last_intraday_price is not None and avg_price is not None and last_intraday_price >= avg_price:
            intraday_state = "分时偏强"
            point_sources.append("分时均价")
        elif last_intraday_price is not None and avg_price is not None:
            intraday_state = "分时偏弱"
            point_sources.append("分时均价")
            risk_tags.append("现价低于分时均价")
        else:
            intraday_state = "分时均价不足"
            risk_tags.append("分时均价缺失")

        volume_ratio = _nullable_float(quote.get("volume_ratio"))
        turnover_rate = _nullable_float(quote.get("turnover_rate"))
        if last_volume is not None and previous_volume is not None:
            if last_volume > previous_volume * 1.2:
                volume_state = "最近1分钟放量"
            elif last_volume < previous_volume * 0.8:
                volume_state = "最近1分钟缩量"
            else:
                volume_state = "最近1分钟量能平稳"
            point_sources.append("1分钟成交量")
        elif volume_ratio is not None:
            volume_state = f"量比{volume_ratio:.2f}，需结合分时量能确认"
            point_sources.append("量比")
        else:
            volume_state = "量能不可确认"
            risk_tags.append("量能缺失")

        support_levels = []
        for name, price, source in [
            ("分时均价", avg_price, "分时均价"),
            ("MA20", ma20, "20日均线"),
            ("20日低点", recent_low_20, "近20日低点"),
            ("BOLL下轨", boll_lower, "BOLL"),
        ]:
            if price is not None and price > 0:
                support_levels.append({"name": name, "price": price, "source": source})

        resistance_levels = []
        for name, price, source in [
            ("MA5", ma5, "5日均线"),
            ("MA10", ma10, "10日均线"),
            ("MA60", ma60, "60日均线"),
            ("BOLL中轨", boll_mid, "BOLL"),
            ("BOLL上轨", boll_upper, "BOLL"),
            ("20日高点", recent_high_20, "近20日高点"),
        ]:
            if price is not None and price > 0:
                resistance_levels.append({"name": name, "price": price, "source": source})

        if order_book.get("status") != "ok":
            risk_tags.append("盘口缺失")
        elif order_book.get("actionability") not in {None, "可买"}:
            risk_tags.append(str(order_book.get("actionability")))
        else:
            point_sources.append("五档盘口")

        if atr is not None:
            point_sources.append("ATR")

        turnaround_parts = []
        if avg_price is not None:
            turnaround_parts.append(f"重新站回分时均价{avg_price:.2f}")
        if ma20 is not None:
            turnaround_parts.append(f"不破MA20 {ma20:.2f}")
        if recent_high_20 is not None:
            turnaround_parts.append(f"放量突破近20日压力{recent_high_20:.2f}")
        turnaround_condition = "，且".join(turnaround_parts) if turnaround_parts else "实时分时、均线和盘口数据恢复后再判断转强条件"

        return {
            "purpose": "把原始技术指标翻译成GPT必须引用的交易语言，避免只给模糊结论",
            "trend_state": trend_state,
            "intraday_state": intraday_state,
            "volume_state": volume_state,
            "support_levels": support_levels,
            "resistance_levels": resistance_levels,
            "buy_trigger": trading_plan.get("buy_condition"),
            "sell_trigger": trading_plan.get("take_profit_line"),
            "failure_line": trading_plan.get("failure_line"),
            "turnaround_condition": turnaround_condition,
            "point_sources": list(dict.fromkeys(point_sources)),
            "risk_tags": list(dict.fromkeys(risk_tags)) or ["暂无明显技术硬伤，但仍需等待触发条件"],
            "required_reasoning_chain": {
                "结论": "先给持有/减仓/观察/买入条件，不要先讲故事",
                "数据证据": "引用实时价、分时均价、量能、盘口、板块和账户约束",
                "技术位来源": "说明买点、卖点、失败线来自哪些技术字段",
                "反证条件": "写明什么情况说明原判断失效或需要升级",
                "执行动作": "量化到价格区间、股数或等待条件",
            },
        }

    def _response_completeness_check(
        self,
        *,
        data_quality: dict[str, Any],
        technical_interpretation: dict[str, Any],
        board: dict[str, Any],
        market: dict[str, Any],
        account: dict[str, Any],
    ) -> dict[str, Any]:
        required_sections = [
            "数据来源与质量",
            "市场状态",
            "板块状态",
            "个股技术结构",
            "分时盘口",
            "账户与T+1",
            "操作计划",
            "反证条件与复盘",
        ]
        support_levels = technical_interpretation.get("support_levels") if isinstance(technical_interpretation, dict) else []
        resistance_levels = technical_interpretation.get("resistance_levels") if isinstance(technical_interpretation, dict) else []
        coverage = {
            "data_quality": bool(data_quality),
            "market_state": market.get("status") == "ok",
            "board_state": data_quality.get("board_status") in {"ok", "partial"},
            "technical_levels": bool(support_levels) and bool(resistance_levels),
            "intraday_vwap": data_quality.get("intraday_status") == "ok"
            and "分时均价" in (technical_interpretation.get("point_sources") or []),
            "order_book": data_quality.get("order_book_status") == "ok",
            "account_constraints": account.get("status") == "ok",
            "review_plan": True,
        }
        missing_or_degraded_items = []
        if not coverage["market_state"]:
            missing_or_degraded_items.append("市场状态缺失")
        if not coverage["board_state"]:
            missing_or_degraded_items.append("板块判断不足")
        if not coverage["technical_levels"]:
            missing_or_degraded_items.append("技术支撑压力不足")
        if not coverage["intraday_vwap"]:
            missing_or_degraded_items.append("分时均价缺失")
        if not coverage["order_book"]:
            missing_or_degraded_items.append("盘口缺失")
        if data_quality.get("recent_trades_status") != "ok":
            missing_or_degraded_items.append("逐笔成交缺失，主动买卖判断降级")

        return {
            "purpose": "约束GPT输出完整分析；缺失项必须明说并降低结论强度",
            "required_sections": required_sections,
            "must_use_reasoning_chain": ["结论", "数据证据", "技术位来源", "反证条件", "执行动作"],
            "coverage": coverage,
            "missing_or_degraded_items": missing_or_degraded_items,
            "reply_rule": "最终回复必须覆盖required_sections；每只重点股必须按must_use_reasoning_chain写，不允许只给结论",
            "anti_lazy_rule": "如果无法覆盖某段，不要省略；写明缺失原因、影响和结论降级方式",
        }

    def _execution_checklist(
        self,
        *,
        quote: dict[str, Any],
        intraday: dict[str, Any],
        order_book: dict[str, Any],
        recent_trades: dict[str, Any],
        technical: dict[str, Any],
        board: dict[str, Any],
        market: dict[str, Any],
        account: dict[str, Any],
        data_quality: dict[str, Any],
        trading_plan: dict[str, Any],
    ) -> dict[str, Any]:
        latest_price = _nullable_float(quote.get("latest_price"))
        positions = account.get("positions") if isinstance(account.get("positions"), list) else []
        matched_position = next(
            (
                item
                for item in positions
                if normalize_code(item.get("code", "")) == normalize_code(quote.get("code", ""))
            ),
            None,
        )
        cost = _nullable_float(matched_position.get("cost")) if isinstance(matched_position, dict) else None
        available = int(matched_position.get("available") or 0) if isinstance(matched_position, dict) else None
        shares = int(matched_position.get("shares") or 0) if isinstance(matched_position, dict) else None

        rows = intraday.get("rows") if isinstance(intraday.get("rows"), list) else []
        last_row = rows[-1] if rows else {}
        last_intraday_price = _nullable_float(last_row.get("close") or last_row.get("price"))
        avg_price = _nullable_float(last_row.get("avg_price") or last_row.get("vwap"))
        intraday_price = last_intraday_price if last_intraday_price is not None else latest_price
        above_avg_price = (
            intraday_price >= avg_price
            if intraday_price is not None and avg_price is not None
            else None
        )
        below_cost = latest_price < cost if latest_price is not None and cost is not None else None

        if data_quality.get("intraday_status") != "ok" or data_quality.get("order_book_status") != "ok":
            immediate_action = "只观察，等待关键盘中数据恢复"
        elif above_avg_price is True and below_cost is not True:
            immediate_action = "可继续观察买盘承接，等待交易计划触发"
        elif above_avg_price is False:
            immediate_action = "暂不追买，先看能否重新站回分时均价"
        elif below_cost is True:
            immediate_action = "低于成本线，先观察反抽质量，不盲目加仓"
        else:
            immediate_action = "数据基本可用，但买卖需等待触发条件"

        bid_total = sum(_nullable_float(item.get("volume")) or 0 for item in order_book.get("bid", []) if isinstance(item, dict))
        ask_total = sum(_nullable_float(item.get("volume")) or 0 for item in order_book.get("ask", []) if isinstance(item, dict))
        sell_pressure_level = "unknown"
        if bid_total or ask_total:
            sell_pressure_level = "偏重" if ask_total > bid_total * 1.2 else "均衡" if ask_total >= bid_total * 0.8 else "较轻"

        return {
            "purpose": "盘中执行前检查清单，帮助GPT避免只讲逻辑、不讲能否动手",
            "data_reliability": {
                "quote": {
                    "status": data_quality.get("quote_status"),
                    "fetched_at": quote.get("fetched_at"),
                    "freshness": quote.get("freshness"),
                },
                "intraday": {
                    "status": data_quality.get("intraday_status"),
                    "fetched_at": intraday.get("fetched_at"),
                },
                "order_book": {
                    "status": data_quality.get("order_book_status"),
                    "fetched_at": order_book.get("fetched_at"),
                },
                "recent_trades": {
                    "status": data_quality.get("recent_trades_status"),
                    "fetched_at": recent_trades.get("fetched_at"),
                },
                "technical": {
                    "status": data_quality.get("technical_status"),
                    "fetched_at": technical.get("fetched_at"),
                },
                "board": {
                    "status": data_quality.get("board_status"),
                    "fetched_at": board.get("fetched_at"),
                },
                "market": {
                    "status": data_quality.get("market_status"),
                    "fetched_at": market.get("fetched_at"),
                },
            },
            "intraday_read": {
                "latest_price": latest_price,
                "last_intraday_price": last_intraday_price,
                "avg_price": avg_price,
                "cost": cost,
                "above_avg_price": above_avg_price,
                "below_cost": below_cost,
                "intraday_pattern": "均价上方" if above_avg_price is True else "均价下方" if above_avg_price is False else "分时均价不可确认",
                "volume_read": "需结合最近3-5根1分钟量能变化判断放量突破或缩量回踩",
            },
            "order_book_read": {
                "bid_total": bid_total or None,
                "ask_total": ask_total or None,
                "sell_pressure_level": sell_pressure_level,
                "needs_active_buying_above": order_book.get("ask", [{}])[0].get("price") if order_book.get("ask") else None,
                "watch_minutes": 5,
                "note": "卖压偏重时，不把盘口静态买盘当作强承接；至少观察3-5分钟主动成交",
            },
            "account_constraints": {
                "shares": shares,
                "available": available,
                "t_plus_1_locked": shares is not None and available is not None and available < shares,
                "cash": account.get("cash"),
                "min_lot_cost": order_book.get("min_lot_cost"),
            },
            "execution_window": {
                "immediate_action": immediate_action,
                "hold_if": "站回或维持分时均价上方，且盘口卖压不继续扩大",
                "sell_if": trading_plan.get("failure_line") or "跌破关键确认线且5-10分钟收不回",
                "buy_if": trading_plan.get("buy_condition"),
                "observe_until": "至少再观察3-5分钟分时均价、主动成交和卖一卖二压单变化",
            },
            "opportunity_cost": {
                "check": "若板块和市场不配合，即使个股未破位，也要考虑资金占用效率",
                "avoid": "不要因为之前反复推荐过就继续给买入结论，必须有新的实时证据",
            },
        }

    def _recent_3d_context(self, history: dict[str, Any]) -> dict[str, Any]:
        rows = history.get("rows") if isinstance(history.get("rows"), list) else []
        recent_rows = rows[-3:] if rows else []
        previous_rows = rows[-4:] if len(rows) >= 4 else recent_rows
        enriched = []
        for index, row in enumerate(recent_rows):
            previous = previous_rows[index] if len(previous_rows) == len(recent_rows) + 1 else None
            previous_close = _nullable_float(previous.get("close")) if isinstance(previous, dict) else None
            open_price = _nullable_float(row.get("open"))
            high = _nullable_float(row.get("high"))
            low = _nullable_float(row.get("low"))
            close = _nullable_float(row.get("close"))
            volume = _nullable_float(row.get("volume"))
            base = previous_close if previous_close not in (None, 0) else open_price
            close_location = None
            if high is not None and low is not None and high != low and close is not None:
                close_location = round((close - low) / (high - low) * 100, 2)
            enriched.append(
                {
                    "date": row.get("date"),
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": _nullable_float(row.get("amount")),
                    "change_pct": _pct_change(close, base),
                    "close_location_pct": close_location,
                }
            )

        closes = [_nullable_float(item.get("close")) for item in enriched]
        highs = [_nullable_float(item.get("high")) for item in enriched]
        lows = [_nullable_float(item.get("low")) for item in enriched]
        volumes = [_nullable_float(item.get("volume")) for item in enriched]
        valid_closes = [value for value in closes if value is not None]
        valid_highs = [value for value in highs if value is not None]
        valid_lows = [value for value in lows if value is not None]
        valid_volumes = [value for value in volumes if value is not None]

        if len(valid_volumes) < 3:
            volume_trend = "不可确认"
        elif valid_volumes[0] < valid_volumes[1] < valid_volumes[2]:
            volume_trend = "放大"
        elif valid_volumes[0] > valid_volumes[1] > valid_volumes[2]:
            volume_trend = "萎缩"
        elif max(valid_volumes) and (max(valid_volumes) - min(valid_volumes)) / max(valid_volumes) <= 0.15:
            volume_trend = "平稳"
        else:
            volume_trend = "混乱"

        if len(valid_closes) >= 3 and valid_closes[0] < valid_closes[1] < valid_closes[2]:
            direction = "连续上涨"
        elif len(valid_closes) >= 3 and valid_closes[0] > valid_closes[1] > valid_closes[2]:
            direction = "连续下跌"
        elif len(valid_closes) >= 2:
            direction = "震荡"
        else:
            direction = "不可确认"

        pattern_tags = []
        if len(enriched) >= 2:
            last = enriched[-1]
            prev = enriched[-2]
            last_close = _nullable_float(last.get("close"))
            prev_close = _nullable_float(prev.get("close"))
            last_volume = _nullable_float(last.get("volume"))
            prev_volume = _nullable_float(prev.get("volume"))
            if last_close is not None and prev_close is not None and last_volume is not None and prev_volume is not None:
                if last_close > prev_close and last_volume > prev_volume:
                    pattern_tags.append("放量上涨")
                elif last_close < prev_close and last_volume > prev_volume:
                    pattern_tags.append("放量下跌")
                elif last_close > prev_close and last_volume <= prev_volume:
                    pattern_tags.append("缩量反弹")
                elif last_close < prev_close and last_volume <= prev_volume:
                    pattern_tags.append("缩量回踩")
        if not pattern_tags:
            pattern_tags.append("三日结构需结合分时确认")

        three_day_high = max(valid_highs) if valid_highs else None
        three_day_low = min(valid_lows) if valid_lows else None
        three_day_amplitude = None
        if three_day_high is not None and three_day_low not in (None, 0):
            three_day_amplitude = round((three_day_high - three_day_low) / three_day_low * 100, 2)

        return {
            "status": history.get("status", "failed"),
            "fetched_at": history.get("fetched_at"),
            "days_count": len(enriched),
            "days": enriched,
            "three_day_return_pct": _pct_change(valid_closes[-1], valid_closes[0]) if len(valid_closes) >= 2 else None,
            "three_day_high": three_day_high,
            "three_day_low": three_day_low,
            "three_day_amplitude_pct": three_day_amplitude,
            "volume_trend_3d": volume_trend,
            "direction_3d": direction,
            "pattern_tags": pattern_tags,
            "usage_note": "用于避免只看当日收盘；必须结合最近三日高低点、量能和当日分时判断。",
        }

    def _today_intraday_summary(self, quote: dict[str, Any], intraday: dict[str, Any]) -> dict[str, Any]:
        rows = intraday.get("rows") if isinstance(intraday.get("rows"), list) else []
        if not rows:
            return {
                "status": intraday.get("status", "failed"),
                "fetched_at": intraday.get("fetched_at"),
                "vwap_deviation_pct": None,
                "close_location_pct": None,
                "high_to_close_drawdown_pct": None,
                "phase_pattern": "分时不可确认",
                "last_5m_direction": "不可确认",
                "last_15m_direction": "不可确认",
                "last_30m_direction": "不可确认",
            }

        def row_price(item: dict[str, Any]) -> float | None:
            return _nullable_float(item.get("close") or item.get("price"))

        first_price = row_price(rows[0])
        last_price = row_price(rows[-1]) or _nullable_float(quote.get("latest_price"))
        avg_price = _nullable_float(rows[-1].get("avg_price") or rows[-1].get("vwap"))
        highs = [_nullable_float(item.get("high")) for item in rows]
        lows = [_nullable_float(item.get("low")) for item in rows]
        valid_highs = [value for value in highs if value is not None]
        valid_lows = [value for value in lows if value is not None]
        day_high = max(valid_highs) if valid_highs else _nullable_float(quote.get("high"))
        day_low = min(valid_lows) if valid_lows else _nullable_float(quote.get("low"))

        close_location = None
        if day_high is not None and day_low is not None and day_high != day_low and last_price is not None:
            close_location = round((last_price - day_low) / (day_high - day_low) * 100, 2)
        drawdown = None
        if day_high not in (None, 0) and last_price is not None:
            drawdown = round((day_high - last_price) / day_high * 100, 2)

        def direction(window: int) -> str:
            segment = rows[-window:] if len(rows) >= window else rows
            if len(segment) < 2:
                return "不可确认"
            start = row_price(segment[0])
            end = row_price(segment[-1])
            if start is None or end is None:
                return "不可确认"
            if end > start:
                return "上行"
            if end < start:
                return "下行"
            return "横盘"

        if last_price is not None and avg_price is not None:
            if last_price >= avg_price and close_location is not None and close_location >= 60:
                phase_pattern = "收盘站上均价且位置偏高"
            elif last_price >= avg_price:
                phase_pattern = "收盘站上均价但位置一般"
            elif close_location is not None and close_location <= 35:
                phase_pattern = "收盘低于均价且接近日内低位"
            else:
                phase_pattern = "收盘低于均价"
        else:
            phase_pattern = "分时均价不足"

        return {
            "status": intraday.get("status", "failed"),
            "fetched_at": intraday.get("fetched_at"),
            "first_price": first_price,
            "last_price": last_price,
            "avg_price": avg_price,
            "day_high_from_intraday": day_high,
            "day_low_from_intraday": day_low,
            "vwap_deviation_pct": _pct_change(last_price, avg_price),
            "close_location_pct": close_location,
            "high_to_close_drawdown_pct": drawdown,
            "total_volume": sum(_nullable_float(item.get("volume")) or 0 for item in rows),
            "last_5m_direction": direction(5),
            "last_15m_direction": direction(15),
            "last_30m_direction": direction(30),
            "phase_pattern": phase_pattern,
            "usage_note": "用于判断站上均价、冲高回落、尾盘偷拉或弱磨，不允许只看最新价。",
        }

    def _candlestick_structure(self, quote: dict[str, Any]) -> dict[str, Any]:
        open_price = _nullable_float(quote.get("open"))
        close = _nullable_float(quote.get("latest_price"))
        high = _nullable_float(quote.get("high"))
        low = _nullable_float(quote.get("low"))
        body_pct = (
            round(abs(close - open_price) / open_price * 100, 2)
            if close is not None and open_price not in (None, 0)
            else None
        )
        upper_shadow_pct = None
        lower_shadow_pct = None
        if None not in (open_price, close, high, low) and open_price:
            upper_shadow_pct = round((high - max(open_price, close)) / open_price * 100, 2)
            lower_shadow_pct = round((min(open_price, close) - low) / open_price * 100, 2)
        tags = []
        if open_price is not None and close is not None:
            tags.append("阳线" if close >= open_price else "阴线")
        if body_pct is not None and body_pct <= 0.3:
            tags.append("小实体/十字倾向")
        if upper_shadow_pct is not None and upper_shadow_pct >= 1.0:
            tags.append("上影线压力")
        if lower_shadow_pct is not None and lower_shadow_pct >= 1.0:
            tags.append("下影线承接")
        if not tags:
            tags.append("K线结构不可确认")
        return {
            "status": quote.get("status", "failed"),
            "fetched_at": quote.get("fetched_at"),
            "open": open_price,
            "close": close,
            "high": high,
            "low": low,
            "body_pct": body_pct,
            "upper_shadow_pct": upper_shadow_pct,
            "lower_shadow_pct": lower_shadow_pct,
            "pattern_tags": tags,
            "usage_note": "用于解释冲高回落、下影承接和实体强弱。",
        }

    def _moving_average_structure(self, quote: dict[str, Any], technical: dict[str, Any]) -> dict[str, Any]:
        latest = _nullable_float(quote.get("latest_price"))
        ma5 = _nullable_float(technical.get("ma5"))
        ma10 = _nullable_float(technical.get("ma10"))
        ma20 = _nullable_float(technical.get("ma20"))
        ma60 = _nullable_float(technical.get("ma60"))
        ma_values = [ma5, ma10, ma20, ma60]
        if sum(value is not None for value in ma_values) < 3:
            structure = "均线不足"
        elif ma5 is not None and ma10 is not None and ma20 is not None and ma60 is not None and ma5 > ma10 > ma20 > ma60:
            structure = "多头排列"
        elif ma5 is not None and ma10 is not None and ma20 is not None and ma60 is not None and ma5 < ma10 < ma20 < ma60:
            structure = "空头排列"
        else:
            structure = "均线纠缠"
        return {
            "status": technical.get("status", "failed"),
            "fetched_at": technical.get("fetched_at"),
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "structure": structure,
            "distance_to_ma5_pct": _pct_change(latest, ma5),
            "distance_to_ma20_pct": _pct_change(latest, ma20),
            "distance_to_ma60_pct": _pct_change(latest, ma60),
            "usage_note": "用于判断日线趋势是进攻、修复还是反弹压力。",
        }

    def _volume_price_relation(self, quote: dict[str, Any], recent_3d_context: dict[str, Any]) -> dict[str, Any]:
        days = recent_3d_context.get("days") if isinstance(recent_3d_context.get("days"), list) else []
        relation = "量价不可确认"
        explanation = "三日成交量或收盘价不足，不能判断量价关系。"
        if len(days) >= 2:
            last = days[-1]
            prev = days[-2]
            last_close = _nullable_float(last.get("close"))
            prev_close = _nullable_float(prev.get("close"))
            last_volume = _nullable_float(last.get("volume"))
            prev_volume = _nullable_float(prev.get("volume"))
            if None not in (last_close, prev_close, last_volume, prev_volume):
                price_up = last_close >= prev_close
                volume_up = last_volume >= prev_volume
                if price_up and volume_up:
                    relation = "放量上涨"
                    explanation = "价格较前一日上行且成交量放大，说明有增量资金参与。"
                elif (not price_up) and volume_up:
                    relation = "放量下跌"
                    explanation = "价格回落但成交量放大，需要警惕资金分歧或抛压释放。"
                elif price_up and not volume_up:
                    relation = "缩量反弹"
                    explanation = "价格反弹但量能未同步，持续性需要分时和板块确认。"
                else:
                    relation = "缩量回踩"
                    explanation = "价格回落且量能收缩，需看是否守住关键支撑。"
        return {
            "status": recent_3d_context.get("status", "failed"),
            "fetched_at": recent_3d_context.get("fetched_at"),
            "relation": relation,
            "volume_ratio": quote.get("volume_ratio"),
            "turnover_rate": quote.get("turnover_rate"),
            "explanation": explanation,
            "usage_note": "用于约束GPT必须说明上涨/下跌是否有量能支持。",
        }

    def _relative_strength(self, quote: dict[str, Any], board: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        stock_change = _nullable_float(quote.get("change_pct"))
        index_values = []
        if isinstance(market.get("index"), dict):
            index_values = [
                _nullable_float(market["index"].get(key))
                for key in ["shanghai_change_pct", "shenzhen_change_pct", "chinext_change_pct"]
            ]
        market_values = [value for value in index_values if value is not None]
        market_change = round(sum(market_values) / len(market_values), 4) if market_values else None
        industry = board.get("industry") if isinstance(board.get("industry"), dict) else {}
        board_change = _nullable_float(industry.get("change_pct")) if industry else None

        def compare(base: float | None, target: float | None, strong_label: str, weak_label: str, sync_label: str) -> str:
            if base is None or target is None:
                return "不可确认"
            spread = base - target
            if spread >= 0.5:
                return strong_label
            if spread <= -0.5:
                return weak_label
            return sync_label

        return {
            "status": "ok" if stock_change is not None and (market_change is not None or board_change is not None) else "partial",
            "fetched_at": quote.get("fetched_at"),
            "stock_change_pct": stock_change,
            "market_change_pct": market_change,
            "board_change_pct": board_change,
            "vs_market": compare(stock_change, market_change, "强于市场", "弱于市场", "同步市场"),
            "vs_board": compare(stock_change, board_change, "强于板块", "弱于板块", "同步板块"),
            "usage_note": "用于判断个股是主动强、跟随强，还是板块强但个股掉队。",
        }

    def _support_resistance_zones(
        self,
        *,
        quote: dict[str, Any],
        intraday_summary: dict[str, Any],
        technical: dict[str, Any],
        recent_3d_context: dict[str, Any],
        account: dict[str, Any],
    ) -> dict[str, Any]:
        positions = account.get("positions") if isinstance(account.get("positions"), list) else []
        matched = next((item for item in positions if normalize_code(item.get("code", "")) == normalize_code(quote.get("code", ""))), {})
        supports = []
        resistances = []

        def add_zone(target: list[dict[str, Any]], name: str, price: float | None, source: str) -> None:
            if price is None or price <= 0:
                return
            target.append({"name": name, "price": round(price, 4), "source": source})

        add_zone(supports, "分时均价", _nullable_float(intraday_summary.get("avg_price")), "intraday_1m.avg_price")
        add_zone(supports, "当日低点", _nullable_float(quote.get("low")), "quote.low")
        add_zone(supports, "三日低点", _nullable_float(recent_3d_context.get("three_day_low")), "recent_3d_context.three_day_low")
        add_zone(supports, "MA20", _nullable_float(technical.get("ma20")), "technical.ma20")
        add_zone(supports, "成本线", _nullable_float(matched.get("cost")) if isinstance(matched, dict) else None, "account.positions.cost")

        add_zone(resistances, "当日高点", _nullable_float(quote.get("high")), "quote.high")
        add_zone(resistances, "三日高点", _nullable_float(recent_3d_context.get("three_day_high")), "recent_3d_context.three_day_high")
        add_zone(resistances, "20日高点", _nullable_float(technical.get("recent_high_20")), "technical.recent_high_20")
        add_zone(resistances, "MA60", _nullable_float(technical.get("ma60")), "technical.ma60")
        add_zone(resistances, "BOLL上轨", _nullable_float(technical.get("boll_upper")), "technical.boll_upper")

        return {
            "status": "ok" if supports and resistances else "partial",
            "fetched_at": quote.get("fetched_at"),
            "support_zones": supports,
            "resistance_zones": resistances,
            "usage_note": "每个买点、卖点、失败线都必须引用这里的来源字段，不能凭感觉写点位。",
        }

    def _risk_volatility(
        self,
        *,
        quote: dict[str, Any],
        technical: dict[str, Any],
        recent_3d_context: dict[str, Any],
        intraday_summary: dict[str, Any],
    ) -> dict[str, Any]:
        amplitude = _nullable_float(quote.get("amplitude"))
        if amplitude is None:
            high = _nullable_float(quote.get("high"))
            low = _nullable_float(quote.get("low"))
            pre_close = _nullable_float(quote.get("pre_close"))
            if high is not None and low is not None and pre_close not in (None, 0):
                amplitude = round((high - low) / pre_close * 100, 2)
        three_day_amp = _nullable_float(recent_3d_context.get("three_day_amplitude_pct"))
        drawdown = _nullable_float(intraday_summary.get("high_to_close_drawdown_pct"))
        risk_level = "中"
        if amplitude is not None and amplitude >= 7 or three_day_amp is not None and three_day_amp >= 12:
            risk_level = "高"
        elif amplitude is not None and amplitude <= 3 and (drawdown is None or drawdown <= 1.5):
            risk_level = "低"
        return {
            "status": "ok" if amplitude is not None else "partial",
            "fetched_at": quote.get("fetched_at"),
            "intraday_amplitude_pct": amplitude,
            "three_day_amplitude_pct": three_day_amp,
            "high_to_close_drawdown_pct": drawdown,
            "atr": technical.get("atr"),
            "risk_level": risk_level,
            "usage_note": "用于判断隔夜风险、追高风险和止损线宽度。",
        }

    def _order_book_interpretation(self, order_book: dict[str, Any]) -> dict[str, Any]:
        bid_total = sum(_nullable_float(item.get("volume")) or 0 for item in order_book.get("bid", []) if isinstance(item, dict))
        ask_total = sum(_nullable_float(item.get("volume")) or 0 for item in order_book.get("ask", []) if isinstance(item, dict))
        bid_ask_ratio = _ratio(bid_total, ask_total)
        if bid_ask_ratio is None:
            pressure = "不可确认"
        elif bid_ask_ratio >= 1.5:
            pressure = "买盘较强"
        elif bid_ask_ratio <= 0.67:
            pressure = "卖压偏重"
        else:
            pressure = "买卖均衡"
        return {
            "status": order_book.get("status", "failed"),
            "fetched_at": order_book.get("fetched_at"),
            "bid_total": bid_total or None,
            "ask_total": ask_total or None,
            "bid_ask_ratio": bid_ask_ratio,
            "pressure": pressure,
            "spread": order_book.get("spread"),
            "actionability": order_book.get("actionability"),
            "top_bid": order_book.get("bid", [{}])[0] if order_book.get("bid") else None,
            "top_ask": order_book.get("ask", [{}])[0] if order_book.get("ask") else None,
            "usage_note": "静态五档只能辅助判断承接；若价格低于均价，买盘厚也不能直接等同强势。",
        }

    def _board_stock_alignment(self, quote: dict[str, Any], board: dict[str, Any]) -> dict[str, Any]:
        stock_change = _nullable_float(quote.get("change_pct"))
        industry = board.get("industry") if isinstance(board.get("industry"), dict) else {}
        industry_change = _nullable_float(industry.get("change_pct")) if industry else None
        status = board.get("status", "failed")
        if status not in {"ok", "partial"} or stock_change is None or industry_change is None:
            stock_vs_board = "不可确认"
            adjustment = "降级"
        else:
            spread = stock_change - industry_change
            if spread >= 0.5:
                stock_vs_board = "强于板块"
                adjustment = "上调" if _nullable_int(industry.get("rank")) is not None and int(industry.get("rank")) <= 10 else "不变"
            elif spread <= -0.5:
                stock_vs_board = "弱于板块"
                adjustment = "下调"
            else:
                stock_vs_board = "同步板块"
                adjustment = "不变"
        leader_code = normalize_code(industry.get("leader_code", "")) if industry else ""
        role = "leader" if leader_code and leader_code == normalize_code(quote.get("code", "")) else "unknown"
        if role == "unknown" and stock_vs_board == "强于板块":
            role = "trend_core"
        elif role == "unknown" and stock_vs_board == "弱于板块":
            role = "laggard"
        elif role == "unknown" and stock_vs_board == "同步板块":
            role = "follower"
        return {
            "status": status,
            "fetched_at": board.get("fetched_at"),
            "industry": industry.get("name") if industry else None,
            "industry_change_pct": industry_change,
            "industry_rank": industry.get("rank") if industry else None,
            "concepts": [item.get("name") for item in board.get("concepts", []) if isinstance(item, dict)],
            "stock_change_pct": stock_change,
            "stock_vs_board": stock_vs_board,
            "stock_role_estimate": role,
            "conclusion_adjustment": adjustment,
            "usage_note": "必须先判断板块，再判断个股；板块数据partial/failed时，买入结论自动降级。",
        }

    def _position_risk_contribution(self, quote: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
        positions = account.get("positions") if isinstance(account.get("positions"), list) else []
        matched = next((item for item in positions if normalize_code(item.get("code", "")) == normalize_code(quote.get("code", ""))), {})
        latest = _nullable_float(quote.get("latest_price"))
        shares = int(matched.get("shares") or 0) if isinstance(matched, dict) else 0
        available = int(matched.get("available") or 0) if isinstance(matched, dict) else 0
        cost = _nullable_float(matched.get("cost")) if isinstance(matched, dict) else None
        market_value = latest * shares if latest is not None else _nullable_float(matched.get("market_value")) if isinstance(matched, dict) else None
        total_asset = _nullable_float(account.get("total_asset"))
        pnl = (latest - cost) * shares if latest is not None and cost is not None else None
        pnl_pct = _pct_change(latest, cost)
        role = "非持仓观察"
        if shares > 0 and pnl is not None:
            role = "利润垫" if pnl > 0 else "风险源" if pnl < 0 else "持平观察"
        return {
            "status": "ok" if matched else "partial",
            "fetched_at": account.get("fetched_at"),
            "shares": shares,
            "available": available,
            "cost": cost,
            "latest_price": latest,
            "market_value": market_value,
            "portfolio_ratio_pct": round(market_value / total_asset * 100, 2) if market_value is not None and total_asset not in (None, 0) else None,
            "profit_loss": pnl,
            "profit_loss_pct": pnl_pct,
            "t_plus_1_locked": shares > available,
            "risk_role": role,
            "usage_note": "用于决定今天能否卖、卖多少，以及该票是利润垫还是风险源。",
        }

    def _technical_level_layers(
        self,
        *,
        quote: dict[str, Any],
        intraday_summary: dict[str, Any],
        support_resistance_zones: dict[str, Any],
        position_risk_contribution: dict[str, Any],
    ) -> dict[str, Any]:
        supports = support_resistance_zones.get("support_zones") or []
        resistances = support_resistance_zones.get("resistance_zones") or []
        avg_price = _nullable_float(intraday_summary.get("avg_price"))
        latest = _nullable_float(quote.get("latest_price"))
        low = _nullable_float(quote.get("low"))
        high = _nullable_float(quote.get("high"))
        cost = _nullable_float(position_risk_contribution.get("cost"))

        def first_price(items: list[dict[str, Any]]) -> tuple[float | None, str]:
            for item in items:
                price = _nullable_float(item.get("price"))
                if price is not None:
                    return price, str(item.get("source") or item.get("name") or "")
            return None, ""

        fallback_support, fallback_support_source = first_price(supports)
        fallback_resistance, fallback_resistance_source = first_price(resistances)
        intraday_strength = avg_price if avg_price is not None else latest
        turn_strong = high if high is not None else fallback_resistance
        hard_stop = low if low is not None else fallback_support
        return {
            "intraday_strength_line": {
                "price": intraday_strength,
                "source": "intraday_1m.avg_price" if avg_price is not None else "quote.latest_price",
                "usage": "站上/跌破后判断日内强弱，不等同最终买卖点。",
            },
            "cost_repair_line": {
                "price": cost,
                "source": "account.positions.cost",
                "usage": "持仓票是否修复到你的成本线。",
            },
            "hard_stop_line": {
                "price": hard_stop,
                "source": "quote.low" if low is not None else fallback_support_source,
                "usage": "跌破且5-10分钟收不回，说明短线结构失败。",
            },
            "turn_strong_line": {
                "price": turn_strong,
                "source": "quote.high" if high is not None else fallback_resistance_source,
                "usage": "放量站上后，弱修复才可能升级为强修复。",
            },
            "take_profit_reference": {
                "price": fallback_resistance,
                "source": fallback_resistance_source,
                "usage": "接近压力区放量滞涨时考虑兑现或降仓。",
            },
        }

    def _next_session_scenarios(
        self,
        *,
        technical_level_layers: dict[str, Any],
        position_risk_contribution: dict[str, Any],
    ) -> dict[str, Any]:
        available = int(position_risk_contribution.get("available") or 0)
        strength_line = technical_level_layers.get("intraday_strength_line", {}).get("price")
        hard_stop = technical_level_layers.get("hard_stop_line", {}).get("price")
        turn_strong = technical_level_layers.get("turn_strong_line", {}).get("price")
        sell_note = f"可卖{available}股" if available > 0 else "可卖0股，只能制定明日/后续预案"
        return {
            "low_open_weak_rebound": {
                "condition": f"低开后反抽不过强弱线{strength_line}",
                "action": f"{sell_note}；若跌破硬处理线{hard_stop}且5-10分钟收不回，优先降风险。",
            },
            "flat_open_chop": {
                "condition": f"平开后围绕强弱线{strength_line}震荡",
                "action": "先观察量能和板块强弱，不在均价下方追买；若板块同步走弱，降低持有评级。",
            },
            "high_open_repair": {
                "condition": f"高开并放量接近或突破转强线{turn_strong}",
                "action": "若板块同步强且盘口卖压下降，可继续持有；若冲高回落，则按压力区兑现/减仓。",
            },
            "usage_note": "回复里必须区分低开、平开、高开三种明日路径，避免只给单一结论。",
        }

    def _review_log_receipt(self) -> dict[str, Any]:
        return {
            "status": "not_logged_by_analysis_endpoint",
            "review_id": None,
            "require_review_id_when_claiming_logged": True,
            "instruction": "stock_intraday_analysis只生成可保存的review_record；若GPT声称已保存，必须额外调用logReview并展示review_id。",
        }

    def _review_record(
        self,
        *,
        code: str,
        quote: dict[str, Any],
        data_quality: dict[str, Any],
        decision_score: dict[str, Any],
        trading_plan: dict[str, Any],
        freshness: str,
        fetched_at_value: str,
    ) -> dict[str, Any]:
        return {
            "code": normalize_code(code),
            "name": quote.get("name"),
            "fetched_at": fetched_at_value,
            "freshness": freshness,
            "latest_price": quote.get("latest_price"),
            "decision_score": decision_score.get("total_score"),
            "probability_band": decision_score.get("probability_band"),
            "suggested_action": decision_score.get("suggested_action"),
            "buy_condition": trading_plan.get("buy_condition"),
            "failure_line": trading_plan.get("failure_line"),
            "take_profit_line": trading_plan.get("take_profit_line"),
            "data_quality_summary": {
                "quote_status": data_quality.get("quote_status"),
                "intraday_status": data_quality.get("intraday_status"),
                "order_book_status": data_quality.get("order_book_status"),
                "recent_trades_status": data_quality.get("recent_trades_status"),
                "technical_status": data_quality.get("technical_status"),
                "board_status": data_quality.get("board_status"),
                "market_status": data_quality.get("market_status"),
            },
            "next_review_fields": [
                "next_trade_date_open",
                "next_trade_date_high",
                "next_trade_date_low",
                "triggered_buy_condition",
                "triggered_failure_line",
                "actual_action",
                "outcome_note",
            ],
        }

    def stock_intraday_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        code = normalize_code(payload.get("code", ""))
        limit = int(payload.get("intraday_limit", 0) or 0) or None
        cash = None
        if isinstance(payload.get("account"), dict):
            cash = _nullable_float(payload["account"].get("cash"))
        phase = trading_phase()
        top_fetched_at = fetched_at()
        missing_fields: list[str] = []

        try:
            quote = self._quote_detail(code)
        except Exception as exc:  # noqa: BLE001
            quote = self._module_failed(exc)
        try:
            intraday = self._intraday_rows(code, limit=limit)
        except Exception as exc:  # noqa: BLE001
            intraday = {"status": "failed", "fetched_at": fetched_at(), "rows": [], "error": f"{type(exc).__name__}: {exc}"}
        try:
            order_book = self._order_book_5(code, cash=cash)
        except Exception as exc:  # noqa: BLE001
            order_book = {"status": "failed", "fetched_at": fetched_at(), "bid": [], "ask": [], "error": f"{type(exc).__name__}: {exc}"}
        try:
            recent_trades = self._recent_trade_rows(code, limit=int(payload.get("trade_limit", 100) or 100))
        except Exception as exc:  # noqa: BLE001
            recent_trades = {"status": "failed", "fetched_at": fetched_at(), "rows": [], "error": f"{type(exc).__name__}: {exc}"}
        try:
            technical = self._technical_indicators(code)
        except Exception as exc:  # noqa: BLE001
            technical = self._module_failed(exc)
        try:
            history = self._history_rows(code)
        except Exception as exc:  # noqa: BLE001
            history = {"status": "failed", "fetched_at": fetched_at(), "rows": [], "error": f"{type(exc).__name__}: {exc}"}
        try:
            board = self._board_context(code)
        except Exception as exc:  # noqa: BLE001
            board = {"status": "failed", "fetched_at": fetched_at(), "industry": None, "concepts": [], "core_stocks": [], "error": f"{type(exc).__name__}: {exc}"}
        market = self._market_context(include_breadth=bool(payload.get("include_market_breadth", False)))
        zt_related = self._zt_pool_related(code)
        account = self._account_context(payload.get("account", {}) or {}, quote, board)

        required_quote_fields = [
            "latest_price",
            "change",
            "change_pct",
            "pre_close",
            "open",
            "high",
            "low",
            "turnover_rate",
            "volume_ratio",
            "volume",
            "amount",
            "total_market_cap",
            "circulating_market_cap",
            "limit_up_price",
            "limit_down_price",
            "amplitude",
        ]
        for field in required_quote_fields:
            if quote.get(field) is None:
                missing_fields.append(f"quote.{field}")
        data_quality = {
            "quote_status": quote.get("status", "failed"),
            "intraday_status": intraday.get("status", "failed"),
            "order_book_status": order_book.get("status", "failed"),
            "recent_trades_status": recent_trades.get("status", "failed"),
            "board_status": board.get("status", "failed"),
            "technical_status": technical.get("status", "failed"),
            "history_status": history.get("status", "failed"),
            "market_status": market.get("status", "failed"),
            "zt_pool_status": zt_related.get("status", "failed"),
            "missing_fields": missing_fields,
            "estimated_delay_seconds": 3 if phase == "continuous_auction" else None,
        }
        decision_score = self._decision_score(
            quote=quote,
            intraday=intraday,
            order_book=order_book,
            recent_trades=recent_trades,
            technical=technical,
            board=board,
            market=market,
            account=account,
            data_quality=data_quality,
        )
        trading_plan = self._trading_plan(
            quote=quote,
            intraday=intraday,
            order_book=order_book,
            technical=technical,
            account=account,
            decision_score=decision_score,
        )
        freshness = "failed" if quote.get("status") == "failed" else freshness_for_phase(phase)
        technical_interpretation = self._technical_interpretation(
            quote=quote,
            intraday=intraday,
            order_book=order_book,
            technical=technical,
            trading_plan=trading_plan,
        )
        response_completeness_check = self._response_completeness_check(
            data_quality=data_quality,
            technical_interpretation=technical_interpretation,
            board=board,
            market=market,
            account=account,
        )
        execution_checklist = self._execution_checklist(
            quote=quote,
            intraday=intraday,
            order_book=order_book,
            recent_trades=recent_trades,
            technical=technical,
            board=board,
            market=market,
            account=account,
            data_quality=data_quality,
            trading_plan=trading_plan,
        )
        recent_3d_context = self._recent_3d_context(history)
        today_intraday_summary = self._today_intraday_summary(quote, intraday)
        candlestick_structure = self._candlestick_structure(quote)
        moving_average_structure = self._moving_average_structure(quote, technical)
        volume_price_relation = self._volume_price_relation(quote, recent_3d_context)
        relative_strength = self._relative_strength(quote, board, market)
        support_resistance_zones = self._support_resistance_zones(
            quote=quote,
            intraday_summary=today_intraday_summary,
            technical=technical,
            recent_3d_context=recent_3d_context,
            account=account,
        )
        risk_volatility = self._risk_volatility(
            quote=quote,
            technical=technical,
            recent_3d_context=recent_3d_context,
            intraday_summary=today_intraday_summary,
        )
        order_book_interpretation = self._order_book_interpretation(order_book)
        board_stock_alignment = self._board_stock_alignment(quote, board)
        position_risk_contribution = self._position_risk_contribution(quote, account)
        technical_level_layers = self._technical_level_layers(
            quote=quote,
            intraday_summary=today_intraday_summary,
            support_resistance_zones=support_resistance_zones,
            position_risk_contribution=position_risk_contribution,
        )
        next_session_scenarios = self._next_session_scenarios(
            technical_level_layers=technical_level_layers,
            position_risk_contribution=position_risk_contribution,
        )
        review_log_receipt = self._review_log_receipt()
        review_record = self._review_record(
            code=code,
            quote=quote,
            data_quality=data_quality,
            decision_score=decision_score,
            trading_plan=trading_plan,
            freshness=freshness,
            fetched_at_value=top_fetched_at,
        )
        return json_safe(
            {
                "code": code,
                "name": quote.get("name"),
                "freshness": freshness,
                "fetched_at": top_fetched_at,
                "data_quality": data_quality,
                "quote": quote,
                "intraday_1m": intraday,
                "order_book_5": order_book,
                "recent_trades": recent_trades,
                "technical": technical,
                "daily_history": history,
                "board": board,
                "market": market,
                "zt_pool_related": zt_related,
                "account": account,
                "decision_score": decision_score,
                "trading_plan": trading_plan,
                "technical_interpretation": technical_interpretation,
                "recent_3d_context": recent_3d_context,
                "today_intraday_summary": today_intraday_summary,
                "candlestick_structure": candlestick_structure,
                "moving_average_structure": moving_average_structure,
                "volume_price_relation": volume_price_relation,
                "relative_strength": relative_strength,
                "support_resistance_zones": support_resistance_zones,
                "risk_volatility": risk_volatility,
                "order_book_interpretation": order_book_interpretation,
                "board_stock_alignment": board_stock_alignment,
                "position_risk_contribution": position_risk_contribution,
                "technical_level_layers": technical_level_layers,
                "next_session_scenarios": next_session_scenarios,
                "review_log_receipt": review_log_receipt,
                "response_completeness_check": response_completeness_check,
                "execution_checklist": execution_checklist,
                "review_record": review_record,
            }
        )

    def market_snapshot(self) -> dict[str, Any]:
        quote_error = None
        index_error = None
        try:
            quotes = normalize_quotes_df(self.provider.quotes())
        except Exception as exc:  # noqa: BLE001
            quotes = pd.DataFrame()
            quote_error = f"{type(exc).__name__}: {exc}"
        try:
            indices = normalize_quotes_df(self.provider.indices())
        except Exception as exc:  # noqa: BLE001
            indices = pd.DataFrame()
            index_error = f"{type(exc).__name__}: {exc}"
        if quotes.empty and indices.empty:
            return response_envelope(
                {"error": quote_error or index_error or "No market data returned"},
                source=self.source,
                freshness="unavailable",
            )
        up_count = int((quotes["day_change_pct"] > 0).sum()) if not quotes.empty else 0
        down_count = int((quotes["day_change_pct"] < 0).sum()) if not quotes.empty else 0
        flat_count = int((quotes["day_change_pct"] == 0).sum()) if not quotes.empty else 0
        limit_up_count = int((quotes["day_change_pct"] >= 9.7).sum()) if not quotes.empty else 0
        limit_down_count = int((quotes["day_change_pct"] <= -9.7).sum()) if not quotes.empty else 0
        total_market_amount = float(quotes["amount"].sum()) if not quotes.empty and "amount" in quotes.columns else None
        shanghai_amount = (
            float(quotes[quotes["code"].str.startswith("6")]["amount"].sum())
            if not quotes.empty and "amount" in quotes.columns
            else None
        )
        shenzhen_amount = (
            float(quotes[quotes["code"].str.startswith(("0", "2", "3"))]["amount"].sum())
            if not quotes.empty and "amount" in quotes.columns
            else None
        )
        up_ratio = up_count / max(up_count + down_count, 1)
        market_temperature = "强势" if up_ratio >= 0.62 else "震荡" if up_ratio >= 0.45 else "弱势"
        data = {
            "indices": indices.head(20).to_dict(orient="records"),
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "real_limit_up_count": limit_up_count,
            "open_board_count": None,
            "total_market_amount": total_market_amount,
            "shanghai_amount": shanghai_amount,
            "shenzhen_amount": shenzhen_amount,
            "market_temperature": market_temperature,
            "breadth_available": not quotes.empty,
            "breadth_error": quote_error,
            "index_error": index_error,
        }
        freshness = "live" if quote_error is None and index_error is None else "partial_live"
        return response_envelope(data, source=self.source, freshness=freshness)

    def stock_quotes(self, codes: list[str]) -> dict[str, Any]:
        try:
            if hasattr(self.provider, "quotes_for"):
                quotes = normalize_quotes_df(self.provider.quotes_for(codes))
            else:
                quotes = normalize_quotes_df(self.provider.quotes())
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)
        code_set = {normalize_code(code) for code in codes}
        data = quotes[quotes["code"].isin(code_set)].to_dict(orient="records")
        return response_envelope({"quotes": data}, source=self.source)

    def stock_bidask(self, code: str, cash: float | None = None) -> dict[str, Any]:
        try:
            raw = _bid_ask_to_dict(self.provider.bid_ask(code))
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)
        latest_price = _safe_float(raw.get("最新"))
        day_change_pct = _safe_float(raw.get("涨幅"), 0.0)
        buy_1 = _safe_float(raw.get("buy_1"))
        sell_1 = _safe_float(raw.get("sell_1"))
        action = classify_bid_ask_actionability(
            latest_price=latest_price,
            day_change_pct=day_change_pct,
            sell_1=sell_1,
            buy_1=buy_1,
            cash=cash,
        )
        data = {
            "code": normalize_code(code),
            "latest_price": latest_price,
            "day_change_pct": day_change_pct,
            "buy_1": buy_1,
            "sell_1": sell_1,
            "raw": raw,
            **action,
        }
        return response_envelope(data, source=self.source)

    def hot_boards(self) -> dict[str, Any]:
        try:
            board_df = self.provider.boards()
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)
        if board_df is None or board_df.empty:
            return response_envelope({"boards": []}, source=self.source, freshness="unavailable")
        if "board_name" not in board_df.columns:
            board_df = normalize_board_snapshot(board_df, "board", "provider")
        boards = summarize_board_strength(board_df, top_n=30)
        return response_envelope({"boards": boards}, source=self.source)

    def technical(self, code: str, report_date: date | None = None) -> dict[str, Any]:
        try:
            hist_df = self.provider.hist(code, report_date=report_date)
            profile = summarize_technical_profile(hist_df)
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)
        return response_envelope({"code": normalize_code(code), **profile}, source=self.source)

    def _previous_recommendation_codes(self, payload: dict[str, Any]) -> set[str]:
        previous = payload.get("previous_recommendations", []) or []
        codes: set[str] = set()
        for item in previous:
            if isinstance(item, dict):
                code = normalize_code(item.get("code", ""))
            else:
                code = normalize_code(str(item))
            if code:
                codes.add(code)
        return codes

    def _candidate_lifecycle(
        self,
        *,
        code: str,
        verdict: str,
        source_reason: Any,
        previous_codes: set[str],
    ) -> dict[str, Any]:
        reason = str(source_reason or "")
        new_evidence_keywords = [
            "突破",
            "站回",
            "放量",
            "板块转强",
            "资金回流",
            "盘口改善",
            "公告",
            "新高",
            "修复",
            "涨停",
            "异动",
            "回踩不破",
        ]
        has_new_evidence = any(keyword in reason for keyword in new_evidence_keywords)
        repeated = code in previous_codes

        if not repeated:
            slot = "买入候选" if verdict == "可重点观察" else "观察池"
            return {
                "status": "新候选",
                "recommendation_slot": slot,
                "duplicate_note": "首次进入本轮候选；仍需通过实时行情、板块和技术触发确认",
                "has_new_realtime_evidence": has_new_evidence,
            }

        if verdict == "不建议买入":
            return {
                "status": "淘汰候选",
                "recommendation_slot": "淘汰池",
                "duplicate_note": "重复候选且本次实时核验触发硬否决，不应继续作为买入推荐",
                "has_new_realtime_evidence": has_new_evidence,
            }

        if has_new_evidence and verdict == "可重点观察":
            return {
                "status": "升级候选",
                "recommendation_slot": "重点观察",
                "duplicate_note": "虽然此前推荐过，但本次给出了新的实时证据，可重新进入重点观察",
                "has_new_realtime_evidence": True,
            }

        return {
            "status": "继续跟踪",
            "recommendation_slot": "观察池",
            "duplicate_note": "此前已经推荐过，且没有新的实时证据，不应作为新的买入推荐重复输出",
            "has_new_realtime_evidence": has_new_evidence,
        }

    def verify_candidates(self, payload: dict[str, Any]) -> dict[str, Any]:
        cash = float(payload.get("cash", 0.0) or 0.0)
        raw_candidates = payload.get("candidates", []) or []
        previous_codes = self._previous_recommendation_codes(payload)
        codes = [normalize_code(item.get("code", "")) for item in raw_candidates]
        quotes_out = self.stock_quotes(codes)
        quotes = {}
        if quotes_out["freshness"] != "unavailable":
            quotes = {item["code"]: item for item in quotes_out.get("data", {}).get("quotes", [])}
        results: list[dict[str, Any]] = []
        for item in raw_candidates:
            code = normalize_code(item.get("code", ""))
            name = str(item.get("name", "") or quotes.get(code, {}).get("name", ""))
            reasons: list[str] = []
            if not is_mainboard_code(code):
                reasons.append("非主板或代码无效")
            if is_st_name(name) or "退" in name:
                reasons.append("ST/退市风险标的")
            if name.strip().upper().startswith(("C", "N")):
                reasons.append("新股/次新波动过大")

            quote = quotes.get(code, {})
            latest_price = quote.get("latest_price")
            day_change_pct = quote.get("day_change_pct")
            bidask_out = self.stock_bidask(code, cash=cash) if code else self._unavailable(ValueError("missing code"))
            bidask = bidask_out["data"] if bidask_out["freshness"] != "unavailable" else {}
            if bidask_out["freshness"] == "unavailable":
                reasons.append("盘口不可确认")
            elif bidask.get("actionability") != "可买":
                reasons.append(str(bidask.get("actionability")))
            latest = bidask.get("latest_price")
            change_pct = bidask.get("day_change_pct")
            if latest is not None and not pd.isna(latest):
                latest_price = latest
            if change_pct is not None and not pd.isna(change_pct):
                day_change_pct = change_pct
            if latest_price is not None and not pd.isna(latest_price) and float(latest_price) * 100 > cash:
                reasons.append("现金不足买一手")
            if day_change_pct is not None and not pd.isna(day_change_pct) and float(day_change_pct) >= 9.5:
                reasons.append("涨幅过高，不适合追高")

            tech_out = self.technical(code) if code else self._unavailable(ValueError("missing code"))
            tech = tech_out["data"] if tech_out["freshness"] != "unavailable" else {}
            if tech_out["freshness"] == "unavailable":
                reasons.append("技术数据不可确认")
            technical_score = tech.get("technical_score")
            if technical_score is not None and technical_score < 45:
                reasons.append("技术结构偏弱")

            hard_reject = any(
                reason in reasons
                for reason in [
                    "非主板或代码无效",
                    "ST/退市风险标的",
                    "新股/次新波动过大",
                    "现金不足买一手",
                    "涨停封板不可追",
                    "跌停风险",
                    "涨幅过高，不适合追高",
                ]
            )
            if hard_reject:
                verdict = "不建议买入"
            elif bidask_out["freshness"] == "unavailable" or tech_out["freshness"] == "unavailable":
                verdict = "只观察"
            elif technical_score is not None and technical_score >= 45:
                verdict = "可重点观察"
            else:
                verdict = "只观察"
            lifecycle = self._candidate_lifecycle(
                code=code,
                verdict=verdict,
                source_reason=item.get("source_reason"),
                previous_codes=previous_codes,
            )
            results.append(
                {
                    "code": code,
                    "name": name,
                    "source_reason": item.get("source_reason"),
                    "latest_price": latest_price,
                    "day_change_pct": day_change_pct,
                    "verdict": verdict,
                    "decision_reasons": reasons or ["实时约束和技术结构未触发硬性否决"],
                    "actionability": bidask.get("actionability"),
                    "min_lot_cost": bidask.get("min_lot_cost"),
                    "technical_score": technical_score,
                    "buy_point": tech.get("buy_point"),
                    "sell_point": tech.get("sell_point"),
                    "stop_loss_point": tech.get("stop_loss_point"),
                    "technical_point_sources": tech.get("technical_point_sources"),
                    "candidate_lifecycle": lifecycle,
                }
            )
        return response_envelope({"results": results}, source=self.source)

    def actionable_candidates(self, cash: float, price_limit: float = 20.0, limit: int = 20) -> dict[str, Any]:
        try:
            quotes = normalize_quotes_df(self.provider.quotes())
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        work = quotes[(quotes["latest_price"] > 0) & (quotes["latest_price"] <= price_limit)].copy()
        work = work.sort_values(by=["day_change_pct", "turnover_rate"], ascending=False).head(limit * 3)
        for _, row in work.iterrows():
            code = str(row["code"])
            name = str(row["name"])
            if not is_mainboard_code(code) or is_st_name(name) or "退" in name:
                continue
            normalized_name = name.strip().upper()
            day_change_pct = float(row["day_change_pct"])
            if normalized_name.startswith(("C", "N")):
                rejected.append({"code": code, "name": name, "reason": "新股/次新波动过大"})
                continue
            bidask = self.stock_bidask(code, cash=cash)
            if bidask["freshness"] == "unavailable":
                rejected.append({"code": code, "name": name, "reason": "盘口不可用"})
                continue
            action = bidask["data"]
            if action["actionability"] != "可买":
                rejected.append({"code": code, "name": name, "reason": action["actionability"]})
                continue
            if day_change_pct >= 9.5:
                rejected.append({"code": code, "name": name, "reason": "涨幅过高不追"})
                continue
            tech = self.technical(code)
            tech_data = tech["data"] if tech["freshness"] != "unavailable" else {}
            candidates.append(
                {
                    "code": code,
                    "name": name,
                    "latest_price": float(row["latest_price"]),
                    "day_change_pct": day_change_pct,
                    "min_lot_cost": action["min_lot_cost"],
                    "actionability": action["actionability"],
                    "technical_score": tech_data.get("technical_score"),
                    "buy_point": tech_data.get("buy_point"),
                    "sell_point": tech_data.get("sell_point"),
                    "stop_loss_point": tech_data.get("stop_loss_point"),
                    "technical_point_sources": tech_data.get("technical_point_sources"),
                }
            )
            if len(candidates) >= limit:
                break
        return response_envelope({"candidates": candidates, "rejected": rejected}, source=self.source)

    def portfolio_analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        cash = float(payload.get("cash", 0.0) or 0.0)
        positions = payload.get("positions", []) or []
        quote_codes = [str(item.get("code", "")) for item in positions]
        quotes_out = self.stock_quotes(quote_codes)
        quotes = {item["code"]: item for item in quotes_out.get("data", {}).get("quotes", [])} if quotes_out["freshness"] != "unavailable" else {}
        holdings: list[dict[str, Any]] = []
        for item in positions:
            code = normalize_code(item.get("code", ""))
            shares = int(float(item.get("shares", 0) or 0))
            available = int(float(item.get("available", 0) or 0))
            cost = float(item.get("cost", 0.0) or 0.0)
            quote = quotes.get(code, {})
            latest = quote.get("latest_price")
            pnl = None if latest is None or pd.isna(latest) else (float(latest) - cost) * shares
            locked = shares > 0 and available <= 0
            action = "今日不可卖出，按锁仓观察；只给明日风险线" if locked else "可按盘面强弱决定是否调整"
            holdings.append(
                {
                    "code": code,
                    "name": item.get("name", ""),
                    "shares": shares,
                    "available": available,
                    "cost": cost,
                    "latest_price": None if latest is None or pd.isna(latest) else float(latest),
                    "unrealized_pnl_estimate": None if pnl is None else float(pnl),
                    "action": action,
                    "t_plus_1_locked": bool(locked),
                }
            )
        data = {
            "cash": cash,
            "market_snapshot": self.market_snapshot()["data"],
            "positions": holdings,
            "operation_suggestions": [
                "先处理可卖弱仓，再考虑新开仓。",
                "若market_snapshot或quotes freshness不是live/cache，应等待可靠实时数据，不输出强买入结论。",
                "available=0仓位禁止给出今天卖出建议。",
            ],
        }
        freshness = "live" if quotes_out["freshness"] != "unavailable" else "unavailable"
        return response_envelope(data, source=self.source, freshness=freshness)


def create_app(service: MarketDataService | None = None):
    try:
        from fastapi import FastAPI, Query  # noqa: PLC0415
        from fastapi.responses import HTMLResponse  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("FastAPI is required to run the HTTP server. Install requirements.txt first.") from exc

    service = service or MarketDataService()
    app = FastAPI(title="A Share Market Data Service", version="0.1.0")

    @app.get("/health")
    def health():
        return service.health()

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return """
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8">
          <title>A股实时持仓分析助手隐私政策</title>
        </head>
        <body>
          <h1>A股实时持仓分析助手隐私政策</h1>
          <p>本服务用于为自定义 GPT 提供 A 股行情、盘口、技术分析和持仓辅助分析接口。</p>
          <p>本服务不要求用户提供 API key、券商账户、交易密码或其他敏感身份凭据。</p>
          <p>请求中可能包含用户主动输入的股票代码、持仓股数、可卖股数、成本价和现金余额，用于生成当次分析结果。</p>
          <p>本服务不会代替用户下单，不会连接券商账户，也不会执行真实交易。</p>
          <p>行情数据来自公开网络数据源，分析结果仅供研究参考，不构成投资建议或收益承诺。</p>
          <p>如需删除或停止使用，请在 ChatGPT 中移除该自定义 GPT 或删除其 Actions 配置。</p>
        </body>
        </html>
        """

    @app.get("/market/snapshot")
    def market_snapshot():
        return service.market_snapshot()

    @app.get("/stock/quotes")
    def stock_quotes(codes: str = Query(..., description="Comma separated stock codes")):
        return service.stock_quotes([code.strip() for code in codes.split(",") if code.strip()])

    @app.get("/stock/bidask")
    def stock_bidask(code: str, cash: float | None = None):
        return service.stock_bidask(code, cash=cash)

    @app.get("/boards/hot")
    def boards_hot():
        return service.hot_boards()

    @app.get("/stock/technical")
    def stock_technical(code: str):
        return service.technical(code)

    @app.post("/stock/intraday-analysis")
    def stock_intraday_analysis(payload: dict[str, Any]):
        return service.stock_intraday_analysis(payload)

    @app.get("/candidates/actionable")
    def candidates_actionable(cash: float, price_limit: float = 20.0, limit: int = 20):
        return service.actionable_candidates(cash=cash, price_limit=price_limit, limit=limit)

    @app.post("/candidates/verify")
    def candidates_verify(payload: dict[str, Any]):
        return service.verify_candidates(payload)

    @app.post("/portfolio/analyze")
    def portfolio_analyze(payload: dict[str, Any]):
        return service.portfolio_analyze(payload)

    @app.post("/reviews/log")
    def reviews_log(payload: dict[str, Any]):
        return service.log_review(payload)

    @app.get("/reviews/recent")
    def reviews_recent(code: str | None = None, limit: int = 10):
        return service.recent_reviews(code=code, limit=limit)

    @app.post("/reviews/evaluate")
    def reviews_evaluate(payload: dict[str, Any]):
        return service.evaluate_review(payload)

    @app.get("/reviews/lessons")
    def reviews_lessons(limit: int = 20):
        return service.review_lessons(limit=limit)

    return app
