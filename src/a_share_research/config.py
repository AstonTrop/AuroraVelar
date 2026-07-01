from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    source_pack: str = "broker_strict"
    window_days: int = 90
    min_institutions: int = 5
    market: str = "mainboard"
    price_basis: str = "close"
    price_limit: float = 20.0
    top_n: int = 15
    report_format: str = "html"
    report_date: date = date.today()
    output_dir: Path = Path("output")
    prefetch_research_limit: int = 120
    tushare_enabled: bool = False
    focus_theme: str = "none"
    focus_boost_weight: float = 0.18

    # Score weights
    diversity_weight: float = 0.35
    rating_weight: float = 0.35
    upside_weight: float = 0.20
    consistency_weight: float = 0.10

    def validate(self) -> None:
        if self.source_pack != "broker_strict":
            raise ValueError("Only source_pack='broker_strict' is supported in this version.")
        if self.price_basis != "close":
            raise ValueError("Only price_basis='close' is supported in this version.")
        if self.market != "mainboard":
            raise ValueError("Only market='mainboard' is supported in this version.")
        if self.report_format != "html":
            raise ValueError("Only report_format='html' is supported in this version.")
        if not (0 < self.top_n <= 200):
            raise ValueError("top_n must be in range 1..200")
        if self.window_days <= 0:
            raise ValueError("window_days must be positive")
        if self.min_institutions < 0:
            raise ValueError("min_institutions must be >= 0")
        if self.price_limit <= 0:
            raise ValueError("price_limit must be positive")
        if self.focus_theme not in {"none", "compute_power"}:
            raise ValueError("focus_theme must be one of {'none', 'compute_power'}")
        if not (0.0 <= self.focus_boost_weight <= 1.0):
            raise ValueError("focus_boost_weight must be in range 0.0..1.0")
