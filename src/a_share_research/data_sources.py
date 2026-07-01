from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
import signal
import time
from typing import Iterable, Sequence

import akshare as ak
import pandas as pd

from .utils import normalize_code, parse_cn_date, parse_numeric, rating_to_score


@dataclass
class SourceBundle:
    raw_records: pd.DataFrame
    forecast_metrics: pd.DataFrame
    sina_target_metrics: pd.DataFrame
    sina_composite_metrics: pd.DataFrame
    price_snapshot: pd.DataFrame


STANDARD_COLUMNS = [
    "code",
    "name",
    "industry",
    "pub_date",
    "institution",
    "rating",
    "target_price",
    "source",
    "report_url",
]


def _empty_standard_df() -> pd.DataFrame:
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def _safe_to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _to_numeric_col(df: pd.DataFrame, col_name: str, default: float = float("nan")) -> pd.Series:
    if col_name in df.columns:
        return pd.to_numeric(df[col_name], errors="coerce")
    return pd.Series(default, index=df.index, dtype="float64")


def _pick_column(df: pd.DataFrame, keyword: str) -> str | None:
    for col in df.columns:
        if keyword in str(col):
            return col
    return None


def _call_with_retries(func, *args, retries: int = 3, delay_seconds: float = 1.5, **kwargs):
    last_error: Exception | None = None
    timeout_seconds = float(kwargs.pop("timeout_seconds", 20.0))

    class _AlarmTimeoutError(TimeoutError):
        pass

    def _timeout_handler(signum, frame):  # noqa: ANN001, ARG001
        raise _AlarmTimeoutError(f"call timed out after {timeout_seconds:.1f}s")

    for attempt in range(retries):
        try:
            previous = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
            try:
                return func(*args, **kwargs)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries - 1:
                time.sleep(delay_seconds * (2**attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unexpected retry state")


def _rating_label_from_counts(row: pd.Series, col_map: dict[str, str]) -> str:
    score_map = {
        "买入": row.get(col_map.get("买入", ""), 0),
        "增持": row.get(col_map.get("增持", ""), 0),
        "中性": row.get(col_map.get("中性", ""), 0),
        "减持": row.get(col_map.get("减持", ""), 0),
        "卖出": row.get(col_map.get("卖出", ""), 0),
    }
    clean = {k: float(v) if pd.notna(v) else 0.0 for k, v in score_map.items()}
    return max(clean, key=clean.get)


def fetch_profit_forecast_source(report_date: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        df = _call_with_retries(ak.stock_profit_forecast_em, retries=3)
    except Exception:
        return _empty_standard_df(), pd.DataFrame(columns=["code", "name"])
    if df.empty:
        return _empty_standard_df(), pd.DataFrame(columns=["code", "name"])

    df = df.copy()
    df["code"] = df["代码"].map(normalize_code)
    df["name"] = df["名称"].astype(str)

    rating_cols = {
        "买入": _pick_column(df, "买入"),
        "增持": _pick_column(df, "增持"),
        "中性": _pick_column(df, "中性"),
        "减持": _pick_column(df, "减持"),
        "卖出": _pick_column(df, "卖出"),
    }

    for label, col in rating_cols.items():
        if col is None:
            df[f"cnt_{label}"] = 0.0
        else:
            df[f"cnt_{label}"] = _safe_to_numeric(df[col]).fillna(0.0)

    df["forecast_total_count"] = (
        df["cnt_买入"]
        + df["cnt_增持"]
        + df["cnt_中性"]
        + df["cnt_减持"]
        + df["cnt_卖出"]
    )

    weighted = (
        df["cnt_买入"] * 95.0
        + df["cnt_增持"] * 80.0
        + df["cnt_中性"] * 55.0
        + df["cnt_减持"] * 25.0
        + df["cnt_卖出"] * 10.0
    )
    df["forecast_rating_score"] = weighted.div(df["forecast_total_count"]).fillna(55.0)

    report_count_col = _pick_column(df, "研报数")
    if report_count_col is not None:
        df["forecast_report_count"] = _safe_to_numeric(df[report_count_col]).fillna(0.0)
    else:
        df["forecast_report_count"] = df["forecast_total_count"]

    df["rating"] = df.apply(lambda row: _rating_label_from_counts(row, rating_cols), axis=1)

    standard_df = pd.DataFrame(
        {
            "code": df["code"],
            "name": df["name"],
            "industry": df.get("行业", pd.Series("", index=df.index)).astype(str),
            "pub_date": pd.Timestamp(report_date),
            "institution": "EASTMONEY_6M_AGGREGATE",
            "rating": df["rating"],
            "target_price": float("nan"),
            "source": "eastmoney_profit_forecast",
            "report_url": "",
        }
    )

    metrics = df[
        [
            "code",
            "name",
            "forecast_rating_score",
            "forecast_report_count",
            "forecast_total_count",
            "cnt_买入",
            "cnt_增持",
            "cnt_中性",
            "cnt_减持",
            "cnt_卖出",
        ]
    ].drop_duplicates(subset=["code"])

    return standard_df, metrics


def _fetch_sina_category(symbol: str) -> pd.DataFrame:
    try:
        df = _call_with_retries(ak.stock_institute_recommend, symbol=symbol, retries=3)
    except Exception:
        return pd.DataFrame()
    if df is None:
        return pd.DataFrame()
    return df.copy()


def fetch_sina_sources(report_date: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    detailed_symbols = ["最新投资评级", "上调评级股票", "下调评级股票", "首次评级股票"]
    detailed_frames: list[pd.DataFrame] = []

    for symbol in detailed_symbols:
        temp = _fetch_sina_category(symbol)
        if temp.empty:
            continue
        required_cols = {"股票代码", "股票名称", "评级机构", "最新评级", "评级日期"}
        if not required_cols.issubset(set(temp.columns)):
            continue
        rec = pd.DataFrame(
            {
                "code": temp["股票代码"].map(normalize_code),
                "name": temp["股票名称"].astype(str),
                "industry": temp.get("行业", pd.Series("", index=temp.index)).astype(str),
                "pub_date": temp["评级日期"].map(parse_cn_date),
                "institution": temp["评级机构"].astype(str),
                "rating": temp["最新评级"].astype(str),
                "target_price": temp.get("目标价", pd.Series(index=temp.index, dtype=object)).map(parse_numeric),
                "source": f"sina_{symbol}",
                "report_url": "",
            }
        )
        detailed_frames.append(rec)

    detailed_df = pd.concat(detailed_frames, ignore_index=True) if detailed_frames else _empty_standard_df()

    # 股票综合评级: aggregated rating counts by stock
    composite_raw = _fetch_sina_category("股票综合评级")
    if composite_raw.empty:
        composite_df = pd.DataFrame(
            columns=[
                "code",
                "sina_comp_rating_score",
                "sina_comp_total_count",
                "sina_comp_buy",
                "sina_comp_add",
                "sina_comp_neutral",
                "sina_comp_reduce",
                "sina_comp_sell",
            ]
        )
    else:
        composite_raw["code"] = composite_raw["股票代码"].map(normalize_code)
        composite_raw["sina_comp_buy"] = _to_numeric_col(composite_raw, "买入家数", 0).fillna(0.0)
        composite_raw["sina_comp_add"] = _to_numeric_col(composite_raw, "增持家数", 0).fillna(0.0)
        composite_raw["sina_comp_neutral"] = _to_numeric_col(composite_raw, "中性家数", 0).fillna(0.0)
        composite_raw["sina_comp_reduce"] = _to_numeric_col(composite_raw, "减持家数", 0).fillna(0.0)
        composite_raw["sina_comp_sell"] = _to_numeric_col(composite_raw, "卖出家数", 0).fillna(0.0)
        composite_raw["sina_comp_total_count"] = (
            composite_raw["sina_comp_buy"]
            + composite_raw["sina_comp_add"]
            + composite_raw["sina_comp_neutral"]
            + composite_raw["sina_comp_reduce"]
            + composite_raw["sina_comp_sell"]
        )
        weighted = (
            composite_raw["sina_comp_buy"] * 95.0
            + composite_raw["sina_comp_add"] * 80.0
            + composite_raw["sina_comp_neutral"] * 55.0
            + composite_raw["sina_comp_reduce"] * 25.0
            + composite_raw["sina_comp_sell"] * 10.0
        )
        composite_raw["sina_comp_rating_score"] = weighted.div(composite_raw["sina_comp_total_count"]).fillna(55.0)
        composite_df = composite_raw[
            [
                "code",
                "sina_comp_rating_score",
                "sina_comp_total_count",
                "sina_comp_buy",
                "sina_comp_add",
                "sina_comp_neutral",
                "sina_comp_reduce",
                "sina_comp_sell",
            ]
        ].drop_duplicates(subset=["code"])

        # Add one synthetic standardized row per stock to keep source traceability
        composite_standard = pd.DataFrame(
            {
                "code": composite_raw["code"],
                "name": composite_raw.get("股票名称", "").astype(str),
                "industry": composite_raw.get("行业", pd.Series("", index=composite_raw.index)).astype(str),
                "pub_date": pd.Timestamp(report_date),
                "institution": "SINA_COMPOSITE_AGGREGATE",
                "rating": composite_raw.get("综合评级↑", "").astype(str),
                "target_price": float("nan"),
                "source": "sina_股票综合评级",
                "report_url": "",
            }
        )
        detailed_df = pd.concat([detailed_df, composite_standard], ignore_index=True)

    # 目标涨幅排名: target-based aggregated metrics
    target_raw = _fetch_sina_category("目标涨幅排名")
    if target_raw.empty:
        target_df = pd.DataFrame(columns=["code", "avg_target_price", "avg_target_upside", "target_org_count"])
    else:
        target_raw["code"] = target_raw["股票代码"].map(normalize_code)
        target_raw["avg_target_price"] = target_raw["平均目标价"].map(parse_numeric)
        target_raw["avg_target_upside"] = target_raw["平均目标涨幅"].map(parse_numeric)
        target_raw["target_org_count"] = _to_numeric_col(target_raw, "评级机构数", 0).fillna(0.0)
        target_df = target_raw[["code", "avg_target_price", "avg_target_upside", "target_org_count"]].drop_duplicates(
            subset=["code"]
        )

        target_standard = pd.DataFrame(
            {
                "code": target_raw["code"],
                "name": target_raw.get("股票名称", "").astype(str),
                "industry": target_raw.get("行业", pd.Series("", index=target_raw.index)).astype(str),
                "pub_date": pd.Timestamp(report_date),
                "institution": "SINA_TARGET_AGGREGATE",
                "rating": "",
                "target_price": target_raw["avg_target_price"],
                "source": "sina_目标涨幅排名",
                "report_url": "",
            }
        )
        detailed_df = pd.concat([detailed_df, target_standard], ignore_index=True)

    return detailed_df, target_df, composite_df


def _fetch_one_eastmoney_report(code: str) -> pd.DataFrame:
    try:
        df = _call_with_retries(ak.stock_research_report_em, symbol=code, retries=2)
    except Exception:
        return _empty_standard_df()

    if df is None or df.empty:
        return _empty_standard_df()

    report_df = pd.DataFrame(
        {
            "code": df["股票代码"].map(normalize_code),
            "name": df["股票简称"].astype(str),
            "industry": df.get("行业", pd.Series("", index=df.index)).astype(str),
            "pub_date": df["日期"].map(parse_cn_date),
            "institution": df["机构"].astype(str),
            "rating": df["东财评级"].astype(str),
            "target_price": float("nan"),
            "source": "eastmoney_research_report",
            "report_url": df.get("报告PDF链接", "").fillna("").astype(str),
        }
    )
    return report_df


def fetch_eastmoney_research_for_codes(codes: Sequence[str], max_workers: int = 6) -> pd.DataFrame:
    if not codes:
        return _empty_standard_df()

    chunks: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_fetch_one_eastmoney_report, code): code for code in codes}
        for future in as_completed(future_map):
            temp = future.result()
            if temp.empty:
                continue
            chunks.append(temp)

    if not chunks:
        return _empty_standard_df()

    return pd.concat(chunks, ignore_index=True)


def fetch_price_snapshot(codes: Iterable[str] | None = None) -> pd.DataFrame:
    """Fetch market snapshot and derive latest available close price.

    Rule for close price:
    - If snapshot timestamp >= 15:00:00, use 最新价 as day close.
    - Otherwise fallback to 昨收.
    """
    def _empty_result() -> pd.DataFrame:
        return pd.DataFrame(columns=["code", "name", "close_price", "last_price", "prev_close", "trade_time"])

    def _shape_result(raw_df: pd.DataFrame) -> pd.DataFrame:
        if raw_df is None or raw_df.empty:
            return _empty_result()
        spot_df = raw_df.copy()
        if "代码" not in spot_df.columns or "名称" not in spot_df.columns:
            return _empty_result()
        spot_df["code"] = spot_df["代码"].map(normalize_code)
        spot_df["name"] = spot_df["名称"].astype(str)
        spot_df["last_price"] = _to_numeric_col(spot_df, "最新价")
        spot_df["prev_close"] = _to_numeric_col(spot_df, "昨收")
        if "时间戳" in spot_df.columns:
            spot_df["trade_time"] = spot_df["时间戳"].astype(str)
        else:
            spot_df["trade_time"] = pd.Series("", index=spot_df.index, dtype=str)

        trade_dt = pd.to_datetime(spot_df["trade_time"], errors="coerce", format="%H:%M:%S")
        is_after_close = trade_dt.dt.hour.fillna(0) >= 15
        spot_df["close_price"] = spot_df["prev_close"]
        spot_df.loc[is_after_close, "close_price"] = spot_df.loc[is_after_close, "last_price"]
        spot_df["close_price"] = spot_df["close_price"].fillna(spot_df["last_price"])

        result = spot_df[["code", "name", "close_price", "last_price", "prev_close", "trade_time"]]
        result = result[result["code"].str.len() == 6]
        result = result.drop_duplicates(subset=["code"], keep="first")
        if codes is not None:
            code_set = set(codes)
            result = result[result["code"].isin(code_set)]
        return result.reset_index(drop=True)

    candidates: list[pd.DataFrame] = []
    requested_codes = set(codes) if codes is not None else set()
    for fetcher in (ak.stock_zh_a_spot, ak.stock_zh_a_spot_em):
        try:
            raw_df = _call_with_retries(fetcher, retries=2)
        except Exception:
            continue
        result = _shape_result(raw_df)
        if result.empty:
            continue
        candidates.append(result)
        if codes is None and len(result) >= 3000:
            return result
        if codes is not None and len(result) >= len(requested_codes):
            return result

    merged = pd.concat(candidates, ignore_index=True).drop_duplicates(subset=["code"], keep="first") if candidates else _empty_result()

    if codes is not None:
        merged = merged[merged["code"].isin(requested_codes)]
        missing_codes = [code for code in requested_codes if code not in set(merged["code"])]
        if missing_codes:
            fallback_rows: list[dict[str, object]] = []
            for code in missing_codes:
                try:
                    hist = ak.stock_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date="20250101",
                        end_date="20500101",
                        adjust="qfq",
                    )
                except Exception:
                    continue
                if hist is None or hist.empty or "收盘" not in hist.columns:
                    continue
                hist = hist.copy()
                hist["收盘"] = pd.to_numeric(hist["收盘"], errors="coerce")
                hist = hist.dropna(subset=["收盘"])
                if hist.empty:
                    continue
                last = hist.iloc[-1]
                prev_close = float(hist.iloc[-2]["收盘"]) if len(hist) >= 2 else float(last["收盘"])
                fallback_rows.append(
                    {
                        "code": code,
                        "name": str(last.get("股票名称", code)) if "股票名称" in hist.columns else code,
                        "close_price": float(last["收盘"]),
                        "last_price": float(last["收盘"]),
                        "prev_close": prev_close,
                        "trade_time": "hist_fallback",
                    }
                )
            if fallback_rows:
                merged = pd.concat([merged, pd.DataFrame(fallback_rows)], ignore_index=True)
                merged = merged.drop_duplicates(subset=["code"], keep="first")
    return merged.reset_index(drop=True)


def build_raw_bundle(report_date: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    forecast_raw, forecast_metrics = fetch_profit_forecast_source(report_date=report_date)
    sina_raw, target_metrics, composite_metrics = fetch_sina_sources(report_date=report_date)

    merged_raw = pd.concat([forecast_raw, sina_raw], ignore_index=True)
    merged_raw = merged_raw.dropna(subset=["code"]) if not merged_raw.empty else merged_raw
    merged_raw = merged_raw[STANDARD_COLUMNS] if not merged_raw.empty else _empty_standard_df()

    return merged_raw, forecast_metrics, target_metrics, composite_metrics


def dedupe_standard_records(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    work = df.copy()
    work["pub_date"] = pd.to_datetime(work["pub_date"], errors="coerce")
    work["key"] = (
        work["code"].fillna("")
        + "|"
        + work["institution"].fillna("")
        + "|"
        + work["pub_date"].dt.strftime("%Y-%m-%d").fillna("")
        + "|"
        + work["rating"].fillna("")
    )
    work = work.sort_values(by=["pub_date"], ascending=False, na_position="last")
    work = work.drop_duplicates(subset=["key"], keep="first")
    work = work.drop(columns=["key"])
    return work.reset_index(drop=True)


def attach_rating_score(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out["rating_score"] = pd.Series(dtype=float)
        return out
    out = df.copy()
    out["rating_score"] = out["rating"].map(rating_to_score)
    return out
