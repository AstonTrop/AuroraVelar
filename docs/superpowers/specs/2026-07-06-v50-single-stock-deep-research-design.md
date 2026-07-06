# V5.0 Single Stock Deep Research Design

## Goal

V5.0 turns the system from a stock-picking assistant into a single-stock deep research system.

The core question is no longer "which stock should I buy?", but:

> At the current price, what is this stock's short-term and swing-trade profit probability, is the risk-reward attractive, where are the risks, and what should we do if the thesis is wrong?

The system should still support portfolio context and account constraints, but stock recommendation and candidate scanning are no longer the default workflow.

## Non-Goals

- Do not default to recommending new stocks.
- Do not treat `buy_now` or executable candidates as investment recommendations.
- Do not invent precise probability numbers when evidence is weak.
- Do not use technical levels without explaining the source.
- Do not analyze a stock without considering its sector.

## Research Framework

Every single-stock analysis must evaluate nine categories.

### 1. Time Horizon

Profit probability must be split by horizon:

- Intraday / next session: mainly uses intraday trend, order book, volume, sector momentum, and market sentiment.
- 3-5 trading days: mainly uses recent three-day structure, short-term trend, volume support, and sector persistence.
- 2-6 week swing: mainly uses daily trend, fundamentals, valuation, events, and industry logic.

This prevents mixing conflicting conclusions, such as "weak today but still acceptable for swing trading."

### 2. Market Environment

The system must judge whether the current market supports risk-taking:

- Major index strength.
- Up/down stock counts.
- Limit-up / limit-down counts.
- Market turnover.
- Attack / trial / defense mode.
- Whether the environment supports opening new positions or only managing existing positions.

Weak markets automatically reduce the confidence of aggressive conclusions.

### 3. Sector Position

Sector analysis is mandatory for A-share stocks:

- Industry and concept mapping.
- Sector change, rank, turnover, and fund flow if available.
- Internal sector breadth.
- Whether sector leaders are strong.
- Estimated role: leader, trend core, low-position catch-up, follower, laggard, or unknown.
- Whether the stock is stronger than, aligned with, or weaker than its sector.

If sector data is missing or partial, buy/add conclusions must be downgraded.

### 4. Technical Structure

Technical analysis must be systematic and source-backed:

- MA5, MA10, MA20, MA60, MA120.
- Current price relative to moving averages.
- Moving-average slope: rising, flat, or falling.
- MACD, RSI, KDJ, BOLL.
- 20-day and 60-day high/low.
- ATR volatility.
- Support, resistance, platform areas.
- Pattern: breakout, pullback, failed breakout, reversal, breakdown repair, weak-to-strong, or high-level acceleration.

Technical analysis is based on price, volume, momentum, and risk management.

### 5. Intraday And Order Book

Short-term judgment must use live or latest intraday details:

- Whether price is above intraday average / VWAP.
- 5/15/30 minute direction.
- Whether the stock rallied then faded.
- Whether pullback volume is shrinking.
- Whether breakout volume is expanding.
- Bid 1-5 and ask 1-5.
- Whether overhead selling pressure is heavy.
- Whether buy orders are active demand or passive resting orders.
- If near limit-up: whether the seal looks stable and whether seal size is decaying.

This determines whether the current price is comfortable to act on.

### 6. Volume, Turnover, And Funds

The system should evaluate:

- Trading amount.
- Turnover rate.
- Volume ratio.
- Recent three-day volume change.
- Up on volume, down on volume, rebound without volume, or healthy pullback.
- Main net inflow/outflow if stable.
- Large-order direction if available.
- Margin financing changes if available.

Volume-price analysis helps classify capital attack, shakeout, distribution, or lack of attention.

### 7. Fundamentals

For single-stock research, fundamentals must cover:

- Revenue growth.
- Net profit growth.
- Adjusted net profit.
- Gross margin and net margin.
- ROE and ROIC.
- Operating cash flow.
- Debt ratio.
- Receivables and inventory.
- Earnings forecast / performance guidance.
- Dividends and buybacks.
- Clarity of main business.
- Loss, goodwill, litigation, reduction, pledge, delisting, or ST risks.

Fundamentals may not determine intraday moves, but they affect whether a stock deserves swing-position capital.

### 8. Valuation And Risk-Reward

The system must judge whether the current price is attractive:

- PE, PB, PS.
- Historical valuation percentile if available.
- Peer valuation comparison if available.
- Market cap size.
- Whether growth justifies valuation.
- Upside target range.
- Downside failure range.
- Risk-reward ratio.

Example rule of thumb: upside 6% and downside 5% is weak risk-reward; upside 12% and downside 4% is more attractive.

### 9. Events And Risks

The system should check:

- Latest announcements.
- Earnings report schedule.
- Unlocks.
- Shareholder reductions.
- Regulatory inquiries.
- Policy catalysts.
- Industry news.
- Product price changes.
- Commodity price changes.
- Peer leader performance.
- Delisting, ST, or earnings thunder risks.

Events can change both probability and risk-reward.

## Default Output Structure

Every single-stock response must use this structure:

1. One-line conclusion: strong hold / repair watch / conditional buy / not suitable to add / risk first.
2. Data quality: what is live, delayed, partial, failed, and confidence level.
3. Market and sector: whether the stock has tailwind.
4. Three-day and daily structure: short-term and swing position.
5. Intraday and order book: whether the current moment is strong.
6. Technical analysis: support, resistance, buy point, sell point, failure line, and source.
7. Fundamental analysis: earnings, cash flow, valuation, and risks.
8. Profit probability: intraday/next session, 3-5 days, and 2-6 week bands with confidence.
9. Risk-reward: upside, downside, and risk-reward ratio.
10. Operation plan: existing position plan and no-position plan.
11. Invalidation conditions: what proves the analysis wrong.
12. Final score: technical score, sector score, fundamental score, risk score, and overall score.

## Probability Model

V5.0 must avoid fake precision. The output should use probability bands rather than overly exact claims:

- High: 65%-75%, only when market, sector, technical, intraday, and data quality all align.
- Medium-high: 58%-65%, when most evidence aligns but one major weakness exists.
- Neutral: 48%-58%, when evidence is mixed.
- Medium-low: 40%-48%, when risk is clearly higher than opportunity.
- Low: below 40%, when market, sector, technical, or risk-reward is poor.

Each horizon gets its own band and confidence level.

## Scoring Model

The system should expose a structured score package:

- Technical score: trend, momentum, support/resistance, volatility, and pattern quality.
- Sector score: sector strength, sector breadth, leader strength, and stock role.
- Intraday score: VWAP position, 5/15/30 minute direction, volume behavior, order book.
- Fundamental score: growth, profitability, cash flow, balance sheet, business clarity.
- Valuation score: valuation level, peer comparison, upside/downside space.
- Risk score: data gaps, event risks, T+1 risk, liquidity, volatility, market risk.
- Overall score: weighted summary adapted to horizon.

Scores must include evidence and weakness notes, not just numbers.

## API Design

V5.0 should strengthen the existing `getStockIntradayAnalysis` path instead of replacing the whole data layer.

### Add Research Fields To `stock_intraday_analysis`

The response should add:

- `v50_research_summary`
- `time_horizon_probability`
- `risk_reward_profile`
- `fundamental_research`
- `valuation_research`
- `event_risk_research`
- `sector_position_research`
- `technical_research`
- `operation_plan_v50`
- `invalidation_conditions`
- `final_research_score`

These fields should organize existing quote, intraday, order book, daily history, board, market, and account data into a research-ready form.

### Candidate Endpoints

Candidate endpoints stay available only for explicit stock-picking requests, but GPT instructions must not use them by default.

When the user asks about a stock, position, or screenshot holding, the default path is single-stock deep research.

## Data Gaps And Degradation

If a module is missing:

- Missing quote: no trading conclusion.
- Missing intraday: no intraday/next-session probability above neutral.
- Missing board: sector score is capped and buy/add conclusion downgraded.
- Missing fundamentals: 2-6 week probability confidence is low.
- Missing valuation: risk-reward must say valuation evidence is incomplete.
- Missing events: event-risk confidence is low.

GPT must say what is missing in plain Chinese and explain how that affects the conclusion.

## GPT Instruction Changes

The GPT instruction should be renamed to V5 single-stock deep research.

Main behavior:

- Do not default to stock picking.
- If user asks "analyze this stock", call single-stock analysis first.
- If user asks "should I buy/sell/hold", still answer through probability, risk-reward, and invalidation, not a naked yes/no.
- If user asks for recommendations, explain that V5 is optimized for deep analysis and ask for candidate names or explicitly use candidate tools only after user confirms.
- Use Chinese terms, explain like the user may be a beginner, and avoid interface-name clutter in the main answer.

## Testing Requirements

Tests must verify:

- Single-stock analysis returns the new V5 fields.
- Probability is split into three horizons.
- Missing intraday data caps intraday probability.
- Missing sector data downgrades sector score and buy/add conclusion.
- Fundamental and valuation gaps are surfaced, not hidden.
- Support, resistance, buy point, sell point, and failure line all include sources.
- Existing account constraints still respect T+1 and `available=0`.
- Candidate endpoint is not described as the default workflow in the GPT instruction.

## Success Criteria

After V5.0, a user asking about one stock should receive a response that:

- Explains whether the stock has short-term and swing-trade edge.
- Separates probability by horizon.
- Connects stock behavior to market and sector.
- Uses technical, intraday, volume, fundamental, valuation, and event evidence.
- Shows the upside/downside risk-reward.
- Gives a plan for both existing holders and non-holders.
- Clearly states what would prove the analysis wrong.
- Does not drift into repetitive stock recommendations unless explicitly asked.
