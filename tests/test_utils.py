import pytest

from src.a_share_research.utils import is_mainboard_code, is_st_name, normalize_code, parse_numeric, rating_to_score


def test_normalize_code_extracts_tail_digits() -> None:
    assert normalize_code("sh600519") == "600519"
    assert normalize_code("bj920000") == "920000"
    assert normalize_code("000001") == "000001"
    assert normalize_code("abc") == ""


def test_mainboard_filter() -> None:
    assert is_mainboard_code("600000")
    assert is_mainboard_code("002001")
    assert not is_mainboard_code("300001")
    assert not is_mainboard_code("830001")


def test_st_name_filter() -> None:
    assert is_st_name("*ST华夏")
    assert is_st_name("ST中珠")
    assert not is_st_name("平安银行")


def test_parse_numeric_percent_and_value() -> None:
    assert parse_numeric("12.3%") == pytest.approx(0.123)
    assert parse_numeric("35.1/80.1%") == 35.1


def test_rating_to_score() -> None:
    assert rating_to_score("买入") == 95.0
    assert rating_to_score("增持") == 80.0
    assert rating_to_score("卖出") == 10.0
