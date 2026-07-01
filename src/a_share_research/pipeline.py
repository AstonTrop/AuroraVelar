from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import PipelineConfig
from .data_sources import (
    attach_rating_score,
    build_raw_bundle,
    dedupe_standard_records,
    fetch_eastmoney_research_for_codes,
    fetch_price_snapshot,
)
from .reporting import build_html_report
from .scoring import score_candidates
from .utils import now_ts


def _log(msg: str) -> None:
    print(f"[{now_ts()}] {msg}")


def _select_prefetch_codes(
    raw_records: pd.DataFrame,
    forecast_metrics: pd.DataFrame,
    target_metrics: pd.DataFrame,
    composite_metrics: pd.DataFrame,
    price_snapshot: pd.DataFrame,
    cfg: PipelineConfig,
) -> list[str]:
    relaxed_min_inst = max(2, cfg.min_institutions - 2)

    prelim = score_candidates(
        raw_records=raw_records,
        forecast_metrics=forecast_metrics,
        target_metrics=target_metrics,
        composite_metrics=composite_metrics,
        price_snapshot=price_snapshot,
        report_date=cfg.report_date,
        window_days=cfg.window_days,
        min_institutions=relaxed_min_inst,
        price_limit=cfg.price_limit,
        top_n=cfg.prefetch_research_limit,
        diversity_weight=cfg.diversity_weight,
        rating_weight=cfg.rating_weight,
        upside_weight=cfg.upside_weight,
        consistency_weight=cfg.consistency_weight,
        focus_theme=cfg.focus_theme,
        focus_boost_weight=cfg.focus_boost_weight,
    )

    if prelim.empty:
        if forecast_metrics.empty:
            return []
        backup = (
            forecast_metrics.sort_values(
                by=["forecast_report_count", "forecast_rating_score"],
                ascending=[False, False],
            )
            .head(cfg.prefetch_research_limit)
            .copy()
        )
        return backup["code"].astype(str).tolist()

    return prelim["code"].astype(str).tolist()


def run_pipeline(cfg: PipelineConfig) -> dict[str, Path | int]:
    cfg.validate()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    _log("开始抓取基础数据源: 东财盈利预测 + 新浪机构评级")
    raw_base, forecast_metrics, target_metrics, composite_metrics = build_raw_bundle(report_date=cfg.report_date)
    raw_base = dedupe_standard_records(raw_base)
    raw_base = attach_rating_score(raw_base)

    _log("抓取价格快照并计算收盘价口径")
    price_snapshot = fetch_price_snapshot(codes=None)

    _log("计算预筛候选并定向抓取东财个股研报")
    prefetch_codes = _select_prefetch_codes(
        raw_records=raw_base,
        forecast_metrics=forecast_metrics,
        target_metrics=target_metrics,
        composite_metrics=composite_metrics,
        price_snapshot=price_snapshot,
        cfg=cfg,
    )
    if prefetch_codes:
        eastmoney_reports = fetch_eastmoney_research_for_codes(prefetch_codes)
    else:
        eastmoney_reports = pd.DataFrame(columns=raw_base.columns)

    merged_raw = pd.concat([raw_base, eastmoney_reports], ignore_index=True)
    merged_raw = dedupe_standard_records(merged_raw)
    merged_raw = attach_rating_score(merged_raw)

    _log("执行硬筛选与综合打分")
    final_candidates = score_candidates(
        raw_records=merged_raw,
        forecast_metrics=forecast_metrics,
        target_metrics=target_metrics,
        composite_metrics=composite_metrics,
        price_snapshot=price_snapshot,
        report_date=cfg.report_date,
        window_days=cfg.window_days,
        min_institutions=cfg.min_institutions,
        price_limit=cfg.price_limit,
        top_n=cfg.top_n,
        diversity_weight=cfg.diversity_weight,
        rating_weight=cfg.rating_weight,
        upside_weight=cfg.upside_weight,
        consistency_weight=cfg.consistency_weight,
        focus_theme=cfg.focus_theme,
        focus_boost_weight=cfg.focus_boost_weight,
    )

    top_csv = cfg.output_dir / "candidates_top15.csv"
    raw_csv = cfg.output_dir / "research_raw_snapshot.csv"
    html_file = cfg.output_dir / "a_share_research_report.html"

    final_candidates.to_csv(top_csv, index=False, encoding="utf-8-sig")
    merged_raw.to_csv(raw_csv, index=False, encoding="utf-8-sig")

    _log("生成静态 HTML 可视化报告")
    build_html_report(
        candidates_df=final_candidates,
        raw_records_df=merged_raw,
        output_path=html_file,
        report_date=str(cfg.report_date),
        window_days=cfg.window_days,
        price_limit=cfg.price_limit,
        min_institutions=cfg.min_institutions,
        top_n=cfg.top_n,
        focus_theme=cfg.focus_theme,
    )

    _log(f"完成: 候选数={len(final_candidates)}")

    return {
        "top_csv": top_csv,
        "raw_csv": raw_csv,
        "html_report": html_file,
        "candidate_count": len(final_candidates),
    }
