from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.a_share_research.market_data_service import (
    AkshareMarketDataProvider,
    CloudMarketDataProvider,
    EastmoneyDirectMarketDataProvider,
    FallbackMarketDataProvider,
    MarketDataService,
    SinaMarketDataProvider,
    StaticMarketDataProvider,
    TencentMarketDataProvider,
    classify_bid_ask_actionability,
    create_app,
    normalize_stock_code,
)


def test_normalize_stock_code() -> None:
    assert normalize_stock_code("SZ000725") == "000725"
    assert normalize_stock_code("600667.SH") == "600667"
    assert normalize_stock_code("bad") == ""


def test_market_snapshot_returns_freshness_and_breadth() -> None:
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame(
            [
                {"代码": "000001", "名称": "平安银行", "最新价": 10.0, "涨跌幅": 1.2},
                {"代码": "000002", "名称": "万科A", "最新价": 8.0, "涨跌幅": -0.8},
                {"代码": "600000", "名称": "浦发银行", "最新价": 9.0, "涨跌幅": 10.0},
            ]
        ),
        indices=pd.DataFrame(
            [
                {"代码": "000001", "名称": "上证指数", "最新价": 3200.0, "涨跌幅": 0.5},
            ]
        ),
    )

    out = MarketDataService(provider=provider).market_snapshot()

    assert out["freshness"] == "live"
    assert out["is_stale"] is False
    assert out["data"]["up_count"] == 2
    assert out["data"]["down_count"] == 1
    assert out["data"]["limit_up_count"] == 1


def test_bidask_identifies_limit_up_sealed() -> None:
    action = classify_bid_ask_actionability(
        latest_price=5.2,
        day_change_pct=10.02,
        sell_1=float("nan"),
        buy_1=5.2,
        cash=5000.0,
    )

    assert action["is_limit_up_sealed"] is True
    assert action["actionability"] == "涨停封板不可追"


def test_actionable_candidates_filters_cash_and_limit_up() -> None:
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame(
            [
                {"代码": "000001", "名称": "可买A", "最新价": 6.0, "涨跌幅": 2.0, "换手率": 3.0, "量比": 1.2},
                {"代码": "000002", "名称": "封板B", "最新价": 5.2, "涨跌幅": 10.02, "换手率": 8.0, "量比": 2.0},
                {"代码": "000003", "名称": "太贵C", "最新价": 18.0, "涨跌幅": 1.0, "换手率": 2.0, "量比": 1.0},
                {"代码": "688001", "名称": "科创D", "最新价": 8.0, "涨跌幅": 3.0, "换手率": 2.0, "量比": 1.0},
                {"代码": "000004", "名称": "ST风险", "最新价": 3.0, "涨跌幅": 2.0, "换手率": 2.0, "量比": 1.0},
            ]
        ),
        bidasks={
            "000001": pd.DataFrame(
                [
                    {"item": "最新", "value": 6.0},
                    {"item": "涨幅", "value": 2.0},
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
        },
    )

    out = MarketDataService(provider=provider).actionable_candidates(cash=1000.0, price_limit=20.0)
    codes = [item["code"] for item in out["data"]["candidates"]]

    assert codes == ["000001"]
    rejected = {item["code"]: item["reason"] for item in out["data"]["rejected"]}
    assert rejected["000002"] == "涨停封板不可追"
    assert rejected["000003"] == "现金不足"


def test_actionable_candidates_rejects_new_stock_and_overheated_gain() -> None:
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame(
            [
                {"代码": "001399", "名称": "C惠科", "最新价": 46.28, "涨跌幅": 21.21, "换手率": 51.0, "量比": 1.1},
                {"代码": "000001", "名称": "高涨幅A", "最新价": 6.0, "涨跌幅": 9.8, "换手率": 3.0, "量比": 1.2},
            ]
        ),
        bidasks={
            "001399": pd.DataFrame(
                [
                    {"item": "最新", "value": 46.28},
                    {"item": "涨幅", "value": 21.21},
                    {"item": "sell_1", "value": 46.29},
                    {"item": "buy_1", "value": 46.28},
                ]
            ),
            "000001": pd.DataFrame(
                [
                    {"item": "最新", "value": 6.0},
                    {"item": "涨幅", "value": 9.8},
                    {"item": "sell_1", "value": 6.01},
                    {"item": "buy_1", "value": 6.0},
                ]
            ),
        },
    )

    out = MarketDataService(provider=provider).actionable_candidates(cash=5000.0, price_limit=50.0)

    assert out["data"]["candidates"] == []
    rejected = {item["code"]: item["reason"] for item in out["data"]["rejected"]}
    assert rejected["001399"] == "新股/次新波动过大"
    assert rejected["000001"] == "涨幅过高不追"


def test_verify_candidates_scores_chatgpt_candidates_without_full_market_scan() -> None:
    class CandidateProvider(StaticMarketDataProvider):
        def quotes(self) -> pd.DataFrame:
            raise AssertionError("candidate verification should not scan the full market")

        def quotes_for(self, codes: list[str]) -> pd.DataFrame:
            normalized = {str(code).zfill(6) for code in codes}
            return self.quotes_df[self.quotes_df["代码"].isin(normalized)].copy()

    provider = CandidateProvider(
        quotes=pd.DataFrame(
            [
                {"代码": "000725", "名称": "京东方A", "最新价": 8.7, "涨跌幅": 2.0},
                {"代码": "688001", "名称": "科创X", "最新价": 18.0, "涨跌幅": 1.0},
                {"代码": "000001", "名称": "高涨幅A", "最新价": 6.0, "涨跌幅": 9.8},
            ]
        ),
        bidasks={
            "000725": pd.DataFrame(
                [
                    {"item": "最新", "value": 8.7},
                    {"item": "涨幅", "value": 2.0},
                    {"item": "sell_1", "value": 8.71},
                    {"item": "buy_1", "value": 8.7},
                ]
            ),
            "000001": pd.DataFrame(
                [
                    {"item": "最新", "value": 6.0},
                    {"item": "涨幅", "value": 9.8},
                    {"item": "sell_1", "value": 6.01},
                    {"item": "buy_1", "value": 6.0},
                ]
            ),
        },
        hist={
            "000725": pd.DataFrame({"收盘": [5 + i * 0.05 for i in range(80)]}),
        },
    )

    out = MarketDataService(provider=provider).verify_candidates(
        {
            "cash": 5000,
            "candidates": [
                {"code": "000725", "name": "京东方A", "source_reason": "面板板块异动"},
                {"code": "688001", "name": "科创X", "source_reason": "AI找到的候选"},
                {"code": "000001", "name": "高涨幅A", "source_reason": "涨幅榜"},
            ],
        }
    )

    verdicts = {item["code"]: item["verdict"] for item in out["data"]["results"]}
    assert verdicts["000725"] == "可重点观察"
    assert verdicts["688001"] == "不建议买入"
    assert verdicts["000001"] == "不建议买入"
    reasons = {item["code"]: item["decision_reasons"] for item in out["data"]["results"]}
    assert "非主板或代码无效" in reasons["688001"]
    assert "涨幅过高，不适合追高" in reasons["000001"]


def test_stock_intraday_analysis_returns_trading_decision_data_contract() -> None:
    hist_df = pd.DataFrame(
        {
            "日期": [f"2026-06-{day:02d}" for day in range(1, 31)] + [f"2026-07-{day:02d}" for day in range(1, 31)],
            "开盘": [7.0 + i * 0.02 for i in range(60)],
            "最高": [7.2 + i * 0.02 for i in range(60)],
            "最低": [6.8 + i * 0.02 for i in range(60)],
            "收盘": [7.0 + i * 0.02 for i in range(60)],
            "成交量": [100000 + i * 1000 for i in range(60)],
        }
    )
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame(
            [
                {
                    "代码": "002100",
                    "名称": "天康生物",
                    "最新价": 8.76,
                    "涨跌额": 0.12,
                    "涨跌幅": 1.39,
                    "昨收": 8.64,
                    "今开": 8.66,
                    "最高": 8.82,
                    "最低": 8.58,
                    "换手率": 3.2,
                    "量比": 1.4,
                    "成交量": 12345600,
                    "成交额": 108000000,
                    "总市值": 12000000000,
                    "流通市值": 9000000000,
                    "涨停价": 9.50,
                    "跌停价": 7.78,
                    "振幅": 2.78,
                }
            ]
        ),
        bidasks={
            "002100": pd.DataFrame(
                [
                    {"item": "最新", "value": 8.76},
                    {"item": "涨幅", "value": 1.39},
                    {"item": "buy_1", "value": 8.75},
                    {"item": "buy_1_volume", "value": 12000},
                    {"item": "buy_2", "value": 8.74},
                    {"item": "buy_2_volume", "value": 18000},
                    {"item": "buy_3", "value": 8.73},
                    {"item": "buy_3_volume", "value": 15000},
                    {"item": "buy_4", "value": 8.72},
                    {"item": "buy_4_volume", "value": 9000},
                    {"item": "buy_5", "value": 8.71},
                    {"item": "buy_5_volume", "value": 7000},
                    {"item": "sell_1", "value": 8.76},
                    {"item": "sell_1_volume", "value": 15000},
                    {"item": "sell_2", "value": 8.77},
                    {"item": "sell_2_volume", "value": 9000},
                    {"item": "sell_3", "value": 8.78},
                    {"item": "sell_3_volume", "value": 21000},
                    {"item": "sell_4", "value": 8.79},
                    {"item": "sell_4_volume", "value": 6000},
                    {"item": "sell_5", "value": 8.80},
                    {"item": "sell_5_volume", "value": 5000},
                ]
            )
        },
        boards_df=pd.DataFrame(
            [
                {
                    "board_type": "行业",
                    "board_name": "饲料",
                    "change_pct": 2.1,
                    "rank": 3,
                    "amount": 3000000000,
                    "turnover_rate": 4.2,
                    "main_net_inflow": 120000000,
                    "up_count": 18,
                    "down_count": 5,
                    "leader_code": "002100",
                    "leader": "天康生物",
                    "leader_change_pct": 6.2,
                    "limit_up_count": 2,
                },
                {
                    "board_type": "概念",
                    "board_name": "猪肉",
                    "change_pct": 1.8,
                    "rank": 5,
                    "amount": 1800000000,
                    "main_net_inflow": 80000000,
                    "leader_code": "002100",
                    "leader": "天康生物",
                    "leader_change_pct": 6.2,
                    "limit_up_count": 1,
                },
            ]
        ),
        hist={"002100": hist_df},
        intraday={
            "002100": pd.DataFrame(
                [
                    {"time": "10:29", "price": 8.74, "avg_price": 8.70, "open": 8.72, "high": 8.75, "low": 8.70, "close": 8.74, "volume": 10000, "amount": 87400},
                    {"time": "10:30", "price": 8.76, "avg_price": 8.71, "open": 8.74, "high": 8.78, "low": 8.73, "close": 8.76, "volume": 12000, "amount": 105120},
                ]
            )
        },
        recent_trades={
            "002100": pd.DataFrame(
                [
                    {"time": "10:30:12", "price": 8.76, "volume": 60000, "amount": 525600, "side": "buy"},
                ]
            )
        },
        zt_pool=pd.DataFrame(
            [
                {
                    "code": "002100",
                    "name": "天康生物",
                    "change_pct": 10.01,
                    "latest_price": 9.50,
                    "limit_up_price": 9.50,
                    "seal_amount": 30000000,
                    "industry": "饲料",
                }
            ]
        ),
    )

    out = MarketDataService(provider=provider).stock_intraday_analysis(
        {
            "code": "002100",
            "account": {
                "cash": 5000,
                "total_asset": 12000,
                "positions": [
                    {"code": "002100", "name": "天康生物", "shares": 100, "available": 0, "cost": 8.5}
                ],
            },
        }
    )

    assert out["freshness"] in {"live", "after_close", "delayed"}
    assert out["data_quality"]["quote_status"] == "ok"
    assert out["data_quality"]["intraday_status"] == "ok"
    assert out["data_quality"]["order_book_status"] == "ok"
    assert out["quote"]["code"] == "002100"
    assert out["quote"]["market"] == "SZ"
    assert out["intraday_1m"]["rows"][-1]["avg_price"] == 8.71
    assert out["order_book_5"]["bid"][0] == {"price": 8.75, "volume": 12000.0}
    assert out["recent_trades"]["rows"][0]["large_order_flag"] is True
    assert out["technical"]["ma5"] is not None
    assert out["technical"]["boll_upper"] is not None
    assert out["board"]["industry"]["name"] == "饲料"
    assert out["market"]["trading_phase"] in {"pre_open", "continuous_auction", "lunch_break", "after_close", "non_trading_day"}
    assert out["account"]["positions"][0]["today_buy_flag"] is True
    assert out["account"]["sector_exposure"][0]["sector"] == "饲料"
    assert out["decision_score"]["style"] == "aggressive_growth"
    assert out["decision_score"]["target_attack_position_pct"] >= 65
    assert out["decision_score"]["probability_band"] in {"高胜率", "中高胜率", "中性", "低胜率", "不适合交易"}
    assert out["trading_plan"]["style_note"] == "偏进攻、弱保守；允许进攻仓位在65%以上，但必须有失败线"
    assert out["trading_plan"]["buy_condition"]
    assert out["trading_plan"]["failure_line"]
    assert out["technical_interpretation"]["trend_state"]
    assert out["technical_interpretation"]["intraday_state"] == "分时偏强"
    assert out["technical_interpretation"]["volume_state"]
    assert out["technical_interpretation"]["support_levels"]
    assert out["technical_interpretation"]["resistance_levels"]
    assert out["technical_interpretation"]["buy_trigger"]
    assert out["technical_interpretation"]["sell_trigger"]
    assert out["technical_interpretation"]["failure_line"]
    assert out["technical_interpretation"]["turnaround_condition"]
    assert "分时均价" in out["technical_interpretation"]["point_sources"]
    assert out["technical_interpretation"]["risk_tags"]
    assert out["recent_3d_context"]["days_count"] == 3
    assert out["recent_3d_context"]["three_day_high"] is not None
    assert out["recent_3d_context"]["three_day_low"] is not None
    assert out["recent_3d_context"]["volume_trend_3d"] in {"放大", "萎缩", "平稳", "混乱", "不可确认"}
    assert out["today_intraday_summary"]["vwap_deviation_pct"] is not None
    assert out["today_intraday_summary"]["close_location_pct"] is not None
    assert out["today_intraday_summary"]["phase_pattern"]
    assert out["candlestick_structure"]["body_pct"] is not None
    assert out["candlestick_structure"]["pattern_tags"]
    assert out["moving_average_structure"]["structure"] in {"多头排列", "空头排列", "均线纠缠", "均线不足"}
    assert out["moving_average_structure"]["distance_to_ma20_pct"] is not None
    assert out["volume_price_relation"]["relation"]
    assert out["relative_strength"]["vs_market"] in {"强于市场", "弱于市场", "同步市场", "不可确认"}
    assert out["support_resistance_zones"]["support_zones"]
    assert out["support_resistance_zones"]["resistance_zones"]
    assert out["risk_volatility"]["intraday_amplitude_pct"] is not None
    assert out["order_book_interpretation"]["bid_ask_ratio"] is not None
    assert out["board_stock_alignment"]["status"] in {"ok", "partial", "failed"}
    assert out["board_stock_alignment"]["stock_vs_board"] in {"强于板块", "弱于板块", "同步板块", "不可确认"}
    assert out["board_stock_alignment"]["conclusion_adjustment"] in {"上调", "不变", "下调", "降级"}
    assert out["position_risk_contribution"]["shares"] == 100
    assert out["position_risk_contribution"]["available"] == 0
    assert out["technical_level_layers"]["intraday_strength_line"]["source"]
    assert out["technical_level_layers"]["turn_strong_line"]["source"]
    assert out["next_session_scenarios"]["low_open_weak_rebound"]["action"]
    assert out["next_session_scenarios"]["flat_open_chop"]["action"]
    assert out["next_session_scenarios"]["high_open_repair"]["action"]
    assert out["review_log_receipt"]["status"] == "not_logged_by_analysis_endpoint"
    assert out["review_log_receipt"]["require_review_id_when_claiming_logged"] is True
    completeness = out["response_completeness_check"]
    assert completeness["required_sections"] == [
        "数据来源与质量",
        "市场状态",
        "板块状态",
        "个股技术结构",
        "分时盘口",
        "账户与T+1",
        "操作计划",
        "反证条件与复盘",
    ]
    assert completeness["must_use_reasoning_chain"] == ["结论", "数据证据", "技术位来源", "反证条件", "执行动作"]
    assert completeness["coverage"]["technical_levels"] is True
    assert completeness["coverage"]["order_book"] is True
    assert completeness["coverage"]["account_constraints"] is True
    assert out["execution_checklist"]["data_reliability"]["quote"]["status"] == "ok"
    assert out["execution_checklist"]["intraday_read"]["above_avg_price"] is True
    assert out["execution_checklist"]["intraday_read"]["below_cost"] is False
    assert out["execution_checklist"]["order_book_read"]["watch_minutes"] == 5
    assert out["execution_checklist"]["execution_window"]["sell_if"]
    assert out["review_record"]["code"] == "002100"
    assert out["review_record"]["decision_score"] == out["decision_score"]["total_score"]
    assert out["review_record"]["next_review_fields"] == [
        "next_trade_date_open",
        "next_trade_date_high",
        "next_trade_date_low",
        "triggered_buy_condition",
        "triggered_failure_line",
        "actual_action",
        "outcome_note",
    ]


def test_stock_intraday_analysis_aggressive_score_penalizes_weak_data_quality() -> None:
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame([{"代码": "002100", "名称": "天康生物", "最新价": 8.76, "涨跌幅": 1.0}]),
        hist={"002100": pd.DataFrame({"收盘": [5 + i * 0.05 for i in range(80)]})},
    )

    out = MarketDataService(provider=provider).stock_intraday_analysis(
        {"code": "002100", "account": {"cash": 5000, "total_asset": 12000, "positions": []}}
    )

    assert out["decision_score"]["total_score"] < 65
    assert out["decision_score"]["probability_band"] in {"中性", "低胜率", "不适合交易"}
    assert "缺少分时或盘口关键数据" in out["decision_score"]["risk_flags"]
    assert out["execution_checklist"]["data_reliability"]["intraday"]["status"] == "failed"
    assert out["execution_checklist"]["execution_window"]["immediate_action"] == "只观察，等待关键盘中数据恢复"
    assert out["review_record"]["data_quality_summary"]["intraday_status"] == "failed"
    assert out["technical_interpretation"]["intraday_state"] == "分时不可确认"
    assert "分时缺失" in out["technical_interpretation"]["risk_tags"]
    assert out["response_completeness_check"]["coverage"]["intraday_vwap"] is False
    assert "分时均价缺失" in out["response_completeness_check"]["missing_or_degraded_items"]


def test_verify_candidates_marks_repeated_names_as_tracking_not_new_recommendations() -> None:
    class CandidateProvider(StaticMarketDataProvider):
        def quotes(self) -> pd.DataFrame:
            raise AssertionError("candidate verification should not scan the full market")

        def quotes_for(self, codes: list[str]) -> pd.DataFrame:
            normalized = {str(code).zfill(6) for code in codes}
            return self.quotes_df[self.quotes_df["代码"].isin(normalized)].copy()

    provider = CandidateProvider(
        quotes=pd.DataFrame(
            [
                {"代码": "000725", "名称": "京东方A", "最新价": 8.7, "涨跌幅": 2.0},
            ]
        ),
        bidasks={
            "000725": pd.DataFrame(
                [
                    {"item": "最新", "value": 8.7},
                    {"item": "涨幅", "value": 2.0},
                    {"item": "sell_1", "value": 8.71},
                    {"item": "buy_1", "value": 8.7},
                ]
            ),
        },
        hist={
            "000725": pd.DataFrame({"收盘": [5 + i * 0.05 for i in range(80)]}),
        },
    )

    out = MarketDataService(provider=provider).verify_candidates(
        {
            "cash": 5000,
            "previous_recommendations": [{"code": "000725", "name": "京东方A"}],
            "candidates": [
                {"code": "000725", "name": "京东方A", "source_reason": "之前观察过的面板方向"},
            ],
        }
    )

    item = out["data"]["results"][0]
    assert item["candidate_lifecycle"]["status"] == "继续跟踪"
    assert item["candidate_lifecycle"]["recommendation_slot"] == "观察池"
    assert "没有新的实时证据" in item["candidate_lifecycle"]["duplicate_note"]


def test_review_ledger_logs_reads_evaluates_and_summarizes_lessons(tmp_path) -> None:
    service = MarketDataService(provider=StaticMarketDataProvider(), review_store_path=tmp_path / "reviews.json")

    logged = service.log_review(
        {
            "code": "600879",
            "name": "航天电子",
            "decision": "反抽不过21.34减仓",
            "decision_score": 65,
            "freshness": "live",
            "key_levels": {"failure_line": 20.99, "turnaround": 21.34},
            "risk_tags": ["低于分时均价", "板块不强"],
            "source": "getStockIntradayAnalysis",
        }
    )

    assert logged["data"]["record"]["code"] == "600879"
    assert logged["data"]["record"]["status"] == "open"
    assert logged["data"]["next_step"] == "下次分析前调用getRecentReviews对比上次判断"

    recent = service.recent_reviews(code="600879", limit=5)
    assert recent["data"]["records"][0]["decision"] == "反抽不过21.34减仓"
    assert recent["data"]["must_compare_with_current_analysis"] is True

    evaluated = service.evaluate_review(
        {
            "review_id": logged["data"]["record"]["review_id"],
            "actual_outcome": "次日低开后继续走弱，减仓判断有效",
            "actual_action": "卖出100股",
            "triggered_failure_line": True,
            "triggered_buy_condition": False,
            "lesson_tags": ["风险线有效", "弱板块不恋战"],
            "outcome_rating": "有效",
        }
    )

    assert evaluated["data"]["record"]["status"] == "evaluated"
    assert evaluated["data"]["record"]["evaluation"]["outcome_rating"] == "有效"

    lessons = service.review_lessons(limit=10)
    assert lessons["data"]["lesson_counts"]["风险线有效"] == 1
    assert lessons["data"]["latest_lessons"][0]["code"] == "600879"


def test_stock_intraday_analysis_does_not_invent_unmatched_board() -> None:
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame([{"代码": "002100", "名称": "天康生物", "最新价": 8.76, "涨跌幅": 1.0}]),
        bidasks={
            "002100": pd.DataFrame(
                [
                    {"item": "最新", "value": 8.76},
                    {"item": "涨幅", "value": 1.0},
                    {"item": "buy_1", "value": 8.75},
                    {"item": "sell_1", "value": 8.76},
                ]
            )
        },
        boards_df=pd.DataFrame(
            [
                {"board_type": "行业", "board_name": "玻璃行业", "leader_code": "603021", "leader": "别的股票"},
            ]
        ),
        hist={"002100": pd.DataFrame({"收盘": [5 + i * 0.05 for i in range(80)]})},
    )

    out = MarketDataService(provider=provider).stock_intraday_analysis({"code": "002100"})

    assert out["board"]["status"] == "partial"
    assert out["board"]["industry"] is None


def test_stock_intraday_analysis_matches_board_by_constituents_when_not_leader() -> None:
    class ConstituentProvider(StaticMarketDataProvider):
        def board_constituents(self, board: dict[str, object]) -> pd.DataFrame:
            if board.get("label") == "new_feed":
                return pd.DataFrame(
                    [
                        {"code": "002100", "name": "天康生物", "change_pct": 1.39},
                        {"code": "000001", "name": "平安银行", "change_pct": -0.2},
                    ]
                )
            return pd.DataFrame()

    provider = ConstituentProvider(
        quotes=pd.DataFrame([{"代码": "002100", "名称": "天康生物", "最新价": 8.76, "涨跌幅": 1.39}]),
        boards_df=pd.DataFrame(
            [
                {
                    "board_type": "行业",
                    "label": "new_feed",
                    "board_name": "饲料",
                    "change_pct": 2.1,
                    "rank": 3,
                    "up_count": 18,
                    "down_count": 5,
                    "leader_code": "000999",
                    "leader": "别的股票",
                    "leader_change_pct": 6.2,
                }
            ]
        ),
    )

    out = MarketDataService(provider=provider).stock_intraday_analysis({"code": "002100"})

    assert out["board"]["status"] == "ok"
    assert out["board"]["industry"]["name"] == "饲料"
    assert out["board"]["industry"]["matched_by"] == "constituent"
    assert out["board_stock_alignment"]["status"] == "ok"


def test_technical_endpoint_returns_trade_points() -> None:
    provider = StaticMarketDataProvider(
        hist={
            "000725": pd.DataFrame({"收盘": [5 + i * 0.05 for i in range(80)]}),
        }
    )

    out = MarketDataService(provider=provider).technical("000725", report_date=date(2026, 7, 1))

    assert out["freshness"] == "live"
    assert out["data"]["buy_point"] > 0
    assert out["data"]["sell_point"] > out["data"]["buy_point"]
    assert "买入点" in out["data"]["technical_point_sources"]


def test_portfolio_analyze_respects_available_zero() -> None:
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame(
            [
                {"代码": "601099", "名称": "太平洋", "最新价": 3.4, "涨跌幅": -1.0},
            ]
        )
    )
    service = MarketDataService(provider=provider)

    out = service.portfolio_analyze(
        {
            "cash": 5735.48,
            "positions": [
                {"code": "601099", "name": "太平洋", "shares": 400, "available": 0, "cost": 3.5},
            ],
        }
    )

    holding = out["data"]["positions"][0]
    assert holding["t_plus_1_locked"] is True
    assert "今日不可卖出" in holding["action"]
    assert "今天卖出" not in holding["action"]


def test_tencent_provider_parses_stock_quotes_and_bidask() -> None:
    text = (
        'v_sz000725="51~京东方A~000725~8.77~8.68~8.49~46253150~23736226~22516924~'
        '8.77~44699~8.76~94006~8.75~223150~8.74~27403~8.73~16487~'
        '8.78~52619~8.79~95380~8.80~155421~8.81~40949~8.82~12966~~'
        '20260701152645~0.09~1.04~8.81~8.31~8.77/46253150/39601267264";'
    )
    provider = TencentMarketDataProvider(fetcher=lambda _symbols: text)

    quotes = provider.quotes_for(["000725"])
    bidask = provider.bid_ask("000725")

    assert quotes.iloc[0]["代码"] == "000725"
    assert quotes.iloc[0]["名称"] == "京东方A"
    assert quotes.iloc[0]["最新价"] == 8.77
    assert quotes.iloc[0]["涨跌幅"] == 1.04
    values = {row["item"]: row["value"] for _, row in bidask.iterrows()}
    assert values["buy_1"] == 8.77
    assert values["sell_1"] == 8.78


def test_stock_quotes_falls_back_to_tencent_when_primary_fails() -> None:
    class BrokenProvider:
        source = "broken"

        def quotes_for(self, codes: list[str]) -> pd.DataFrame:
            raise ConnectionError("primary disconnected")

    text = 'v_sh600879="1~航天电子~600879~21.45~21.29~21.27~0~0~0~21.45~1~21.44~1~21.43~1~21.42~1~21.41~1~21.46~1~~20260701152615~0.16~0.75~21.75~21.03";'
    provider = FallbackMarketDataProvider(
        primary=BrokenProvider(),
        fallback=TencentMarketDataProvider(fetcher=lambda _symbols: text),
    )

    out = MarketDataService(provider=provider).stock_quotes(["600879"])

    assert out["freshness"] == "live"
    assert out["source"] == "broken+tencent/qt"
    assert out["data"]["quotes"][0]["code"] == "600879"
    assert out["data"]["quotes"][0]["latest_price"] == 21.45


def test_eastmoney_direct_provider_parses_full_market_quotes() -> None:
    payload = {
        "data": {
            "total": 3,
            "diff": [
                {"f12": "000001", "f14": "平安银行", "f2": 10.0, "f3": 1.2, "f8": 2.0, "f10": 1.1, "f6": 1000000},
                {"f12": "000002", "f14": "万科A", "f2": 8.0, "f3": -0.8, "f8": 3.0, "f10": 0.9, "f6": 800000},
                {"f12": "600000", "f14": "浦发银行", "f2": 9.0, "f3": 10.0, "f8": 4.0, "f10": 2.0, "f6": 900000},
            ],
        }
    }
    provider = EastmoneyDirectMarketDataProvider(fetcher=lambda **_kwargs: payload, page_size=100)

    quotes = provider.quotes()

    assert len(quotes) == 3
    assert list(quotes["代码"]) == ["000001", "000002", "600000"]
    assert list(quotes["涨跌幅"]) == [1.2, -0.8, 10.0]


def test_sina_provider_paginates_full_market_quotes() -> None:
    payloads = {
        ("sh_a", 1): [
            {"code": "600000", "name": "浦发银行", "trade": "9.00", "changepercent": "1.20", "turnoverratio": "2.0", "amount": "1000000"},
        ],
        ("sz_a", 1): [
            {"code": "000001", "name": "平安银行", "trade": "10.00", "changepercent": "-0.80", "turnoverratio": "3.0", "amount": "800000"},
        ],
    }
    provider = SinaMarketDataProvider(fetcher=lambda *, node, page, page_size: payloads.get((node, page), []), page_size=100)

    quotes = provider.quotes()

    assert set(quotes["代码"]) == {"600000", "000001"}
    assert set(quotes["涨跌幅"]) == {1.2, -0.8}


def test_sina_provider_parses_boards() -> None:
    payload = 'var S_Finance_bankuai_sinaindustry = {"new_dlhy":"new_dlhy,电力行业,62,8.9,0.19,2.23,1,2,sh600021,9.95,10.00,0.90,上海电力"}'
    provider = SinaMarketDataProvider(
        fetcher=lambda *, node, page, page_size: [],
        board_fetcher=lambda board_type: payload,
    )

    boards = provider.boards()

    assert boards.iloc[0]["board_name"] == "电力行业"
    assert boards.iloc[0]["label"] == "new_dlhy"
    assert boards.iloc[0]["change_pct"] == 2.23
    assert boards.iloc[0]["leader_code"] == "600021"
    assert boards.iloc[0]["leader"] == "上海电力"


def test_akshare_provider_parses_recent_trades_from_tencent_tick() -> None:
    class FakeAk:
        @staticmethod
        def stock_zh_a_tick_tx_js(symbol: str) -> pd.DataFrame:
            assert symbol == "sh600879"
            return pd.DataFrame(
                [
                    {"成交时间": "09:30:02", "成交价格": 21.00, "成交量": 726, "成交金额": 1524615, "性质": "买盘"},
                    {"成交时间": "09:30:05", "成交价格": 21.07, "成交量": 835, "成交金额": 1756641, "性质": "卖盘"},
                ]
            )

    class Provider(AkshareMarketDataProvider):
        def _ak(self):
            return FakeAk

    trades = Provider().recent_trades("600879", limit=1)

    assert list(trades.columns) == ["time", "price", "volume", "amount", "side"]
    assert trades.iloc[0].to_dict() == {
        "time": "09:30:02",
        "price": 21.0,
        "volume": 72600.0,
        "amount": 1524615.0,
        "side": "buy",
    }


def test_sina_provider_parses_daily_hist() -> None:
    provider = SinaMarketDataProvider(
        fetcher=lambda *, node, page, page_size: [],
        hist_fetcher=lambda *, symbol, datalen: [
            {"day": "2026-06-29", "open": "10.0", "high": "10.5", "low": "9.8", "close": "10.2", "volume": "1000"},
            {"day": "2026-06-30", "open": "10.2", "high": "10.8", "low": "10.1", "close": "10.6", "volume": "1200"},
        ],
    )

    hist = provider.hist("600000")

    assert list(hist["收盘"]) == [10.2, 10.6]
    assert list(hist["日期"]) == ["2026-06-29", "2026-06-30"]


def test_sina_provider_parses_intraday_amount_as_numeric() -> None:
    provider = SinaMarketDataProvider(
        fetcher=lambda *, node, page, page_size: [],
    )
    provider._fetch_intraday = lambda *, symbol, datalen: [  # type: ignore[method-assign]
        {"day": "2026-07-01 10:29:00", "open": "8.70", "high": "8.76", "low": "8.68", "close": "8.75", "volume": "10000", "amount": "87500.00"},
        {"day": "2026-07-01 10:30:00", "open": "8.75", "high": "8.78", "low": "8.74", "close": "8.76", "volume": "12000", "amount": "105120.00"},
    ]

    intraday = provider.intraday_1m("002100")

    assert list(intraday["amount"]) == [87500.0, 105120.0]
    assert round(float(intraday.iloc[-1]["avg_price"]), 4) == 8.7555


def test_fallback_provider_uses_fallback_when_boards_empty() -> None:
    class EmptyBoards:
        source = "empty"

        def boards(self) -> pd.DataFrame:
            return pd.DataFrame()

    fallback = StaticMarketDataProvider(
        boards_df=pd.DataFrame(
            [
                {
                    "board_type": "行业",
                    "board_name": "电力行业",
                    "change_pct": 2.23,
                    "up_count": 0,
                    "down_count": 0,
                    "leader": "上海电力",
                    "leader_change_pct": 9.95,
                }
            ]
        )
    )
    provider = FallbackMarketDataProvider(primary=EmptyBoards(), fallback=fallback)

    boards = provider.boards()

    assert boards.iloc[0]["board_name"] == "电力行业"


def test_cloud_provider_routes_methods_to_lightweight_sources() -> None:
    calls: list[str] = []

    class FullMarket:
        source = "full"

        def quotes(self) -> pd.DataFrame:
            calls.append("full.quotes")
            return pd.DataFrame([{"代码": "000001", "名称": "平安银行", "最新价": 10.0, "涨跌幅": 1.0}])

    class Realtime:
        source = "realtime"

        def quotes_for(self, codes: list[str]) -> pd.DataFrame:
            calls.append("realtime.quotes_for")
            return pd.DataFrame([{"代码": codes[0], "名称": "京东方A", "最新价": 8.7, "涨跌幅": 1.0}])

        def indices(self) -> pd.DataFrame:
            calls.append("realtime.indices")
            return pd.DataFrame([{"代码": "000001", "名称": "上证指数", "最新价": 3200.0, "涨跌幅": 0.5}])

        def bid_ask(self, code: str) -> pd.DataFrame:
            calls.append("realtime.bid_ask")
            return pd.DataFrame([{"item": "最新", "value": 8.7}, {"item": "涨幅", "value": 1.0}])

    class BoardAndHistory:
        source = "sina-like"

        def boards(self) -> pd.DataFrame:
            calls.append("sina.boards")
            return pd.DataFrame([{"board_name": "电力行业", "change_pct": 2.0, "leader": "上海电力"}])

        def hist(self, code: str, report_date: date | None = None) -> pd.DataFrame:
            calls.append("sina.hist")
            return pd.DataFrame({"收盘": [5 + i * 0.05 for i in range(80)]})

    provider = CloudMarketDataProvider(
        full_market_provider=FullMarket(),
        realtime_provider=Realtime(),
        board_provider=BoardAndHistory(),
        history_provider=BoardAndHistory(),
    )

    assert provider.quotes_for(["000725"]).iloc[0]["代码"] == "000725"
    assert provider.indices().iloc[0]["名称"] == "上证指数"
    assert provider.boards().iloc[0]["board_name"] == "电力行业"
    assert not provider.hist("000725").empty
    assert provider.quotes().iloc[0]["代码"] == "000001"
    assert calls == [
        "realtime.quotes_for",
        "realtime.indices",
        "sina.boards",
        "sina.hist",
        "full.quotes",
    ]


def test_default_market_snapshot_uses_direct_breadth_when_akshare_fails() -> None:
    class BrokenAkshare:
        source = "broken-akshare"

        def quotes(self) -> pd.DataFrame:
            raise ConnectionError("akshare quote disconnected")

        def indices(self) -> pd.DataFrame:
            return pd.DataFrame([{"代码": "000001", "名称": "上证指数", "最新价": 3200.0, "涨跌幅": 0.5}])

    payload = {
        "data": {
            "total": 2,
            "diff": [
                {"f12": "000001", "f14": "平安银行", "f2": 10.0, "f3": 1.2},
                {"f12": "000002", "f14": "万科A", "f2": 8.0, "f3": -0.8},
            ],
        }
    }
    provider = FallbackMarketDataProvider(
        primary=BrokenAkshare(),
        fallback=EastmoneyDirectMarketDataProvider(fetcher=lambda **_kwargs: payload, page_size=100),
    )

    out = MarketDataService(provider=provider).market_snapshot()

    assert out["freshness"] == "live"
    assert out["data"]["breadth_available"] is True
    assert out["data"]["up_count"] == 1
    assert out["data"]["down_count"] == 1
    assert out["data"]["breadth_error"] is None


def test_market_snapshot_rejects_incomplete_primary_breadth() -> None:
    class IncompletePrimary:
        source = "incomplete-primary"

        def quotes(self) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"代码": f"000{i:03d}", "名称": f"样本{i}", "最新价": 10.0, "涨跌幅": 10.0}
                    for i in range(100)
                ]
            )

        def indices(self) -> pd.DataFrame:
            return pd.DataFrame([{"代码": "000001", "名称": "上证指数", "最新价": 3200.0, "涨跌幅": 0.5}])

    payload = {
        "data": {
            "total": 2,
            "diff": [
                {"f12": "000001", "f14": "平安银行", "f2": 10.0, "f3": 1.2},
                {"f12": "000002", "f14": "万科A", "f2": 8.0, "f3": -0.8},
            ],
        }
    }
    provider = FallbackMarketDataProvider(
        primary=IncompletePrimary(),
        fallback=EastmoneyDirectMarketDataProvider(fetcher=lambda **_kwargs: payload, page_size=100),
    )

    out = MarketDataService(provider=provider).market_snapshot()

    assert out["freshness"] == "live"
    assert out["data"]["up_count"] == 1
    assert out["data"]["down_count"] == 1


def test_market_snapshot_partial_fallback_uses_json_safe_nulls() -> None:
    class BrokenQuotesProvider:
        source = "broken"

        def quotes(self) -> pd.DataFrame:
            raise ConnectionError("full market disconnected")

        def indices(self) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"代码": "000001", "名称": "上证指数", "最新价": 4112.45, "涨跌幅": 0.44, "换手率": float("nan")},
                ]
            )

    out = MarketDataService(provider=BrokenQuotesProvider()).market_snapshot()

    assert out["freshness"] == "partial_live"
    assert out["data"]["breadth_available"] is False
    assert out["data"]["indices"][0]["turnover_rate"] is None


def test_portfolio_intraday_decision_fast_mode_skips_slow_recent_trades() -> None:
    class FastProvider(StaticMarketDataProvider):
        def recent_trades(self, code: str, limit: int = 100) -> pd.DataFrame:
            raise AssertionError("fast portfolio decision should not call slow recent trades")

    hist_df = pd.DataFrame(
        {
            "日期": [f"2026-06-{day:02d}" for day in range(1, 31)] + [f"2026-07-{day:02d}" for day in range(1, 31)],
            "开盘": [8.0 + i * 0.01 for i in range(60)],
            "最高": [8.1 + i * 0.01 for i in range(60)],
            "最低": [7.9 + i * 0.01 for i in range(60)],
            "收盘": [8.0 + i * 0.01 for i in range(60)],
            "成交量": [100000 + i * 1000 for i in range(60)],
        }
    )
    provider = FastProvider(
        quotes=pd.DataFrame(
            [
                {"代码": "000725", "名称": "京东方A", "最新价": 9.1, "涨跌幅": 1.2, "今开": 9.0, "最高": 9.2, "最低": 8.9},
                {"代码": "600879", "名称": "航天电子", "最新价": 20.9, "涨跌幅": -0.8, "今开": 21.0, "最高": 21.2, "最低": 20.8},
            ]
        ),
        indices=pd.DataFrame([{"代码": "000001", "名称": "上证指数", "最新价": 3200.0, "涨跌幅": 0.5}]),
        boards_df=pd.DataFrame(
            [
                {
                    "board_type": "行业",
                    "board_name": "电子元件",
                    "change_pct": 1.0,
                    "up_ratio": 0.6,
                    "leader_code": "000725",
                    "leader": "京东方A",
                    "leader_price": 9.1,
                    "leader_change_pct": 1.2,
                    "board_action": "可参与",
                }
            ]
        ),
        bidasks={
            "000725": pd.DataFrame([{"item": "最新", "value": 9.1}, {"item": "涨幅", "value": 1.2}, {"item": "buy_1", "value": 9.1}, {"item": "sell_1", "value": 9.11}]),
            "600879": pd.DataFrame([{"item": "最新", "value": 20.9}, {"item": "涨幅", "value": -0.8}, {"item": "buy_1", "value": 20.9}, {"item": "sell_1", "value": 20.91}]),
        },
        hist={"000725": hist_df, "600879": hist_df},
        intraday={
            "000725": pd.DataFrame([{"time": "10:00", "close": 9.1, "avg_price": 9.05, "volume": 10000, "amount": 91000}]),
            "600879": pd.DataFrame([{"time": "10:00", "close": 20.9, "avg_price": 21.0, "volume": 8000, "amount": 167200}]),
        },
    )

    out = MarketDataService(provider=provider).portfolio_intraday_decision(
        {
            "cash": 3000,
            "mode": "fast",
            "positions": [
                {"code": "000725", "name": "京东方A", "shares": 100, "available": 100, "cost": 8.6},
                {"code": "600879", "name": "航天电子", "shares": 100, "available": 100, "cost": 21.3},
            ],
        }
    )

    assert out["freshness"] in {"live", "partial_live"}
    assert out["data"]["mode"] == "fast"
    assert len(out["data"]["positions"]) == 2
    assert out["data"]["positions"][0]["recent_trades_status"] == "skipped"
    assert "一次调用" in out["data"]["speed_note"]


def test_openapi_marks_read_only_actions_as_non_consequential() -> None:
    schema_text = Path("chatgpt_action_openapi.yaml").read_text(encoding="utf-8")

    for operation_id in [
        "getMarketSnapshot",
        "getHotBoards",
        "getStockIntradayAnalysis",
        "getPortfolioIntradayDecision",
        "verifyCandidates",
    ]:
        section_start = schema_text.index(f"operationId: {operation_id}")
        section = schema_text[section_start : section_start + 500]
        assert "x-openai-isConsequential: false" in section


def test_gpt_instructions_define_default_fast_portfolio_analysis() -> None:
    instructions = Path("GPT_STOCK_TRADING_ASSISTANT_V4_2.md").read_text(encoding="utf-8")
    schema_text = Path("chatgpt_action_openapi.yaml").read_text(encoding="utf-8")

    assert len(instructions) <= 7000
    assert "默认两段式分析" in instructions
    assert "先做组合快速总览" in instructions
    assert "自动升级深度分析" in instructions
    assert "组合快速分析" in schema_text
    assert "需要盘中全面分析时，应在快速总览后升级深度分析" in schema_text


def test_privacy_endpoint_returns_plain_policy_page() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(service=MarketDataService(provider=StaticMarketDataProvider())))

    response = client.get("/privacy")

    assert response.status_code == 200
    assert "A股实时持仓分析助手隐私政策" in response.text
    assert "不要求用户提供 API key" in response.text


def test_review_routes_support_gpt_action_memory_loop(tmp_path) -> None:
    from fastapi.testclient import TestClient

    service = MarketDataService(provider=StaticMarketDataProvider(), review_store_path=tmp_path / "reviews.json")
    client = TestClient(create_app(service=service))

    log_response = client.post(
        "/reviews/log",
        json={
            "code": "000725",
            "name": "京东方A",
            "decision": "9.20站不回则减仓",
            "decision_score": 62,
            "risk_tags": ["分时偏弱"],
        },
    )

    assert log_response.status_code == 200
    review_id = log_response.json()["data"]["record"]["review_id"]

    recent_response = client.get("/reviews/recent", params={"code": "000725", "limit": 3})
    assert recent_response.status_code == 200
    assert recent_response.json()["data"]["records"][0]["review_id"] == review_id

    evaluate_response = client.post(
        "/reviews/evaluate",
        json={
            "review_id": review_id,
            "actual_outcome": "未站回9.20后继续走弱",
            "actual_action": "减仓",
            "lesson_tags": ["均价下方不恋战"],
            "outcome_rating": "有效",
        },
    )
    assert evaluate_response.status_code == 200
    assert evaluate_response.json()["data"]["record"]["status"] == "evaluated"

    lessons_response = client.get("/reviews/lessons")
    assert lessons_response.status_code == 200
    assert lessons_response.json()["data"]["lesson_counts"]["均价下方不恋战"] == 1
