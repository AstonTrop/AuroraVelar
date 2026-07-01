from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable

import pandas as pd

RATING_SCORE_MAP = {
    "强烈买入": 100.0,
    "买入": 95.0,
    "增持": 80.0,
    "推荐": 80.0,
    "审慎推荐": 75.0,
    "持有": 60.0,
    "中性": 55.0,
    "观望": 50.0,
    "减持": 25.0,
    "卖出": 10.0,
    "回避": 5.0,
}


def normalize_code(raw: object) -> str:
    text = str(raw).strip()
    match = re.search(r"(\d{6})$", text)
    if match:
        return match.group(1)
    if len(text) == 6 and text.isdigit():
        return text
    return ""


def is_mainboard_code(code: str) -> bool:
    return code.startswith(("600", "601", "603", "605", "000", "001", "002"))


def is_st_name(name: object) -> bool:
    if name is None:
        return False
    text = str(name).upper()
    return "ST" in text


def parse_cn_date(value: object) -> pd.Timestamp:
    if value is None or pd.isna(value):
        return pd.NaT
    text = str(value).strip()
    if not text:
        return pd.NaT
    text = text.replace("/", "-").replace(".", "-")
    try:
        return pd.to_datetime(text, errors="coerce")
    except Exception:
        return pd.NaT


def cutoff_date(report_date: date, window_days: int) -> pd.Timestamp:
    return pd.Timestamp(report_date) - pd.Timedelta(days=window_days)


def parse_numeric(value: object) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    text = str(value).strip().replace(",", "")
    if not text:
        return float("nan")
    if "/" in text:
        left = text.split("/", 1)[0]
        return parse_numeric(left)
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100.0
        except ValueError:
            return float("nan")
    try:
        return float(text)
    except ValueError:
        return float("nan")


def rating_to_score(rating: object) -> float:
    if rating is None or pd.isna(rating):
        return float("nan")
    text = str(rating).strip()
    if not text:
        return float("nan")
    text_upper = text.upper()
    if text_upper in {"BUY", "OUTPERFORM", "OVERWEIGHT"}:
        return 95.0
    if text_upper in {"HOLD", "NEUTRAL"}:
        return 55.0
    if text_upper in {"UNDERPERFORM", "SELL"}:
        return 15.0
    for key, score in RATING_SCORE_MAP.items():
        if key in text:
            return score
    return float("nan")


def clamp(value: float, low: float, high: float) -> float:
    if pd.isna(value):
        return float("nan")
    return min(max(value, low), high)


def safe_mean(values: Iterable[float], default: float = float("nan")) -> float:
    arr = [float(v) for v in values if not pd.isna(v)]
    if not arr:
        return default
    return float(sum(arr) / len(arr))


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
