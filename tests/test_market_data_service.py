from __future__ import annotations

from datetime import date

import pandas as pd

from src.a_share_research.market_data_service import (
    MarketDataService,
    StaticMarketDataProvider,
    classify_bid_ask_actionability,
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
