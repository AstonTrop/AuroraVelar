from datetime import date

import pandas as pd

from src.a_share_research.scoring import score_candidates


def test_scoring_filters_and_ranks() -> None:
    raw = pd.DataFrame(
        [
            # stock A: 5 institutions, should pass
            {"code": "600000", "name": "A银行", "pub_date": "2026-03-10", "institution": "机1", "rating": "买入", "target_price": 12, "source": "sina_最新投资评级", "report_url": ""},
            {"code": "600000", "name": "A银行", "pub_date": "2026-03-10", "institution": "机2", "rating": "买入", "target_price": 12, "source": "sina_上调评级股票", "report_url": ""},
            {"code": "600000", "name": "A银行", "pub_date": "2026-03-11", "institution": "机3", "rating": "增持", "target_price": 12, "source": "eastmoney_research_report", "report_url": ""},
            {"code": "600000", "name": "A银行", "pub_date": "2026-03-12", "institution": "机4", "rating": "买入", "target_price": 12, "source": "eastmoney_research_report", "report_url": ""},
            {"code": "600000", "name": "A银行", "pub_date": "2026-03-13", "institution": "机5", "rating": "买入", "target_price": 12, "source": "sina_首次评级股票", "report_url": ""},
            {"code": "600000", "name": "A银行", "pub_date": "2026-03-17", "institution": "EASTMONEY_6M_AGGREGATE", "rating": "买入", "target_price": None, "source": "eastmoney_profit_forecast", "report_url": ""},
            # stock B: only 3 institutions, should fail min institutions
            {"code": "601000", "name": "B银行", "pub_date": "2026-03-10", "institution": "机1", "rating": "买入", "target_price": 11, "source": "sina_最新投资评级", "report_url": ""},
            {"code": "601000", "name": "B银行", "pub_date": "2026-03-10", "institution": "机2", "rating": "中性", "target_price": 11, "source": "sina_上调评级股票", "report_url": ""},
            {"code": "601000", "name": "B银行", "pub_date": "2026-03-12", "institution": "机3", "rating": "增持", "target_price": 11, "source": "eastmoney_research_report", "report_url": ""},
            # stock C: price over 20 should fail
            {"code": "601001", "name": "C银行", "pub_date": "2026-03-10", "institution": "机1", "rating": "买入", "target_price": 35, "source": "sina_最新投资评级", "report_url": ""},
            {"code": "601001", "name": "C银行", "pub_date": "2026-03-10", "institution": "机2", "rating": "买入", "target_price": 35, "source": "sina_上调评级股票", "report_url": ""},
            {"code": "601001", "name": "C银行", "pub_date": "2026-03-11", "institution": "机3", "rating": "买入", "target_price": 35, "source": "eastmoney_research_report", "report_url": ""},
            {"code": "601001", "name": "C银行", "pub_date": "2026-03-12", "institution": "机4", "rating": "买入", "target_price": 35, "source": "eastmoney_research_report", "report_url": ""},
            {"code": "601001", "name": "C银行", "pub_date": "2026-03-13", "institution": "机5", "rating": "买入", "target_price": 35, "source": "sina_首次评级股票", "report_url": ""},
        ]
    )

    forecast = pd.DataFrame(
        [
            {"code": "600000", "forecast_rating_score": 90.0, "forecast_report_count": 10},
            {"code": "601000", "forecast_rating_score": 70.0, "forecast_report_count": 8},
            {"code": "601001", "forecast_rating_score": 88.0, "forecast_report_count": 12},
        ]
    )

    target = pd.DataFrame(
        [
            {"code": "600000", "avg_target_upside": 0.20},
            {"code": "601000", "avg_target_upside": 0.05},
            {"code": "601001", "avg_target_upside": 0.30},
        ]
    )

    comp = pd.DataFrame(
        [
            {"code": "600000", "sina_comp_rating_score": 88.0, "sina_comp_total_count": 6},
            {"code": "601000", "sina_comp_rating_score": 60.0, "sina_comp_total_count": 3},
            {"code": "601001", "sina_comp_rating_score": 90.0, "sina_comp_total_count": 8},
        ]
    )

    price = pd.DataFrame(
        [
            {"code": "600000", "name": "A银行", "close_price": 10.0},
            {"code": "601000", "name": "B银行", "close_price": 9.0},
            {"code": "601001", "name": "C银行", "close_price": 25.0},
        ]
    )

    out = score_candidates(
        raw_records=raw,
        forecast_metrics=forecast,
        target_metrics=target,
        composite_metrics=comp,
        price_snapshot=price,
        report_date=date(2026, 3, 17),
        window_days=90,
        min_institutions=5,
        price_limit=20,
        top_n=15,
        diversity_weight=0.35,
        rating_weight=0.35,
        upside_weight=0.20,
        consistency_weight=0.10,
    )

    assert len(out) == 1
    assert out.iloc[0]["code"] == "600000"
    assert out.iloc[0]["institution_count"] >= 5
    assert 0 <= out.iloc[0]["composite_score"] <= 100


def test_compute_power_focus_boost_prioritizes_theme_stock() -> None:
    raw = pd.DataFrame(
        [
            {"code": "600100", "name": "算力A", "industry": "通信设备", "pub_date": "2026-03-10", "institution": "机1", "rating": "买入", "target_price": 12, "source": "sina_最新投资评级", "report_url": ""},
            {"code": "600100", "name": "算力A", "industry": "通信设备", "pub_date": "2026-03-11", "institution": "机2", "rating": "买入", "target_price": 13, "source": "eastmoney_research_report", "report_url": ""},
            {"code": "600100", "name": "算力A", "industry": "通信设备", "pub_date": "2026-03-12", "institution": "机3", "rating": "增持", "target_price": 13, "source": "sina_首次评级股票", "report_url": ""},
            {"code": "600100", "name": "算力A", "industry": "通信设备", "pub_date": "2026-03-13", "institution": "机4", "rating": "买入", "target_price": 14, "source": "eastmoney_research_report", "report_url": ""},
            {"code": "600100", "name": "算力A", "industry": "通信设备", "pub_date": "2026-03-14", "institution": "机5", "rating": "买入", "target_price": 14, "source": "sina_上调评级股票", "report_url": ""},
            {"code": "600200", "name": "传统B", "industry": "家电行业", "pub_date": "2026-03-10", "institution": "机1", "rating": "买入", "target_price": 11, "source": "sina_最新投资评级", "report_url": ""},
            {"code": "600200", "name": "传统B", "industry": "家电行业", "pub_date": "2026-03-11", "institution": "机2", "rating": "买入", "target_price": 11, "source": "eastmoney_research_report", "report_url": ""},
            {"code": "600200", "name": "传统B", "industry": "家电行业", "pub_date": "2026-03-12", "institution": "机3", "rating": "增持", "target_price": 11, "source": "sina_首次评级股票", "report_url": ""},
            {"code": "600200", "name": "传统B", "industry": "家电行业", "pub_date": "2026-03-13", "institution": "机4", "rating": "买入", "target_price": 12, "source": "eastmoney_research_report", "report_url": ""},
            {"code": "600200", "name": "传统B", "industry": "家电行业", "pub_date": "2026-03-14", "institution": "机5", "rating": "买入", "target_price": 12, "source": "sina_上调评级股票", "report_url": ""},
        ]
    )

    forecast = pd.DataFrame(
        [
            {"code": "600100", "forecast_rating_score": 86.0, "forecast_report_count": 10},
            {"code": "600200", "forecast_rating_score": 90.0, "forecast_report_count": 10},
        ]
    )
    target = pd.DataFrame(
        [
            {"code": "600100", "avg_target_upside": 0.20},
            {"code": "600200", "avg_target_upside": 0.20},
        ]
    )
    comp = pd.DataFrame(
        [
            {"code": "600100", "sina_comp_rating_score": 86.0, "sina_comp_total_count": 5},
            {"code": "600200", "sina_comp_rating_score": 90.0, "sina_comp_total_count": 5},
        ]
    )
    price = pd.DataFrame(
        [
            {"code": "600100", "name": "算力A", "close_price": 10.0},
            {"code": "600200", "name": "传统B", "close_price": 10.0},
        ]
    )

    out = score_candidates(
        raw_records=raw,
        forecast_metrics=forecast,
        target_metrics=target,
        composite_metrics=comp,
        price_snapshot=price,
        report_date=date(2026, 3, 19),
        window_days=90,
        min_institutions=5,
        price_limit=20,
        top_n=2,
        diversity_weight=0.35,
        rating_weight=0.35,
        upside_weight=0.20,
        consistency_weight=0.10,
        focus_theme="compute_power",
        focus_boost_weight=0.2,
    )

    assert len(out) == 2
    assert out.iloc[0]["code"] == "600100"
    assert out.iloc[0]["focus_theme_score"] > out.iloc[1]["focus_theme_score"]
    assert out.iloc[0]["adjusted_score"] >= out.iloc[0]["composite_score"]
