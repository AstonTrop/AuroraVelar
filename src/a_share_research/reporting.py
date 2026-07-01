from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
from jinja2 import Template

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>A股多机构研报筛选报告</title>
  <style>
    body { font-family: "PingFang SC", "Microsoft YaHei", sans-serif; background:#f3f6fb; color:#1f2937; margin:0; }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 24px; }
    .card { background:#ffffff; border-radius:12px; padding:18px; margin-bottom:16px; box-shadow: 0 8px 24px rgba(0,0,0,0.06); }
    h1, h2 { margin: 8px 0 12px 0; }
    .meta { display:flex; gap:12px; flex-wrap:wrap; }
    .badge { background:#eef2ff; color:#3730a3; border-radius:999px; padding:6px 12px; font-size:13px; }
    table { width:100%; border-collapse: collapse; font-size:13px; }
    th, td { border:1px solid #e5e7eb; padding:6px 8px; text-align:left; }
    th { background:#f8fafc; }
    .muted { color:#6b7280; font-size:12px; }
    .insight-grid { display:grid; grid-template-columns:1fr; gap:12px; }
    .insight-item { border:1px solid #e5e7eb; border-radius:10px; padding:12px; background:#fafcff; }
    .insight-head { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:6px; }
    .tag { border-radius:999px; padding:4px 10px; font-size:12px; background:#e0f2fe; color:#075985; }
    .reason { margin:6px 0 0 0; }
    .reason li { margin:2px 0; }
  </style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>A股多机构研报筛股（券商研报严格版）</h1>
    <div class="meta">
      <span class="badge">报告日期: {{ report_date }}</span>
      <span class="badge">窗口: 近{{ window_days }}天</span>
      <span class="badge">市场: 沪深主板</span>
      <span class="badge">价格阈值: 收盘价 &lt; {{ price_limit }} 元</span>
      <span class="badge">机构门槛: ≥ {{ min_institutions }} 家</span>
      {% if focus_theme == "compute_power" %}
      <span class="badge">主题偏置: 算力优先</span>
      {% endif %}
    </div>
    <p class="muted">说明: 本报告仅用于研究辅助，不构成投资建议。</p>
  </div>

  <div class="card">
    <h2>Top{{ top_n }} 候选列表</h2>
    {{ top_table | safe }}
  </div>

  <div class="card">
    <h2>综合评分排名</h2>
    {{ bar_chart | safe }}
  </div>

  <div class="card">
    <h2>收盘价与目标上行空间</h2>
    {{ scatter_chart | safe }}
  </div>

  <div class="card">
    <h2>机构覆盖度</h2>
    {{ inst_chart | safe }}
  </div>

  <div class="card">
    <h2>候选股介绍与推荐理由</h2>
    {% if candidate_insights and candidate_insights|length > 0 %}
    <div class="insight-grid">
      {% for item in candidate_insights %}
      <div class="insight-item">
        <div class="insight-head">
          <strong>{{ item.name }}（{{ item.code }}）</strong>
          <span class="tag">收盘价 {{ item.close_price }} 元</span>
          <span class="tag">机构覆盖 {{ item.institution_count }} 家</span>
          <span class="tag">综合分 {{ item.composite_score }}</span>
        </div>
        <div>{{ item.introduction }}</div>
        <ul class="reason">
          <li>{{ item.reason_1 }}</li>
          <li>{{ item.reason_2 }}</li>
          <li>{{ item.reason_3 }}</li>
          <li>{{ item.reason_4 }}</li>
        </ul>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <p>暂无可展示的候选股介绍。</p>
    {% endif %}
  </div>

  <div class="card">
    <h2>研报原始快照（前200行）</h2>
    {{ raw_table | safe }}
  </div>
</div>
</body>
</html>
"""


def _fmt_candidate_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out
    out["close_price"] = out["close_price"].map(lambda x: f"{x:.2f}")
    out["target_upside_pct"] = out["target_upside_pct"].map(lambda x: f"{x:.2f}%")
    for col in [
        "rating_strength_score",
        "diversity_score",
        "upside_score",
        "consistency_score",
        "composite_score",
        "focus_theme_score",
        "focus_bonus",
        "adjusted_score",
    ]:
        if col not in out.columns:
            continue
        out[col] = out[col].map(lambda x: f"{x:.2f}")
    return out


def _top_institutions_by_code(raw_records_df: pd.DataFrame, code: str, top_n: int = 3) -> str:
    if raw_records_df.empty or "institution" not in raw_records_df.columns:
        return "暂无"
    code_df = raw_records_df[raw_records_df["code"] == code].copy()
    if code_df.empty:
        return "暂无"
    inst = code_df["institution"].astype(str)
    inst = inst[~inst.str.contains("_AGGREGATE", na=False)]
    inst = inst[inst.str.strip() != ""]
    if inst.empty:
        return "暂无"
    return "、".join(inst.value_counts().head(top_n).index.tolist())


def _primary_industry_by_code(raw_records_df: pd.DataFrame, code: str) -> str:
    if raw_records_df.empty or "industry" not in raw_records_df.columns:
        return "行业待补充"
    code_df = raw_records_df[raw_records_df["code"] == code].copy()
    if code_df.empty:
        return "行业待补充"
    ind = code_df["industry"].astype(str).str.strip()
    ind = ind[(ind != "") & (ind != "nan")]
    if ind.empty:
        return "行业待补充"
    return ind.value_counts().idxmax()


def _latest_report_date_by_code(raw_records_df: pd.DataFrame, code: str) -> str:
    if raw_records_df.empty or "pub_date" not in raw_records_df.columns:
        return "未知"
    code_df = raw_records_df[raw_records_df["code"] == code].copy()
    if code_df.empty:
        return "未知"
    dt = pd.to_datetime(code_df["pub_date"], errors="coerce").dropna()
    if dt.empty:
        return "未知"
    return dt.max().strftime("%Y-%m-%d")


def _build_candidate_insights(candidates_df: pd.DataFrame, raw_records_df: pd.DataFrame) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    if candidates_df.empty:
        return insights

    for _, row in candidates_df.iterrows():
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        industry = _primary_industry_by_code(raw_records_df, code)
        top_inst = _top_institutions_by_code(raw_records_df, code)
        latest_date = _latest_report_date_by_code(raw_records_df, code)
        close_price = float(row.get("close_price", 0.0))
        institution_count = int(row.get("institution_count", 0))
        source_group_count = int(row.get("source_group_count", 0))
        rating_score = float(row.get("rating_strength_score", 0.0))
        upside_pct = float(row.get("target_upside_pct", 0.0))
        consistency_score = float(row.get("consistency_score", 0.0))
        composite_score = float(row.get("composite_score", 0.0))
        adjusted_score = float(row.get("adjusted_score", composite_score))
        focus_theme_score = float(row.get("focus_theme_score", 0.0))
        focus_bonus = float(row.get("focus_bonus", 0.0))
        buy_signal = str(row.get("buy_signal", ""))

        introduction = (
            f"{name}属于{industry}，当前纳入候选的收盘价为{close_price:.2f}元。"
            f"近90天统计到{institution_count}家机构覆盖，最近可见研报日期为{latest_date}，"
            f"高频覆盖机构包括{top_inst}。"
        )

        reason_1 = f"机构多样性: 覆盖机构{institution_count}家，跨源覆盖{source_group_count}类，形成多机构共识基础。"
        reason_2 = f"评级强度: 评级强度分{rating_score:.2f}，当前信号为“{buy_signal}”。"
        reason_3 = f"目标空间: 模型估算目标上行空间约{upside_pct:.2f}%。"
        if focus_theme_score > 0:
            reason_4 = (
                f"主题偏置与综合: 算力相关度{focus_theme_score:.2f}，主题加分{focus_bonus:.2f}，"
                f"基础分{composite_score:.2f}，调整后综合分{adjusted_score:.2f}。"
            )
        else:
            reason_4 = f"一致性与综合: 跨源一致性分{consistency_score:.2f}，综合评分{composite_score:.2f}。"

        insights.append(
            {
                "code": code,
                "name": name,
                "close_price": f"{close_price:.2f}",
                "institution_count": str(institution_count),
                "composite_score": f"{composite_score:.2f}",
                "adjusted_score": f"{adjusted_score:.2f}",
                "introduction": introduction,
                "reason_1": reason_1,
                "reason_2": reason_2,
                "reason_3": reason_3,
                "reason_4": reason_4,
            }
        )

    return insights


def build_html_report(
    candidates_df: pd.DataFrame,
    raw_records_df: pd.DataFrame,
    output_path: Path,
    report_date: str,
    window_days: int,
    price_limit: float,
    min_institutions: int,
    top_n: int,
    focus_theme: str = "none",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    top_table_df = _fmt_candidate_table(candidates_df)
    top_table = top_table_df.to_html(index=False, escape=False) if not top_table_df.empty else "<p>暂无符合条件的候选股票。</p>"

    if candidates_df.empty:
        bar_chart_html = "<p>暂无数据</p>"
        scatter_chart_html = "<p>暂无数据</p>"
        inst_chart_html = "<p>暂无数据</p>"
    else:
        bar_fig = px.bar(
            candidates_df,
            x="name",
            y="composite_score",
            hover_data=["code", "close_price", "institution_count", "buy_signal"],
            title="Top 候选综合分",
            color="composite_score",
            color_continuous_scale="Blues",
        )
        bar_fig.update_layout(xaxis_title="股票", yaxis_title="综合分", height=450)

        scatter_fig = px.scatter(
            candidates_df,
            x="close_price",
            y="target_upside_pct",
            color="buy_signal",
            size="institution_count",
            hover_data=["code", "composite_score"],
            title="收盘价 vs 目标上行空间",
        )
        scatter_fig.update_layout(xaxis_title="收盘价(元)", yaxis_title="目标上行空间(%)", height=450)

        inst_fig = px.bar(
            candidates_df.sort_values("institution_count", ascending=False),
            x="name",
            y="institution_count",
            color="institution_count",
            color_continuous_scale="Teal",
            title="机构覆盖家数",
        )
        inst_fig.update_layout(xaxis_title="股票", yaxis_title="机构数", height=420)

        bar_chart_html = bar_fig.to_html(full_html=False, include_plotlyjs="cdn")
        scatter_chart_html = scatter_fig.to_html(full_html=False, include_plotlyjs=False)
        inst_chart_html = inst_fig.to_html(full_html=False, include_plotlyjs=False)

    raw_show = raw_records_df.copy()
    if "pub_date" in raw_show.columns:
        raw_show["pub_date"] = pd.to_datetime(raw_show["pub_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    raw_table = raw_show.head(200).to_html(index=False, escape=False)
    candidate_insights = _build_candidate_insights(candidates_df=candidates_df, raw_records_df=raw_records_df)

    html = Template(HTML_TEMPLATE).render(
        report_date=report_date,
        window_days=window_days,
        price_limit=f"{price_limit:.2f}",
        min_institutions=min_institutions,
        top_n=top_n,
        focus_theme=focus_theme,
        top_table=top_table,
        bar_chart=bar_chart_html,
        scatter_chart=scatter_chart_html,
        inst_chart=inst_chart_html,
        candidate_insights=candidate_insights,
        raw_table=raw_table,
    )

    output_path.write_text(html, encoding="utf-8")
