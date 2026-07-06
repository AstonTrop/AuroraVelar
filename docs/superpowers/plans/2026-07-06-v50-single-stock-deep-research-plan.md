# V5.0 Single Stock Deep Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the default GPT workflow from candidate stock picking to single-stock deep research with horizon-based probability, risk-reward, fundamentals, valuation, sector position, and invalidation logic.

**Architecture:** Keep the existing market-data service and `stock_intraday_analysis` endpoint. Add a V5 research layer inside `MarketDataService` that organizes existing quote, intraday, order book, history, board, market, and account data into research-ready sections. Update the GPT instruction and OpenAPI descriptions so ChatGPT defaults to single-stock analysis and only uses candidate tools when explicitly asked.

**Tech Stack:** Python, pandas, FastAPI, pytest, YAML OpenAPI, Markdown GPT instruction files.

---

## File Structure

- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/src/a_share_research/market_data_service.py`
  - Add V5 helper methods and include V5 fields in `stock_intraday_analysis`.
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/tests/test_market_data_service.py`
  - Add failing tests for V5 research fields and degradation behavior.
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/GPT_STOCK_TRADING_ASSISTANT_V4_2.md`
  - Replace V4.5 candidate-first instruction with V5 single-stock deep research instruction.
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/chatgpt_action_openapi.yaml`
  - Update action descriptions to make `getStockIntradayAnalysis` the default single-stock research action and candidate tools explicit-only.
- Optional verify only: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/scripts/run_market_data_server.py`
  - No code changes expected; used for route validation if needed.

---

## Task 1: Add V5 Contract Test For Single-Stock Research

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/tests/test_market_data_service.py`

- [ ] **Step 1: Write the failing test**

Append this test after `test_stock_intraday_analysis_returns_professional_diagnosis_sections`:

```python
def test_stock_intraday_analysis_returns_v50_deep_research_sections() -> None:
    hist_df = pd.DataFrame(
        {
            "日期": [f"2026-06-{day:02d}" for day in range(1, 31)] + [f"2026-07-{day:02d}" for day in range(1, 31)],
            "开盘": [7.0 + i * 0.03 for i in range(60)],
            "最高": [7.2 + i * 0.03 for i in range(60)],
            "最低": [6.8 + i * 0.03 for i in range(60)],
            "收盘": [7.0 + i * 0.03 for i in range(60)],
            "成交量": [100000 + i * 2000 for i in range(60)],
        }
    )
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame(
            [
                {
                    "代码": "002100",
                    "名称": "天康生物",
                    "最新价": 8.76,
                    "涨跌幅": 1.8,
                    "今开": 8.6,
                    "最高": 8.9,
                    "最低": 8.55,
                    "换手率": 4.5,
                    "量比": 1.4,
                    "总市值": 12000000000,
                    "流通市值": 9000000000,
                }
            ]
        ),
        indices=pd.DataFrame([{"代码": "000001", "名称": "上证指数", "最新价": 3200.0, "涨跌幅": 0.5}]),
        boards_df=pd.DataFrame(
            [
                {
                    "board_type": "行业",
                    "board_name": "农牧饲渔",
                    "change_pct": 2.8,
                    "rank": 6,
                    "up_ratio": 0.72,
                    "main_net_inflow": 120000000,
                    "leader_code": "002100",
                    "leader": "天康生物",
                    "leader_change_pct": 4.2,
                    "limit_up_count": 2,
                }
            ]
        ),
        bidasks={
            "002100": pd.DataFrame(
                [
                    {"item": "最新", "value": 8.76},
                    {"item": "涨幅", "value": 1.8},
                    {"item": "buy_1", "value": 8.76},
                    {"item": "buy_1_vol", "value": 12000},
                    {"item": "sell_1", "value": 8.77},
                    {"item": "sell_1_vol", "value": 9000},
                ]
            )
        },
        hist={"002100": hist_df},
        intraday={
            "002100": pd.DataFrame(
                [
                    {"time": "09:31", "close": 8.6, "avg_price": 8.58, "volume": 10000, "amount": 86000},
                    {"time": "10:00", "close": 8.76, "avg_price": 8.68, "volume": 18000, "amount": 157680},
                ]
            )
        },
    )

    out = MarketDataService(provider=provider).stock_intraday_analysis(
        {"code": "002100", "account": {"cash": 5000, "total_asset": 12000, "positions": [{"code": "002100", "shares": 100, "available": 100, "cost": 8.5}]}}
    )

    assert out["v50_research_summary"]["research_version"] == "V5.0"
    assert out["v50_research_summary"]["default_workflow"] == "单股深度研究，不默认选股"
    assert out["sector_position_research"]["must_use_in_reply"] is True
    assert out["sector_position_research"]["stock_vs_sector"] in {"强于板块", "同步板块", "弱于板块", "不可确认"}
    assert out["technical_research"]["level_source_policy"] == "所有关键点位必须带来源"
    assert set(out["time_horizon_probability"]) == {"intraday_next_session", "three_to_five_days", "two_to_six_weeks"}
    assert out["time_horizon_probability"]["intraday_next_session"]["probability_band"]
    assert out["time_horizon_probability"]["two_to_six_weeks"]["confidence"] in {"高", "中", "低"}
    assert out["risk_reward_profile"]["risk_reward_ratio"] is not None
    assert out["risk_reward_profile"]["upside_reference"]["source"]
    assert out["risk_reward_profile"]["downside_reference"]["source"]
    assert out["fundamental_research"]["status"] in {"ok", "partial", "failed"}
    assert out["valuation_research"]["status"] in {"ok", "partial", "failed"}
    assert out["event_risk_research"]["status"] in {"ok", "partial", "failed"}
    assert out["operation_plan_v50"]["existing_position_plan"]
    assert out["operation_plan_v50"]["no_position_plan"]
    assert out["invalidation_conditions"]["technical"]
    assert out["final_research_score"]["overall_score"] is not None
    assert out["final_research_score"]["score_components"]["sector_score"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_stock_intraday_analysis_returns_v50_deep_research_sections -q
```

Expected: FAIL with missing key such as `KeyError: 'v50_research_summary'`.

---

## Task 2: Implement V5 Research Helper Methods

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/src/a_share_research/market_data_service.py`

- [ ] **Step 1: Add V5 helper methods near existing professional diagnosis helpers**

Add methods with these exact names:

```python
def _v50_score_from_status(self, status: str, default: float = 50.0) -> float:
    return {"ok": 75.0, "partial": 55.0, "skipped": 45.0, "failed": 35.0}.get(str(status), default)
```

```python
def _v50_probability_label(self, score: float) -> tuple[str, str]:
    if score >= 70:
        return "65%-75%", "高"
    if score >= 62:
        return "58%-65%", "中"
    if score >= 50:
        return "48%-58%", "中"
    if score >= 42:
        return "40%-48%", "中"
    return "40%以下", "低"
```

Implement these research methods:

- `_v50_sector_position_research(board_stock_alignment, sector_alignment)`
- `_v50_technical_research(professional_technical_diagnosis, technical_level_layers, moving_average_structure, recent_3d_context, today_intraday_summary, volume_price_relation, risk_volatility)`
- `_v50_fundamental_research(fundamental_diagnosis)`
- `_v50_valuation_research(fundamental_diagnosis, risk_reward_profile=None)`
- `_v50_event_risk_research(data_quality, zt_related)`
- `_v50_risk_reward_profile(quote, technical_level_layers, support_resistance_zones)`
- `_v50_time_horizon_probability(decision_score, data_quality, sector_position_research, technical_research, fundamental_research, valuation_research, risk_reward_profile)`
- `_v50_operation_plan(quote, position_risk_contribution, technical_level_layers, time_horizon_probability, risk_reward_profile)`
- `_v50_invalidation_conditions(technical_level_layers, sector_position_research, data_quality)`
- `_v50_final_research_score(technical_research, sector_position_research, fundamental_research, valuation_research, time_horizon_probability, risk_reward_profile, data_quality)`
- `_v50_research_summary(quote, data_quality, final_research_score, time_horizon_probability)`

- [ ] **Step 2: Include V5 fields in `stock_intraday_analysis`**

After `professional_scenario_plan`, compute the V5 fields and include them in the returned dictionary:

```python
sector_position_research = self._v50_sector_position_research(board_stock_alignment, sector_alignment)
technical_research = self._v50_technical_research(
    professional_technical_diagnosis,
    technical_level_layers,
    moving_average_structure,
    recent_3d_context,
    today_intraday_summary,
    volume_price_relation,
    risk_volatility,
)
fundamental_research = self._v50_fundamental_research(fundamental_diagnosis)
risk_reward_profile = self._v50_risk_reward_profile(quote, technical_level_layers, support_resistance_zones)
valuation_research = self._v50_valuation_research(fundamental_diagnosis, risk_reward_profile)
event_risk_research = self._v50_event_risk_research(data_quality, zt_related)
time_horizon_probability = self._v50_time_horizon_probability(
    decision_score,
    data_quality,
    sector_position_research,
    technical_research,
    fundamental_research,
    valuation_research,
    risk_reward_profile,
)
operation_plan_v50 = self._v50_operation_plan(
    quote,
    position_risk_contribution,
    technical_level_layers,
    time_horizon_probability,
    risk_reward_profile,
)
invalidation_conditions = self._v50_invalidation_conditions(technical_level_layers, sector_position_research, data_quality)
final_research_score = self._v50_final_research_score(
    technical_research,
    sector_position_research,
    fundamental_research,
    valuation_research,
    time_horizon_probability,
    risk_reward_profile,
    data_quality,
)
v50_research_summary = self._v50_research_summary(quote, data_quality, final_research_score, time_horizon_probability)
```

Add returned keys:

```python
"v50_research_summary": v50_research_summary,
"time_horizon_probability": time_horizon_probability,
"risk_reward_profile": risk_reward_profile,
"fundamental_research": fundamental_research,
"valuation_research": valuation_research,
"event_risk_research": event_risk_research,
"sector_position_research": sector_position_research,
"technical_research": technical_research,
"operation_plan_v50": operation_plan_v50,
"invalidation_conditions": invalidation_conditions,
"final_research_score": final_research_score,
```

- [ ] **Step 3: Run V5 contract test**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_stock_intraday_analysis_returns_v50_deep_research_sections -q
```

Expected: PASS.

---

## Task 3: Add V5 Degradation Tests

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/tests/test_market_data_service.py`

- [ ] **Step 1: Add test for missing intraday and missing sector data**

Append:

```python
def test_v50_deep_research_degrades_when_intraday_or_sector_missing() -> None:
    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame([{"代码": "002100", "名称": "天康生物", "最新价": 8.76, "涨跌幅": 1.0}]),
        hist={"002100": pd.DataFrame({"收盘": [5 + i * 0.05 for i in range(80)]})},
    )

    out = MarketDataService(provider=provider).stock_intraday_analysis({"code": "002100"})

    assert out["data_quality"]["intraday_status"] == "failed"
    assert out["sector_position_research"]["status"] in {"partial", "failed"}
    assert out["time_horizon_probability"]["intraday_next_session"]["probability_band"] in {"48%-58%", "40%-48%", "40%以下"}
    assert out["time_horizon_probability"]["two_to_six_weeks"]["confidence"] == "低"
    assert "分时数据缺失" in out["invalidation_conditions"]["data_quality"]
    assert "板块数据不足" in out["sector_position_research"]["weaknesses"]
```

- [ ] **Step 2: Verify RED or GREEN depending on Task 2 completeness**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_v50_deep_research_degrades_when_intraday_or_sector_missing -q
```

Expected after Task 2: PASS. If it fails, adjust only V5 degradation logic.

---

## Task 4: Rewrite GPT Instruction To V5 Single-Stock Research

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/GPT_STOCK_TRADING_ASSISTANT_V4_2.md`
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/tests/test_market_data_service.py`

- [ ] **Step 1: Add instruction test**

Update `test_gpt_instructions_reference_required_actions` or add a new test near the existing instruction tests:

```python
def test_v50_gpt_instruction_defaults_to_single_stock_deep_research() -> None:
    instructions = Path("GPT_STOCK_TRADING_ASSISTANT_V4_2.md").read_text(encoding="utf-8")

    assert "V5 单股深度研究版" in instructions
    assert "不再默认选股" in instructions
    assert "getStockIntradayAnalysis" in instructions
    assert "盈利概率" in instructions
    assert "日内/次日" in instructions
    assert "3-5个交易日" in instructions
    assert "2-6周波段" in instructions
    assert "候选股工具只在用户明确要求选股时使用" in instructions
```

- [ ] **Step 2: Run instruction test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_v50_gpt_instruction_defaults_to_single_stock_deep_research -q
```

Expected: FAIL because V4.5 instruction still focuses on candidate logic.

- [ ] **Step 3: Replace instruction file content with V5**

Replace the file with a concise Chinese instruction under 7000 characters. Required sections:

- Role: A-share single-stock deep research assistant.
- Default workflow: single-stock research first, not stock picking.
- Required action: use `getStockIntradayAnalysis` for any stock-specific question.
- Optional actions: candidate tools only when user explicitly asks for stock picking.
- Output structure: the 12-section V5 output.
- Probability rules: split into 日内/次日、3-5个交易日、2-6周波段.
- Mandatory sector integration.
- Mandatory fundamentals and valuation.
- Plain-Chinese beginner explanation.
- Data-quality degradation.
- Account/T+1 handling.

- [ ] **Step 4: Run instruction test**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_v50_gpt_instruction_defaults_to_single_stock_deep_research -q
```

Expected: PASS.

---

## Task 5: Update OpenAPI Descriptions

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/chatgpt_action_openapi.yaml`
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/tests/test_market_data_service.py`

- [ ] **Step 1: Add OpenAPI description test**

Append or update the OpenAPI validation test:

```python
def test_openapi_describes_v50_single_stock_research_default() -> None:
    text = Path("chatgpt_action_openapi.yaml").read_text(encoding="utf-8")

    assert "V5单股深度研究" in text
    assert "不再默认选股" in text
    assert "time_horizon_probability" in text
    assert "risk_reward_profile" in text
    assert "candidate tools only" not in text
    assert "候选工具只在用户明确要求选股时使用" in text
```

- [ ] **Step 2: Run OpenAPI test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_openapi_describes_v50_single_stock_research_default -q
```

Expected: FAIL before YAML update.

- [ ] **Step 3: Update YAML descriptions**

Update:

- `info.description`
- `/stock/intraday-analysis.description`
- `/stock/intraday-analysis.responses.200.description`
- `/candidates/actionable.description` if present
- `/candidates/verify.description`

Make clear:

- `getStockIntradayAnalysis` is the default single-stock research action.
- It returns V5 fields including `time_horizon_probability`, `risk_reward_profile`, `fundamental_research`, `valuation_research`, `sector_position_research`, and `final_research_score`.
- Candidate endpoints are explicit-only, not default.

- [ ] **Step 4: Validate YAML and OpenAPI tests**

Run:

```bash
python3 - <<'PY'
import yaml
data = yaml.safe_load(open("chatgpt_action_openapi.yaml", encoding="utf-8"))
print(data["openapi"], len(data["paths"]))
PY
python3 -m pytest tests/test_market_data_service.py::test_openapi_describes_v50_single_stock_research_default -q
```

Expected: YAML parses and test passes.

---

## Task 6: Full Regression

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py tests/test_portfolio.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Check prompt length**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
text = Path("GPT_STOCK_TRADING_ASSISTANT_V4_2.md").read_text(encoding="utf-8")
print(len(text))
PY
```

Expected: character count is under 7000.

- [ ] **Step 3: Commit implementation**

Run:

```bash
git status --short
git add src/a_share_research/market_data_service.py tests/test_market_data_service.py GPT_STOCK_TRADING_ASSISTANT_V4_2.md chatgpt_action_openapi.yaml
git commit -m "Add V5 single stock deep research workflow"
```

Expected: commit succeeds.

---

## Self-Review

Spec coverage:

- V5 single-stock default workflow: Task 4 and Task 5.
- V5 research fields: Task 1 and Task 2.
- Horizon-based probability: Task 1 and Task 2.
- Sector requirement: Task 1, Task 2, Task 3, Task 4.
- Technical, fundamental, valuation, risk-reward, event risks: Task 1 and Task 2.
- Data-quality degradation: Task 3 and Task 4.
- Candidate tools explicit-only: Task 4 and Task 5.
- Regression and prompt length: Task 6.

Placeholder scan: no placeholder tasks; every task includes concrete files and commands.

Type consistency: V5 field names match the design document and test assertions.
