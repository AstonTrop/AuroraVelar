# A股多机构研报筛股工具（券商研报严格版）

## 功能
- 多源数据: 东方财富盈利预测、东方财富个股研报、新浪机构评级。
- 严格筛选: 仅沪深主板、剔除 ST、收盘价小于 20 元、近 90 天机构覆盖至少 5 家。
- 综合评分: 机构多样性 35% + 评级强度 35% + 目标价空间 20% + 跨源一致性 10%。
- 输出: `candidates_top15.csv`、`research_raw_snapshot.csv`、静态 HTML 可视化报告。

## 运行
```bash
python3 -m pip install -r requirements.txt
python3 run_analysis.py \
  --source-pack broker_strict \
  --window-days 90 \
  --min-institutions 5 \
  --market mainboard \
  --price-basis close \
  --price-limit 20 \
  --top-n 15 \
  --report-format html
```

## 持仓全盘分析
- 输入模板: `股票代码 | 股票名称 | 仓位% | 成本价`
- 每次执行分析都会联网抓取最新价格、大盘指数、市场广度、重点持仓新闻/公告、技术位和基本面摘要。
- 输出固定包含: 市场环境、投资结论、组合总览、个股诊断、调仓建议、多空要点、跟踪指标、新标的建议。
- 会自动保存最近一次持仓到 `output/portfolio/latest_positions.csv`，后续可直接复用。
- 同时输出 Markdown 和 HTML 两种报告，适合终端查看和直接打开阅读。

```bash
python3 run_portfolio_analysis.py \
  --positions-file examples/portfolio_positions_template.txt \
  --report-date 2026-06-29 \
  --output-dir output/portfolio \
  --focus-theme compute_power
```

也可以直接把持仓文本作为参数传入:

```bash
python3 run_portfolio_analysis.py \
  --positions-text $'000725 | 京东方A | 8.0% | 8.614\n600522 | 中天科技 | 18.0% | 59.231'
```

如果上一次持仓已经保存，只想重新执行一次最新联网分析，可以直接复用最近持仓:

```bash
python3 run_portfolio_analysis.py \
  --use-latest-positions \
  --latest-positions-file output/portfolio/latest_positions.csv
```

如果最近持仓快照已经存在，直接运行 `python3 run_portfolio_analysis.py` 也会默认优先复用最近持仓。

## 实时市场快报
- 如果你这次只想看大盘、热点方向和候选股，不带持仓，也可以单独运行市场快报。
- 会联网抓取最新指数、市场广度、热点方向和高分候选股，并输出 Markdown / HTML 报告。

```bash
python3 run_market_brief.py \
  --report-date 2026-06-29 \
  --output-dir output/market
```

默认会复用 `output/portfolio/market_snapshot_cache.csv` 作为市场快照缓存，方便大盘快报和持仓分析共享同一份全市场数据。

## 输出文件
- `output/candidates_top15.csv`
- `output/research_raw_snapshot.csv`
- `output/a_share_research_report.html`
- `output/portfolio/portfolio_positions_snapshot.csv`
- `output/portfolio/latest_positions.csv`
- `output/portfolio/portfolio_analysis_report.md`
- `output/portfolio/portfolio_analysis_report.html`
- `output/market/market_brief_report.md`
- `output/market/market_brief_report.html`
- `output/market/market_brief_candidates.csv`

## 备注
- 已预留 `--tushare-enabled` 参数，当前默认关闭（无 Token 场景）。
- 结果仅用于研究辅助，不构成投资建议。
