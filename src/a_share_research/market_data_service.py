from __future__ import annotations

from datetime import date, datetime
from typing import Any
import re

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


def _quote_col(df: pd.DataFrame, *names: str) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


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


class SinaMarketDataProvider:
    source = SINA_SOURCE

    def __init__(self, fetcher: Any | None = None, page_size: int = 100) -> None:
        self.fetcher = fetcher or self._fetch_page
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

    def indices(self) -> pd.DataFrame:
        raise RuntimeError("Sina market-center fallback only provides full-market stock quotes")

    def bid_ask(self, code: str) -> pd.DataFrame:
        raise RuntimeError("Sina market-center fallback does not provide bid/ask depth")

    def boards(self) -> pd.DataFrame:
        raise RuntimeError("Sina market-center fallback does not provide board heat data")

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        raise RuntimeError("Sina market-center fallback does not provide historical bars")


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
                    "涨跌幅": parse_numeric(row[32]) if len(row) > 32 else float("nan"),
                    "成交额": parse_numeric(row[38]) if len(row) > 38 else float("nan"),
                    "换手率": parse_numeric(row[38]) if len(row) > 38 else float("nan"),
                    "量比": parse_numeric(row[49]) if len(row) > 49 else float("nan"),
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
            "sell_1": parse_numeric(row[19]) if len(row) > 19 else float("nan"),
        }
        return pd.DataFrame([{"item": key, "value": value} for key, value in data.items()])

    def boards(self) -> pd.DataFrame:
        raise RuntimeError("Tencent fallback does not provide board heat data")

    def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
        raise RuntimeError("Tencent fallback does not provide historical bars")


class FallbackMarketDataProvider:
    def __init__(self, primary: Any | None = None, fallback: Any | None = None) -> None:
        self.primary = primary or AkshareMarketDataProvider()
        self.fallback = fallback or TencentMarketDataProvider()
        self.source = f"{getattr(self.primary, 'source', DEFAULT_SOURCE)}+{getattr(self.fallback, 'source', TENCENT_SOURCE)}"

    def _with_fallback(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self.primary, method)(*args, **kwargs)
        except Exception:
            return getattr(self.fallback, method)(*args, **kwargs)

    def quotes(self) -> pd.DataFrame:
        try:
            quotes = self.primary.quotes()
            if isinstance(quotes, pd.DataFrame) and 0 < len(quotes) < MIN_FULL_MARKET_ROWS:
                raise RuntimeError(f"Primary full-market quotes incomplete: {len(quotes)} rows")
            return quotes
        except Exception:
            return self.fallback.quotes()

    def quotes_for(self, codes: list[str]) -> pd.DataFrame:
        if hasattr(self.primary, "quotes_for"):
            try:
                return self.primary.quotes_for(codes)
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


def create_default_provider() -> FallbackMarketDataProvider:
    return FallbackMarketDataProvider(
        primary=AkshareMarketDataProvider(),
        fallback=FallbackMarketDataProvider(
            primary=EastmoneyDirectMarketDataProvider(),
            fallback=FallbackMarketDataProvider(
                primary=SinaMarketDataProvider(),
                fallback=TencentMarketDataProvider(),
            ),
        ),
    )


class StaticMarketDataProvider:
    source: str = "static-test"

    def __init__(
        self,
        quotes: pd.DataFrame | None = None,
        indices: pd.DataFrame | None = None,
        bidasks: dict[str, pd.DataFrame] | None = None,
        boards_df: pd.DataFrame | None = None,
        hist: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        self.quotes_df = quotes.copy() if quotes is not None else pd.DataFrame()
        self.indices_df = indices.copy() if indices is not None else pd.DataFrame()
        self.bidasks = bidasks or {}
        self.boards_df = boards_df.copy() if boards_df is not None else pd.DataFrame()
        self.hist_map = hist or {}

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


class MarketDataService:
    def __init__(self, provider: Any | None = None) -> None:
        self.provider = provider or create_default_provider()

    @property
    def source(self) -> str:
        return str(getattr(self.provider, "source", DEFAULT_SOURCE))

    def _unavailable(self, exc: Exception) -> dict[str, Any]:
        return response_envelope({"error": f"{type(exc).__name__}: {exc}"}, source=self.source, freshness="unavailable")

    def health(self) -> dict[str, Any]:
        return response_envelope({"status": "ok", "provider": self.source}, source=self.source)

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
        limit_up_count = int((quotes["day_change_pct"] >= 9.7).sum()) if not quotes.empty else 0
        limit_down_count = int((quotes["day_change_pct"] <= -9.7).sum()) if not quotes.empty else 0
        up_ratio = up_count / max(up_count + down_count, 1)
        market_temperature = "强势" if up_ratio >= 0.62 else "震荡" if up_ratio >= 0.45 else "弱势"
        data = {
            "indices": indices.head(20).to_dict(orient="records"),
            "up_count": up_count,
            "down_count": down_count,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
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
            bidask = self.stock_bidask(code, cash=cash)
            if bidask["freshness"] == "unavailable":
                rejected.append({"code": code, "name": name, "reason": "盘口不可用"})
                continue
            action = bidask["data"]
            if action["actionability"] != "可买":
                rejected.append({"code": code, "name": name, "reason": action["actionability"]})
                continue
            tech = self.technical(code)
            tech_data = tech["data"] if tech["freshness"] != "unavailable" else {}
            candidates.append(
                {
                    "code": code,
                    "name": name,
                    "latest_price": float(row["latest_price"]),
                    "day_change_pct": float(row["day_change_pct"]),
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

    @app.get("/candidates/actionable")
    def candidates_actionable(cash: float, price_limit: float = 20.0, limit: int = 20):
        return service.actionable_candidates(cash=cash, price_limit=price_limit, limit=limit)

    @app.post("/portfolio/analyze")
    def portfolio_analyze(payload: dict[str, Any]):
        return service.portfolio_analyze(payload)

    return app
