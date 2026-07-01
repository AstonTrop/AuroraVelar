from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
from jinja2 import Template

from .data_sources import (
    _call_with_retries,
    attach_rating_score,
    build_raw_bundle,
    dedupe_standard_records,
    fetch_eastmoney_research_for_codes,
    fetch_price_snapshot,
)
from .scoring import score_candidates
from .utils import normalize_code, now_ts, parse_numeric


POSITION_COLUMNS = ["code", "name", "position_pct", "cost_price", "share_count", "available_count"]
POSITION_REQUIRED_COLUMNS = ["code", "name", "position_pct", "cost_price"]
TRACKED_INDEX_CODES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
}

PORTFOLIO_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>持仓全盘分析报告</title>
  <style>
    body { font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background:#f4f7fb; color:#18212f; margin:0; }
    .wrap { max-width: 1180px; margin: 0 auto; padding: 24px; }
    .card { background:#fff; border-radius:16px; padding:20px; margin-bottom:16px; box-shadow:0 10px 30px rgba(15,23,42,0.08); }
    h1, h2, h3 { margin:0 0 12px; }
    .meta, .chips { display:flex; gap:10px; flex-wrap:wrap; }
    .chip { padding:6px 12px; border-radius:999px; background:#e8f0ff; color:#1d4ed8; font-size:13px; }
    .warn { background:#fff1f2; color:#be123c; }
    .good { background:#ecfdf5; color:#047857; }
    .bad { background:#fef2f2; color:#b91c1c; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { border:1px solid #e5e7eb; padding:8px 10px; text-align:left; vertical-align:top; }
    th { background:#f8fafc; }
    ul { margin:0; padding-left:18px; }
    li { margin:4px 0; }
    .muted { color:#64748b; font-size:12px; }
    .bar { width:140px; height:10px; border-radius:999px; background:#e5e7eb; overflow:hidden; }
    .bar > span { display:block; height:100%; background:linear-gradient(90deg,#22c55e,#16a34a); }
  </style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>持仓全盘分析报告</h1>
    <div class="meta">
      <span class="chip">报告日期 {{ report_date }}</span>
      <span class="chip">生成时间 {{ generated_at }}</span>
      <span class="chip">大盘节奏 {{ trend_label }}</span>
      <span class="chip">市场广度 {{ breadth_label }}</span>
      <span class="chip">风险水平 {{ risk_level }}</span>
    </div>
    <p class="muted">说明: 本报告用于研究辅助，不构成投资建议。</p>
  </div>

  <div class="card">
    <h2>市场环境</h2>
    <div class="chips">
      <span class="chip">候选池快照来源 {{ candidate_snapshot_source }}</span>
      {% for item in index_snapshot %}
      <span class="chip">{{ item.name }} {{ item.price }} ({{ item.change_pct }})</span>
      {% endfor %}
    </div>
    <p>热点方向: {{ hot_sectors }}</p>
  </div>

  <div class="card">
    <h2>投资结论</h2>
    <p>{{ recommendation_summary }}</p>
  </div>

  <div class="card">
    <h2>组合总览</h2>
    <div class="chips">
      <span class="chip">股票仓位 {{ stock_exposure_pct }}</span>
      <span class="chip">现金仓位 {{ cash_pct }}</span>
      <span class="chip">最大单票 {{ top_holding_pct }}</span>
      <span class="chip">前三持仓 {{ top3_holding_pct }}</span>
      <span class="chip {% if available_sell_ratio_value >= 80 %}good{% elif available_sell_ratio_value <= 20 %}bad{% endif %}">可卖比例 {{ available_sell_ratio }}</span>
      {% for flag in risk_flags %}
      <span class="chip warn">{{ flag }}</span>
      {% endfor %}
    </div>
  </div>

  <div class="card">
    <h2>个股诊断</h2>
    {{ positions_table | safe }}
  </div>

  <div class="card">
    <h2>调仓建议</h2>
    <ul>{% for item in rebalance_actions %}<li>{{ item }}</li>{% endfor %}</ul>
  </div>

  <div class="card">
    <h2>多空要点</h2>
    <h3>偏多因素</h3>
    <ul>{% for item in bull_points %}<li>{{ item }}</li>{% endfor %}</ul>
    <h3>偏空因素</h3>
    <ul>{% for item in bear_points %}<li>{{ item }}</li>{% endfor %}</ul>
  </div>

  <div class="card">
    <h2>跟踪指标</h2>
    <ul>{% for item in monitoring_points %}<li>{{ item }}</li>{% endfor %}</ul>
  </div>

  <div class="card">
    <h2>新标的建议</h2>
    <ul>{% for item in replacement_candidates %}<li>{{ item.name }}（{{ item.code }}）: 候选分 {{ item.score }}，当前价格 {{ item.close_price }}</li>{% endfor %}</ul>
  </div>
</div>
</body>
</html>
"""

MARKET_BRIEF_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>A股实时市场快报</title>
  <style>
    body { font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background:#f4f7fb; color:#18212f; margin:0; }
    .wrap { max-width: 1180px; margin: 0 auto; padding: 24px; }
    .card { background:#fff; border-radius:16px; padding:20px; margin-bottom:16px; box-shadow:0 10px 30px rgba(15,23,42,0.08); }
    h1, h2, h3 { margin:0 0 12px; }
    .meta, .chips { display:flex; gap:10px; flex-wrap:wrap; }
    .chip { padding:6px 12px; border-radius:999px; background:#e8f0ff; color:#1d4ed8; font-size:13px; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { border:1px solid #e5e7eb; padding:8px 10px; text-align:left; vertical-align:top; }
    th { background:#f8fafc; }
    ul { margin:0; padding-left:18px; }
    li { margin:4px 0; }
    .muted { color:#64748b; font-size:12px; }
  </style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>A股实时市场快报</h1>
    <div class="meta">
      <span class="chip">报告日期 {{ report_date }}</span>
      <span class="chip">生成时间 {{ generated_at }}</span>
      <span class="chip">大盘节奏 {{ trend_label }}</span>
      <span class="chip">市场广度 {{ breadth_label }}</span>
      <span class="chip">风险水平 {{ risk_level }}</span>
    </div>
    <p class="muted">说明: 本报告用于研究辅助，不构成投资建议。</p>
  </div>

  <div class="card">
    <h2>市场环境</h2>
    <div class="chips">
      <span class="chip">候选池快照来源 {{ candidate_snapshot_source }}</span>
      {% for item in index_snapshot %}
      <span class="chip">{{ item.name }} {{ item.price }} ({{ item.change_pct }})</span>
      {% endfor %}
      {% for sector in hot_sectors %}
      <span class="chip">{{ sector }}</span>
      {% endfor %}
    </div>
  </div>

  <div class="card">
    <h2>市场结论</h2>
    <ul>{% for item in market_conclusions %}<li>{{ item }}</li>{% endfor %}</ul>
  </div>

  <div class="card">
    <h2>候选方向</h2>
    {{ candidates_table | safe }}
  </div>
</div>
</body>
</html>
"""


@dataclass(frozen=True)
class PortfolioAnalysisConfig:
    report_date: date = date.today()
    output_dir: Path = Path("output")
    focus_theme: str = "none"
    focus_boost_weight: float = 0.18
    window_days: int = 90
    min_institutions: int = 5
    price_limit: float = 20.0
    top_n_candidates: int = 5
    important_holding_count: int = 3
    notice_lookback_days: int = 3
    latest_positions_path: Path = Path("output/portfolio/latest_positions.csv")
    market_snapshot_cache_path: Path = Path("output/portfolio/market_snapshot_cache.csv")
    market_snapshot_cache_minutes: int = 15


def _log(msg: str) -> None:
    print(f"[{now_ts()}] {msg}")


def _empty_market_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "code",
            "name",
            "current_price",
            "day_change_pct",
            "industry",
            "research_score",
            "fundamental_score",
            "fundamental_summary",
            "institution_count",
            "source_group_count",
            "target_upside_pct",
            "technical_score",
            "trend_score",
            "momentum_score",
            "rsi_score",
            "volatility_score",
            "setup_tags",
            "technical_bias",
            "news_summary",
            "notice_summary",
        ]
    )


def _ensure_text_column(df: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype=object)
    return df[column].fillna(default).astype(str)


def _safe_float(value: object, default: float = 0.0) -> float:
    numeric = parse_numeric(value)
    if pd.isna(numeric):
        return default
    return float(numeric)


def _parse_position_pct(value: object) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    text = str(value).strip().replace("％", "%")
    if text.endswith("%"):
        text = text[:-1]
    return float(parse_numeric(text))


def _latest_financial_period(columns: list[str]) -> str:
    valid = [col for col in columns if str(col).isdigit() and len(str(col)) == 8]
    if not valid:
        return ""
    return sorted(valid, reverse=True)[0]


def summarize_fundamentals_from_abstract(abstract_df: pd.DataFrame) -> dict[str, Any]:
    if abstract_df is None or abstract_df.empty:
        return {"latest_period": "", "score": 50.0, "summary": "基本面数据缺失"}

    latest_period = _latest_financial_period(list(abstract_df.columns))
    if not latest_period:
        return {"latest_period": "", "score": 50.0, "summary": "基本面数据缺失"}

    metric_map = {}
    for _, row in abstract_df.iterrows():
        metric_map[str(row.get("指标", "")).strip()] = _safe_float(row.get(latest_period), default=float("nan"))

    revenue = metric_map.get("营业总收入", float("nan"))
    net_profit = metric_map.get("归母净利润", float("nan"))
    roe = metric_map.get("净资产收益率", metric_map.get("加权净资产收益率", float("nan")))
    gross_margin = metric_map.get("销售毛利率", metric_map.get("毛利率", float("nan")))

    score = 50.0
    if pd.notna(net_profit):
        score += 15.0 if net_profit > 0 else -15.0
    if pd.notna(roe):
        score += 15.0 if roe >= 10.0 else 5.0 if roe >= 6.0 else -10.0
    if pd.notna(gross_margin):
        score += 10.0 if gross_margin >= 20.0 else 4.0 if gross_margin >= 10.0 else -6.0
    if pd.notna(revenue):
        score += 10.0 if revenue > 0 else -10.0
    score = max(0.0, min(100.0, score))

    parts: list[str] = []
    if pd.notna(revenue):
        parts.append(f"营业总收入{revenue / 1e8:.1f}亿元")
    if pd.notna(net_profit):
        parts.append(f"归母净利润{net_profit / 1e8:.1f}亿元")
    if pd.notna(roe):
        parts.append(f"ROE {roe:.1f}%")
    if pd.notna(gross_margin):
        parts.append(f"毛利率{gross_margin:.1f}%")
    summary = "，".join(parts) if parts else "基本面数据缺失"

    return {
        "latest_period": latest_period,
        "score": score,
        "summary": summary,
    }


def summarize_fundamentals_from_indicator(indicator_df: pd.DataFrame) -> dict[str, Any]:
    if indicator_df is None or indicator_df.empty or "日期" not in indicator_df.columns:
        return {"latest_period": "", "score": 50.0, "summary": "基本面数据缺失"}

    work = indicator_df.copy()
    work["日期"] = pd.to_datetime(work["日期"], errors="coerce")
    work = work.dropna(subset=["日期"]).sort_values(by="日期")
    if work.empty:
        return {"latest_period": "", "score": 50.0, "summary": "基本面数据缺失"}

    row = work.iloc[-1]
    latest_period = row["日期"].strftime("%Y%m%d")
    roe = _safe_float(row.get("加权净资产收益率(%)", row.get("净资产收益率(%)", float("nan"))), default=float("nan"))
    revenue_growth = _safe_float(row.get("主营业务收入增长率(%)"), default=float("nan"))
    profit_growth = _safe_float(row.get("净利润增长率(%)"), default=float("nan"))
    debt_ratio = _safe_float(row.get("资产负债率(%)"), default=float("nan"))
    cashflow_ratio = _safe_float(row.get("经营现金净流量与净利润的比率(%)"), default=float("nan"))

    score = 50.0
    if pd.notna(roe):
        score += 15.0 if roe >= 12.0 else 8.0 if roe >= 8.0 else -8.0
    if pd.notna(revenue_growth):
        score += 10.0 if revenue_growth >= 10.0 else 4.0 if revenue_growth >= 0.0 else -8.0
    if pd.notna(profit_growth):
        score += 12.0 if profit_growth >= 15.0 else 5.0 if profit_growth >= 0.0 else -10.0
    if pd.notna(debt_ratio):
        score += 6.0 if debt_ratio <= 55.0 else 2.0 if debt_ratio <= 70.0 else -8.0
    if pd.notna(cashflow_ratio):
        score += 8.0 if cashflow_ratio >= 80.0 else 4.0 if cashflow_ratio >= 20.0 else -6.0
    score = max(0.0, min(100.0, score))

    parts: list[str] = []
    if pd.notna(roe):
        parts.append(f"加权ROE {roe:.1f}%")
    if pd.notna(revenue_growth):
        parts.append(f"营收增速{revenue_growth:.1f}%")
    if pd.notna(profit_growth):
        parts.append(f"净利增速{profit_growth:.1f}%")
    if pd.notna(debt_ratio):
        parts.append(f"资产负债率{debt_ratio:.1f}%")
    if pd.notna(cashflow_ratio):
        parts.append(f"经营现金/净利润{cashflow_ratio:.1f}%")
    summary = "，".join(parts) if parts else "基本面数据缺失"

    return {
        "latest_period": latest_period,
        "score": score,
        "summary": summary,
    }


def summarize_market_overview(
    index_df: pd.DataFrame,
    breadth_df: pd.DataFrame,
    report_date: date,
    hot_sectors: list[str] | None = None,
) -> dict[str, Any]:
    index_snapshot: list[dict[str, Any]] = []
    if index_df is not None and not index_df.empty:
        code_col = "代码" if "代码" in index_df.columns else "code"
        name_col = "名称" if "名称" in index_df.columns else "name"
        price_col = "最新价" if "最新价" in index_df.columns else "price"
        change_col = "涨跌幅" if "涨跌幅" in index_df.columns else "change_pct"
        work = index_df.copy()
        work[code_col] = work[code_col].astype(str)
        for code in TRACKED_INDEX_CODES:
            matched = work[work[code_col] == code]
            if matched.empty:
                continue
            row = matched.iloc[0]
            index_snapshot.append(
                {
                    "code": code,
                    "name": str(row.get(name_col, TRACKED_INDEX_CODES[code])),
                    "price": _safe_float(row.get(price_col), default=float("nan")),
                    "change_pct": _safe_float(row.get(change_col), default=0.0),
                }
            )

    breadth_work = breadth_df.copy() if breadth_df is not None else pd.DataFrame()
    if not breadth_work.empty:
        if "day_change_pct" in breadth_work.columns:
            change_series = pd.to_numeric(breadth_work["day_change_pct"], errors="coerce")
        else:
            last = pd.to_numeric(breadth_work.get("last_price"), errors="coerce")
            prev = pd.to_numeric(breadth_work.get("prev_close"), errors="coerce")
            valid_last = last.where(last > 0)
            valid_prev = prev.where(prev > 0)
            change_series = (valid_last - valid_prev).div(valid_prev) * 100.0
        up_count = int((change_series > 0).sum())
        down_count = int((change_series < 0).sum())
    else:
        up_count = 0
        down_count = 0
        change_series = pd.Series(dtype=float)

    limit_up_count = int((change_series >= 9.8).sum()) if not change_series.empty else 0
    limit_down_count = int((change_series <= -9.8).sum()) if not change_series.empty else 0
    active_count = int(change_series.notna().sum()) if not change_series.empty else 0

    avg_index_change = sum(item["change_pct"] for item in index_snapshot) / len(index_snapshot) if index_snapshot else 0.0
    cyb_change = next((item["change_pct"] for item in index_snapshot if item["name"] == "创业板指"), avg_index_change)
    hs300_change = next((item["change_pct"] for item in index_snapshot if item["name"] == "沪深300"), avg_index_change)

    if avg_index_change >= 1.5 or cyb_change - hs300_change >= 2.0:
        trend_label = "强势上攻"
    elif avg_index_change >= 0.3:
        trend_label = "偏强震荡"
    elif avg_index_change <= -1.0:
        trend_label = "弱势承压"
    else:
        trend_label = "震荡整理"

    directional_count = up_count + down_count
    if active_count > 0 and directional_count == 0:
        breadth_label = "涨跌数据不足"
    elif up_count > down_count * 1.2:
        breadth_label = "上涨家数占优"
    elif down_count > up_count * 1.2:
        breadth_label = "下跌家数占优"
    else:
        breadth_label = "涨跌分化"

    if avg_index_change <= -1.0 or down_count > up_count * 1.5:
        risk_level = "偏高"
    elif avg_index_change >= 1.0 and up_count >= down_count:
        risk_level = "中等"
    else:
        risk_level = "中等"

    up_ratio = up_count / active_count if active_count else 0.0
    if active_count <= 0 or directional_count == 0:
        if avg_index_change >= 1.0:
            market_temperature = "强势"
            max_stock_exposure_pct = 80.0
        elif avg_index_change >= -0.5:
            market_temperature = "震荡"
            max_stock_exposure_pct = 65.0
        elif avg_index_change >= -1.2:
            market_temperature = "弱势"
            max_stock_exposure_pct = 50.0
        else:
            market_temperature = "极弱"
            max_stock_exposure_pct = 35.0
    elif avg_index_change >= 0.8 and up_ratio >= 0.58:
        market_temperature = "强势"
        max_stock_exposure_pct = 85.0
    elif avg_index_change >= -0.5 and up_ratio >= 0.42:
        market_temperature = "震荡"
        max_stock_exposure_pct = 70.0
    elif avg_index_change >= -1.2 and up_ratio >= 0.28:
        market_temperature = "弱势"
        max_stock_exposure_pct = 55.0
    else:
        market_temperature = "极弱"
        max_stock_exposure_pct = 40.0

    if active_count <= 0 or directional_count == 0:
        activity_label = "成交活跃度缺失"
    elif limit_up_count >= max(limit_down_count * 2, 20):
        activity_label = "涨停扩散较强"
    elif limit_down_count >= max(limit_up_count * 2, 20):
        activity_label = "跌停风险扩散"
    else:
        activity_label = "活跃度中性"

    return {
        "report_date": report_date.isoformat(),
        "trend_label": trend_label,
        "breadth_label": breadth_label,
        "risk_level": risk_level,
        "market_temperature": market_temperature,
        "max_stock_exposure_pct": max_stock_exposure_pct,
        "activity_label": activity_label,
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "index_snapshot": index_snapshot,
        "hot_sectors": hot_sectors or [],
    }


def summarize_hot_sectors(candidate_df: pd.DataFrame, top_n: int = 3) -> list[str]:
    if candidate_df is None or candidate_df.empty or "industry" not in candidate_df.columns:
        return []
    work = candidate_df.copy()
    work["industry"] = work["industry"].fillna("").astype(str).str.strip()
    work = work[(work["industry"].ne("")) & (work["industry"].str.lower().ne("nan"))]
    if work.empty:
        return []
    score_col = "adjusted_score" if "adjusted_score" in work.columns else "composite_score"
    work[score_col] = pd.to_numeric(work[score_col], errors="coerce").fillna(0.0)
    top_slice = work.sort_values(by=score_col, ascending=False).head(max(top_n * 5, 10))
    grouped = (
        top_slice.groupby("industry", as_index=False)
        .agg(avg_score=(score_col, "mean"), count=("code", "nunique"))
        .sort_values(by=["count", "avg_score"], ascending=[False, False])
    )
    return grouped.head(top_n)["industry"].astype(str).tolist()


def normalize_board_snapshot(raw_df: pd.DataFrame, board_type: str) -> pd.DataFrame:
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(
            columns=[
                "board_type",
                "board_name",
                "change_pct",
                "up_count",
                "down_count",
                "up_ratio",
                "leader_code",
                "leader",
                "leader_price",
                "leader_change_pct",
                "board_action",
            ]
        )

    work = raw_df.copy()

    def _find_col(candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate in work.columns:
                return candidate
        return None

    name_col = _find_col(["板块名称", "板块", "名称", "行业名称", "概念名称"])
    change_col = _find_col(["涨跌幅", "涨幅", "涨跌幅%"])
    up_col = _find_col(["上涨家数", "上涨数"])
    down_col = _find_col(["下跌家数", "下跌数"])
    leader_code_col = _find_col(["领涨股票-代码", "领涨股代码", "股票代码", "板块异动最频繁个股及所属类型-股票代码"])
    leader_col = _find_col(["领涨股票", "领涨股", "股票名称", "板块异动最频繁个股及所属类型-股票名称"])
    leader_price_col = _find_col(["领涨股票-最新价", "领涨股最新价", "个股-当前价"])
    leader_change_col = _find_col(["领涨股票-涨跌幅", "领涨股涨跌幅", "领涨股票涨跌幅", "个股-涨跌幅"])
    if name_col is None or change_col is None:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "board_type": board_type,
            "board_name": work[name_col].fillna("").astype(str).str.strip(),
            "change_pct": work[change_col].map(parse_numeric),
            "up_count": work[up_col].map(parse_numeric) if up_col else 0.0,
            "down_count": work[down_col].map(parse_numeric) if down_col else 0.0,
            "leader_code": work[leader_code_col].map(normalize_code) if leader_code_col else "",
            "leader": work[leader_col].fillna("").astype(str) if leader_col else "",
            "leader_price": work[leader_price_col].map(parse_numeric) if leader_price_col else float("nan"),
            "leader_change_pct": work[leader_change_col].map(parse_numeric) if leader_change_col else 0.0,
        }
    )
    out = out[out["board_name"].ne("")]
    out["up_count"] = pd.to_numeric(out["up_count"], errors="coerce").fillna(0.0)
    out["down_count"] = pd.to_numeric(out["down_count"], errors="coerce").fillna(0.0)
    total = out["up_count"] + out["down_count"]
    out["up_ratio"] = out["up_count"].div(total.where(total > 0)).fillna(0.0)

    def _action(row: pd.Series) -> str:
        change_pct = float(row.get("change_pct", 0.0))
        up_ratio = float(row.get("up_ratio", 0.0))
        spread_count = float(row.get("up_count", 0.0)) + float(row.get("down_count", 0.0))
        if spread_count <= 0:
            return "只观察" if change_pct >= 0.0 else "回避"
        if change_pct >= 1.0 and up_ratio >= 0.60:
            return "可参与"
        if change_pct >= 0.0 and up_ratio >= 0.45:
            return "只观察"
        return "回避"

    out["board_action"] = out.apply(_action, axis=1)
    return out.sort_values(by="change_pct", ascending=False).reset_index(drop=True)


def fetch_board_snapshot() -> tuple[pd.DataFrame, str]:
    frames: list[pd.DataFrame] = []
    fetchers = [
        ("行业", "eastmoney_industry", ak.stock_board_industry_name_em),
        ("概念", "eastmoney_concept", ak.stock_board_concept_name_em),
        ("行业", "sina_sector", ak.stock_sector_spot),
        ("异动", "eastmoney_change", ak.stock_board_change_em),
    ]
    used_sources: list[str] = []
    for board_type, source_name, fetcher in fetchers:
        try:
            raw = _call_with_retries(fetcher, retries=1, timeout_seconds=12.0)
        except Exception:  # noqa: BLE001
            continue
        shaped = normalize_board_snapshot(raw, board_type=board_type)
        if not shaped.empty:
            frames.append(shaped)
            used_sources.append(source_name)
    if not frames:
        return pd.DataFrame(), "unavailable"
    return pd.concat(frames, ignore_index=True), "live:" + ",".join(used_sources)


def summarize_board_strength(board_df: pd.DataFrame, top_n: int = 8) -> list[dict[str, Any]]:
    if board_df is None or board_df.empty:
        return []
    work = board_df.copy()
    work["change_pct"] = pd.to_numeric(work["change_pct"], errors="coerce").fillna(0.0)
    work["up_ratio"] = pd.to_numeric(work["up_ratio"], errors="coerce").fillna(0.0)
    work["heat_score"] = work["change_pct"] * 12.0 + work["up_ratio"] * 40.0
    records: list[dict[str, Any]] = []
    for _, row in work.sort_values(by="heat_score", ascending=False).head(top_n).iterrows():
        records.append(
            {
                "board_type": str(row.get("board_type", "")),
                "board_name": str(row.get("board_name", "")),
                "change_pct": float(row.get("change_pct", 0.0)),
                "up_ratio": float(row.get("up_ratio", 0.0)),
                "leader_code": str(row.get("leader_code", "")),
                "leader": str(row.get("leader", "")),
                "leader_price": float(row.get("leader_price", 0.0))
                if pd.notna(row.get("leader_price", float("nan")))
                else float("nan"),
                "leader_change_pct": float(row.get("leader_change_pct", 0.0)),
                "board_action": str(row.get("board_action", "只观察")),
            }
        )
    return records


def _is_main_board_code(code: str) -> bool:
    text = normalize_code(code)
    return text.startswith(("000", "001", "002", "600", "601", "603", "605"))


def _compact_stock_name(value: object) -> str:
    text = str(value or "").strip()
    fullwidth_map = str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ", "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return text.translate(fullwidth_map).replace(" ", "").replace("\u3000", "")


def _prepare_price_lookup(price_snapshot: pd.DataFrame | None) -> pd.DataFrame:
    if price_snapshot is None or price_snapshot.empty:
        return pd.DataFrame(columns=["lookup_code", "lookup_name_key", "lookup_price"])

    work = price_snapshot.copy()
    code_col = "code" if "code" in work.columns else "代码" if "代码" in work.columns else ""
    name_col = "name" if "name" in work.columns else "名称" if "名称" in work.columns else ""
    if not code_col or not name_col:
        return pd.DataFrame(columns=["lookup_code", "lookup_name_key", "lookup_price"])

    price_col = ""
    for candidate in ["close_price", "last_price", "current_price", "最新价"]:
        if candidate in work.columns:
            price_col = candidate
            break
    if not price_col:
        return pd.DataFrame(columns=["lookup_code", "lookup_name_key", "lookup_price"])

    out = pd.DataFrame(
        {
            "lookup_code": work[code_col].map(normalize_code),
            "lookup_name_key": work[name_col].map(_compact_stock_name),
            "lookup_price": pd.to_numeric(work[price_col], errors="coerce"),
        }
    )
    return out.dropna(subset=["lookup_price"]).drop_duplicates(subset=["lookup_code"], keep="first")


def build_board_leader_candidates(
    board_df: pd.DataFrame,
    price_limit: float = 30.0,
    top_n: int = 10,
    price_snapshot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if board_df is None or board_df.empty:
        return pd.DataFrame()

    required = {"board_name", "change_pct", "leader", "leader_code"}
    if not required.issubset(set(board_df.columns)):
        return pd.DataFrame()

    work = board_df.copy()
    work["leader_code"] = work["leader_code"].map(normalize_code)
    work["leader"] = work["leader"].fillna("").astype(str).str.strip()
    work["leader_price"] = pd.to_numeric(work.get("leader_price"), errors="coerce")
    work["change_pct"] = pd.to_numeric(work["change_pct"], errors="coerce").fillna(0.0)
    work["leader_change_pct"] = pd.to_numeric(work.get("leader_change_pct"), errors="coerce").fillna(0.0)
    price_lookup = _prepare_price_lookup(price_snapshot)
    if not price_lookup.empty:
        code_lookup = price_lookup[["lookup_code", "lookup_price"]].drop_duplicates(subset=["lookup_code"], keep="first").rename(
            columns={"lookup_code": "leader_code", "lookup_price": "code_lookup_price"}
        )
        work = work.merge(code_lookup, on="leader_code", how="left")
        work["leader_price"] = work["leader_price"].where(work["leader_price"].notna(), work["code_lookup_price"])
        work = work.drop(columns=["code_lookup_price"])

        if work["leader_code"].eq("").any():
            work["leader_name_key"] = work["leader"].map(_compact_stock_name)
            name_lookup = price_lookup.drop_duplicates(subset=["lookup_name_key"], keep="first").rename(
                columns={
                    "lookup_code": "name_lookup_code",
                    "lookup_name_key": "leader_name_key",
                    "lookup_price": "name_lookup_price",
                }
            )
            work = work.merge(name_lookup, on="leader_name_key", how="left")
            missing_code_mask = work["leader_code"].eq("")
            work["leader_code"] = work["leader_code"].where(~missing_code_mask, work["name_lookup_code"].fillna(""))
            work["leader_price"] = work["leader_price"].where(work["leader_price"].notna(), work["name_lookup_price"])
            work = work.drop(columns=["leader_name_key", "name_lookup_code", "name_lookup_price"])
    work = work[
        work["leader_code"].map(_is_main_board_code)
        & work["leader"].ne("")
        & ~work["leader"].str.contains("ST", case=False, na=False)
        & ~work["leader"].str.contains("退", na=False)
        & work["leader_price"].between(0.01, price_limit)
    ].copy()
    if work.empty:
        return pd.DataFrame()

    action_bonus = work.get("board_action", pd.Series("", index=work.index)).map(
        {"可参与": 8.0, "只观察": 2.5, "回避": -8.0}
    ).fillna(0.0)
    work["adjusted_score"] = (
        58.0
        + work["change_pct"].clip(-5.0, 8.0) * 3.0
        + work["leader_change_pct"].clip(-5.0, 10.0) * 1.2
        + action_bonus
    )
    out = pd.DataFrame(
        {
            "code": work["leader_code"],
            "name": work["leader"].map(_compact_stock_name),
            "industry": work["board_name"].fillna("").astype(str),
            "close_price": work["leader_price"],
            "composite_score": work["adjusted_score"],
            "adjusted_score": work["adjusted_score"],
            "candidate_source": "board_hot_leader",
        }
    )
    return (
        out.sort_values(by="adjusted_score", ascending=False)
        .drop_duplicates(subset=["code"], keep="first")
        .head(top_n)
        .reset_index(drop=True)
    )


def _match_board(subject: object, board_strength: list[dict[str, Any]]) -> dict[str, Any] | None:
    clean = str(subject or "").strip()
    if not clean or clean.lower() == "nan":
        return None
    for board in board_strength:
        board_name = str(board.get("board_name", "")).strip()
        if clean == board_name:
            return board
    for board in board_strength:
        board_name = str(board.get("board_name", "")).strip()
        if board_name and (clean in board_name or board_name in clean):
            return board
    keyword_aliases = [
        (["京东方", "面板", "显示"], ["面板", "显示技术", "OLED"]),
        (["完美世界", "恺英", "世纪华通", "游戏"], ["游戏", "网络游戏", "手游"]),
        (["地产", "新城控股", "招商蛇口", "保利"], ["房地产", "房地产开发"]),
        (["太阳纸业", "造纸"], ["造纸"]),
        (["中天科技", "光通信", "光纤"], ["光通信", "5G", "通信设备"]),
        (["华电", "电力"], ["电力", "火电", "公用事业"]),
    ]
    for text_keywords, board_keywords in keyword_aliases:
        if not any(keyword in clean for keyword in text_keywords):
            continue
        for board in board_strength:
            board_name = str(board.get("board_name", "")).strip()
            if any(keyword in board_name for keyword in board_keywords):
                return board
    return None


def _board_match_text(row: pd.Series) -> str:
    board_name = str(row.get("matched_board", "") or "")
    if not board_name:
        return "板块判断不足"
    return (
        f"{board_name}{float(row.get('board_change_pct', 0.0)):+.2f}%，"
        f"{row.get('board_action', '只观察')}"
    )


def attach_board_context_to_candidates(
    candidates_df: pd.DataFrame,
    board_strength: list[dict[str, Any]],
) -> pd.DataFrame:
    if candidates_df is None or candidates_df.empty:
        return candidates_df.copy() if candidates_df is not None else pd.DataFrame()

    work = candidates_df.copy()
    board_rows: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        matched = _match_board(f"{row.get('name', '')} {row.get('industry', '')}", board_strength)
        board_rows.append(
            {
                "matched_board": matched.get("board_name", "") if matched else "",
                "board_action": matched.get("board_action", "板块判断不足") if matched else "板块判断不足",
                "board_change_pct": float(matched.get("change_pct", 0.0)) if matched else 0.0,
                "board_up_ratio": float(matched.get("up_ratio", 0.0)) if matched else 0.0,
            }
        )
    board_df = pd.DataFrame(board_rows, index=work.index)
    work = pd.concat([work, board_df], axis=1)

    base_score_col = "adjusted_score" if "adjusted_score" in work.columns else "composite_score"
    work[base_score_col] = pd.to_numeric(work[base_score_col], errors="coerce").fillna(0.0)
    action_bonus = work["board_action"].map({"可参与": 8.0, "只观察": 2.5, "回避": -8.0}).fillna(0.0)
    heat_bonus = pd.to_numeric(work["board_change_pct"], errors="coerce").fillna(0.0).clip(-3.0, 5.0)
    work["market_adjusted_score"] = work[base_score_col] + action_bonus + heat_bonus
    return work.sort_values(by=["market_adjusted_score", base_score_col], ascending=False).reset_index(drop=True)


def summarize_technical_profile(hist_df: pd.DataFrame) -> dict[str, Any]:
    if hist_df is None or hist_df.empty or "收盘" not in hist_df.columns:
        return {
            "technical_score": 50.0,
            "trend_score": 50.0,
            "momentum_score": 50.0,
            "rsi_score": 50.0,
            "volatility_score": 50.0,
            "setup_tags": "技术数据缺失",
            "technical_summary": "技术位数据缺失",
            "buy_point": float("nan"),
            "sell_point": float("nan"),
            "stop_loss_point": float("nan"),
            "technical_point_sources": "技术数据缺失，无法计算买卖点。",
            "technical_plan": "技术数据缺失，暂不设买入/卖出点。",
        }

    work = hist_df.copy()
    work["收盘"] = pd.to_numeric(work["收盘"], errors="coerce")
    work = work.dropna(subset=["收盘"])
    if len(work) < 20:
        return {
            "technical_score": 50.0,
            "trend_score": 50.0,
            "momentum_score": 50.0,
            "rsi_score": 50.0,
            "volatility_score": 50.0,
            "setup_tags": "样本不足",
            "technical_summary": "技术样本不足",
            "buy_point": float("nan"),
            "sell_point": float("nan"),
            "stop_loss_point": float("nan"),
            "technical_point_sources": "日线样本不足20日，无法计算有效买卖点。",
            "technical_plan": "技术样本不足，暂不设买入/卖出点。",
        }

    close = work["收盘"]
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    avg_gain = up.rolling(14).mean()
    avg_loss = down.rolling(14).mean()
    rs = avg_gain.div(avg_loss.replace(0.0, pd.NA))
    rsi14 = 100.0 - (100.0 / (1.0 + rs))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_dif = ema12 - ema26
    macd_dea = macd_dif.ewm(span=9, adjust=False).mean()
    daily_return = close.pct_change()
    volatility_20 = daily_return.rolling(20).std() * (252 ** 0.5) * 100.0

    last_close = float(close.iloc[-1])
    last_ma20 = float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else float("nan")
    last_ma60 = float(ma60.iloc[-1]) if pd.notna(ma60.iloc[-1]) else float("nan")
    last_rsi = float(rsi14.iloc[-1]) if pd.notna(rsi14.iloc[-1]) else float("nan")
    last_macd = float(macd_dif.iloc[-1]) if pd.notna(macd_dif.iloc[-1]) else float("nan")
    last_dea = float(macd_dea.iloc[-1]) if pd.notna(macd_dea.iloc[-1]) else float("nan")
    last_volatility = float(volatility_20.iloc[-1]) if pd.notna(volatility_20.iloc[-1]) else float("nan")
    high_20 = float(close.tail(20).max())
    low_20 = float(close.tail(20).min())
    breakout_20 = last_close >= high_20

    score = 50.0
    trend_score = 50.0
    momentum_score = 50.0
    rsi_score = 50.0
    volatility_score = 50.0
    parts: list[str] = []
    tags: list[str] = []
    if pd.notna(last_ma20) and pd.notna(last_ma60):
        if last_close >= last_ma20 and last_close >= last_ma60:
            score += 18.0
            trend_score = 88.0
            parts.append("高于20日和60日均线")
            tags.append("趋势多头")
        elif last_close >= last_ma20:
            score += 8.0
            trend_score = 68.0
            parts.append("站上20日均线")
            tags.append("短线转强")
        elif last_close < last_ma20 and last_close < last_ma60:
            score -= 18.0
            trend_score = 25.0
            parts.append("跌破60日均线")
            tags.append("趋势弱势")
        else:
            trend_score = 48.0
            parts.append("接近均线分水岭")
            tags.append("均线分歧")
    if pd.notna(last_macd) and pd.notna(last_dea):
        if last_macd >= last_dea:
            score += 10.0
            momentum_score = 70.0
            parts.append("MACD偏多")
            tags.append("动量偏多")
        else:
            score -= 8.0
            momentum_score = 32.0
            parts.append("MACD偏空")
            tags.append("动量偏空")
    if breakout_20:
        score += 10.0
        trend_score = max(trend_score, 92.0)
        parts.append("接近20日新高")
        tags.append("20日突破")
    if pd.notna(last_rsi):
        if last_rsi >= 75.0:
            score -= 6.0
            rsi_score = 38.0
            parts.append(f"RSI{last_rsi:.0f}偏热")
            tags.append("短线过热")
        elif last_rsi <= 30.0:
            score -= 8.0
            rsi_score = 32.0
            parts.append(f"RSI{last_rsi:.0f}偏弱")
            tags.append("弱势超跌")
        else:
            score += 4.0
            rsi_score = 68.0
            parts.append(f"RSI{last_rsi:.0f}中性")
            tags.append("RSI健康")
    if pd.notna(last_volatility):
        if last_volatility <= 25.0:
            volatility_score = 74.0
            tags.append("波动温和")
        elif last_volatility <= 45.0:
            volatility_score = 58.0
            tags.append("波动可控")
        else:
            volatility_score = 35.0
            score -= 5.0
            tags.append("波动偏大")

    support_candidates = [value for value in [last_ma20, last_ma60, low_20] if pd.notna(value) and value > 0]
    support_line = max([value for value in [last_ma20, last_ma60] if pd.notna(value) and value > 0], default=low_20)
    if pd.notna(last_ma20) and last_close >= last_ma20:
        buy_point = last_ma20
        buy_source = "MA20回踩确认"
    elif pd.notna(last_ma20):
        buy_point = last_ma20 * 1.01
        buy_source = "重新站上MA20确认"
    else:
        buy_point = last_close
        buy_source = "现价附近确认"

    sell_base = max(high_20, last_close)
    if pd.notna(last_rsi) and last_rsi >= 75.0:
        sell_point = max(last_close * 1.03, high_20)
        sell_source = "RSI偏热叠加20日高点"
    else:
        sell_point = max(sell_base * 1.03, buy_point * 1.05)
        sell_source = "20日高点突破/压力位"

    if support_candidates:
        stop_loss_point = min(support_candidates) * 0.98
        stop_source = "20日低点/MA60防线"
    else:
        stop_loss_point = buy_point * 0.92
        stop_source = "买入点下方8%风控"

    if stop_loss_point >= buy_point:
        stop_loss_point = buy_point * 0.94
        stop_source = f"{stop_source}，下修至买入点下方6%"
    if sell_point <= buy_point:
        sell_point = buy_point * 1.05
        sell_source = f"{sell_source}，上修至买入点上方5%"

    technical_point_sources = (
        f"买入点: {buy_source}；"
        f"卖出/减仓点: {sell_source}；"
        f"止损点: {stop_source}；"
        f"参考指标: MA20={last_ma20:.2f}，MA60={last_ma60:.2f}，20日高点={high_20:.2f}，20日低点={low_20:.2f}，RSI={last_rsi:.0f}，MACD DIF/DEA={last_macd:.2f}/{last_dea:.2f}"
    )
    technical_plan = (
        f"买入点{buy_point:.2f}；"
        f"卖出/减仓点{sell_point:.2f}；"
        f"止损点{stop_loss_point:.2f}；"
        f"来源: {buy_source} / {sell_source} / {stop_source}"
    )

    return {
        "technical_score": max(0.0, min(100.0, score)),
        "trend_score": max(0.0, min(100.0, trend_score)),
        "momentum_score": max(0.0, min(100.0, momentum_score)),
        "rsi_score": max(0.0, min(100.0, rsi_score)),
        "volatility_score": max(0.0, min(100.0, volatility_score)),
        "setup_tags": "、".join(dict.fromkeys(tags)) if tags else "技术中性",
        "technical_summary": "，".join(parts) if parts else "技术位中性",
        "buy_point": round(float(buy_point), 2),
        "sell_point": round(float(sell_point), 2),
        "stop_loss_point": round(float(stop_loss_point), 2),
        "technical_point_sources": technical_point_sources,
        "technical_plan": technical_plan,
    }


def _fetch_technical_profile(code: str, report_date: date) -> dict[str, Any]:
    start_date = (report_date - timedelta(days=160)).strftime("%Y%m%d")
    end_date = report_date.strftime("%Y%m%d")
    hist_df: pd.DataFrame | None = None
    try:
        hist_df = _call_with_retries(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
            retries=3,
            timeout_seconds=10.0,
        )
    except Exception:  # noqa: BLE001
        hist_df = None
    if hist_df is None or hist_df.empty:
        market_prefix = "sh" if str(code).startswith(("5", "6", "9")) else "sz"
        try:
            tx_df = _call_with_retries(
                ak.stock_zh_a_hist_tx,
                symbol=f"{market_prefix}{code}",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
                retries=2,
                timeout_seconds=10.0,
            )
            hist_df = tx_df.rename(columns={"close": "收盘"})
        except Exception:  # noqa: BLE001
            return {"technical_score": 50.0, "technical_summary": "技术位数据缺失"}
    return summarize_technical_profile(hist_df)


def attach_technical_context_to_candidates(
    candidates_df: pd.DataFrame,
    report_date: date,
    profile_fetcher=None,
    limit: int = 15,
) -> pd.DataFrame:
    if candidates_df is None or candidates_df.empty:
        return candidates_df.copy() if candidates_df is not None else pd.DataFrame()

    fetcher = profile_fetcher or _fetch_technical_profile
    work = candidates_df.copy().reset_index(drop=True)
    score_col = "market_adjusted_score" if "market_adjusted_score" in work.columns else "adjusted_score"
    if score_col not in work.columns:
        score_col = "composite_score"
    work[score_col] = pd.to_numeric(work[score_col], errors="coerce").fillna(0.0)
    work["technical_score"] = 50.0
    work["technical_summary"] = "技术位待补充"
    work["buy_point"] = float("nan")
    work["sell_point"] = float("nan")
    work["stop_loss_point"] = float("nan")
    work["technical_point_sources"] = "技术位待补充"
    work["technical_plan"] = "技术位待补充"

    target_index = work.sort_values(by=score_col, ascending=False).head(limit).index
    for idx in target_index:
        code = str(work.loc[idx, "code"])
        profile = fetcher(code, report_date)
        work.loc[idx, "technical_score"] = float(profile.get("technical_score", 50.0))
        work.loc[idx, "technical_summary"] = str(profile.get("technical_summary", "技术位数据缺失"))
        work.loc[idx, "buy_point"] = float(profile.get("buy_point", float("nan")))
        work.loc[idx, "sell_point"] = float(profile.get("sell_point", float("nan")))
        work.loc[idx, "stop_loss_point"] = float(profile.get("stop_loss_point", float("nan")))
        work.loc[idx, "technical_point_sources"] = str(profile.get("technical_point_sources", "技术位数据缺失"))
        work.loc[idx, "technical_plan"] = str(profile.get("technical_plan", "技术位数据缺失"))

    technical_bonus = (pd.to_numeric(work["technical_score"], errors="coerce").fillna(50.0) - 50.0) * 0.35
    work["market_adjusted_score"] = work[score_col] + technical_bonus
    return work.sort_values(by=["market_adjusted_score", "technical_score", score_col], ascending=False).reset_index(drop=True)


def refresh_candidate_realtime_prices(
    candidates_df: pd.DataFrame,
    price_fetcher=None,
    limit: int = 30,
) -> tuple[pd.DataFrame, str]:
    if candidates_df is None or candidates_df.empty:
        return candidates_df.copy() if candidates_df is not None else pd.DataFrame(), "unavailable"

    work = candidates_df.copy().reset_index(drop=True)
    if "close_price" not in work.columns:
        work["close_price"] = float("nan")
    work["snapshot_close_price"] = pd.to_numeric(work["close_price"], errors="coerce")
    work["realtime_price_source"] = "stale_snapshot"

    score_col = "market_adjusted_score" if "market_adjusted_score" in work.columns else "adjusted_score"
    if score_col not in work.columns:
        score_col = "composite_score" if "composite_score" in work.columns else ""
    target = work.copy()
    if score_col:
        target[score_col] = pd.to_numeric(target[score_col], errors="coerce").fillna(0.0)
        target = target.sort_values(by=score_col, ascending=False)
    target_codes = target.head(limit)["code"].astype(str).map(normalize_code).dropna().unique().tolist()
    if not target_codes:
        return work, "unavailable"

    fetcher = price_fetcher or fetch_price_snapshot
    try:
        quote_df = fetcher(codes=target_codes)
    except Exception:  # noqa: BLE001
        return work, "unavailable"
    if quote_df is None or quote_df.empty or "code" not in quote_df.columns:
        return work, "unavailable"

    quotes = quote_df.copy()
    quotes["code"] = quotes["code"].map(normalize_code)
    if "last_price" not in quotes.columns:
        quotes["last_price"] = float("nan")
    if "close_price" not in quotes.columns:
        quotes["close_price"] = float("nan")
    quotes["last_price"] = pd.to_numeric(quotes["last_price"], errors="coerce")
    quotes["close_price"] = pd.to_numeric(quotes["close_price"], errors="coerce")
    quotes["fresh_candidate_price"] = quotes["last_price"].where(quotes["last_price"] > 0, quotes["close_price"])
    quotes = quotes[["code", "fresh_candidate_price"]].dropna(subset=["fresh_candidate_price"])
    quotes = quotes.drop_duplicates(subset=["code"], keep="first")
    if quotes.empty:
        return work, "unavailable"

    work = work.merge(quotes, on="code", how="left")
    refreshed = work["fresh_candidate_price"].notna() & (work["fresh_candidate_price"] > 0)
    work["close_price"] = work["fresh_candidate_price"].where(refreshed, work["close_price"])
    work["realtime_price_source"] = work["realtime_price_source"].where(~refreshed, "live_quote")
    work = work.drop(columns=["fresh_candidate_price"])
    return work, "live_quote" if refreshed.any() else "unavailable"


def _fetch_bid_ask_snapshot(code: str) -> pd.DataFrame:
    return ak.stock_bid_ask_em(symbol=code)


def _bid_ask_items_to_dict(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty or "item" not in df.columns or "value" not in df.columns:
        return {}
    return {str(row["item"]).strip(): row["value"] for _, row in df.iterrows()}


def _classify_candidate_actionability(
    *,
    latest_price: float,
    day_change_pct: float,
    sell_1: float,
    buy_1: float,
    estimated_cash_amount: float | None,
) -> tuple[str, int, str]:
    min_lot_cost = latest_price * 100.0 if latest_price > 0 else float("nan")
    cash_known = estimated_cash_amount is not None and not pd.isna(estimated_cash_amount)
    cash_sufficient = not cash_known or (not pd.isna(min_lot_cost) and float(estimated_cash_amount) >= min_lot_cost)
    sell_missing = pd.isna(sell_1) or sell_1 <= 0
    limit_up_sealed = latest_price > 0 and day_change_pct >= 9.7 and sell_missing and buy_1 > 0

    cash_note = ""
    if cash_known and not pd.isna(min_lot_cost):
        cash_note = f"一手约{min_lot_cost:.0f}元，现金约{float(estimated_cash_amount):.0f}元。"

    if latest_price <= 0 or pd.isna(latest_price):
        return "价格缺失", 90, "实时价格缺失，不能作为盘中买入候选。"
    if limit_up_sealed:
        return "涨停封板不可追", 80, f"涨停且卖一缺失，实际买入需排队，成交往往意味着炸板风险。{cash_note}".strip()
    if not cash_sufficient:
        return "现金不足", 70, f"当前现金不足以买入A股最低一手。{cash_note}".strip()
    if day_change_pct >= 8.0:
        return "高位只观察", 40, f"涨幅已接近涨停区，追高胜率下降，等回落或次日确认。{cash_note}".strip()
    if sell_missing:
        return "盘口不完整仅观察", 50, f"盘口卖盘数据缺失，暂不作为可执行买入。{cash_note}".strip()
    return "可执行观察", 0, f"盘口正常且现金满足一手，仍需结合板块延续和分时承接分批处理。{cash_note}".strip()


def _ensure_candidate_actionability_columns(candidates_df: pd.DataFrame) -> pd.DataFrame:
    work = candidates_df.copy()
    defaults = {
        "latest_price": work["close_price"] if "close_price" in work.columns else float("nan"),
        "day_change_pct_live": float("nan"),
        "buy_1": float("nan"),
        "sell_1": float("nan"),
        "min_lot_cost": float("nan"),
        "cash_sufficient": False,
        "is_limit_up_sealed": False,
        "actionability": "盘口待核查",
        "actionability_rank": 95,
        "action_note": "未完成实时盘口核查，只能作为观察池，不能直接视为买入建议。",
    }
    for column, default in defaults.items():
        if column not in work.columns:
            work[column] = default
    return work


def attach_candidate_actionability(
    candidates_df: pd.DataFrame,
    estimated_cash_amount: float | None = None,
    bid_ask_fetcher=None,
    limit: int = 20,
) -> tuple[pd.DataFrame, str]:
    if candidates_df is None or candidates_df.empty:
        return candidates_df.copy() if candidates_df is not None else pd.DataFrame(), "unavailable"

    work = _ensure_candidate_actionability_columns(candidates_df)
    fetcher = bid_ask_fetcher or _fetch_bid_ask_snapshot
    score_col = "market_adjusted_score" if "market_adjusted_score" in work.columns else "adjusted_score"
    if score_col not in work.columns:
        score_col = "composite_score" if "composite_score" in work.columns else ""
    target = work.copy()
    if score_col:
        target[score_col] = pd.to_numeric(target[score_col], errors="coerce").fillna(0.0)
        target = target.sort_values(by=score_col, ascending=False)

    refreshed_any = False
    for idx in target.head(limit).index:
        code = normalize_code(work.loc[idx, "code"])
        if not code:
            continue
        try:
            quote = _bid_ask_items_to_dict(fetcher(code))
        except Exception:  # noqa: BLE001
            continue
        if not quote:
            continue

        latest_price = parse_numeric(quote.get("最新"))
        if pd.isna(latest_price) or latest_price <= 0:
            latest_price = parse_numeric(work.loc[idx, "close_price"]) if "close_price" in work.columns else float("nan")
        day_change_pct = parse_numeric(quote.get("涨幅"))
        buy_1 = parse_numeric(quote.get("buy_1"))
        sell_1 = parse_numeric(quote.get("sell_1"))
        actionability, rank, note = _classify_candidate_actionability(
            latest_price=latest_price,
            day_change_pct=day_change_pct,
            sell_1=sell_1,
            buy_1=buy_1,
            estimated_cash_amount=estimated_cash_amount,
        )
        min_lot_cost = latest_price * 100.0 if latest_price > 0 else float("nan")
        cash_known = estimated_cash_amount is not None and not pd.isna(estimated_cash_amount)
        cash_sufficient = bool(not cash_known or (not pd.isna(min_lot_cost) and float(estimated_cash_amount) >= min_lot_cost))

        work.loc[idx, "latest_price"] = latest_price
        work.loc[idx, "close_price"] = latest_price if latest_price > 0 else work.loc[idx, "close_price"]
        work.loc[idx, "day_change_pct_live"] = day_change_pct
        work.loc[idx, "buy_1"] = buy_1
        work.loc[idx, "sell_1"] = sell_1
        work.loc[idx, "min_lot_cost"] = min_lot_cost
        work.loc[idx, "cash_sufficient"] = cash_sufficient
        work.loc[idx, "is_limit_up_sealed"] = actionability == "涨停封板不可追"
        work.loc[idx, "actionability"] = actionability
        work.loc[idx, "actionability_rank"] = rank
        work.loc[idx, "action_note"] = note
        refreshed_any = True

    return work, "live_bid_ask" if refreshed_any else "unavailable"


def load_fallback_candidates_snapshot(
    path: str | Path,
    price_snapshot: pd.DataFrame | None = None,
    top_n: int = 10,
) -> pd.DataFrame:
    fallback_path = Path(path)
    if not fallback_path.exists():
        return pd.DataFrame()
    work = pd.read_csv(fallback_path, dtype={"code": str})
    if work.empty or "code" not in work.columns:
        return pd.DataFrame()

    work = work.copy()
    work["code"] = work["code"].map(normalize_code)
    work = work[work["code"].astype(str).str.len() == 6]
    if work.empty:
        return pd.DataFrame()
    if "industry" in work.columns:
        work["industry"] = work["industry"].fillna("").astype(str)
        work["industry"] = work["industry"].where(work["industry"].str.lower().ne("nan"), "")

    for col in [
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
    ]:
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    if price_snapshot is not None and not price_snapshot.empty:
        px = price_snapshot[["code", "close_price"]].drop_duplicates(subset=["code"], keep="first").copy()
        px["code"] = px["code"].map(normalize_code)
        work = work.merge(px.rename(columns={"close_price": "live_close_price"}), on="code", how="left")
        if "live_close_price" in work.columns:
            work["close_price"] = work["live_close_price"].where(work["live_close_price"].notna(), work["close_price"])
            work = work.drop(columns=["live_close_price"])

    return work.head(top_n).reset_index(drop=True)


def normalize_index_snapshot(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["代码", "名称", "最新价", "涨跌幅"])
    work = df.copy()
    if source == "em":
        def _normalize_em_code(code: object) -> str:
            text = str(code).strip()
            if text == "000001":
                return "sh000001"
            if text == "000300":
                return "sh000300"
            if text in {"399001", "399006"}:
                return f"sz{text}"
            return text

        work["代码"] = work["代码"].map(_normalize_em_code)
    else:
        work["代码"] = work["代码"].astype(str)
    return work[["代码", "名称", "最新价", "涨跌幅"]].copy()


def parse_positions_text(raw_text: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for line in str(raw_text or "").splitlines():
        clean = line.strip()
        if not clean:
            continue
        parts = [part.strip() for part in clean.split("|")]
        if len(parts) not in {4, 6}:
            raise ValueError(
                f"持仓行格式错误，需为 `代码 | 名称 | 仓位% | 成本价` 或 `代码 | 名称 | 仓位% | 成本价 | 持仓股数 | 可卖股数`: {clean}"
            )
        code = normalize_code(parts[0])
        if not code:
            raise ValueError(f"无法识别股票代码: {parts[0]}")
        share_count = float(parse_numeric(parts[4])) if len(parts) == 6 else float("nan")
        available_count = float(parse_numeric(parts[5])) if len(parts) == 6 else float("nan")
        rows.append(
            {
                "code": code,
                "name": parts[1],
                "position_pct": _parse_position_pct(parts[2]),
                "cost_price": float(parse_numeric(parts[3])),
                "share_count": share_count,
                "available_count": available_count,
            }
        )
    out = pd.DataFrame(rows, columns=POSITION_COLUMNS)
    if out.empty:
        raise ValueError(
            "未解析到任何持仓，请按 `代码 | 名称 | 仓位% | 成本价` 或 `代码 | 名称 | 仓位% | 成本价 | 持仓股数 | 可卖股数` 提供。"
        )
    return out


def save_latest_positions_snapshot(positions_df: pd.DataFrame, path: str | Path) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = positions_df.reindex(columns=POSITION_COLUMNS).copy()
    snapshot.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


def load_latest_positions_snapshot(path: str | Path) -> pd.DataFrame:
    return load_positions_file(path)


def save_market_snapshot_cache(snapshot_df: pd.DataFrame, path: str | Path, generated_at: str | None = None) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = snapshot_df.copy()
    work["_cache_generated_at"] = generated_at or now_ts()
    work.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


def load_cached_market_snapshot(
    path: str | Path,
    max_age_minutes: int,
    now_dt: datetime | None = None,
) -> pd.DataFrame:
    cache_path = Path(path)
    if not cache_path.exists():
        return pd.DataFrame()
    work = pd.read_csv(cache_path, dtype=str)
    if work.empty or "_cache_generated_at" not in work.columns:
        return pd.DataFrame()
    generated_at = str(work.iloc[0]["_cache_generated_at"]).strip()
    try:
        generated_dt = datetime.strptime(generated_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return pd.DataFrame()
    current_dt = now_dt or datetime.now()
    if current_dt - generated_dt > timedelta(minutes=max_age_minutes):
        return pd.DataFrame()
    return work


def load_any_cached_market_snapshot(path: str | Path) -> pd.DataFrame:
    cache_path = Path(path)
    if not cache_path.exists():
        return pd.DataFrame()
    work = pd.read_csv(cache_path, dtype=str)
    if work.empty:
        return pd.DataFrame()
    return work


def resolve_analysis_paths(
    output_dir: Path,
    latest_positions_file: str | Path | None,
    market_snapshot_cache_file: str | Path | None,
) -> tuple[Path, Path]:
    latest_path = Path(latest_positions_file) if latest_positions_file else output_dir / "latest_positions.csv"
    cache_path = (
        Path(market_snapshot_cache_file)
        if market_snapshot_cache_file
        else output_dir / "market_snapshot_cache.csv"
    )
    return latest_path, cache_path


def load_positions_file(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"持仓文件不存在: {file_path}")

    if file_path.suffix.lower() in {".txt", ".md"}:
        return parse_positions_text(file_path.read_text(encoding="utf-8"))

    df = pd.read_csv(file_path, dtype=str)
    rename_map = {
        "股票代码": "code",
        "证券代码": "code",
        "代码": "code",
        "股票名称": "name",
        "证券名称": "name",
        "名称": "name",
        "仓位": "position_pct",
        "仓位%": "position_pct",
        "position": "position_pct",
        "position_pct": "position_pct",
        "成本": "cost_price",
        "成本价": "cost_price",
        "cost": "cost_price",
        "cost_price": "cost_price",
        "持仓": "share_count",
        "持仓股数": "share_count",
        "持仓数量": "share_count",
        "share_count": "share_count",
        "可用": "available_count",
        "可卖": "available_count",
        "可卖股数": "available_count",
        "available_count": "available_count",
    }
    df = df.rename(columns=rename_map)
    missing = [col for col in POSITION_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"持仓文件缺少字段: {missing}")
    for optional_col in {"share_count", "available_count"}:
        if optional_col not in df.columns:
            df[optional_col] = float("nan")
    df = df[POSITION_COLUMNS].copy()
    df["code"] = df["code"].map(normalize_code)
    df["name"] = df["name"].astype(str)
    df["position_pct"] = df["position_pct"].map(_parse_position_pct)
    df["cost_price"] = df["cost_price"].map(parse_numeric).astype(float)
    df["share_count"] = pd.to_numeric(df["share_count"], errors="coerce")
    df["available_count"] = pd.to_numeric(df["available_count"], errors="coerce")
    return df


def _research_metrics_for_holdings(
    positions_df: pd.DataFrame,
    raw_records: pd.DataFrame,
    forecast_metrics: pd.DataFrame,
    target_metrics: pd.DataFrame,
    composite_metrics: pd.DataFrame,
) -> pd.DataFrame:
    codes = set(positions_df["code"].astype(str))
    held_raw = raw_records[raw_records["code"].isin(codes)].copy()
    held_raw = attach_rating_score(held_raw)

    metrics = positions_df[["code", "name"]].drop_duplicates().copy()
    metrics["industry"] = ""

    if not held_raw.empty:
        industry_map = (
            held_raw.assign(industry=held_raw["industry"].fillna("").astype(str).str.strip())
            .groupby("code", as_index=True)["industry"]
            .agg(lambda s: s[s.ne("")].value_counts().idxmax() if any(s.ne("")) else "")
        )
        metrics = metrics.merge(industry_map.rename("industry"), left_on="code", right_index=True, how="left")
        metrics["industry"] = metrics["industry_y"].fillna(metrics["industry_x"]).fillna("")
        metrics = metrics.drop(columns=["industry_x", "industry_y"])

        detailed_rows = held_raw[
            (~held_raw["institution"].str.contains("_AGGREGATE", na=False))
            & (~held_raw["source"].isin({"eastmoney_profit_forecast"}))
        ]
        inst = detailed_rows.groupby("code", as_index=True)["institution"].nunique().rename("institution_count")
        source_group = detailed_rows.groupby("code", as_index=True)["source"].nunique().rename("source_group_count")
        detail_rating = held_raw.groupby("code", as_index=True)["rating_score"].mean().rename("detail_rating_score")
        metrics = metrics.merge(inst, left_on="code", right_index=True, how="left")
        metrics = metrics.merge(source_group, left_on="code", right_index=True, how="left")
        metrics = metrics.merge(detail_rating, left_on="code", right_index=True, how="left")
    else:
        metrics["institution_count"] = 0
        metrics["source_group_count"] = 0
        metrics["detail_rating_score"] = float("nan")

    if not forecast_metrics.empty:
        metrics = metrics.merge(
            forecast_metrics[["code", "forecast_rating_score"]],
            on="code",
            how="left",
        )
    else:
        metrics["forecast_rating_score"] = float("nan")

    if not composite_metrics.empty:
        metrics = metrics.merge(
            composite_metrics[["code", "sina_comp_rating_score"]],
            on="code",
            how="left",
        )
    else:
        metrics["sina_comp_rating_score"] = float("nan")

    if not target_metrics.empty:
        metrics = metrics.merge(
            target_metrics[["code", "avg_target_upside"]],
            on="code",
            how="left",
        )
    else:
        metrics["avg_target_upside"] = float("nan")

    metrics["institution_count"] = metrics["institution_count"].fillna(0).astype(int)
    metrics["source_group_count"] = metrics["source_group_count"].fillna(0).astype(int)
    metrics["target_upside_pct"] = metrics["avg_target_upside"].fillna(0.0) * 100.0

    rating_cols = ["detail_rating_score", "forecast_rating_score", "sina_comp_rating_score"]
    metrics["research_score"] = metrics[rating_cols].mean(axis=1, skipna=True).fillna(50.0)
    metrics["research_score"] = metrics["research_score"].clip(lower=0.0, upper=100.0)
    return metrics[
        [
            "code",
            "name",
            "industry",
            "institution_count",
            "source_group_count",
            "target_upside_pct",
            "research_score",
        ]
    ]


def _fetch_recent_news(code: str, limit: int = 3) -> list[dict[str, str]]:
    try:
        df = ak.stock_news_em(symbol=code)
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    work = df.head(limit).copy()
    news: list[dict[str, str]] = []
    for _, row in work.iterrows():
        news.append(
            {
                "title": str(row.get("新闻标题", "")).strip(),
                "published_at": str(row.get("发布时间", "")).strip(),
                "source": str(row.get("文章来源", "")).strip(),
                "url": str(row.get("新闻链接", "")).strip(),
            }
        )
    return news


def _fetch_recent_notices(code: str, report_date: date, days: int = 3, limit: int = 3) -> list[dict[str, str]]:
    frames: list[pd.DataFrame] = []
    for offset in range(days):
        day = (report_date - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            temp = ak.stock_notice_report(symbol="全部", date=day)
        except Exception:  # noqa: BLE001
            continue
        if temp is None or temp.empty:
            continue
        frames.append(temp[temp["代码"].astype(str) == code].copy())
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["公告标题", "公告日期"]).head(limit)
    notices: list[dict[str, str]] = []
    for _, row in df.iterrows():
        notices.append(
            {
                "title": str(row.get("公告标题", "")).strip(),
                "published_at": str(row.get("公告日期", "")).strip(),
                "category": str(row.get("公告类型", "")).strip(),
                "url": str(row.get("网址", "")).strip(),
            }
        )
    return notices


def _fetch_fundamental_summary(code: str) -> dict[str, Any]:
    try:
        abstract_df = ak.stock_financial_abstract(symbol=code)
    except Exception:  # noqa: BLE001
        abstract_df = pd.DataFrame()
    abstract_summary = summarize_fundamentals_from_abstract(abstract_df)
    if abstract_summary["summary"] != "基本面数据缺失":
        return abstract_summary

    try:
        indicator_df = ak.stock_financial_analysis_indicator(symbol=code, start_year=str(max(date.today().year - 3, 2000)))
    except Exception:  # noqa: BLE001
        return abstract_summary
    indicator_summary = summarize_fundamentals_from_indicator(indicator_df)
    if indicator_summary["summary"] != "基本面数据缺失":
        return indicator_summary
    return abstract_summary


def _fetch_index_snapshot() -> pd.DataFrame:
    try:
        df = ak.stock_zh_index_spot_sina()
    except Exception:  # noqa: BLE001
        df = pd.DataFrame()
    if df is not None and not df.empty:
        work = normalize_index_snapshot(df, source="sina")
        work = work[work["代码"].isin(TRACKED_INDEX_CODES.keys())].copy()
        if not work.empty:
            return work

    try:
        em_df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    except Exception:  # noqa: BLE001
        em_df = pd.DataFrame()
    if em_df is not None and not em_df.empty:
        work = normalize_index_snapshot(em_df, source="em")
        work = work[work["代码"].isin(TRACKED_INDEX_CODES.keys())].copy()
        if not work.empty:
            return work

    fallback_rows: list[dict[str, Any]] = []
    for code in TRACKED_INDEX_CODES:
        hist_symbol = code
        try:
            hist = ak.stock_zh_index_daily(symbol=hist_symbol)
        except Exception:  # noqa: BLE001
            continue
        if hist is None or hist.empty:
            continue
        row = hist.iloc[-1]
        fallback_rows.append(
            {
                "代码": code,
                "名称": TRACKED_INDEX_CODES[code],
                "最新价": float(row.get("close", 0.0)),
                "涨跌幅": 0.0,
            }
        )
    return pd.DataFrame(fallback_rows, columns=["代码", "名称", "最新价", "涨跌幅"])


def _build_market_overview(report_date: date, breadth_df: pd.DataFrame) -> dict[str, Any]:
    index_df = _fetch_index_snapshot()
    return summarize_market_overview(index_df=index_df, breadth_df=breadth_df, report_date=report_date)


def _load_or_fetch_candidate_snapshot(cfg: PortfolioAnalysisConfig) -> tuple[pd.DataFrame, str]:
    def _normalize_snapshot_numeric(df: pd.DataFrame) -> pd.DataFrame:
        work = df.copy()
        for col in ["close_price", "last_price", "prev_close"]:
            if col in work.columns:
                work[col] = pd.to_numeric(work[col], errors="coerce")
        return work

    def _is_usable_snapshot(df: pd.DataFrame) -> bool:
        if df is None or df.empty or "code" not in df.columns:
            return False
        code_count = int(df["code"].astype(str).nunique())
        last_count = int(pd.to_numeric(df.get("last_price"), errors="coerce").notna().sum()) if "last_price" in df.columns else 0
        prev_count = int(pd.to_numeric(df.get("prev_close"), errors="coerce").notna().sum()) if "prev_close" in df.columns else 0
        return code_count >= 1000 and min(last_count, prev_count) >= 500

    cached = load_cached_market_snapshot(
        cfg.market_snapshot_cache_path,
        max_age_minutes=cfg.market_snapshot_cache_minutes,
    )
    if not cached.empty:
        cached = _normalize_snapshot_numeric(cached)
        return cached, "cache"

    fresh = fetch_price_snapshot(codes=None)
    if _is_usable_snapshot(fresh):
        save_market_snapshot_cache(fresh, cfg.market_snapshot_cache_path)
        return fresh, "live"

    stale = load_any_cached_market_snapshot(cfg.market_snapshot_cache_path)
    if not stale.empty:
        stale = _normalize_snapshot_numeric(stale)
        return stale, "stale_cache"
    return fresh, "live"


def _fetch_technical_bias(code: str, report_date: date) -> str:
    return str(_fetch_technical_profile(code, report_date).get("technical_summary", "技术位数据缺失"))


def _build_market_snapshot(
    positions_df: pd.DataFrame,
    report_date: date,
    raw_records: pd.DataFrame,
    forecast_metrics: pd.DataFrame,
    target_metrics: pd.DataFrame,
    composite_metrics: pd.DataFrame,
    important_holding_count: int,
    notice_lookback_days: int,
    fallback_price_snapshot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    codes = positions_df["code"].astype(str).tolist()
    price_df = fetch_price_snapshot(codes=codes)
    if (price_df is None or price_df.empty) and fallback_price_snapshot is not None and not fallback_price_snapshot.empty:
        price_df = fallback_price_snapshot[fallback_price_snapshot["code"].isin(codes)].copy()
    elif fallback_price_snapshot is not None and not fallback_price_snapshot.empty:
        missing_codes = [code for code in codes if code not in set(price_df["code"].astype(str))]
        if missing_codes:
            supplement = fallback_price_snapshot[fallback_price_snapshot["code"].isin(missing_codes)].copy()
            if not supplement.empty:
                price_df = pd.concat([price_df, supplement], ignore_index=True).drop_duplicates(subset=["code"], keep="first")
    price_df = price_df.rename(columns={"close_price": "current_price"}).copy()
    if price_df.empty:
        return _empty_market_df()
    last_price = pd.to_numeric(price_df["last_price"], errors="coerce")
    last_price = last_price.where(last_price > 0)
    prev_close = pd.to_numeric(price_df["prev_close"], errors="coerce")
    valid_prev_close = prev_close.where(prev_close > 0)
    price_df["day_change_pct"] = (
        (last_price - valid_prev_close).div(valid_prev_close) * 100.0
    ).fillna(0.0)
    research_df = _research_metrics_for_holdings(
        positions_df=positions_df,
        raw_records=raw_records,
        forecast_metrics=forecast_metrics,
        target_metrics=target_metrics,
        composite_metrics=composite_metrics,
    )
    market_df = price_df.merge(
        research_df.drop(columns=["name"]),
        on="code",
        how="left",
    )
    market_df["industry"] = market_df["industry"].fillna("")
    market_df["research_score"] = market_df["research_score"].fillna(50.0)
    market_df["institution_count"] = market_df["institution_count"].fillna(0).astype(int)
    market_df["source_group_count"] = market_df["source_group_count"].fillna(0).astype(int)
    market_df["target_upside_pct"] = market_df["target_upside_pct"].fillna(0.0)
    market_df["technical_score"] = 50.0
    market_df["trend_score"] = 50.0
    market_df["momentum_score"] = 50.0
    market_df["rsi_score"] = 50.0
    market_df["volatility_score"] = 50.0
    market_df["setup_tags"] = "技术待补充"
    market_df["technical_bias"] = "技术位数据缺失"
    market_df["buy_point"] = float("nan")
    market_df["sell_point"] = float("nan")
    market_df["stop_loss_point"] = float("nan")
    market_df["technical_point_sources"] = "技术位数据缺失"
    market_df["technical_plan"] = "技术位数据缺失"
    market_df["news_summary"] = ""
    market_df["notice_summary"] = ""
    market_df["fundamental_score"] = 50.0
    market_df["fundamental_summary"] = "基本面数据缺失"

    important_codes = (
        positions_df.sort_values(by="position_pct", ascending=False)
        .head(important_holding_count)["code"]
        .astype(str)
        .tolist()
    )
    for code in important_codes:
        technical = _fetch_technical_profile(code, report_date)
        market_df.loc[market_df["code"] == code, "technical_bias"] = str(
            technical.get("technical_summary", "技术位数据缺失")
        )
        for col in ["technical_score", "trend_score", "momentum_score", "rsi_score", "volatility_score"]:
            market_df.loc[market_df["code"] == code, col] = float(technical.get(col, 50.0))
        market_df.loc[market_df["code"] == code, "setup_tags"] = str(technical.get("setup_tags", "技术待补充"))
        for col in ["buy_point", "sell_point", "stop_loss_point"]:
            market_df.loc[market_df["code"] == code, col] = float(technical.get(col, float("nan")))
        market_df.loc[market_df["code"] == code, "technical_point_sources"] = str(
            technical.get("technical_point_sources", "技术位数据缺失")
        )
        market_df.loc[market_df["code"] == code, "technical_plan"] = str(
            technical.get("technical_plan", "技术位数据缺失")
        )
        news_items = _fetch_recent_news(code)
        notice_items = _fetch_recent_notices(code, report_date=report_date, days=notice_lookback_days)
        fundamental = _fetch_fundamental_summary(code)
        news_text = "；".join(item["title"] for item in news_items[:2]) if news_items else "暂无显著新闻"
        notice_text = "；".join(item["title"] for item in notice_items[:2]) if notice_items else "暂无近期公告"
        market_df.loc[market_df["code"] == code, "news_summary"] = news_text
        market_df.loc[market_df["code"] == code, "notice_summary"] = notice_text
        market_df.loc[market_df["code"] == code, "fundamental_score"] = float(fundamental["score"])
        market_df.loc[market_df["code"] == code, "fundamental_summary"] = str(fundamental["summary"])
    return market_df[
        [
            "code",
            "name",
            "current_price",
            "day_change_pct",
            "industry",
            "research_score",
            "fundamental_score",
            "fundamental_summary",
            "institution_count",
            "source_group_count",
            "target_upside_pct",
            "technical_score",
            "trend_score",
            "momentum_score",
            "rsi_score",
            "volatility_score",
            "setup_tags",
            "technical_bias",
            "buy_point",
            "sell_point",
            "stop_loss_point",
            "technical_point_sources",
            "technical_plan",
            "news_summary",
            "notice_summary",
        ]
    ]


def _portfolio_tag(diagnosis: str) -> str:
    mapping = {
        "可持有": "核心持仓",
        "可减仓": "减仓候选",
        "可加仓": "观察加仓",
        "应替换": "清仓候选",
    }
    return mapping.get(diagnosis, "核心持仓")


def _target_position_text(diagnosis: str, current_position: float) -> str:
    if diagnosis == "应替换":
        return "0%-2%"
    if diagnosis == "可减仓":
        low = max(4.0, round(current_position * 0.55, 1))
        high = max(low, round(current_position * 0.75, 1))
        return f"{low:.1f}%-{high:.1f}%"
    if diagnosis == "可加仓":
        low = round(current_position, 1)
        high = min(20.0, round(current_position + 5.0, 1))
        return f"{low:.1f}%-{high:.1f}%"
    low = max(0.0, round(current_position - 2.0, 1))
    high = round(current_position + 2.0, 1)
    return f"{low:.1f}%-{high:.1f}%"


def _diagnosis_for_row(row: pd.Series, duplicate_industries: set[str]) -> str:
    position_pct = float(row.get("position_pct", 0.0))
    pnl_pct = float(row.get("pnl_pct", 0.0))
    research_score = float(row.get("research_score", 50.0))
    fundamental_score = float(row.get("fundamental_score", 50.0))
    industry = str(row.get("industry", ""))

    if min(research_score, fundamental_score) < 45.0 or (pnl_pct <= -12.0 and position_pct >= 8.0):
        return "应替换"
    if position_pct >= 25.0 or (industry in duplicate_industries and position_pct >= 15.0 and research_score < 70.0):
        return "可减仓"
    if research_score >= 75.0 and fundamental_score >= 65.0 and position_pct <= 12.0 and pnl_pct > -8.0:
        return "可加仓"
    return "可持有"


def _adjust_diagnosis_for_market(row: pd.Series, market_context: dict[str, Any]) -> str:
    diagnosis = str(row.get("diagnosis", "可持有"))
    board_action = str(row.get("board_action", "板块判断不足"))
    market_temperature = str(market_context.get("market_temperature", "震荡"))
    position_pct = float(row.get("position_pct", 0.0))

    if diagnosis == "可加仓" and market_temperature in {"弱势", "极弱"}:
        return "可持有"
    if diagnosis == "可加仓" and board_action == "回避":
        return "可持有"
    if diagnosis != "应替换" and board_action == "回避" and position_pct >= 15.0:
        return "可减仓"
    return diagnosis


def _build_reason(row: pd.Series) -> str:
    return (
        f"交易分{row.get('trade_setup_score', 50.0):.1f}（{row.get('trade_setup_note', '证据链待补充')}），"
        f"研报强度{row['research_score']:.1f}，当前盈亏{row['pnl_pct']:.1f}%，"
        f"机构覆盖{int(row['institution_count'])}家，板块: {row.get('board_match_text', '板块判断不足')}，"
        f"基本面: {row['fundamental_summary']}，技术位: {row['technical_bias']}。"
    )


def _score_trade_setup(row: pd.Series, market_context: dict[str, Any]) -> tuple[float, str]:
    technical_score = float(row.get("technical_score", 50.0))
    research_score = float(row.get("research_score", 50.0))
    fundamental_score = float(row.get("fundamental_score", 50.0))
    position_pct = float(row.get("position_pct", 0.0))
    board_action = str(row.get("board_action", "板块判断不足"))
    market_temperature = str(market_context.get("market_temperature", "震荡"))
    available_count = row.get("available_count")
    share_count = row.get("share_count")

    board_score_map = {"可参与": 82.0, "只观察": 58.0, "回避": 28.0, "板块判断不足": 45.0}
    market_score_map = {"强势": 78.0, "震荡": 62.0, "弱势": 42.0, "极弱": 25.0}
    board_score = board_score_map.get(board_action, 45.0)
    market_score = market_score_map.get(market_temperature, 60.0)

    score = (
        technical_score * 0.28
        + board_score * 0.24
        + fundamental_score * 0.18
        + research_score * 0.15
        + market_score * 0.15
    )
    penalties: list[str] = []
    positives: list[str] = []

    if position_pct >= 35.0:
        score -= 16.0
        penalties.append("仓位过重")
    elif position_pct >= 20.0:
        score -= 8.0
        penalties.append("仓位偏重")
    else:
        positives.append("仓位灵活")

    if pd.notna(share_count) and float(share_count) > 0 and pd.notna(available_count):
        if float(available_count) <= 0:
            score -= 8.0
            penalties.append("今日不可卖")
        elif float(available_count) >= float(share_count):
            positives.append("流动性可执行")

    if technical_score >= 75.0:
        positives.append("技术强势")
    elif technical_score <= 40.0:
        penalties.append("技术弱势")
    if board_action == "可参与":
        positives.append("板块配合")
    elif board_action == "回避":
        penalties.append("板块回避")

    note_parts = positives[:3] + penalties[:3]
    return max(0.0, min(100.0, score)), "、".join(note_parts) if note_parts else "中性观察"


def _available_sell_text(row: pd.Series) -> str:
    share_count = row.get("share_count")
    available_count = row.get("available_count")
    if pd.isna(share_count) or share_count <= 0:
        return "未提供股数"
    if pd.isna(available_count):
        return f"持仓{int(share_count)}股，可卖未知"
    ratio = float(available_count) / float(share_count) * 100.0 if share_count else 0.0
    if available_count <= 0:
        return f"持仓{int(share_count)}股，今日可卖0股（疑似T+1锁定）"
    return f"持仓{int(share_count)}股，今日可卖{int(available_count)}股（{ratio:.0f}%）"


def _rebalance_execution_text(row: pd.Series) -> str:
    share_count = row.get("share_count")
    available_count = row.get("available_count")
    diagnosis = str(row.get("diagnosis", ""))
    position_pct = float(row.get("position_pct", 0.0))

    if pd.isna(share_count) or share_count <= 0:
        return "未提供股数，暂按仓位比例建议。"
    if pd.isna(available_count):
        return f"持仓{int(share_count)}股，但可卖股数未知，执行前需先核对。"

    sellable = int(max(0.0, float(available_count)))
    total = int(max(0.0, float(share_count)))
    if diagnosis in {"可减仓", "应替换"}:
        if sellable <= 0:
            return f"今日不可卖，待转为可用后再执行；当前最多可卖0股。"
        if diagnosis == "应替换":
            planned = min(sellable, total)
        else:
            target_pct = position_pct * 0.65
            raw_reduce = total * max(0.0, position_pct - target_pct) / max(position_pct, 0.01)
            planned = min(sellable, max(100, int(raw_reduce // 100) * 100))
            if planned >= total and total <= 100:
                return (
                    f"理论上应降仓，但你当前仅有1手且A股需整手卖出；"
                    f"若执行只能一次卖出{sellable}股，效果会接近清仓。"
                )
        return f"今日最多可卖{sellable}股，按A股整手先卖{planned}股更可执行。"
    if diagnosis == "可加仓":
        return "属于加仓观察标的，若放量确认可用现金分批加，不涉及当日卖出约束。"
    return f"当前以持有为主；若需腾挪资金，今日最多可卖{sellable}股。"


def _build_recommendation_summary(positions_df: pd.DataFrame, replacements: list[dict[str, Any]]) -> str:
    replace_names = positions_df.loc[positions_df["diagnosis"] == "应替换", "name"].astype(str).tolist()
    add_names = positions_df.loc[positions_df["diagnosis"] == "可加仓", "name"].astype(str).tolist()
    trim_names = positions_df.loc[positions_df["diagnosis"] == "可减仓", "name"].astype(str).tolist()
    actionable_replacements = [item for item in replacements if item.get("actionability") == "可执行观察"]

    actions: list[str] = []
    if replace_names:
        actions.append(f"优先处理{'、'.join(replace_names)}这类弱势持仓")
    if trim_names:
        actions.append(f"对{'、'.join(trim_names)}控制集中度")
    if add_names:
        actions.append(f"观察{'、'.join(add_names)}的加仓窗口")
    if actionable_replacements:
        actions.append(f"可执行替补方向优先看{actionable_replacements[0]['name']}")
    elif replacements:
        actions.append("外部强势股先放观察池，暂无适合盘中直接新开的替补")
    if not actions:
        actions.append("当前组合以持有观察为主，等待更明确的强弱分化信号")
    return "；".join(actions) + "。"


def _build_bull_bear_points(
    positions_df: pd.DataFrame,
    market_context: dict[str, Any],
    replacements: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    bull_points: list[str] = []
    bear_points: list[str] = []

    add_df = positions_df[positions_df["diagnosis"] == "可加仓"]
    if not add_df.empty:
        row = add_df.sort_values(by="research_score", ascending=False).iloc[0]
        bull_points.append(
            f"{row['name']}研报强度{row['research_score']:.1f}、基本面分{row['fundamental_score']:.1f}，属于当前组合里更有赔率的进攻点。"
        )
    if market_context.get("trend_label") in {"偏强震荡", "强势上攻"}:
        bull_points.append(f"大盘处于{market_context.get('trend_label')}，对进攻型仓位更友好。")
    actionable_replacements = [item for item in replacements if item.get("actionability") == "可执行观察"]
    if actionable_replacements:
        bull_points.append(
            f"替补池里{actionable_replacements[0]['name']}当前候选分{actionable_replacements[0]['score']:.1f}且盘口可执行，说明外部仍有可切换机会。"
        )
    elif replacements:
        bull_points.append("外部有强势标的，但当前多属于不可追或待核查状态，不能直接视为买点。")

    replace_df = positions_df[positions_df["diagnosis"] == "应替换"]
    if not replace_df.empty:
        row = replace_df.sort_values(by="research_score").iloc[0]
        bear_points.append(
            f"{row['name']}研报强度{row['research_score']:.1f}且基本面分{row['fundamental_score']:.1f}，继续持有会拖累组合效率。"
        )
    trim_df = positions_df[positions_df["diagnosis"] == "可减仓"]
    if not trim_df.empty:
        row = trim_df.sort_values(by="position_pct", ascending=False).iloc[0]
        bear_points.append(
            f"{row['name']}当前仓位{row['position_pct']:.1f}%偏重，若判断失误会放大回撤。"
        )
    if market_context.get("breadth_label") == "下跌家数占优":
        bear_points.append("市场广度偏弱，新增仓位的胜率会被压制。")

    if not bull_points:
        bull_points.append("组合暂未出现特别强的进攻信号，更多依赖后续市场确认。")
    if not bear_points:
        bear_points.append("当前没有特别突出的单点风险，但仍需防范风格切换。")
    return bull_points[:3], bear_points[:3]


def _build_monitoring_points(positions_df: pd.DataFrame) -> list[str]:
    monitoring: list[str] = []
    for _, row in positions_df.head(3).iterrows():
        monitoring.append(
            f"{row['name']}: 观察现价{row['current_price']:.3f}相对{row['technical_bias']}的延续性，以及目标仓位{row['target_position_pct']}是否需要调整。"
        )
    weak_df = positions_df[positions_df["diagnosis"] == "应替换"]
    if not weak_df.empty:
        row = weak_df.iloc[0]
        monitoring.append(
            f"{row['name']}: 若新闻和公告继续偏空，优先执行替换，不再恋战。"
        )
    return monitoring[:5]


def _pick_replacement_target(positions_df: pd.DataFrame) -> str:
    weak = positions_df[positions_df["diagnosis"].isin(["应替换", "可减仓"])].copy()
    if weak.empty:
        return "现金观察仓"
    weak["priority"] = weak["diagnosis"].map({"应替换": 0, "可减仓": 1}).fillna(2)
    row = weak.sort_values(by=["priority", "position_pct"], ascending=[True, False]).iloc[0]
    return f"{row['name']}（{row['code']}）"


def _build_execution_plans(
    positions_df: pd.DataFrame,
    market_context: dict[str, Any],
    replacements: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    intraday: list[str] = []
    next_day: list[str] = []
    post_close: list[str] = []
    market_temperature = str(market_context.get("market_temperature", "震荡"))
    breadth_label = str(market_context.get("breadth_label", "未知"))

    for _, row in positions_df.sort_values(by="position_pct", ascending=False).iterrows():
        available = row.get("available_count")
        available_int = 0 if pd.isna(available) else int(max(0.0, float(available)))
        name_code = f"{row['name']}（{row['code']}）"
        diagnosis = str(row.get("diagnosis", "可持有"))
        if available_int <= 0 and diagnosis in {"可减仓", "应替换"}:
            next_day.append(f"{name_code}: 今日可卖0股，明日若转为可用再按整手复核是否降仓。")
        elif diagnosis in {"可减仓", "应替换"}:
            intraday.append(f"{name_code}: 今日最多可卖{available_int}股，优先作为降风险或换强板块的资金来源。")
        elif diagnosis == "可加仓":
            if market_temperature in {"强势", "震荡"} and str(row.get("board_action")) == "可参与":
                intraday.append(f"{name_code}: 板块可参与，可用现金分批观察加仓，不追涨一次打满。")
            else:
                intraday.append(f"{name_code}: 个股条件尚可，但{breadth_label}下先观察，不急追。")

    actionable_replacements = [item for item in replacements if item.get("actionability") == "可执行观察"]
    watch_only_replacements = [item for item in replacements if item.get("actionability") != "可执行观察"]
    if actionable_replacements:
        top = actionable_replacements[0]
        post_close.append(
            f"盘后复核{top['name']}（{top['code']}）是否继续强于现有弱项，若板块热度延续再考虑替换{top.get('replace_target', '弱势持仓')}。"
        )
    elif watch_only_replacements:
        top = watch_only_replacements[0]
        post_close.append(
            f"{top['name']}（{top['code']}）当前为{top.get('actionability', '只观察')}，盘中不追；盘后只复核其板块持续性，不作为立即买入建议。"
        )
    if market_temperature in {"弱势", "极弱"}:
        post_close.append("盘后重点看下跌家数是否收敛；若继续扩散，次日仍以控仓和去弱为主。")
    else:
        post_close.append("盘后确认热点板块是否连续扩散；只有强板块延续，新增仓位胜率才更高。")

    if not intraday:
        intraday.append("盘中暂无必须执行动作，优先观察市场温度和可卖约束变化。")
    if not next_day:
        next_day.append("明日无单独锁定事项，按盘后复盘和开盘强弱再调整。")
    return intraday[:5], next_day[:5], post_close[:5]


def _estimate_total_assets_and_cash(positions_df: pd.DataFrame, cash_pct: float) -> tuple[float, float]:
    if positions_df.empty:
        return float("nan"), float("nan")
    work = positions_df.copy()
    work["position_pct"] = pd.to_numeric(work.get("position_pct"), errors="coerce")
    work["current_price"] = pd.to_numeric(work.get("current_price"), errors="coerce")
    work["share_count"] = pd.to_numeric(work.get("share_count"), errors="coerce")
    valid = work[(work["position_pct"] > 0) & (work["current_price"] > 0) & (work["share_count"] > 0)].copy()
    if valid.empty:
        return float("nan"), float("nan")
    estimated_totals = valid["current_price"] * valid["share_count"] / (valid["position_pct"] / 100.0)
    estimated_totals = estimated_totals.replace([float("inf"), -float("inf")], pd.NA).dropna()
    if estimated_totals.empty:
        return float("nan"), float("nan")
    estimated_total = float(estimated_totals.median())
    estimated_cash = estimated_total * max(0.0, cash_pct) / 100.0
    return estimated_total, estimated_cash


def analyze_portfolio_snapshot(
    positions_df: pd.DataFrame,
    market_df: pd.DataFrame,
    candidate_df: pd.DataFrame | None,
    report_date: date,
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_context = market_context or {}
    merged = positions_df.copy()
    merged["code"] = merged["code"].map(normalize_code)
    merged = merged.merge(market_df.drop(columns=["name"]), on="code", how="left")
    if "current_price" not in merged.columns:
        merged["current_price"] = merged["cost_price"]
    if "day_change_pct" not in merged.columns:
        merged["day_change_pct"] = 0.0
    if "research_score" not in merged.columns:
        merged["research_score"] = 50.0
    if "fundamental_score" not in merged.columns:
        merged["fundamental_score"] = 50.0
    if "institution_count" not in merged.columns:
        merged["institution_count"] = 0
    if "source_group_count" not in merged.columns:
        merged["source_group_count"] = 0
    if "target_upside_pct" not in merged.columns:
        merged["target_upside_pct"] = 0.0
    for technical_col in ["technical_score", "trend_score", "momentum_score", "rsi_score", "volatility_score"]:
        if technical_col not in merged.columns:
            merged[technical_col] = 50.0
    if "setup_tags" not in merged.columns:
        merged["setup_tags"] = "技术待补充"
    if "technical_bias" not in merged.columns:
        merged["technical_bias"] = "技术位数据缺失"
    for point_col in ["buy_point", "sell_point", "stop_loss_point"]:
        if point_col not in merged.columns:
            merged[point_col] = float("nan")
    if "technical_point_sources" not in merged.columns:
        merged["technical_point_sources"] = "技术位数据缺失"
    if "technical_plan" not in merged.columns:
        merged["technical_plan"] = "技术位数据缺失"
    if "share_count" not in merged.columns:
        merged["share_count"] = float("nan")
    if "available_count" not in merged.columns:
        merged["available_count"] = float("nan")
    merged["current_price"] = pd.to_numeric(merged["current_price"], errors="coerce").fillna(merged["cost_price"])
    merged["day_change_pct"] = pd.to_numeric(merged["day_change_pct"], errors="coerce").fillna(0.0)
    merged["industry"] = merged["industry"].fillna("行业待补充")
    merged["research_score"] = pd.to_numeric(merged["research_score"], errors="coerce").fillna(50.0)
    merged["fundamental_score"] = pd.to_numeric(merged["fundamental_score"], errors="coerce").fillna(50.0)
    merged["fundamental_summary"] = _ensure_text_column(
        merged, "fundamental_summary", default="基本面数据缺失"
    )
    merged["institution_count"] = merged["institution_count"].fillna(0).astype(int)
    merged["source_group_count"] = merged["source_group_count"].fillna(0).astype(int)
    merged["target_upside_pct"] = merged["target_upside_pct"].fillna(0.0)
    for technical_col in ["technical_score", "trend_score", "momentum_score", "rsi_score", "volatility_score"]:
        merged[technical_col] = pd.to_numeric(merged[technical_col], errors="coerce").fillna(50.0)
    merged["setup_tags"] = _ensure_text_column(merged, "setup_tags", default="技术待补充")
    merged["technical_bias"] = merged["technical_bias"].fillna("技术位数据缺失")
    for point_col in ["buy_point", "sell_point", "stop_loss_point"]:
        merged[point_col] = pd.to_numeric(merged[point_col], errors="coerce")
    merged["technical_point_sources"] = _ensure_text_column(
        merged, "technical_point_sources", default="技术位数据缺失"
    )
    merged["technical_plan"] = _ensure_text_column(merged, "technical_plan", default="技术位数据缺失")
    merged["news_summary"] = _ensure_text_column(merged, "news_summary")
    merged["notice_summary"] = _ensure_text_column(merged, "notice_summary")
    merged["share_count"] = pd.to_numeric(merged["share_count"], errors="coerce")
    merged["available_count"] = pd.to_numeric(merged["available_count"], errors="coerce")
    merged["available_ratio_pct"] = (
        merged["available_count"].div(merged["share_count"]).where(merged["share_count"] > 0) * 100.0
    )
    merged["pnl_pct"] = (merged["current_price"] - merged["cost_price"]).div(merged["cost_price"]) * 100.0
    board_strength = market_context.get("board_strength", [])
    board_rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        matched = _match_board(f"{row.get('name', '')} {row.get('industry', '')}", board_strength)
        board_rows.append(
            {
                "matched_board": matched.get("board_name", "") if matched else "",
                "board_action": matched.get("board_action", "板块判断不足") if matched else "板块判断不足",
                "board_change_pct": float(matched.get("change_pct", 0.0)) if matched else 0.0,
                "board_up_ratio": float(matched.get("up_ratio", 0.0)) if matched else 0.0,
            }
        )
    merged = pd.concat([merged, pd.DataFrame(board_rows, index=merged.index)], axis=1)
    merged["board_match_text"] = merged.apply(_board_match_text, axis=1)
    trade_scores = merged.apply(lambda row: _score_trade_setup(row, market_context), axis=1)
    merged["trade_setup_score"] = [item[0] for item in trade_scores]
    merged["trade_setup_note"] = [item[1] for item in trade_scores]

    stock_exposure = round(float(merged["position_pct"].sum()), 1)
    cash_pct = round(max(0.0, 100.0 - stock_exposure), 1)
    estimated_total_assets, estimated_cash_amount = _estimate_total_assets_and_cash(merged, cash_pct)
    top_holding_pct = round(float(merged["position_pct"].max()), 1) if not merged.empty else 0.0
    top3_pct = round(float(merged.nlargest(min(3, len(merged)), "position_pct")["position_pct"].sum()), 1) if not merged.empty else 0.0
    industry_weight = merged.groupby("industry", as_index=True)["position_pct"].sum().sort_values(ascending=False)
    duplicate_industries = set(industry_weight[industry_weight >= 35.0].index.tolist())

    risk_flags: list[str] = []
    if stock_exposure >= 85.0:
        risk_flags.append("总仓位偏满")
    if top_holding_pct >= 30.0:
        risk_flags.append("单票仓位过高")
    if top3_pct >= 70.0:
        risk_flags.append("前三持仓过于集中")
    if duplicate_industries:
        risk_flags.append("行业重复暴露")
    if merged["pnl_pct"].min() <= -12.0:
        risk_flags.append("存在亏损扩大的持仓")
    locked_weight = merged.loc[
        (merged["share_count"] > 0) & (merged["available_count"].fillna(-1) <= 0),
        "position_pct",
    ].sum()
    if locked_weight >= 20.0:
        risk_flags.append("较大仓位今日不可卖")

    merged["diagnosis"] = merged.apply(lambda row: _diagnosis_for_row(row, duplicate_industries), axis=1)
    merged["diagnosis"] = merged.apply(lambda row: _adjust_diagnosis_for_market(row, market_context), axis=1)
    merged["portfolio_tag"] = merged["diagnosis"].map(_portfolio_tag)
    merged["target_position_pct"] = merged.apply(
        lambda row: _target_position_text(str(row["diagnosis"]), float(row["position_pct"])),
        axis=1,
    )
    merged["reason"] = merged.apply(_build_reason, axis=1)
    merged["available_sell_text"] = merged.apply(_available_sell_text, axis=1)
    merged["execution_note"] = merged.apply(_rebalance_execution_text, axis=1)

    rebalance_actions: list[str] = []
    for _, row in merged.sort_values(by=["diagnosis", "position_pct"], ascending=[True, False]).iterrows():
        action = (
            f"{row['name']}（{row['code']}）: {row['diagnosis']}，建议目标仓位 {row['target_position_pct']}，"
            f"{row['execution_note']}；"
            f"触发参考为 {row['technical_bias']}；"
            f"技术计划: {row.get('technical_plan', '技术位数据缺失')}；"
            f"点位来源: {row.get('technical_point_sources', '技术位数据缺失')}；"
            f"新闻: {row['news_summary'] or '暂无显著新闻'}；"
            f"公告: {row['notice_summary'] or '暂无近期公告'}。"
        )
        rebalance_actions.append(action)

    replacements: list[dict[str, Any]] = []
    if candidate_df is not None and not candidate_df.empty:
        held_codes = set(merged["code"])
        score_col = "market_adjusted_score" if "market_adjusted_score" in candidate_df.columns else "adjusted_score"
        candidate_work = candidate_df[~candidate_df["code"].isin(held_codes)].copy()
        if "actionability" not in candidate_work.columns:
            if market_context.get("enable_live_candidate_actionability"):
                candidate_work, actionability_source = attach_candidate_actionability(
                    candidate_work,
                    estimated_cash_amount=estimated_cash_amount,
                    limit=max(10, len(merged) + 5),
                )
                market_context["candidate_actionability_source"] = actionability_source
            else:
                candidate_work = _ensure_candidate_actionability_columns(candidate_work)
                market_context.setdefault("candidate_actionability_source", "not_checked")
        else:
            candidate_work = _ensure_candidate_actionability_columns(candidate_work)
            market_context.setdefault("candidate_actionability_source", "provided")
        if score_col in candidate_work.columns:
            candidate_work[score_col] = pd.to_numeric(candidate_work[score_col], errors="coerce").fillna(0.0)
            candidate_work["actionability_rank"] = pd.to_numeric(
                candidate_work["actionability_rank"], errors="coerce"
            ).fillna(60)
            candidate_work = candidate_work.sort_values(by=["actionability_rank", score_col], ascending=[True, False])
        candidate_work = candidate_work.head(5)
        replace_target = _pick_replacement_target(merged)
        for _, row in candidate_work.iterrows():
            raw_industry = str(row.get("industry", ""))
            industry = "" if raw_industry.lower() == "nan" else raw_industry
            board_action = str(row.get("board_action", "板块判断不足"))
            replacements.append(
                {
                    "code": str(row.get("code", "")),
                    "name": str(row.get("name", "")),
                    "score": float(row.get(score_col, row.get("adjusted_score", row.get("composite_score", 0.0)))),
                    "close_price": float(row.get("close_price", 0.0)),
                    "industry": industry,
                    "board_action": board_action,
                    "board_change_pct": float(row.get("board_change_pct", 0.0)),
                    "technical_score": float(row.get("technical_score", 50.0)),
                    "technical_summary": str(row.get("technical_summary", "技术位待补充")),
                    "buy_point": float(row.get("buy_point", float("nan"))),
                    "sell_point": float(row.get("sell_point", float("nan"))),
                    "stop_loss_point": float(row.get("stop_loss_point", float("nan"))),
                    "technical_point_sources": str(row.get("technical_point_sources", "技术位待补充")),
                    "technical_plan": str(row.get("technical_plan", "技术位待补充")),
                    "actionability": str(row.get("actionability", "盘口待核查")),
                    "action_note": str(row.get("action_note", "未完成实时盘口核查，只能作为观察池。")),
                    "actionability_rank": float(row.get("actionability_rank", 60.0)),
                    "min_lot_cost": float(row.get("min_lot_cost", 0.0)),
                    "replace_target": replace_target,
                    "replace_reason": (
                        f"候选分{float(row.get(score_col, row.get('adjusted_score', 0.0))):.1f}，"
                        f"{board_action}，技术面{row.get('technical_summary', '技术位待补充')}，"
                        f"{row.get('actionability', '盘口待核查')}，可作为{replace_target}的观察替补。"
                    ),
                }
            )

    overview = {
        "report_date": report_date.isoformat(),
        "stock_exposure_pct": stock_exposure,
        "cash_pct": cash_pct,
        "top_holding_pct": top_holding_pct,
        "top3_holding_pct": top3_pct,
        "estimated_total_assets": estimated_total_assets,
        "estimated_cash_amount": estimated_cash_amount,
        "available_sell_ratio_pct": round(
            float(merged["available_count"].fillna(0.0).sum())
            / float(merged["share_count"].fillna(0.0).sum())
            * 100.0,
            1,
        )
        if float(merged["share_count"].fillna(0.0).sum()) > 0
        else float("nan"),
        "risk_flags": risk_flags,
        "industry_concentration": industry_weight.head(3).round(1).to_dict(),
    }
    recommendation_summary = _build_recommendation_summary(merged, replacements)
    bull_points, bear_points = _build_bull_bear_points(merged, market_context, replacements)
    monitoring_points = _build_monitoring_points(merged)
    intraday_actions, next_day_actions, post_close_review = _build_execution_plans(
        positions_df=merged,
        market_context=market_context,
        replacements=replacements,
    )
    return {
        "market_context": market_context,
        "overview": overview,
        "positions": merged.sort_values(by="position_pct", ascending=False).reset_index(drop=True),
        "rebalance_actions": rebalance_actions,
        "replacement_candidates": replacements,
        "recommendation_summary": recommendation_summary,
        "bull_points": bull_points,
        "bear_points": bear_points,
        "monitoring_points": monitoring_points,
        "intraday_actions": intraday_actions,
        "next_day_actions": next_day_actions,
        "post_close_review": post_close_review,
    }


def build_portfolio_markdown_report(analysis: dict[str, Any]) -> str:
    market_context = analysis.get("market_context", {})
    overview = analysis["overview"]
    positions_df = analysis["positions"]
    replacements = analysis["replacement_candidates"]
    intraday_actions = analysis.get("intraday_actions", [])
    next_day_actions = analysis.get("next_day_actions", [])
    post_close_review = analysis.get("post_close_review", [])

    lines = [
        f"# 实时市场驱动持仓分析（{overview['report_date']}）",
        "",
    ]

    index_text = "；".join(
        f"{item['name']} {item['price']:.2f} ({item['change_pct']:+.2f}%)"
        for item in market_context.get("index_snapshot", [])
    )
    board_strength = market_context.get("board_strength", [])
    board_source = market_context.get("board_snapshot_source", "未知")
    top_board_names = "、".join(item.get("board_name", "") for item in board_strength[:3]) or "暂无"
    lines.extend([
        "## 1. 实时盘面",
        f"- 报告生成时间: {market_context.get('generated_at', '未知')}",
        f"- 行情快照来源: {market_context.get('candidate_snapshot_source', '未知')}；板块快照来源: {board_source}",
        f"- 市场温度: {market_context.get('market_temperature', '未知')}；建议股票仓位上限: {market_context.get('max_stock_exposure_pct', 0):.0f}%",
        f"- 大盘节奏: {market_context.get('trend_label', '未知')}；风险水平: {market_context.get('risk_level', '未知')}",
        f"- 市场广度: {market_context.get('breadth_label', '未知')}，上涨 {market_context.get('up_count', 0)} 家，下跌 {market_context.get('down_count', 0)} 家，涨停约 {market_context.get('limit_up_count', 0)} 家，跌停约 {market_context.get('limit_down_count', 0)} 家",
        f"- 成交活跃度: {market_context.get('activity_label', '未知')}",
        f"- 关键指数: {index_text or '暂无指数快照'}",
        "",
        "## 2. 板块强弱",
        f"- 热点方向: {top_board_names}",
    ])
    if board_strength:
        lines.extend(["", "| 类型 | 板块 | 涨跌幅 | 上涨比例 | 领涨股 | 参与判断 |", "| --- | --- | ---: | ---: | --- | --- |"])
        for item in board_strength[:8]:
            lines.append(
                f"| {item.get('board_type', '')} | {item.get('board_name', '')} | {float(item.get('change_pct', 0.0)):+.2f}% | {float(item.get('up_ratio', 0.0)):.0%} | {item.get('leader', '')} | {item.get('board_action', '只观察')} |"
            )
    else:
        lines.append("- 板块判断不足：本次未拿到可用行业/概念板块快照，候选替换只保留个股与研报维度。")

    lines.extend([
        "",
        "## 3. 你的持仓",
        f"- 股票仓位: {overview['stock_exposure_pct']:.1f}%",
        f"- 现金仓位: {overview['cash_pct']:.1f}%",
        f"- 估算现金: {float(overview.get('estimated_cash_amount', float('nan'))):.0f} 元"
        if pd.notna(overview.get("estimated_cash_amount"))
        else "- 估算现金: 未能从持仓股数和仓位比例反推",
        f"- 最大单票: {overview['top_holding_pct']:.1f}%",
        f"- 前三持仓合计: {overview['top3_holding_pct']:.1f}%",
        f"- 风险提示: {'；'.join(overview['risk_flags']) if overview['risk_flags'] else '暂无显著风险'}",
        f"- 组合可卖比例: {overview['available_sell_ratio_pct']:.1f}%"
        if pd.notna(overview["available_sell_ratio_pct"])
        else "- 组合可卖比例: 未提供持仓股数/可卖股数",
        "",
        "| 代码 | 名称 | 仓位% | 持仓/可卖 | 现价 | 当日涨跌 | 成本盈亏 | 所属板块 | 交易分 | 诊断 | 目标仓位 | 技术计划 | 核心依据 |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | --- | ---: | --- | --- | --- | --- |",
    ])
    for _, row in positions_df.iterrows():
        share_count_text = "--" if pd.isna(row["share_count"]) else str(int(row["share_count"]))
        available_count_text = "--" if pd.isna(row["available_count"]) else str(int(row["available_count"]))
        lines.append(
            f"| {row['code']} | {row['name']} | {float(row['position_pct']):.1f} | {share_count_text}/{available_count_text} | {float(row['current_price']):.3f} | {float(row['day_change_pct']):+.2f}% | {float(row['pnl_pct']):+.1f}% | {row.get('board_match_text', '板块判断不足')} | {float(row.get('trade_setup_score', 50.0)):.1f} | {row['diagnosis']} | {row['target_position_pct']} | {row.get('technical_plan', '技术位数据缺失')}；依据明细: {row.get('technical_point_sources', '技术位数据缺失')} | {row['reason']} |"
        )

    lines.extend(["", "## 4. 执行约束"])
    for _, row in positions_df.iterrows():
        lines.append(f"- {row['name']}（{row['code']}）: {row['available_sell_text']}；{row['execution_note']}")

    lines.extend([
        "",
        "## 5. 候选替补股",
        f"- 候选来源: {market_context.get('candidate_recommendation_source', 'live')}；筛选行情源: {market_context.get('candidate_snapshot_source', '未知')}；候选现价源: {market_context.get('candidate_realtime_price_source', '未知')}；盘口可执行性源: {market_context.get('candidate_actionability_source', '未知')}",
    ])
    if replacements:
        actionable_count = sum(1 for item in replacements if item.get("actionability") == "可执行观察")
        if actionable_count <= 0:
            lines.append("- 暂无盘中可执行候选：下列股票只是强势但不可追观察，不建议现在直接新开。")
        else:
            lines.append(f"- 盘中可执行候选 {actionable_count} 个；其余只放观察池，不作为立即买入建议。")
        lines.extend(
            [
                "",
                "| 代码 | 名称 | 行业 | 现价 | 一手金额 | 市场修正分 | 技术分 | 买入可执行性 | 技术计划 | 技术面 | 板块状态 | 替换对象 | 替换理由 |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in replacements:
            actionability = item.get("actionability", "盘口待核查")
            action_label = actionability if actionability == "可执行观察" else f"强势但不可追观察: {actionability}"
            lines.append(
                f"| {item['code']} | {item['name']} | {item.get('industry', '')} | {item['close_price']:.2f} | {float(item.get('min_lot_cost', 0.0)):.0f} | {item['score']:.1f} | {float(item.get('technical_score', 50.0)):.1f} | {action_label}: {item.get('action_note', '')} | {item.get('technical_plan', '技术位待补充')}；依据明细: {item.get('technical_point_sources', '技术位待补充')} | {item.get('technical_summary', '技术位待补充')} | {item.get('board_action', '板块判断不足')} {float(item.get('board_change_pct', 0.0)):+.2f}% | {item.get('replace_target', '')} | {item.get('replace_reason', '')} |"
            )
    else:
        lines.extend(
            [
                "| 代码 | 名称 | 行业 | 现价 | 一手金额 | 市场修正分 | 技术分 | 买入可执行性 | 技术计划 | 技术面 | 板块状态 | 替换对象 | 替换理由 |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |",
                "| - | - | - | - | - | - | - | - | - | - | - | - | 暂无替补标的，建议先在现有持仓内完成去弱留强。 |",
            ]
        )

    lines.extend(["", "## 6. 操作建议", "### 今天盘中可执行"])
    for item in intraday_actions:
        lines.append(f"- {item}")
    lines.extend(["", "### 明天优先处理"])
    for item in next_day_actions:
        lines.append(f"- {item}")
    lines.extend(["", "### 盘后复盘结论"])
    for item in post_close_review:
        lines.append(f"- {item}")

    return "\n".join(lines) + "\n"


def build_portfolio_html_report(analysis: dict[str, Any]) -> str:
    market_context = analysis.get("market_context", {})
    overview = analysis["overview"]
    positions_df = analysis["positions"].copy()
    positions_view = positions_df[
        [
            "code",
            "name",
            "position_pct",
            "share_count",
            "available_count",
            "available_sell_text",
            "current_price",
            "pnl_pct",
            "diagnosis",
            "portfolio_tag",
            "target_position_pct",
            "execution_note",
            "fundamental_summary",
        ]
    ].copy()
    positions_view["share_count"] = positions_view["share_count"].map(
        lambda x: "--" if pd.isna(x) else str(int(float(x)))
    )
    positions_view["available_count"] = positions_view["available_count"].map(
        lambda x: "--" if pd.isna(x) else str(int(float(x)))
    )
    positions_view.columns = [
        "代码",
        "名称",
        "仓位%",
        "持仓股数",
        "可卖股数",
        "可卖状态",
        "现价",
        "盈亏%",
        "诊断",
        "标签",
        "目标仓位",
        "执行备注",
        "基本面摘要",
    ]
    positions_table = positions_view.to_html(index=False, escape=False)

    html = Template(PORTFOLIO_HTML_TEMPLATE).render(
        report_date=overview["report_date"],
        generated_at=market_context.get("generated_at", "未知"),
        trend_label=market_context.get("trend_label", "未知"),
        breadth_label=market_context.get("breadth_label", "未知"),
        risk_level=market_context.get("risk_level", "未知"),
        candidate_snapshot_source=market_context.get("candidate_snapshot_source", "未知"),
        index_snapshot=[
            {
                "name": item.get("name", ""),
                "price": f"{float(item.get('price', 0.0)):.2f}",
                "change_pct": f"{float(item.get('change_pct', 0.0)):+.2f}%",
            }
            for item in market_context.get("index_snapshot", [])
        ],
        hot_sectors="、".join(market_context.get("hot_sectors", [])) or "暂无",
        recommendation_summary=analysis.get("recommendation_summary", "当前暂无明确投资结论。"),
        stock_exposure_pct=f"{overview['stock_exposure_pct']:.1f}%",
        cash_pct=f"{overview['cash_pct']:.1f}%",
        top_holding_pct=f"{overview['top_holding_pct']:.1f}%",
        top3_holding_pct=f"{overview['top3_holding_pct']:.1f}%",
        available_sell_ratio="未提供"
        if pd.isna(overview.get("available_sell_ratio_pct"))
        else f"{overview['available_sell_ratio_pct']:.1f}%",
        available_sell_ratio_value=0.0
        if pd.isna(overview.get("available_sell_ratio_pct"))
        else float(overview["available_sell_ratio_pct"]),
        risk_flags=overview.get("risk_flags", []),
        positions_table=positions_table,
        rebalance_actions=analysis.get("rebalance_actions", []),
        bull_points=analysis.get("bull_points", []),
        bear_points=analysis.get("bear_points", []),
        monitoring_points=analysis.get("monitoring_points", []),
        replacement_candidates=[
            {
                "name": item.get("name", ""),
                "code": item.get("code", ""),
                "score": f"{float(item.get('score', 0.0)):.1f}",
                "close_price": f"{float(item.get('close_price', 0.0)):.2f}",
            }
            for item in analysis.get("replacement_candidates", [])
        ],
    )
    return html


def _build_market_conclusions(market_context: dict[str, Any], candidates_df: pd.DataFrame) -> list[str]:
    conclusions: list[str] = []
    trend_label = str(market_context.get("trend_label", "未知"))
    breadth_label = str(market_context.get("breadth_label", "未知"))
    hot_sectors = market_context.get("hot_sectors", [])

    conclusions.append(f"当前大盘处于{trend_label}，市场广度表现为{breadth_label}。")
    if hot_sectors:
        conclusions.append(f"热点方向集中在{'、'.join(hot_sectors)}。")

    if candidates_df is not None and not candidates_df.empty:
        top_row = candidates_df.iloc[0]
        conclusions.append(
            f"候选股里{top_row['name']}（{top_row['code']}）综合分最高，属于当前更值得优先跟踪的方向。"
        )
        strong_count = int((pd.to_numeric(candidates_df.get("adjusted_score"), errors="coerce") >= 75.0).sum())
        conclusions.append(f"当前候选池中高分标的约有 {strong_count} 只，可用于后续调仓或新开仓观察。")

    if not hot_sectors and trend_label in {"震荡整理", "弱势承压"}:
        conclusions.append("市场主线不够清晰，短线更适合控制追高节奏。")
    return conclusions[:4]


def build_market_brief_markdown_report(
    report_date: date,
    market_context: dict[str, Any],
    candidates_df: pd.DataFrame,
) -> str:
    index_text = "；".join(
        f"{item['name']} {item['price']:.2f} ({item['change_pct']:+.2f}%)"
        for item in market_context.get("index_snapshot", [])
    )
    hot_sectors = "、".join(market_context.get("hot_sectors", [])) or "暂无"
    conclusions = _build_market_conclusions(market_context, candidates_df)

    lines = [
        f"# A股实时市场快报（{report_date.isoformat()}）",
        "",
        "## 市场环境",
        f"- 报告生成时间: {market_context.get('generated_at', '未知')}",
        f"- 候选池快照来源: {market_context.get('candidate_snapshot_source', '未知')}",
        f"- 候选现价源: {market_context.get('candidate_realtime_price_source', '未知')}",
        f"- 候选建议来源: {market_context.get('candidate_recommendation_source', 'live')}",
        f"- 大盘节奏: {market_context.get('trend_label', '未知')}",
        f"- 市场广度: {market_context.get('breadth_label', '未知')}，上涨 {market_context.get('up_count', 0)} 家，下跌 {market_context.get('down_count', 0)} 家",
        f"- 风险水平: {market_context.get('risk_level', '未知')}",
        f"- 关键指数: {index_text or '暂无指数快照'}",
        f"- 热点方向: {hot_sectors}",
        "",
        "## 市场结论",
    ]
    for item in conclusions:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## 候选方向",
            "| 代码 | 名称 | 行业 | 现价 | 综合分 | 买点信号 | 机构数 | 跨源覆盖 |",
            "| --- | --- | --- | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    if candidates_df is None or candidates_df.empty:
        lines.append("| - | - | - | - | - | - | - | - |")
    else:
        for _, row in candidates_df.head(10).iterrows():
            lines.append(
                "| {code} | {name} | {industry} | {close_price:.2f} | {adjusted_score:.1f} | {buy_signal} | {institution_count} | {source_group_count} |".format(
                    **row.to_dict()
                )
            )

    return "\n".join(lines) + "\n"


def build_market_brief_html_report(
    report_date: date,
    market_context: dict[str, Any],
    candidates_df: pd.DataFrame,
) -> str:
    candidates_view = pd.DataFrame()
    if candidates_df is not None and not candidates_df.empty:
        candidates_view = candidates_df.head(10)[
            [
                "code",
                "name",
                "industry",
                "close_price",
                "adjusted_score",
                "buy_signal",
                "institution_count",
                "source_group_count",
            ]
        ].copy()
        candidates_view.columns = ["代码", "名称", "行业", "现价", "综合分", "买点信号", "机构数", "跨源覆盖"]
    candidates_table = candidates_view.to_html(index=False, escape=False) if not candidates_view.empty else "<p>暂无候选方向。</p>"

    return Template(MARKET_BRIEF_HTML_TEMPLATE).render(
        report_date=report_date.isoformat(),
        generated_at=market_context.get("generated_at", "未知"),
        trend_label=market_context.get("trend_label", "未知"),
        breadth_label=market_context.get("breadth_label", "未知"),
        risk_level=market_context.get("risk_level", "未知"),
        candidate_snapshot_source=market_context.get("candidate_snapshot_source", "未知"),
        index_snapshot=[
            {
                "name": item.get("name", ""),
                "price": f"{float(item.get('price', 0.0)):.2f}",
                "change_pct": f"{float(item.get('change_pct', 0.0)):+.2f}%",
            }
            for item in market_context.get("index_snapshot", [])
        ],
        hot_sectors=market_context.get("hot_sectors", []),
        market_conclusions=_build_market_conclusions(market_context, candidates_df),
        candidates_table=candidates_table,
    )


def run_market_brief(cfg: PortfolioAnalysisConfig) -> dict[str, Any]:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    _log("抓取市场研报基础数据")
    raw_base, forecast_metrics, target_metrics, composite_metrics = build_raw_bundle(report_date=cfg.report_date)

    _log("抓取全市场价格快照与候选方向")
    candidate_price_snapshot, candidate_snapshot_source = _load_or_fetch_candidate_snapshot(cfg)
    market_context = _build_market_overview(report_date=cfg.report_date, breadth_df=candidate_price_snapshot)
    market_context["generated_at"] = now_ts()
    market_context["candidate_snapshot_source"] = candidate_snapshot_source
    board_df, board_snapshot_source = fetch_board_snapshot()
    board_strength = summarize_board_strength(board_df, top_n=200)
    market_context["board_snapshot_source"] = board_snapshot_source
    market_context["board_strength"] = board_strength

    candidates_df = score_candidates(
        raw_records=attach_rating_score(raw_base),
        forecast_metrics=forecast_metrics,
        target_metrics=target_metrics,
        composite_metrics=composite_metrics,
        price_snapshot=candidate_price_snapshot,
        report_date=cfg.report_date,
        window_days=cfg.window_days,
        min_institutions=cfg.min_institutions,
        price_limit=cfg.price_limit,
        top_n=max(cfg.top_n_candidates, 10),
        diversity_weight=0.35,
        rating_weight=0.35,
        upside_weight=0.20,
        consistency_weight=0.10,
        focus_theme=cfg.focus_theme,
        focus_boost_weight=cfg.focus_boost_weight,
    )
    candidate_recommendation_source = "live"
    if candidates_df.empty:
        candidates_df = load_fallback_candidates_snapshot(
            cfg.output_dir.parent / "candidates_top15.csv",
            price_snapshot=candidate_price_snapshot,
            top_n=max(cfg.top_n_candidates, 10),
        )
        if not candidates_df.empty:
            candidate_recommendation_source = "fallback_snapshot"
    board_leader_candidates = build_board_leader_candidates(
        board_df,
        price_limit=cfg.price_limit,
        top_n=max(cfg.top_n_candidates, 10),
        price_snapshot=candidate_price_snapshot,
    )
    if not board_leader_candidates.empty:
        candidates_df = pd.concat([board_leader_candidates, candidates_df], ignore_index=True)
        candidates_df["adjusted_score"] = pd.to_numeric(candidates_df.get("adjusted_score"), errors="coerce").fillna(0.0)
        candidates_df = (
            candidates_df.sort_values(by="adjusted_score", ascending=False)
            .drop_duplicates(subset=["code"], keep="first")
            .reset_index(drop=True)
        )
        candidate_recommendation_source = f"{candidate_recommendation_source}+board_hot_leader"
    candidates_df = attach_board_context_to_candidates(candidates_df, board_strength)
    candidates_df = attach_technical_context_to_candidates(
        candidates_df,
        report_date=cfg.report_date,
        limit=max(cfg.top_n_candidates, 10),
    )
    candidates_df, candidate_realtime_price_source = refresh_candidate_realtime_prices(
        candidates_df,
        limit=max(cfg.top_n_candidates + 10, 20),
    )
    market_context["hot_sectors"] = (
        [item["board_name"] for item in board_strength[:3]] if board_strength else summarize_hot_sectors(candidates_df)
    )
    market_context["candidate_recommendation_source"] = candidate_recommendation_source
    market_context["candidate_realtime_price_source"] = candidate_realtime_price_source

    report_text = build_market_brief_markdown_report(
        report_date=cfg.report_date,
        market_context=market_context,
        candidates_df=candidates_df,
    )
    report_html = build_market_brief_html_report(
        report_date=cfg.report_date,
        market_context=market_context,
        candidates_df=candidates_df,
    )

    report_path = cfg.output_dir / "market_brief_report.md"
    report_html_path = cfg.output_dir / "market_brief_report.html"
    candidates_path = cfg.output_dir / "market_brief_candidates.csv"
    report_path.write_text(report_text, encoding="utf-8")
    report_html_path.write_text(report_html, encoding="utf-8")
    candidates_df.to_csv(candidates_path, index=False, encoding="utf-8-sig")

    return {
        "report_path": report_path,
        "report_html_path": report_html_path,
        "candidates_path": candidates_path,
        "market_context": market_context,
        "candidates": candidates_df,
        "market_conclusions": _build_market_conclusions(market_context, candidates_df),
    }


def run_portfolio_analysis(
    positions_df: pd.DataFrame,
    cfg: PortfolioAnalysisConfig,
) -> dict[str, Any]:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    _log("抓取研报基础数据")
    raw_base, forecast_metrics, target_metrics, composite_metrics = build_raw_bundle(report_date=cfg.report_date)

    _log("补充持仓相关东财个股研报")
    eastmoney_reports = fetch_eastmoney_research_for_codes(positions_df["code"].astype(str).tolist())
    merged_raw = pd.concat([raw_base, eastmoney_reports], ignore_index=True)
    merged_raw = dedupe_standard_records(merged_raw)

    _log("生成市场环境快照与候选替补池")
    candidate_price_snapshot, candidate_snapshot_source = _load_or_fetch_candidate_snapshot(cfg)
    market_context = _build_market_overview(report_date=cfg.report_date, breadth_df=candidate_price_snapshot)
    market_context["generated_at"] = now_ts()
    market_context["candidate_snapshot_source"] = candidate_snapshot_source
    board_df, board_snapshot_source = fetch_board_snapshot()
    board_strength = summarize_board_strength(board_df, top_n=200)
    market_context["board_snapshot_source"] = board_snapshot_source
    market_context["board_strength"] = board_strength

    _log("抓取持仓最新价格、新闻、公告与技术位")
    market_df = _build_market_snapshot(
        positions_df=positions_df,
        report_date=cfg.report_date,
        raw_records=merged_raw,
        forecast_metrics=forecast_metrics,
        target_metrics=target_metrics,
        composite_metrics=composite_metrics,
        important_holding_count=cfg.important_holding_count,
        notice_lookback_days=cfg.notice_lookback_days,
        fallback_price_snapshot=candidate_price_snapshot,
    )
    candidates_df = score_candidates(
        raw_records=attach_rating_score(merged_raw),
        forecast_metrics=forecast_metrics,
        target_metrics=target_metrics,
        composite_metrics=composite_metrics,
        price_snapshot=candidate_price_snapshot,
        report_date=cfg.report_date,
        window_days=cfg.window_days,
        min_institutions=cfg.min_institutions,
        price_limit=cfg.price_limit,
        top_n=max(cfg.top_n_candidates + len(positions_df), 15),
        diversity_weight=0.35,
        rating_weight=0.35,
        upside_weight=0.20,
        consistency_weight=0.10,
        focus_theme=cfg.focus_theme,
        focus_boost_weight=cfg.focus_boost_weight,
    )
    candidate_recommendation_source = "live"
    if candidates_df.empty:
        candidates_df = load_fallback_candidates_snapshot(
            cfg.output_dir.parent / "candidates_top15.csv",
            price_snapshot=candidate_price_snapshot,
            top_n=max(cfg.top_n_candidates + len(positions_df), 15),
        )
        if not candidates_df.empty:
            candidate_recommendation_source = "fallback_snapshot"
    board_leader_candidates = build_board_leader_candidates(
        board_df,
        price_limit=cfg.price_limit,
        top_n=max(cfg.top_n_candidates + len(positions_df), 15),
        price_snapshot=candidate_price_snapshot,
    )
    if not board_leader_candidates.empty:
        candidates_df = pd.concat([board_leader_candidates, candidates_df], ignore_index=True)
        candidates_df["adjusted_score"] = pd.to_numeric(candidates_df.get("adjusted_score"), errors="coerce").fillna(0.0)
        candidates_df = (
            candidates_df.sort_values(by="adjusted_score", ascending=False)
            .drop_duplicates(subset=["code"], keep="first")
            .reset_index(drop=True)
        )
        candidate_recommendation_source = f"{candidate_recommendation_source}+board_hot_leader"
    candidates_df = attach_board_context_to_candidates(candidates_df, board_strength)
    candidates_df = attach_technical_context_to_candidates(
        candidates_df,
        report_date=cfg.report_date,
        limit=max(cfg.top_n_candidates + len(positions_df), 15),
    )
    candidates_df, candidate_realtime_price_source = refresh_candidate_realtime_prices(
        candidates_df,
        limit=max(cfg.top_n_candidates + len(positions_df) + 10, 20),
    )
    market_context["hot_sectors"] = (
        [item["board_name"] for item in board_strength[:3]] if board_strength else summarize_hot_sectors(candidates_df)
    )
    market_context["candidate_recommendation_source"] = candidate_recommendation_source
    market_context["candidate_realtime_price_source"] = candidate_realtime_price_source
    market_context["enable_live_candidate_actionability"] = True

    _log("输出持仓组合诊断")
    analysis = analyze_portfolio_snapshot(
        positions_df=positions_df,
        market_df=market_df,
        candidate_df=candidates_df,
        report_date=cfg.report_date,
        market_context=market_context,
    )
    report_text = build_portfolio_markdown_report(analysis)
    report_html = build_portfolio_html_report(analysis)

    report_path = cfg.output_dir / "portfolio_analysis_report.md"
    report_html_path = cfg.output_dir / "portfolio_analysis_report.html"
    positions_path = cfg.output_dir / "portfolio_positions_snapshot.csv"
    report_path.write_text(report_text, encoding="utf-8")
    report_html_path.write_text(report_html, encoding="utf-8")
    analysis["positions"].to_csv(positions_path, index=False, encoding="utf-8-sig")
    latest_positions_path = save_latest_positions_snapshot(positions_df, cfg.latest_positions_path)

    analysis["report_path"] = report_path
    analysis["report_html_path"] = report_html_path
    analysis["positions_path"] = positions_path
    analysis["latest_positions_path"] = latest_positions_path
    return analysis
