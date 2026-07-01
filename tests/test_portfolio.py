from datetime import date
from datetime import datetime
import argparse
from pathlib import Path
import time

import pandas as pd
from run_portfolio_analysis import _load_positions
from src.a_share_research import data_sources as data_sources_module

from src.a_share_research.portfolio import (
    analyze_portfolio_snapshot,
    attach_board_context_to_candidates,
    attach_candidate_actionability,
    attach_technical_context_to_candidates,
    build_board_leader_candidates,
    build_market_brief_html_report,
    build_market_brief_markdown_report,
    build_portfolio_html_report,
    build_portfolio_markdown_report,
    load_cached_market_snapshot,
    load_any_cached_market_snapshot,
    load_fallback_candidates_snapshot,
    load_latest_positions_snapshot,
    normalize_board_snapshot,
    normalize_index_snapshot,
    parse_positions_text,
    refresh_candidate_realtime_prices,
    resolve_analysis_paths,
    save_market_snapshot_cache,
    save_latest_positions_snapshot,
    summarize_technical_profile,
    summarize_fundamentals_from_abstract,
    summarize_fundamentals_from_indicator,
    summarize_hot_sectors,
    summarize_market_overview,
)


def test_parse_positions_text_normalizes_template() -> None:
    raw_text = """
    000725 | 京东方A | 7.4% | 8.614
    600055 | 万东医疗 | 12 | 14.22
    600795|国电电力|20.5%|4.31
    """

    out = parse_positions_text(raw_text)

    assert list(out["code"]) == ["000725", "600055", "600795"]
    assert list(out["name"]) == ["京东方A", "万东医疗", "国电电力"]
    assert out.iloc[0]["position_pct"] == 7.4
    assert out.iloc[1]["position_pct"] == 12.0
    assert out.iloc[2]["cost_price"] == 4.31


def test_parse_positions_text_supports_share_and_available_counts() -> None:
    raw_text = "600522 | 中天科技 | 49.8% | 60.771 | 100 | 0"

    out = parse_positions_text(raw_text)

    assert out.iloc[0]["share_count"] == 100
    assert out.iloc[0]["available_count"] == 0


def test_analyze_portfolio_snapshot_flags_concentration_and_actions() -> None:
    positions = pd.DataFrame(
        [
            {"code": "000001", "name": "进攻A", "position_pct": 36.0, "cost_price": 10.0, "share_count": 300, "available_count": 300},
            {"code": "000002", "name": "稳健B", "position_pct": 24.0, "cost_price": 8.0, "share_count": 200, "available_count": 200},
            {"code": "000003", "name": "弱势C", "position_pct": 18.0, "cost_price": 12.0, "share_count": 100, "available_count": 0},
            {"code": "000004", "name": "潜力D", "position_pct": 8.0, "cost_price": 9.0, "share_count": 100, "available_count": 100},
        ]
    )
    market = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "进攻A",
                "current_price": 11.8,
                "day_change_pct": 2.5,
                "industry": "AI硬件",
                "research_score": 62.0,
                "fundamental_score": 54.0,
                "fundamental_summary": "营收稳步增长，ROE中性。",
                "institution_count": 8,
                "source_group_count": 3,
                "target_upside_pct": 15.0,
                "technical_bias": "高于20日和60日均线",
            },
            {
                "code": "000002",
                "name": "稳健B",
                "current_price": 8.6,
                "day_change_pct": 0.8,
                "industry": "AI硬件",
                "research_score": 82.0,
                "fundamental_score": 82.0,
                "fundamental_summary": "归母净利润增长，ROE较强。",
                "institution_count": 11,
                "source_group_count": 4,
                "target_upside_pct": 18.0,
                "technical_bias": "站上20日均线",
            },
            {
                "code": "000003",
                "name": "弱势C",
                "current_price": 9.6,
                "day_change_pct": -3.2,
                "industry": "消费电子",
                "research_score": 38.0,
                "fundamental_score": 35.0,
                "fundamental_summary": "净利润承压，毛利率偏弱。",
                "institution_count": 3,
                "source_group_count": 1,
                "target_upside_pct": 4.0,
                "technical_bias": "跌破60日均线",
            },
            {
                "code": "000004",
                "name": "潜力D",
                "current_price": 9.4,
                "day_change_pct": 1.1,
                "industry": "云计算",
                "research_score": 79.0,
                "fundamental_score": 76.0,
                "fundamental_summary": "收入增长稳定，现金流正常。",
                "institution_count": 9,
                "source_group_count": 3,
                "target_upside_pct": 22.0,
                "technical_bias": "接近平台突破位",
            },
        ]
    )
    replacements = pd.DataFrame(
        [
            {"code": "600001", "name": "替补一号", "adjusted_score": 88.0, "close_price": 15.2},
            {"code": "600002", "name": "替补二号", "adjusted_score": 84.5, "close_price": 12.8},
        ]
    )

    analysis = analyze_portfolio_snapshot(
        positions_df=positions,
        market_df=market,
        candidate_df=replacements,
        report_date=date(2026, 6, 29),
        market_context={
            "report_date": "2026-06-29",
            "generated_at": "2026-06-29 14:30:00",
            "trend_label": "偏强震荡",
            "breadth_label": "上涨家数占优",
            "risk_level": "中等",
            "market_temperature": "震荡",
            "max_stock_exposure_pct": 70.0,
            "activity_label": "活跃度中性",
            "candidate_snapshot_source": "live",
            "board_snapshot_source": "live",
            "index_snapshot": [
                {"name": "上证指数", "price": 3200.1, "change_pct": 0.8},
                {"name": "创业板指", "price": 2100.2, "change_pct": 1.6},
            ],
            "hot_sectors": ["算力", "PCB", "光模块"],
            "board_strength": [
                {
                    "board_type": "行业",
                    "board_name": "AI硬件",
                    "change_pct": 1.8,
                    "up_ratio": 0.72,
                    "leader": "强势龙头",
                    "leader_change_pct": 8.5,
                    "board_action": "可参与",
                },
                {
                    "board_type": "行业",
                    "board_name": "消费电子",
                    "change_pct": -1.2,
                    "up_ratio": 0.25,
                    "leader": "",
                    "leader_change_pct": 0.0,
                    "board_action": "回避",
                },
                {
                    "board_type": "行业",
                    "board_name": "云计算",
                    "change_pct": 2.0,
                    "up_ratio": 0.80,
                    "leader": "云龙头",
                    "leader_change_pct": 7.2,
                    "board_action": "可参与",
                },
            ],
        },
    )

    overview = analysis["overview"]
    decisions = analysis["positions"]

    assert overview["stock_exposure_pct"] == 86.0
    assert overview["cash_pct"] == 14.0
    assert overview["top_holding_pct"] == 36.0
    assert "单票仓位过高" in overview["risk_flags"]
    assert "行业重复暴露" in overview["risk_flags"]
    assert overview["available_sell_ratio_pct"] == 85.7

    by_code = decisions.set_index("code")
    assert by_code.loc["000001", "diagnosis"] == "可减仓"
    assert by_code.loc["000001", "portfolio_tag"] == "减仓候选"
    assert by_code.loc["000001", "trade_setup_score"] < by_code.loc["000004", "trade_setup_score"]
    assert "交易分" in by_code.loc["000004", "reason"]
    assert by_code.loc["000003", "diagnosis"] == "应替换"
    assert by_code.loc["000003", "portfolio_tag"] == "清仓候选"
    assert "今日不可卖" in by_code.loc["000003", "execution_note"]
    assert by_code.loc["000004", "diagnosis"] == "可加仓"
    assert by_code.loc["000004", "portfolio_tag"] == "观察加仓"
    assert by_code.loc["000004", "board_action"] == "可参与"

    report = build_portfolio_markdown_report(analysis)
    assert "## 1. 实时盘面" in report
    assert "## 2. 板块强弱" in report
    assert "## 3. 你的持仓" in report
    assert "## 4. 执行约束" in report
    assert "## 5. 候选替补股" in report
    assert "## 6. 操作建议" in report
    assert "偏强震荡" in report
    assert "2026-06-29 14:30:00" in report
    assert "今天盘中可执行" in report
    assert "明天优先处理" in report
    assert "盘后复盘结论" in report
    assert "归母净利润增长，ROE较强。" in report
    assert "弱势C" in report
    assert "持仓/可卖" in report
    assert "交易分" in report
    assert "今日不可卖" in report
    assert "AI硬件" in report

    html_report = build_portfolio_html_report(analysis)
    assert "<html" in html_report.lower()
    assert "市场环境" in html_report
    assert "投资结论" in html_report
    assert "多空要点" in html_report
    assert "跟踪指标" in html_report
    assert "持仓股数" in html_report
    assert "可卖股数" in html_report
    assert "可卖比例" in html_report


def test_summarize_market_overview_builds_trend_and_breadth_labels() -> None:
    index_df = pd.DataFrame(
        [
            {"代码": "sh000001", "名称": "上证指数", "最新价": 3200.0, "涨跌幅": 0.6},
            {"代码": "sz399001", "名称": "深证成指", "最新价": 10200.0, "涨跌幅": 1.2},
            {"代码": "sz399006", "名称": "创业板指", "最新价": 2100.0, "涨跌幅": 1.8},
            {"代码": "sh000300", "名称": "沪深300", "最新价": 3900.0, "涨跌幅": 0.3},
        ]
    )
    breadth_df = pd.DataFrame(
        [
            {"code": "000001", "name": "A", "last_price": 10.0, "prev_close": 9.8},
            {"code": "000002", "name": "B", "last_price": 8.0, "prev_close": 7.9},
            {"code": "000003", "name": "C", "last_price": 6.0, "prev_close": 6.1},
            {"code": "000004", "name": "D", "last_price": 5.0, "prev_close": 4.8},
        ]
    )

    out = summarize_market_overview(index_df=index_df, breadth_df=breadth_df, report_date=date(2026, 6, 29))

    assert out["trend_label"] == "偏强震荡"
    assert out["breadth_label"] == "上涨家数占优"
    assert out["risk_level"] == "中等"
    assert out["market_temperature"] == "强势"
    assert out["limit_up_count"] == 0
    assert out["limit_down_count"] == 0
    assert out["hot_sectors"] == []
    assert len(out["index_snapshot"]) == 4


def test_board_snapshot_normalization_and_candidate_market_ranking() -> None:
    raw_board = pd.DataFrame(
        [
            {"板块名称": "游戏", "涨跌幅": 2.5, "上涨家数": 20, "下跌家数": 5, "领涨股票": "完美世界", "领涨股票-涨跌幅": 8.8},
            {"板块名称": "煤炭", "涨跌幅": -1.2, "上涨家数": 3, "下跌家数": 25, "领涨股票": "煤炭A", "领涨股票-涨跌幅": 1.1},
        ]
    )
    board_df = normalize_board_snapshot(raw_board, board_type="行业")
    board_strength = board_df.to_dict("records")
    candidates = pd.DataFrame(
        [
            {"code": "002624", "name": "完美世界", "industry": "游戏", "adjusted_score": 75.0, "close_price": 12.0},
            {"code": "600100", "name": "煤炭A", "industry": "煤炭", "adjusted_score": 80.0, "close_price": 9.0},
        ]
    )

    ranked = attach_board_context_to_candidates(candidates, board_strength)

    assert list(board_df["board_action"]) == ["可参与", "回避"]
    assert ranked.iloc[0]["code"] == "002624"
    assert ranked.iloc[0]["board_action"] == "可参与"


def test_candidate_board_context_uses_name_keywords_when_industry_missing() -> None:
    board_strength = [
        {
            "board_name": "游戏",
            "board_action": "可参与",
            "change_pct": 2.6,
            "up_ratio": 0.7,
        }
    ]
    candidates = pd.DataFrame(
        [
            {"code": "002624", "name": "完美世界", "industry": "", "adjusted_score": 75.0, "close_price": 12.0},
        ]
    )

    ranked = attach_board_context_to_candidates(candidates, board_strength)

    assert ranked.iloc[0]["matched_board"] == "游戏"
    assert ranked.iloc[0]["board_action"] == "可参与"


def test_board_snapshot_normalizes_stock_sector_spot_shape() -> None:
    raw_board = pd.DataFrame(
        [
            {
                "板块": "玻璃行业",
                "涨跌幅": 2.85,
                "公司家数": 19,
                "股票代码": "000012",
                "股票名称": "南玻A",
                "个股-当前价": 6.12,
                "个股-涨跌幅": 10.02,
            },
            {"板块": "船舶制造", "涨跌幅": 0.81, "公司家数": 8, "股票名称": "ST亚光", "个股-涨跌幅": 2.05},
        ]
    )

    out = normalize_board_snapshot(raw_board, board_type="行业")

    assert list(out["board_name"]) == ["玻璃行业", "船舶制造"]
    assert out.iloc[0]["leader"] == "南玻A"
    assert out.iloc[0]["leader_code"] == "000012"
    assert out.iloc[0]["leader_price"] == 6.12
    assert out.iloc[0]["leader_change_pct"] == 10.02
    assert out.iloc[0]["board_action"] == "只观察"


def test_build_board_leader_candidates_filters_to_low_price_main_board() -> None:
    board_df = pd.DataFrame(
        [
            {
                "board_type": "异动",
                "board_name": "玻璃基板",
                "change_pct": 6.0,
                "up_ratio": 0.0,
                "leader": "南玻A",
                "leader_code": "000012",
                "leader_price": 6.12,
                "leader_change_pct": 10.0,
                "board_action": "只观察",
            },
            {
                "board_type": "异动",
                "board_name": "游戏",
                "change_pct": 3.0,
                "up_ratio": 0.0,
                "leader": "ST游戏",
                "leader_code": "002000",
                "leader_price": 4.0,
                "leader_change_pct": 5.0,
                "board_action": "只观察",
            },
            {
                "board_type": "异动",
                "board_name": "科创",
                "change_pct": 8.0,
                "up_ratio": 0.0,
                "leader": "科创A",
                "leader_code": "688001",
                "leader_price": 8.0,
                "leader_change_pct": 6.0,
                "board_action": "只观察",
            },
        ]
    )

    out = build_board_leader_candidates(board_df, price_limit=15.0, top_n=5)

    assert list(out["code"]) == ["000012"]
    assert out.iloc[0]["name"] == "南玻A"
    assert out.iloc[0]["industry"] == "玻璃基板"
    assert out.iloc[0]["candidate_source"] == "board_hot_leader"


def test_build_board_leader_candidates_fills_missing_code_from_price_snapshot() -> None:
    board_df = pd.DataFrame(
        [
            {
                "board_type": "行业",
                "board_name": "玻璃制造",
                "change_pct": 4.6,
                "up_ratio": 1.0,
                "leader": "南  玻Ａ",
                "leader_code": "",
                "leader_price": float("nan"),
                "leader_change_pct": 10.0,
                "board_action": "可参与",
            },
        ]
    )
    price_snapshot = pd.DataFrame(
        [{"code": "000012", "name": "南玻A", "close_price": 6.12, "last_price": 6.12}]
    )

    out = build_board_leader_candidates(board_df, price_limit=15.0, top_n=5, price_snapshot=price_snapshot)

    assert list(out["code"]) == ["000012"]
    assert out.iloc[0]["close_price"] == 6.12
    assert out.iloc[0]["industry"] == "玻璃制造"


def test_candidate_technical_context_changes_ranking_and_adds_summary() -> None:
    candidates = pd.DataFrame(
        [
            {"code": "000001", "name": "弱技术", "market_adjusted_score": 80.0, "adjusted_score": 80.0},
            {"code": "000002", "name": "强技术", "market_adjusted_score": 76.0, "adjusted_score": 76.0},
        ]
    )
    technical_map = {
        "000001": {"technical_score": 35.0, "technical_summary": "跌破60日均线，MACD偏空"},
        "000002": {"technical_score": 90.0, "technical_summary": "高于20日和60日均线，MACD偏多，接近20日新高"},
    }

    out = attach_technical_context_to_candidates(
        candidates,
        report_date=date(2026, 6, 30),
        profile_fetcher=lambda code, report_date: technical_map[code],
        limit=2,
    )

    assert out.iloc[0]["code"] == "000002"
    assert "MACD偏多" in out.iloc[0]["technical_summary"]
    assert out.iloc[0]["technical_score"] == 90.0


def test_refresh_candidate_realtime_prices_uses_last_price_for_display() -> None:
    candidates = pd.DataFrame(
        [
            {"code": "000001", "name": "候选A", "close_price": 10.0, "market_adjusted_score": 80.0},
            {"code": "000002", "name": "候选B", "close_price": 8.0, "market_adjusted_score": 75.0},
        ]
    )
    quote = pd.DataFrame(
        [
            {"code": "000001", "name": "候选A", "close_price": 11.0, "last_price": 12.34, "prev_close": 10.8, "trade_time": "14:30:00"},
        ]
    )

    out, source = refresh_candidate_realtime_prices(
        candidates,
        price_fetcher=lambda codes: quote,
        limit=5,
    )

    by_code = out.set_index("code")
    assert source == "live_quote"
    assert by_code.loc["000001", "close_price"] == 12.34
    assert by_code.loc["000001", "snapshot_close_price"] == 10.0
    assert by_code.loc["000001", "realtime_price_source"] == "live_quote"
    assert by_code.loc["000002", "close_price"] == 8.0
    assert by_code.loc["000002", "realtime_price_source"] == "stale_snapshot"


def test_attach_candidate_actionability_blocks_limit_up_and_cash_shortfall() -> None:
    candidates = pd.DataFrame(
        [
            {"code": "000001", "name": "可买A", "close_price": 6.0, "market_adjusted_score": 78.0},
            {"code": "000002", "name": "封板B", "close_price": 5.2, "market_adjusted_score": 88.0},
            {"code": "000003", "name": "太贵C", "close_price": 18.0, "market_adjusted_score": 82.0},
        ]
    )

    bid_ask = {
        "000001": pd.DataFrame(
            [
                {"item": "最新", "value": 6.0},
                {"item": "涨幅", "value": 2.1},
                {"item": "sell_1", "value": 6.01},
                {"item": "buy_1", "value": 6.0},
            ]
        ),
        "000002": pd.DataFrame(
            [
                {"item": "最新", "value": 5.2},
                {"item": "涨幅", "value": 10.02},
                {"item": "sell_1", "value": "-"},
                {"item": "buy_1", "value": 5.2},
            ]
        ),
        "000003": pd.DataFrame(
            [
                {"item": "最新", "value": 18.0},
                {"item": "涨幅", "value": 1.0},
                {"item": "sell_1", "value": 18.01},
                {"item": "buy_1", "value": 18.0},
            ]
        ),
    }

    out, source = attach_candidate_actionability(
        candidates,
        estimated_cash_amount=1200.0,
        bid_ask_fetcher=lambda code: bid_ask[code],
        limit=5,
    )

    by_code = out.set_index("code")
    assert source == "live_bid_ask"
    assert by_code.loc["000001", "actionability"] == "可执行观察"
    assert by_code.loc["000002", "actionability"] == "涨停封板不可追"
    assert by_code.loc["000003", "actionability"] == "现金不足"
    assert by_code.loc["000002", "is_limit_up_sealed"]
    assert by_code.loc["000003", "min_lot_cost"] == 1800.0


def test_unchecked_candidate_actionability_sorts_after_checked_quotes() -> None:
    candidates = pd.DataFrame(
        [
            {"code": "000001", "name": "封板A", "close_price": 5.2, "market_adjusted_score": 88.0},
            {"code": "000002", "name": "未查B", "close_price": 7.0, "market_adjusted_score": 99.0},
        ]
    )

    out, _ = attach_candidate_actionability(
        candidates,
        estimated_cash_amount=2000.0,
        bid_ask_fetcher=lambda code: pd.DataFrame(
            [
                {"item": "最新", "value": 5.2},
                {"item": "涨幅", "value": 10.02},
                {"item": "sell_1", "value": "-"},
                {"item": "buy_1", "value": 5.2},
            ]
        ),
        limit=1,
    )

    by_code = out.set_index("code")
    assert by_code.loc["000002", "actionability_rank"] < by_code.loc["000001", "actionability_rank"]
    assert by_code.loc["000001", "actionability"] == "盘口待核查"


def test_report_separates_non_actionable_candidate_from_buy_recommendation() -> None:
    positions = pd.DataFrame(
        [
            {"code": "000001", "name": "弱势A", "position_pct": 30.0, "cost_price": 10.0, "share_count": 300, "available_count": 300},
        ]
    )
    market = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "弱势A",
                "current_price": 8.0,
                "day_change_pct": -2.0,
                "industry": "弱板块",
                "research_score": 35.0,
                "fundamental_score": 35.0,
                "fundamental_summary": "基本面偏弱。",
                "technical_bias": "跌破60日均线",
            },
        ]
    )
    candidates = pd.DataFrame(
        [
            {
                "code": "600707",
                "name": "封板候选",
                "industry": "热门板块",
                "close_price": 16.89,
                "market_adjusted_score": 95.0,
                "technical_score": 80.0,
                "technical_summary": "涨停突破",
                "board_action": "可参与",
                "board_change_pct": 4.0,
                "actionability": "涨停封板不可追",
                "actionability_rank": 80,
                "action_note": "涨停封板，卖一缺失，不建议排板追入。",
                "min_lot_cost": 1689.0,
            }
        ]
    )

    analysis = analyze_portfolio_snapshot(
        positions_df=positions,
        market_df=market,
        candidate_df=candidates,
        report_date=date(2026, 6, 30),
        market_context={"market_temperature": "强势", "breadth_label": "上涨家数占优"},
    )
    report = build_portfolio_markdown_report(analysis)

    assert "暂无盘中可执行候选" in report
    assert "强势但不可追观察" in report
    assert "涨停封板不可追" in report
    assert "替补方向优先看封板候选" not in analysis["recommendation_summary"]


def test_summarize_technical_profile_scores_breakout() -> None:
    hist = pd.DataFrame(
        {
            "收盘": [10 + i * 0.1 for i in range(70)],
        }
    )

    out = summarize_technical_profile(hist)

    assert out["technical_score"] >= 75.0
    assert "高于20日和60日均线" in out["technical_summary"]
    assert out["trend_score"] >= 80.0
    assert out["momentum_score"] >= 60.0
    assert "趋势多头" in out["setup_tags"]


def test_summarize_technical_profile_adds_trade_points_and_sources() -> None:
    hist = pd.DataFrame(
        {
            "收盘": [10 + i * 0.08 for i in range(80)],
        }
    )

    out = summarize_technical_profile(hist)

    assert out["buy_point"] > 0
    assert out["sell_point"] > out["buy_point"]
    assert out["stop_loss_point"] < out["buy_point"]
    assert "MA20" in out["technical_point_sources"]
    assert "20日高点" in out["technical_point_sources"]
    assert "买入点" in out["technical_plan"]
    assert "卖出/减仓点" in out["technical_plan"]
    assert "止损点" in out["technical_plan"]


def test_portfolio_report_includes_technical_trade_plan() -> None:
    positions = pd.DataFrame(
        [
            {"code": "000001", "name": "技术A", "position_pct": 10.0, "cost_price": 10.0, "share_count": 100, "available_count": 100},
        ]
    )
    market = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "技术A",
                "current_price": 11.0,
                "day_change_pct": 1.0,
                "industry": "测试行业",
                "research_score": 75.0,
                "fundamental_score": 70.0,
                "fundamental_summary": "基本面平稳。",
                "institution_count": 3,
                "source_group_count": 1,
                "target_upside_pct": 10.0,
                "technical_score": 80.0,
                "trend_score": 88.0,
                "momentum_score": 70.0,
                "rsi_score": 68.0,
                "volatility_score": 58.0,
                "setup_tags": "趋势多头",
                "technical_bias": "高于20日和60日均线，MACD偏多",
                "buy_point": 10.8,
                "sell_point": 12.2,
                "stop_loss_point": 10.1,
                "technical_point_sources": "买入点: MA20回踩确认；卖出/减仓点: 20日高点突破；止损点: 20日低点",
                "technical_plan": "买入点10.80；卖出/减仓点12.20；止损点10.10；来源: MA20/20日高低点",
            },
        ]
    )

    analysis = analyze_portfolio_snapshot(
        positions_df=positions,
        market_df=market,
        candidate_df=pd.DataFrame(),
        report_date=date(2026, 7, 1),
        market_context={"market_temperature": "震荡", "breadth_label": "上涨家数占优"},
    )
    report = build_portfolio_markdown_report(analysis)

    assert "技术计划" in report
    assert "买入点10.80" in report
    assert "卖出/减仓点12.20" in report
    assert "MA20回踩确认" in report


def test_normalize_index_snapshot_supports_em_shape() -> None:
    em_df = pd.DataFrame(
        [
            {"代码": "000001", "名称": "上证指数", "最新价": 3200.0, "涨跌幅": 0.6},
            {"代码": "399001", "名称": "深证成指", "最新价": 10200.0, "涨跌幅": 1.2},
            {"代码": "399006", "名称": "创业板指", "最新价": 2100.0, "涨跌幅": 1.8},
            {"代码": "000300", "名称": "沪深300", "最新价": 3900.0, "涨跌幅": 0.3},
        ]
    )

    out = normalize_index_snapshot(em_df, source="em")

    assert list(out["代码"]) == ["sh000001", "sz399001", "sz399006", "sh000300"]


def test_summarize_fundamentals_from_abstract_extracts_latest_metrics() -> None:
    abstract_df = pd.DataFrame(
        [
            {"选项": "常用指标", "指标": "归母净利润", "20260331": 12_000_000_000, "20251231": 38_000_000_000},
            {"选项": "常用指标", "指标": "营业总收入", "20260331": 85_000_000_000, "20251231": 260_000_000_000},
            {"选项": "常用指标", "指标": "净资产收益率", "20260331": 11.2, "20251231": 14.8},
            {"选项": "常用指标", "指标": "销售毛利率", "20260331": 18.5, "20251231": 19.1},
        ]
    )

    out = summarize_fundamentals_from_abstract(abstract_df)

    assert out["latest_period"] == "20260331"
    assert out["score"] >= 70.0
    assert "归母净利润" in out["summary"]
    assert "ROE" in out["summary"]


def test_summarize_fundamentals_from_indicator_extracts_latest_metrics() -> None:
    indicator_df = pd.DataFrame(
        [
            {
                "日期": "2025-12-31",
                "加权净资产收益率(%)": 9.2,
                "主营业务收入增长率(%)": 6.5,
                "净利润增长率(%)": 8.8,
                "资产负债率(%)": 52.0,
                "经营现金净流量与净利润的比率(%)": 66.0,
            },
            {
                "日期": "2026-03-31",
                "加权净资产收益率(%)": 13.8,
                "主营业务收入增长率(%)": 18.5,
                "净利润增长率(%)": 24.2,
                "资产负债率(%)": 48.3,
                "经营现金净流量与净利润的比率(%)": 105.0,
            },
        ]
    )

    out = summarize_fundamentals_from_indicator(indicator_df)

    assert out["latest_period"] == "20260331"
    assert out["score"] >= 80.0
    assert "加权ROE" in out["summary"]
    assert "净利增速24.2%" in out["summary"]


def test_summarize_hot_sectors_prefers_repeated_high_score_industries() -> None:
    candidate_df = pd.DataFrame(
        [
            {"code": "000001", "name": "A", "industry": "算力", "adjusted_score": 88.0},
            {"code": "000002", "name": "B", "industry": "算力", "adjusted_score": 84.0},
            {"code": "000003", "name": "C", "industry": "光模块", "adjusted_score": 87.0},
            {"code": "000004", "name": "D", "industry": "光模块", "adjusted_score": 86.0},
            {"code": "000005", "name": "E", "industry": "银行", "adjusted_score": 92.0},
            {"code": "000006", "name": "F", "industry": "", "adjusted_score": 95.0},
        ]
    )

    out = summarize_hot_sectors(candidate_df, top_n=2)

    assert out == ["光模块", "算力"]


def test_fetch_price_snapshot_falls_back_to_em_when_primary_source_is_empty(monkeypatch) -> None:
    empty_df = pd.DataFrame()
    em_df = pd.DataFrame(
        [
            {"代码": "000725", "名称": "京东方A", "最新价": 7.95, "昨收": 7.79, "时间戳": "15:00:00"},
            {"代码": "002624", "名称": "完美世界", "最新价": 11.54, "昨收": 11.30, "时间戳": "15:00:00"},
        ]
    )

    monkeypatch.setattr(data_sources_module.ak, "stock_zh_a_spot", lambda: empty_df)
    monkeypatch.setattr(data_sources_module.ak, "stock_zh_a_spot_em", lambda: em_df)

    out = data_sources_module.fetch_price_snapshot(codes=["000725"])

    assert list(out["code"]) == ["000725"]
    assert out.iloc[0]["close_price"] == 7.95


def test_fetch_price_snapshot_handles_missing_trade_time(monkeypatch) -> None:
    spot_df = pd.DataFrame(
        [
            {"代码": "000725", "名称": "京东方A", "最新价": 8.64, "昨收": 7.95},
        ]
    )

    monkeypatch.setattr(data_sources_module.ak, "stock_zh_a_spot", lambda: spot_df)
    monkeypatch.setattr(data_sources_module.ak, "stock_zh_a_spot_em", lambda: pd.DataFrame())

    out = data_sources_module.fetch_price_snapshot(codes=["000725"])

    assert list(out["code"]) == ["000725"]
    assert out.iloc[0]["trade_time"] == ""
    assert out.iloc[0]["close_price"] == 7.95


def test_call_with_retries_times_out_slow_source() -> None:
    def slow_source():
        time.sleep(0.2)
        return "ok"

    start = time.time()
    try:
        data_sources_module._call_with_retries(slow_source, retries=1, timeout_seconds=0.05)
    except TimeoutError:
        elapsed = time.time() - start
        assert elapsed < 0.2
    else:
        raise AssertionError("expected timeout")


def test_fetch_price_snapshot_falls_back_to_hist_for_missing_codes(monkeypatch) -> None:
    empty_df = pd.DataFrame()
    hist_df = pd.DataFrame(
        [
            {"收盘": 7.79},
            {"收盘": 7.95},
        ]
    )

    monkeypatch.setattr(data_sources_module.ak, "stock_zh_a_spot", lambda: empty_df)
    monkeypatch.setattr(data_sources_module.ak, "stock_zh_a_spot_em", lambda: empty_df)
    monkeypatch.setattr(data_sources_module.ak, "stock_zh_a_hist", lambda **kwargs: hist_df)

    out = data_sources_module.fetch_price_snapshot(codes=["000725"])

    assert list(out["code"]) == ["000725"]
    assert out.iloc[0]["close_price"] == 7.95
    assert out.iloc[0]["prev_close"] == 7.79


def test_build_market_brief_reports_include_market_and_candidates() -> None:
    market_context = {
        "generated_at": "2026-06-29 15:10:00",
        "candidate_snapshot_source": "live",
        "trend_label": "偏强震荡",
        "breadth_label": "上涨家数占优",
        "risk_level": "中等",
        "up_count": 3200,
        "down_count": 1800,
        "index_snapshot": [
            {"name": "上证指数", "price": 4073.9, "change_pct": 1.16},
            {"name": "创业板指", "price": 4216.7, "change_pct": 0.54},
        ],
        "hot_sectors": ["房地产开发", "造纸", "服装家纺"],
    }
    candidates_df = pd.DataFrame(
        [
            {
                "code": "002624",
                "name": "完美世界",
                "industry": "游戏",
                "close_price": 11.54,
                "adjusted_score": 80.1,
                "buy_signal": "买入偏强",
                "institution_count": 10,
                "source_group_count": 3,
            },
            {
                "code": "002078",
                "name": "太阳纸业",
                "industry": "造纸",
                "close_price": 12.14,
                "adjusted_score": 78.7,
                "buy_signal": "谨慎买入",
                "institution_count": 8,
                "source_group_count": 2,
            },
        ]
    )

    markdown = build_market_brief_markdown_report(date(2026, 6, 29), market_context, candidates_df)
    html = build_market_brief_html_report(date(2026, 6, 29), market_context, candidates_df)

    assert "## 市场环境" in markdown
    assert "## 市场结论" in markdown
    assert "## 候选方向" in markdown
    assert "房地产开发、造纸、服装家纺" in markdown
    assert "完美世界" in markdown
    assert "<html" in html.lower()
    assert "A股实时市场快报" in html
    assert "候选方向" in html


def test_load_fallback_candidates_snapshot_updates_live_prices(tmp_path: Path) -> None:
    fallback_file = tmp_path / "candidates_top15.csv"
    fallback_file.write_text(
        "code,name,industry,close_price,adjusted_score,buy_signal,institution_count,source_group_count\n"
        "002624,完美世界,游戏,10.00,80.1,买入偏强,10,3\n",
        encoding="utf-8",
    )
    price_snapshot = pd.DataFrame(
        [
            {"code": "002624", "close_price": 11.54},
        ]
    )

    out = load_fallback_candidates_snapshot(fallback_file, price_snapshot=price_snapshot, top_n=5)

    assert list(out["code"]) == ["002624"]
    assert out.iloc[0]["close_price"] == 11.54


def test_save_and_load_latest_positions_snapshot_roundtrip(tmp_path: Path) -> None:
    positions = pd.DataFrame(
        [
            {"code": "000725", "name": "京东方A", "position_pct": 8.0, "cost_price": 8.614},
            {"code": "600522", "name": "中天科技", "position_pct": 18.0, "cost_price": 59.231},
        ]
    )
    latest_file = tmp_path / "latest_positions.csv"

    save_latest_positions_snapshot(positions, latest_file)
    loaded = load_latest_positions_snapshot(latest_file)

    assert latest_file.exists()
    assert list(loaded["code"]) == ["000725", "600522"]
    assert list(loaded["name"]) == ["京东方A", "中天科技"]
    assert loaded.iloc[1]["position_pct"] == 18.0


def test_market_snapshot_cache_roundtrip_with_freshness_check(tmp_path: Path) -> None:
    snapshot = pd.DataFrame(
        [
            {"code": "000725", "name": "京东方A", "close_price": 7.74, "last_price": 7.74, "prev_close": 7.81},
            {"code": "600522", "name": "中天科技", "close_price": 59.34, "last_price": 59.34, "prev_close": 59.23},
        ]
    )
    cache_file = tmp_path / "market_snapshot.csv"

    saved = save_market_snapshot_cache(snapshot, cache_file, generated_at="2026-06-29 14:35:00")
    loaded = load_cached_market_snapshot(cache_file, max_age_minutes=15, now_dt=datetime(2026, 6, 29, 14, 40, 0))
    expired = load_cached_market_snapshot(cache_file, max_age_minutes=3, now_dt=datetime(2026, 6, 29, 14, 40, 0))

    assert saved.exists()
    assert list(loaded["code"]) == ["000725", "600522"]
    assert loaded.iloc[0]["_cache_generated_at"] == "2026-06-29 14:35:00"
    assert expired.empty


def test_load_any_cached_market_snapshot_reads_stale_cache(tmp_path: Path) -> None:
    cache_file = tmp_path / "market_snapshot.csv"
    cache_file.write_text(
        "code,name,close_price,last_price,prev_close,_cache_generated_at\n000725,京东方A,7.95,7.95,7.79,2026-06-29 14:35:00\n",
        encoding="utf-8",
    )

    out = load_any_cached_market_snapshot(cache_file)

    assert list(out["code"]) == ["000725"]


def test_resolve_analysis_paths_defaults_to_output_dir(tmp_path: Path) -> None:
    output_dir = tmp_path / "portfolio-run"

    latest_path, cache_path = resolve_analysis_paths(output_dir=output_dir, latest_positions_file=None, market_snapshot_cache_file=None)

    assert latest_path == output_dir / "latest_positions.csv"
    assert cache_path == output_dir / "market_snapshot_cache.csv"


def test_cli_load_positions_falls_back_to_latest_snapshot(tmp_path: Path) -> None:
    latest_file = tmp_path / "latest_positions.csv"
    latest_file.write_text("code,name,position_pct,cost_price\n000725,京东方A,8.0,8.614\n", encoding="utf-8")

    args = argparse.Namespace(
        positions_text=None,
        positions_file=None,
        use_latest_positions=False,
    )

    loaded = _load_positions(args, latest_positions_path=latest_file)

    assert list(loaded["code"]) == ["000725"]
    assert loaded.iloc[0]["name"] == "京东方A"
