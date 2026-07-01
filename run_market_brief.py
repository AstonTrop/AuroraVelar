from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from src.a_share_research.portfolio import PortfolioAnalysisConfig, run_market_brief


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股实时市场快报")
    parser.add_argument("--report-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", default="output/market")
    parser.add_argument("--market-snapshot-cache-file", default="output/portfolio/market_snapshot_cache.csv")
    parser.add_argument("--market-snapshot-cache-minutes", type=int, default=15)
    parser.add_argument("--focus-theme", default="none", help="none | compute_power | 算力")
    parser.add_argument("--focus-boost-weight", type=float, default=0.18)
    parser.add_argument("--window-days", type=int, default=90)
    parser.add_argument("--min-institutions", type=int, default=5)
    parser.add_argument("--price-limit", type=float, default=20.0)
    parser.add_argument("--top-n-candidates", type=int, default=10)
    return parser.parse_args()


def _normalize_focus_theme(raw: str) -> str:
    focus_theme = raw.strip().lower()
    if focus_theme in {"算力", "算力板块", "compute", "compute-power"}:
        return "compute_power"
    if focus_theme in {"", "none", "off", "general"}:
        return "none"
    return focus_theme


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    cfg = PortfolioAnalysisConfig(
        report_date=date.fromisoformat(args.report_date),
        output_dir=output_dir,
        focus_theme=_normalize_focus_theme(args.focus_theme),
        focus_boost_weight=args.focus_boost_weight,
        window_days=args.window_days,
        min_institutions=args.min_institutions,
        price_limit=args.price_limit,
        top_n_candidates=args.top_n_candidates,
        market_snapshot_cache_path=Path(args.market_snapshot_cache_file),
        market_snapshot_cache_minutes=args.market_snapshot_cache_minutes,
    )

    result = run_market_brief(cfg=cfg)
    print("\n=== 输出文件 ===")
    print(f"市场快报: {result['report_path']}")
    print(f"HTML报告: {result['report_html_path']}")
    print(f"候选清单: {result['candidates_path']}")
    market_context = result.get("market_context", {})
    if market_context:
        print("\n=== 市场环境 ===")
        print(f"候选池快照来源: {market_context.get('candidate_snapshot_source', '未知')}")
        print(f"大盘节奏: {market_context.get('trend_label', '未知')}")
        print(f"市场广度: {market_context.get('breadth_label', '未知')}")
        print(f"风险水平: {market_context.get('risk_level', '未知')}")
        print(f"热点方向: {'、'.join(market_context.get('hot_sectors', [])) or '暂无'}")


if __name__ == "__main__":
    main()
