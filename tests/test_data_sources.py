import pandas as pd

from src.a_share_research.data_sources import dedupe_standard_records


def test_dedupe_standard_records_keeps_latest() -> None:
    df = pd.DataFrame(
        [
            {
                "code": "600000",
                "name": "浦发银行",
                "pub_date": "2026-03-02",
                "institution": "机构A",
                "rating": "买入",
                "target_price": 12,
                "source": "sina_最新投资评级",
                "report_url": "",
            },
            {
                "code": "600000",
                "name": "浦发银行",
                "pub_date": "2026-03-02",
                "institution": "机构A",
                "rating": "买入",
                "target_price": 13,
                "source": "sina_最新投资评级",
                "report_url": "",
            },
        ]
    )

    out = dedupe_standard_records(df)
    assert len(out) == 1
    assert str(out.iloc[0]["pub_date"]).startswith("2026-03-02")
