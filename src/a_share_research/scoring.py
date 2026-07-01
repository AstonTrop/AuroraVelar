from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .utils import clamp, cutoff_date, is_mainboard_code, is_st_name, parse_numeric, rating_to_score


OUTPUT_COLUMNS = [
    "code",
    "name",
    "industry",
    "close_price",
    "institution_count",
    "source_group_count",
    "rating_strength_score",
    "diversity_score",
    "upside_score",
    "consistency_score",
    "focus_theme_score",
    "focus_bonus",
    "adjusted_score",
    "target_upside_pct",
    "composite_score",
    "buy_signal",
]


def _empty_output() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def _normalize_0_100(series: pd.Series, low: float, high: float, fill: float = 50.0) -> pd.Series:
    denom = high - low
    if denom <= 0:
        return pd.Series(fill, index=series.index, dtype=float)
    out = (series - low) / denom * 100.0
    out = out.clip(lower=0.0, upper=100.0)
    return out.fillna(fill)


def _weighted_row_score(values: list[float], weights: list[float], default: float = 55.0) -> float:
    effective = [(v, w) for v, w in zip(values, weights) if not pd.isna(v)]
    if not effective:
        return default
    weight_sum = sum(w for _, w in effective)
    if weight_sum == 0:
        return default
    return sum(v * w for v, w in effective) / weight_sum


def _build_upside(raw_records: pd.DataFrame, target_metrics: pd.DataFrame, merged: pd.DataFrame) -> pd.Series:
    raw_target = raw_records.copy()
    raw_target["target_price"] = raw_target["target_price"].map(parse_numeric)
    raw_target = raw_target[(raw_target["target_price"] > 0) & raw_target["close_price"].notna() & (raw_target["close_price"] > 0)]

    if raw_target.empty:
        detailed_upside = pd.Series(dtype=float)
    else:
        detailed_upside = (
            raw_target.assign(upside=lambda d: d["target_price"] / d["close_price"] - 1.0)
            .groupby("code", as_index=True)["upside"]
            .mean()
        )

    merged = merged.set_index("code", drop=False)

    if not target_metrics.empty and {"code", "avg_target_upside"}.issubset(target_metrics.columns):
        tm = target_metrics.set_index("code")
        merged["upside_from_agg"] = tm["avg_target_upside"]
    else:
        merged["upside_from_agg"] = float("nan")

    merged["upside_from_detail"] = detailed_upside
    merged["upside_raw"] = merged["upside_from_agg"].where(merged["upside_from_agg"].notna(), merged["upside_from_detail"])
    merged["upside_raw"] = merged["upside_raw"].fillna(0.05)
    merged["upside_raw"] = merged["upside_raw"].map(lambda x: clamp(float(x), -0.30, 1.50))

    out = _normalize_0_100(merged["upside_raw"], low=-0.30, high=1.50, fill=50.0)
    return out


def _rating_signal(score: float) -> str:
    if score >= 85:
        return "强买共识"
    if score >= 72:
        return "买入偏强"
    if score >= 60:
        return "谨慎买入"
    if score >= 50:
        return "中性偏多"
    return "观望"


COMPUTE_POWER_KEYWORDS = [
    "算力",
    "gpu",
    "ai",
    "人工智能",
    "aigc",
    "服务器",
    "液冷",
    "光模块",
    "cpo",
    "数据中心",
    "idc",
    "云计算",
    "交换机",
    "芯片",
    "半导体",
    "存储",
    "光通信",
    "通信设备",
]

COMPUTE_POWER_SECONDARY_KEYWORDS = [
    "网络",
    "信息",
    "科技",
    "电子",
    "通信",
    "软件",
    "计算",
]

COMPUTE_POWER_INDUSTRY_HINTS = [
    "通信设备",
    "通信服务",
    "半导体",
    "计算机设备",
    "软件开发",
    "it服务",
    "互联网服务",
    "光学光电子",
    "元件",
]


def _normalize_focus_theme(theme: str) -> str:
    raw = str(theme or "").strip().lower()
    if raw in {"compute_power", "算力", "算力板块", "compute", "compute-power"}:
        return "compute_power"
    return "none"


def _compute_power_theme_score(name: str, industry: str) -> float:
    clean_industry = "" if str(industry).lower() == "nan" else str(industry)
    text = f"{name} {clean_industry}".lower()
    score = 0.0
    match_count = 0
    for kw in COMPUTE_POWER_KEYWORDS:
        if kw.lower() in text:
            score += 22.0
            match_count += 1
    secondary_count = 0
    for kw in COMPUTE_POWER_SECONDARY_KEYWORDS:
        if kw.lower() in text:
            score += 12.0
            secondary_count += 1
    if any(h.lower() in str(industry).lower() for h in COMPUTE_POWER_INDUSTRY_HINTS):
        score += 20.0
    if match_count >= 2:
        score += 10.0
    if match_count == 0 and secondary_count > 0:
        score += 15.0
    return float(min(score, 100.0))


def score_candidates(
    raw_records: pd.DataFrame,
    forecast_metrics: pd.DataFrame,
    target_metrics: pd.DataFrame,
    composite_metrics: pd.DataFrame,
    price_snapshot: pd.DataFrame,
    report_date: date,
    window_days: int,
    min_institutions: int,
    price_limit: float,
    top_n: int,
    diversity_weight: float,
    rating_weight: float,
    upside_weight: float,
    consistency_weight: float,
    focus_theme: str = "none",
    focus_boost_weight: float = 0.18,
) -> pd.DataFrame:
    if raw_records.empty or price_snapshot.empty:
        return _empty_output()

    work = raw_records.copy()
    if "industry" not in work.columns:
        work["industry"] = ""
    work["pub_date"] = pd.to_datetime(work["pub_date"], errors="coerce")

    # 90-day filter for detailed records; keep aggregate sources as they are rolling stats
    window_cutoff = cutoff_date(report_date, window_days)
    aggregate_sources = {
        "eastmoney_profit_forecast",
        "sina_股票综合评级",
        "sina_目标涨幅排名",
    }
    detailed_mask = ~work["source"].isin(aggregate_sources)
    work = work[(~detailed_mask) | (work["pub_date"] >= window_cutoff)]

    # Attach price and canonical name
    px = price_snapshot[["code", "name", "close_price"]].copy()
    px = px.drop_duplicates(subset=["code"], keep="first")
    merged = px.copy()
    industry_map = (
        work.assign(industry=work["industry"].fillna("").astype(str).str.strip())
        .groupby("code", as_index=True)["industry"]
        .agg(lambda s: s[s.ne("")].value_counts().idxmax() if any(s.ne("")) else "")
    )
    merged = merged.merge(industry_map.rename("industry"), left_on="code", right_index=True, how="left")
    merged["industry"] = merged["industry"].fillna("")

    # Hard filters: mainboard + non-ST + price < limit
    merged = merged[merged["code"].map(is_mainboard_code)]
    merged = merged[~merged["name"].map(is_st_name)]
    merged = merged[merged["close_price"].notna() & (merged["close_price"] < price_limit)]
    if merged.empty:
        return _empty_output()

    work = work[work["code"].isin(set(merged["code"]))]

    # Institution diversity from detailed institution-bearing records
    detailed_institution_rows = work[
        (~work["institution"].str.contains("_AGGREGATE", na=False)) & (~work["source"].isin({"eastmoney_profit_forecast"}))
    ]
    inst_count = (
        detailed_institution_rows.groupby("code", as_index=True)["institution"].nunique().rename("institution_count")
    )
    merged = merged.merge(inst_count, left_on="code", right_index=True, how="left")
    merged["institution_count"] = merged["institution_count"].fillna(0).astype(int)

    # Hard filter: minimum institutions
    merged = merged[merged["institution_count"] >= min_institutions]
    if merged.empty:
        return _empty_output()

    # Rating strengths from source layers
    rating_detail = work.copy()
    rating_detail["rating_score"] = rating_detail["rating"].map(rating_to_score)
    detail_score = rating_detail.groupby("code", as_index=True)["rating_score"].mean().rename("detail_rating_score")

    merged = merged.merge(detail_score, left_on="code", right_index=True, how="left")

    if not forecast_metrics.empty:
        merged = merged.merge(
            forecast_metrics[["code", "forecast_rating_score", "forecast_report_count"]],
            on="code",
            how="left",
        )
    else:
        merged["forecast_rating_score"] = float("nan")
        merged["forecast_report_count"] = 0.0

    if not composite_metrics.empty:
        merged = merged.merge(
            composite_metrics[["code", "sina_comp_rating_score", "sina_comp_total_count"]],
            on="code",
            how="left",
        )
    else:
        merged["sina_comp_rating_score"] = float("nan")
        merged["sina_comp_total_count"] = 0.0

    merged["rating_strength_score"] = merged.apply(
        lambda row: _weighted_row_score(
            values=[row.get("detail_rating_score"), row.get("forecast_rating_score"), row.get("sina_comp_rating_score")],
            weights=[0.50, 0.25, 0.25],
            default=55.0,
        ),
        axis=1,
    )

    # Diversity score
    merged["diversity_score"] = _normalize_0_100(
        merged["institution_count"].map(lambda x: np.log1p(x)),
        low=0.0,
        high=float(np.log1p(20)),
        fill=0.0,
    )

    # Upside score
    raw_for_upside = work.merge(merged[["code", "close_price"]], on="code", how="inner")
    merged["upside_score"] = _build_upside(raw_for_upside, target_metrics=target_metrics, merged=merged.reset_index(drop=True)).values

    # Consistency score: source coverage + rating agreement across source groups
    source_group = rating_detail[["code", "source", "rating_score"]].copy()
    source_group["source_group"] = source_group["source"].map(
        lambda s: "eastmoney_report"
        if s == "eastmoney_research_report"
        else ("forecast" if s == "eastmoney_profit_forecast" else "sina")
    )

    source_presence = source_group.groupby("code", as_index=True)["source_group"].nunique().rename("source_group_count")
    merged = merged.merge(source_presence, left_on="code", right_index=True, how="left")
    merged["source_group_count"] = merged["source_group_count"].fillna(1)

    group_rating = source_group.groupby(["code", "source_group"], as_index=False)["rating_score"].mean()
    rating_std = group_rating.groupby("code", as_index=True)["rating_score"].std(ddof=0).rename("rating_std")
    merged = merged.merge(rating_std, left_on="code", right_index=True, how="left")

    merged["presence_ratio"] = (merged["source_group_count"] / 3.0).clip(upper=1.0)
    merged["agreement_ratio"] = 1.0 - (merged["rating_std"].fillna(20.0) / 40.0).clip(lower=0.0, upper=1.0)
    merged["consistency_score"] = (0.7 * merged["presence_ratio"] + 0.3 * merged["agreement_ratio"]) * 100.0

    # Final composite score
    merged["composite_score"] = (
        merged["diversity_score"] * diversity_weight
        + merged["rating_strength_score"] * rating_weight
        + merged["upside_score"] * upside_weight
        + merged["consistency_score"] * consistency_weight
    )

    merged["target_upside_pct"] = (merged["upside_score"] / 100.0) * 1.8 - 0.3
    merged["target_upside_pct"] = merged["target_upside_pct"] * 100.0
    merged["buy_signal"] = merged["rating_strength_score"].map(_rating_signal)
    normalized_theme = _normalize_focus_theme(focus_theme)
    if normalized_theme == "compute_power":
        merged["focus_theme_score"] = merged.apply(
            lambda row: _compute_power_theme_score(str(row.get("name", "")), str(row.get("industry", ""))),
            axis=1,
        )
    else:
        merged["focus_theme_score"] = 0.0
    merged["focus_bonus"] = merged["focus_theme_score"] * float(max(focus_boost_weight, 0.0))
    merged["adjusted_score"] = merged["composite_score"] + merged["focus_bonus"]

    out = merged.sort_values(
        by=["adjusted_score", "focus_theme_score", "composite_score", "institution_count"],
        ascending=[False, False, False, False],
    ).head(top_n)

    return out[OUTPUT_COLUMNS].reset_index(drop=True)
