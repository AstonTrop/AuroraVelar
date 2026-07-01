from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from src.a_share_research.config import PipelineConfig
from src.a_share_research.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股多机构研报筛股工具")
    parser.add_argument("--source-pack", default="broker_strict")
    parser.add_argument("--window-days", type=int, default=90)
    parser.add_argument("--min-institutions", type=int, default=5)
    parser.add_argument("--market", default="mainboard")
    parser.add_argument("--price-basis", default="close")
    parser.add_argument("--price-limit", type=float, default=20.0)
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--report-format", default="html")
    parser.add_argument("--report-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--prefetch-research-limit", type=int, default=120)
    parser.add_argument("--tushare-enabled", action="store_true")
    parser.add_argument("--focus-theme", default="none", help="none | compute_power | 算力")
    parser.add_argument("--focus-boost-weight", type=float, default=0.18)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    focus_theme = args.focus_theme.strip().lower()
    if focus_theme in {"算力", "算力板块", "compute", "compute-power"}:
        focus_theme = "compute_power"
    if focus_theme in {"", "none", "off", "general"}:
        focus_theme = "none"

    cfg = PipelineConfig(
        source_pack=args.source_pack,
        window_days=args.window_days,
        min_institutions=args.min_institutions,
        market=args.market,
        price_basis=args.price_basis,
        price_limit=args.price_limit,
        top_n=args.top_n,
        report_format=args.report_format,
        report_date=date.fromisoformat(args.report_date),
        output_dir=Path(args.output_dir),
        prefetch_research_limit=args.prefetch_research_limit,
        tushare_enabled=args.tushare_enabled,
        focus_theme=focus_theme,
        focus_boost_weight=args.focus_boost_weight,
    )

    result = run_pipeline(cfg)
    print("\n=== 输出文件 ===")
    print(f"Top候选: {result['top_csv']}")
    print(f"原始快照: {result['raw_csv']}")
    print(f"HTML报告: {result['html_report']}")
    print(f"候选数量: {result['candidate_count']}")


if __name__ == "__main__":
    main()
