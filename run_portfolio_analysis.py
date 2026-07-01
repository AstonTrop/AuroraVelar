from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from src.a_share_research.portfolio import (
    PortfolioAnalysisConfig,
    load_positions_file,
    parse_positions_text,
    resolve_analysis_paths,
    run_portfolio_analysis,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股持仓全盘分析工具")
    parser.add_argument("--positions-file", help="持仓文件路径，支持 .txt/.md/.csv")
    parser.add_argument(
        "--positions-text",
        help="直接传入持仓文本，格式为 `代码 | 名称 | 仓位% | 成本价`，也支持追加 `| 持仓股数 | 可卖股数`",
    )
    parser.add_argument("--use-latest-positions", action="store_true", help="使用最近一次保存的持仓快照")
    parser.add_argument("--latest-positions-file", default="output/portfolio/latest_positions.csv")
    parser.add_argument("--report-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", default="output/portfolio")
    parser.add_argument("--market-snapshot-cache-file", default="output/portfolio/market_snapshot_cache.csv")
    parser.add_argument("--market-snapshot-cache-minutes", type=int, default=15)
    parser.add_argument("--focus-theme", default="none", help="none | compute_power | 算力")
    parser.add_argument("--focus-boost-weight", type=float, default=0.18)
    parser.add_argument("--window-days", type=int, default=90)
    parser.add_argument("--min-institutions", type=int, default=5)
    parser.add_argument("--price-limit", type=float, default=20.0)
    parser.add_argument("--top-n-candidates", type=int, default=5)
    parser.add_argument("--important-holding-count", type=int, default=3)
    parser.add_argument("--notice-lookback-days", type=int, default=3)
    return parser.parse_args()


def _normalize_focus_theme(raw: str) -> str:
    focus_theme = raw.strip().lower()
    if focus_theme in {"算力", "算力板块", "compute", "compute-power"}:
        return "compute_power"
    if focus_theme in {"", "none", "off", "general"}:
        return "none"
    return focus_theme


def _load_positions(args: argparse.Namespace, latest_positions_path: Path):
    if args.positions_text:
        return parse_positions_text(args.positions_text)
    if args.positions_file:
        return load_positions_file(args.positions_file)
    if args.use_latest_positions or latest_positions_path.exists():
        return load_positions_file(latest_positions_path)
    raise ValueError("请提供 --positions-file、--positions-text，或先保留一份最近持仓快照。")


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    latest_positions_path, market_snapshot_cache_path = resolve_analysis_paths(
        output_dir=output_dir,
        latest_positions_file=args.latest_positions_file,
        market_snapshot_cache_file=args.market_snapshot_cache_file,
    )
    positions_df = _load_positions(args, latest_positions_path=latest_positions_path)
    cfg = PortfolioAnalysisConfig(
        report_date=date.fromisoformat(args.report_date),
        output_dir=output_dir,
        focus_theme=_normalize_focus_theme(args.focus_theme),
        focus_boost_weight=args.focus_boost_weight,
        window_days=args.window_days,
        min_institutions=args.min_institutions,
        price_limit=args.price_limit,
        top_n_candidates=args.top_n_candidates,
        important_holding_count=args.important_holding_count,
        notice_lookback_days=args.notice_lookback_days,
        latest_positions_path=latest_positions_path,
        market_snapshot_cache_path=market_snapshot_cache_path,
        market_snapshot_cache_minutes=args.market_snapshot_cache_minutes,
    )

    result = run_portfolio_analysis(positions_df=positions_df, cfg=cfg)
    print("\n=== 输出文件 ===")
    print(f"持仓快照: {result['positions_path']}")
    print(f"最新持仓缓存: {result['latest_positions_path']}")
    print(f"分析报告: {result['report_path']}")
    print(f"HTML报告: {result['report_html_path']}")
    market_context = result.get("market_context", {})
    if market_context:
        print("\n=== 市场环境 ===")
        print(f"候选池快照来源: {market_context.get('candidate_snapshot_source', '未知')}")
        print(f"大盘节奏: {market_context.get('trend_label', '未知')}")
        print(f"市场广度: {market_context.get('breadth_label', '未知')}")
        print(f"风险水平: {market_context.get('risk_level', '未知')}")
    print("\n=== 组合摘要 ===")
    overview = result["overview"]
    print(f"股票仓位: {overview['stock_exposure_pct']:.1f}%")
    print(f"现金仓位: {overview['cash_pct']:.1f}%")
    print(f"最大单票: {overview['top_holding_pct']:.1f}%")
    print(f"前三持仓: {overview['top3_holding_pct']:.1f}%")
    print(f"风险提示: {'；'.join(overview['risk_flags']) if overview['risk_flags'] else '暂无显著风险'}")


if __name__ == "__main__":
    main()
