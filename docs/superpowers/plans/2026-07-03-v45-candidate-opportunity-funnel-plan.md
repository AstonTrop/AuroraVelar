# V4.5 Candidate Opportunity Funnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build V4.5 candidate recommendation as an opportunity funnel that selects stocks by market, sector, role, entry setup, risk/reward, account constraints, and anti-repeat evidence.

**Architecture:** Extend `MarketDataService.actionable_candidates()` rather than creating a second endpoint first, so the existing GPT Action remains usable. Add focused helper methods for market gate, sector gate, entry setup, stock role, account gate, and final recommendation tier; keep the returned `buy_now/strong_buy/trial_buy/conditional_buy` compatibility fields while adding V4.5 `opportunity_pools` and explanatory fields.

**Tech Stack:** Python, pandas, FastAPI/OpenAPI YAML, pytest. Follow the existing `StaticMarketDataProvider` test pattern and keep changes localized to `src/a_share_research/market_data_service.py`, `tests/test_market_data_service.py`, `chatgpt_action_openapi.yaml`, and `GPT_STOCK_TRADING_ASSISTANT_V4_2.md`.

---

## File Structure

- Modify `src/a_share_research/market_data_service.py`
  - Add V4.5 helper methods near existing candidate helper methods:
    - `_candidate_market_gate`
    - `_candidate_sector_gate`
    - `_candidate_stock_role`
    - `_candidate_entry_setup`
    - `_candidate_account_gate`
    - `_candidate_opportunity_source`
    - `_candidate_recommendation_tier`
  - Extend `actionable_candidates()` output with V4.5 fields while preserving current response keys.
- Modify `tests/test_market_data_service.py`
  - Add focused tests for strong sector/role recommendation, board missing downgrade, duplicate cooling, high-gain rejection, below-VWAP downgrade, and account gate downgrade.
- Modify `chatgpt_action_openapi.yaml`
  - Document V4.5 response semantics and new query params if needed.
- Modify `GPT_STOCK_TRADING_ASSISTANT_V4_2.md`
  - Update the GPT instruction to V4.5 opportunity funnel wording under 7000 characters.

---

### Task 1: Add V4.5 Candidate Helper Tests

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/tests/test_market_data_service.py`

- [ ] **Step 1: Write failing tests for V4.5 recommendation gating**

Add these tests after `test_actionable_candidates_separates_strong_buy_from_loose_buy_now`:

```python
def test_v45_strong_candidate_requires_sector_role_entry_and_account_gate() -> None:
    class V45Service(MarketDataService):
        def market_snapshot(self) -> dict:
            return {"freshness": "live", "data": {"risk_mode": "attack", "breadth": {"up_count": 3600, "down_count": 1400}}}

        def hot_boards(self, limit: int = 20) -> dict:
            return {
                "freshness": "live",
                "data": {
                    "boards": [
                        {"name": "汽车零部件", "change_pct": 3.5, "rank": 3, "up_ratio": 0.72, "leader_name": "强结构A", "leader_change_pct": 5.2}
                    ]
                },
            }

        def technical(self, code: str, report_date: date | None = None) -> dict:
            return {
                "freshness": "live",
                "data": {
                    "code": code,
                    "technical_score": 72.0,
                    "buy_point": 9.95,
                    "sell_point": 10.9,
                    "stop_loss_point": 9.7,
                    "technical_point_sources": "测试买点来自MA20回踩确认",
                },
            }

        def intraday_1m(self, code: str, limit: int = 0) -> dict:
            return {
                "freshness": "live",
                "data": {
                    "rows": [
                        {"time": "10:28", "close": 9.96, "avg_price": 9.94, "volume": 100000},
                        {"time": "10:29", "close": 10.00, "avg_price": 9.95, "volume": 140000},
                    ]
                },
            }

    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame(
            [
                {"代码": "000001", "名称": "强结构A", "最新价": 10.0, "涨跌幅": 2.0, "换手率": 4.0, "量比": 1.3, "行业": "汽车零部件"},
            ]
        ),
        bidasks={
            "000001": pd.DataFrame(
                [
                    {"item": "最新", "value": 10.0},
                    {"item": "涨幅", "value": 2.0},
                    {"item": "sell_1", "value": 10.01},
                    {"item": "buy_1", "value": 10.0},
                ]
            )
        },
    )

    out = V45Service(provider=provider).actionable_candidates(cash=6000.0, price_limit=30.0, limit=5)
    data = out["data"]
    item = data["strong_buy"][0]

    assert item["code"] == "000001"
    assert item["recommendation_tier"] == "强推荐可买"
    assert item["sector_gate"]["status"] == "pass"
    assert item["stock_role"] in {"leader", "trend_core", "low_position_repair", "turnaround"}
    assert item["entry_setup"] in {"vwap_reclaim", "vwap_pullback_hold", "trend_continuation", "breakout_confirmed"}
    assert item["account_gate"]["decision"] == "pass"
    assert data["opportunity_pools"]["mainline_core"][0]["code"] == "000001"


def test_v45_missing_sector_data_downgrades_candidate_to_conditional() -> None:
    class MissingSectorService(MarketDataService):
        def market_snapshot(self) -> dict:
            return {"freshness": "live", "data": {"risk_mode": "attack"}}

        def hot_boards(self, limit: int = 20) -> dict:
            return {"freshness": "unavailable", "data": {"error": "board unavailable"}}

        def technical(self, code: str, report_date: date | None = None) -> dict:
            return {"freshness": "live", "data": {"technical_score": 75.0, "buy_point": 9.95, "sell_point": 10.9, "stop_loss_point": 9.7}}

        def intraday_1m(self, code: str, limit: int = 0) -> dict:
            return {"freshness": "live", "data": {"rows": [{"time": "10:29", "close": 10.0, "avg_price": 9.95, "volume": 100000}]}}

    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame([{"代码": "000001", "名称": "强结构A", "最新价": 10.0, "涨跌幅": 2.0, "换手率": 4.0, "量比": 1.3}]),
        bidasks={"000001": pd.DataFrame([{"item": "最新", "value": 10.0}, {"item": "涨幅", "value": 2.0}, {"item": "sell_1", "value": 10.01}, {"item": "buy_1", "value": 10.0}])},
    )

    out = MissingSectorService(provider=provider).actionable_candidates(cash=6000.0, price_limit=30.0, limit=5)
    data = out["data"]

    assert data["strong_buy"] == []
    assert data["conditional_buy"][0]["code"] == "000001"
    assert data["conditional_buy"][0]["sector_gate"]["status"] == "unknown"
    assert "板块数据不可确认" in data["conditional_buy"][0]["downgrade_reasons"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_v45_strong_candidate_requires_sector_role_entry_and_account_gate tests/test_market_data_service.py::test_v45_missing_sector_data_downgrades_candidate_to_conditional -q
```

Expected: FAIL because V4.5 fields like `recommendation_tier`, `sector_gate`, and `opportunity_pools` do not exist yet.

---

### Task 2: Implement V4.5 Helper Methods and Candidate Fields

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/src/a_share_research/market_data_service.py`

- [ ] **Step 1: Add helper methods before `actionable_candidates()`**

Insert these methods after `_candidate_trade_quality()`:

```python
    def _candidate_market_gate(self) -> dict[str, Any]:
        snapshot = self.market_snapshot()
        freshness = snapshot.get("freshness")
        data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
        risk_mode = str(data.get("risk_mode") or data.get("market_condition") or "unknown").lower()
        breadth = data.get("breadth") if isinstance(data.get("breadth"), dict) else {}
        up_count = _nullable_float(breadth.get("up_count"))
        down_count = _nullable_float(breadth.get("down_count"))
        status = "unknown"
        reason = "市场数据不可确认"
        if freshness in {"live", "partial_live", "delayed", "after_close"}:
            if risk_mode in {"attack", "strong", "强势"}:
                status = "pass"
                reason = "市场状态允许进攻"
            elif risk_mode in {"defense", "weak", "弱势", "极弱"}:
                status = "weak_pass"
                reason = "市场偏弱，只允许小仓或观察"
            elif up_count is not None and down_count is not None and up_count > down_count:
                status = "pass"
                reason = "上涨家数多于下跌家数，市场允许试错"
            else:
                status = "weak_pass"
                reason = "市场方向不够明确，只允许试错"
        return {
            "status": status,
            "freshness": freshness,
            "risk_mode": risk_mode,
            "up_count": up_count,
            "down_count": down_count,
            "reason": reason,
        }

    def _candidate_sector_gate(self, *, row: pd.Series, boards: dict[str, Any]) -> dict[str, Any]:
        name = str(row.get("name") or row.get("名称") or "")
        industry = str(row.get("industry") or row.get("行业") or "")
        board_rows = boards.get("boards") if isinstance(boards.get("boards"), list) else []
        if not board_rows:
            return {
                "status": "unknown",
                "industry": industry or None,
                "concepts": [],
                "board_rank": None,
                "board_change_pct": None,
                "board_up_ratio": None,
                "leader_strength": "unknown",
                "stock_vs_board": "unknown",
                "reason": "板块数据不可确认",
            }
        matched = None
        for index, board in enumerate(board_rows, start=1):
            board_name = str(board.get("name") or board.get("板块") or "")
            if industry and (industry in board_name or board_name in industry):
                matched = (index, board)
                break
            leader_name = str(board.get("leader_name") or board.get("领涨股") or "")
            if leader_name and leader_name in name:
                matched = (index, board)
                break
        if matched is None:
            return {
                "status": "unknown",
                "industry": industry or None,
                "concepts": [],
                "board_rank": None,
                "board_change_pct": None,
                "board_up_ratio": None,
                "leader_strength": "unknown",
                "stock_vs_board": "unknown",
                "reason": "未匹配到所属强弱板块",
            }
        rank, board = matched
        board_change = _nullable_float(board.get("change_pct") or board.get("涨跌幅"))
        board_up_ratio = _nullable_float(board.get("up_ratio") or board.get("上涨比例"))
        stock_change = _nullable_float(row.get("day_change_pct") or row.get("涨跌幅"))
        leader_change = _nullable_float(board.get("leader_change_pct") or board.get("领涨股涨跌幅"))
        stock_vs_board = "unknown"
        if stock_change is not None and board_change is not None:
            if stock_change >= board_change + 1:
                stock_vs_board = "stronger"
            elif stock_change < board_change - 1:
                stock_vs_board = "weaker"
            else:
                stock_vs_board = "in_line"
        leader_strength = "unknown"
        if leader_change is not None:
            leader_strength = "strong" if leader_change >= 5 else "mixed" if leader_change >= 2 else "weak"
        status = "unknown"
        reason = "板块强弱需要继续确认"
        if board_change is not None and board_change >= 2 and stock_vs_board in {"stronger", "in_line"}:
            status = "pass"
            reason = "板块强且个股没有掉队"
        elif board_change is not None and board_change >= 1 and stock_vs_board == "stronger":
            status = "weak_pass"
            reason = "板块一般但个股强于板块"
        elif stock_vs_board == "weaker":
            status = "fail"
            reason = "个股弱于所属板块"
        return {
            "status": status,
            "industry": industry or board.get("name"),
            "concepts": [],
            "board_rank": rank,
            "board_change_pct": board_change,
            "board_up_ratio": board_up_ratio,
            "leader_strength": leader_strength,
            "stock_vs_board": stock_vs_board,
            "reason": reason,
        }

    def _candidate_stock_role(self, *, row: pd.Series, sector_gate: dict[str, Any]) -> str:
        change_pct = _nullable_float(row.get("day_change_pct") or row.get("涨跌幅"))
        turnover = _nullable_float(row.get("turnover_rate") or row.get("换手率"))
        volume_ratio = _nullable_float(row.get("volume_ratio") or row.get("量比"))
        if sector_gate.get("status") == "unknown":
            return "unknown"
        if change_pct is not None and change_pct >= 5 and sector_gate.get("stock_vs_board") in {"stronger", "in_line"}:
            return "leader"
        if turnover is not None and turnover >= 3 and volume_ratio is not None and volume_ratio >= 1:
            return "trend_core"
        if change_pct is not None and 0 <= change_pct <= 3 and sector_gate.get("status") in {"pass", "weak_pass"}:
            return "low_position_repair"
        if change_pct is not None and -1 <= change_pct <= 2 and sector_gate.get("stock_vs_board") == "stronger":
            return "turnaround"
        return "follower"

    def _candidate_entry_setup(self, *, latest_price: float | None, buy_point: float | None, intraday: dict[str, Any]) -> dict[str, Any]:
        rows = intraday.get("rows") if isinstance(intraday.get("rows"), list) else []
        last = rows[-1] if rows else {}
        avg_price = _nullable_float(last.get("avg_price") or last.get("vwap"))
        close = _nullable_float(last.get("close") or last.get("price")) or latest_price
        setup = "not_triggered"
        reason = "分时数据不足，买点未确认"
        if close is not None and avg_price is not None:
            if close >= avg_price and latest_price is not None and buy_point is not None and abs(latest_price - buy_point) / latest_price <= 0.015:
                setup = "vwap_pullback_hold"
                reason = "现价在买点附近且站在分时均价上方"
            elif close >= avg_price:
                setup = "vwap_reclaim"
                reason = "价格站回分时均价"
            else:
                reason = "价格仍低于分时均价"
        return {"type": setup, "avg_price": avg_price, "last_price": close, "reason": reason}

    def _candidate_account_gate(self, *, cash: float, min_lot_cost: float | None, latest_price: float | None) -> dict[str, Any]:
        can_buy = min_lot_cost is not None and min_lot_cost <= cash
        cash_after = round(cash - min_lot_cost, 2) if can_buy and min_lot_cost is not None else None
        decision = "pass" if can_buy else "fail"
        reason = "现金可以买一手" if can_buy else "现金不足买一手"
        return {
            "can_buy_lot": can_buy,
            "min_lot_cost": min_lot_cost,
            "cash_after_buy": cash_after,
            "stock_position_after_buy_pct": None,
            "same_sector_exposure_after_buy_pct": None,
            "t_plus_1_risk": "medium" if can_buy else "high",
            "decision": decision,
            "reason": reason,
        }

    def _candidate_opportunity_source(self, *, sector_gate: dict[str, Any], stock_role: str, entry_setup: str, duplicate: bool) -> str:
        if duplicate:
            return "cooldown_watch"
        if stock_role in {"leader", "trend_core"} and sector_gate.get("status") == "pass":
            return "mainline_core"
        if entry_setup == "vwap_pullback_hold":
            return "pullback_repair"
        if entry_setup == "vwap_reclaim":
            return "turnaround"
        return "conditional_watch"

    def _candidate_recommendation_tier(
        self,
        *,
        market_gate: dict[str, Any],
        sector_gate: dict[str, Any],
        stock_role: str,
        entry_setup: str,
        account_gate: dict[str, Any],
        trade_quality_tier: str,
        duplicate: bool,
    ) -> tuple[str, list[str], list[str]]:
        reasons: list[str] = []
        downgrades: list[str] = []
        if market_gate.get("status") == "unknown":
            downgrades.append("市场数据不可确认")
        if sector_gate.get("status") == "unknown":
            downgrades.append("板块数据不可确认")
        if sector_gate.get("status") == "fail":
            downgrades.append("板块门槛失败")
        if stock_role in {"follower", "unknown"}:
            downgrades.append("个股角色不是核心")
        if entry_setup == "not_triggered":
            downgrades.append("买点未触发")
        if account_gate.get("decision") == "fail":
            downgrades.append(account_gate.get("reason") or "账户门槛失败")
        if duplicate:
            downgrades.append("近期已推荐且没有新证据")
        if trade_quality_tier == "强推荐可买":
            reasons.append("基础技术质量达强推荐门槛")
        if sector_gate.get("status") == "pass":
            reasons.append("板块门槛通过")
        if entry_setup != "not_triggered":
            reasons.append("买点正在发生")
        if account_gate.get("decision") == "pass":
            reasons.append("账户可以买一手")

        if not downgrades and trade_quality_tier == "强推荐可买" and market_gate.get("status") == "pass":
            return "强推荐可买", reasons, downgrades
        if account_gate.get("decision") == "pass" and sector_gate.get("status") in {"pass", "weak_pass"} and entry_setup != "not_triggered" and stock_role not in {"follower", "unknown"} and not duplicate:
            return "小仓试错", reasons, downgrades
        if account_gate.get("decision") == "fail" or sector_gate.get("status") == "fail":
            return "剔除", reasons, downgrades
        return "条件观察", reasons, downgrades
```

- [ ] **Step 2: Extend `actionable_candidates()` to call V4.5 helpers**

Inside `actionable_candidates()`, after `buckets` and `rejected` are initialized, add:

```python
        market_gate = self._candidate_market_gate()
        boards_result = self.hot_boards(limit=20)
        boards_data = boards_result.get("data") if boards_result.get("freshness") != "unavailable" and isinstance(boards_result.get("data"), dict) else {}
        opportunity_pools: dict[str, list[dict[str, Any]]] = {
            "mainline_core": [],
            "pullback_repair": [],
            "turnaround": [],
            "sold_reclaim": [],
            "cooldown_watch": [],
            "conditional_watch": [],
        }
```

When `work.empty` response is returned, include:

```python
                    "market_gate": market_gate,
                    "sector_candidates": [],
                    "opportunity_pools": opportunity_pools,
```

Inside the candidate loop, after `item.update(self._candidate_trade_quality(...))`, add:

```python
            intraday_result = self.intraday_1m(code, limit=30)
            intraday_data = intraday_result.get("data") if intraday_result.get("freshness") != "unavailable" and isinstance(intraday_result.get("data"), dict) else {}
            sector_gate = self._candidate_sector_gate(row=row, boards=boards_data)
            stock_role = self._candidate_stock_role(row=row, sector_gate=sector_gate)
            entry = self._candidate_entry_setup(latest_price=latest_price, buy_point=buy_point, intraday=intraday_data)
            account_gate = self._candidate_account_gate(cash=cash, min_lot_cost=action.get("min_lot_cost"), latest_price=latest_price)
            duplicate = code in previous_codes
            opportunity_source = self._candidate_opportunity_source(
                sector_gate=sector_gate,
                stock_role=stock_role,
                entry_setup=entry["type"],
                duplicate=duplicate,
            )
            recommendation_tier, recommendation_reasons, v45_downgrades = self._candidate_recommendation_tier(
                market_gate=market_gate,
                sector_gate=sector_gate,
                stock_role=stock_role,
                entry_setup=entry["type"],
                account_gate=account_gate,
                trade_quality_tier=str(item.get("trade_quality_tier") or ""),
                duplicate=duplicate,
            )
            item.update(
                {
                    "market_gate": market_gate,
                    "sector_gate": sector_gate,
                    "stock_role": stock_role,
                    "entry_setup": entry["type"],
                    "entry_setup_detail": entry,
                    "account_gate": account_gate,
                    "opportunity_source": opportunity_source,
                    "recommendation_tier": recommendation_tier,
                    "recommendation_reasons": recommendation_reasons,
                    "downgrade_reasons": list(dict.fromkeys([*item.get("downgrade_reasons", []), *v45_downgrades])),
                    "anti_repeat_status": "近期已推荐，冷却观察" if duplicate else "新候选或有待验证候选",
                    "new_evidence": recommendation_reasons if duplicate and recommendation_tier != "条件观察" else [],
                }
            )
```

After duplicate handling, keep duplicate bucket behavior but also set:

```python
                item["opportunity_source"] = "cooldown_watch"
                item["recommendation_tier"] = "条件观察"
                item["anti_repeat_status"] = "近期已推荐，冷却观察"
```

Before adding to buckets, append to the opportunity pool:

```python
            opportunity_pools.setdefault(str(item.get("opportunity_source") or "conditional_watch"), []).append(item)
```

At response construction, change tier derivation to use `recommendation_tier`:

```python
        strong_buy = [item for item in buy_now if item.get("recommendation_tier") == "强推荐可买"]
        trial_buy = [item for item in buy_now if item.get("recommendation_tier") == "小仓试错"]
        conditional_buy = [
            item
            for item in [*buy_now, *buckets["等回踩"], *buckets["等突破"], *buckets["只观察"]]
            if item.get("recommendation_tier") == "条件观察"
        ][:limit]
```

Include in response data:

```python
                "market_gate": market_gate,
                "sector_candidates": boards_data.get("boards", [])[:10] if isinstance(boards_data.get("boards"), list) else [],
                "opportunity_pools": {key: value[:limit] for key, value in opportunity_pools.items()},
```

Update `selection_policy` to:

```python
                "selection_policy": "V4.5机会漏斗：先看市场和板块，再看个股角色、分时买点、风险收益比、账户约束和重复冷却；buy_now仍只代表可执行，recommendation_tier才决定是否推荐。",
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_v45_strong_candidate_requires_sector_role_entry_and_account_gate tests/test_market_data_service.py::test_v45_missing_sector_data_downgrades_candidate_to_conditional -q
```

Expected: PASS.

- [ ] **Step 4: Run existing candidate tests**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py -q
```

Expected: PASS. If older tests expect `strong_buy` from `trade_quality_tier`, update assertions to expect `recommendation_tier` while keeping `trade_quality_tier` present.

- [ ] **Step 5: Commit**

```bash
git add src/a_share_research/market_data_service.py tests/test_market_data_service.py
git commit -m "Add V4.5 candidate opportunity funnel"
```

---

### Task 3: Add Anti-Repeat and High-Risk Candidate Tests

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/tests/test_market_data_service.py`
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/src/a_share_research/market_data_service.py`

- [ ] **Step 1: Add failing tests for repeat cooling and below-VWAP downgrade**

Add:

```python
def test_v45_recent_candidate_goes_to_cooldown_watch_even_if_technically_buyable() -> None:
    class RepeatService(MarketDataService):
        def market_snapshot(self) -> dict:
            return {"freshness": "live", "data": {"risk_mode": "attack"}}

        def hot_boards(self, limit: int = 20) -> dict:
            return {"freshness": "live", "data": {"boards": [{"name": "汽车零部件", "change_pct": 3.5, "rank": 2, "up_ratio": 0.7}]}}

        def technical(self, code: str, report_date: date | None = None) -> dict:
            return {"freshness": "live", "data": {"technical_score": 72.0, "buy_point": 9.95, "sell_point": 10.9, "stop_loss_point": 9.7}}

        def intraday_1m(self, code: str, limit: int = 0) -> dict:
            return {"freshness": "live", "data": {"rows": [{"time": "10:29", "close": 10.0, "avg_price": 9.95, "volume": 100000}]}}

    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame([{"代码": "000001", "名称": "强结构A", "最新价": 10.0, "涨跌幅": 2.0, "换手率": 4.0, "量比": 1.3, "行业": "汽车零部件"}]),
        bidasks={"000001": pd.DataFrame([{"item": "最新", "value": 10.0}, {"item": "涨幅", "value": 2.0}, {"item": "sell_1", "value": 10.01}, {"item": "buy_1", "value": 10.0}])},
    )

    out = RepeatService(provider=provider).actionable_candidates(cash=6000.0, price_limit=30.0, limit=5, recent_codes="000001")

    assert out["data"]["strong_buy"] == []
    assert out["data"]["opportunity_pools"]["cooldown_watch"][0]["code"] == "000001"
    assert out["data"]["opportunity_pools"]["cooldown_watch"][0]["recommendation_tier"] == "条件观察"


def test_v45_candidate_below_intraday_average_cannot_be_strong_buy() -> None:
    class BelowVwapService(MarketDataService):
        def market_snapshot(self) -> dict:
            return {"freshness": "live", "data": {"risk_mode": "attack"}}

        def hot_boards(self, limit: int = 20) -> dict:
            return {"freshness": "live", "data": {"boards": [{"name": "汽车零部件", "change_pct": 3.5, "rank": 2, "up_ratio": 0.7}]}}

        def technical(self, code: str, report_date: date | None = None) -> dict:
            return {"freshness": "live", "data": {"technical_score": 72.0, "buy_point": 9.95, "sell_point": 10.9, "stop_loss_point": 9.7}}

        def intraday_1m(self, code: str, limit: int = 0) -> dict:
            return {"freshness": "live", "data": {"rows": [{"time": "10:29", "close": 9.9, "avg_price": 10.05, "volume": 100000}]}}

    provider = StaticMarketDataProvider(
        quotes=pd.DataFrame([{"代码": "000001", "名称": "强结构A", "最新价": 10.0, "涨跌幅": 2.0, "换手率": 4.0, "量比": 1.3, "行业": "汽车零部件"}]),
        bidasks={"000001": pd.DataFrame([{"item": "最新", "value": 10.0}, {"item": "涨幅", "value": 2.0}, {"item": "sell_1", "value": 10.01}, {"item": "buy_1", "value": 10.0}])},
    )

    out = BelowVwapService(provider=provider).actionable_candidates(cash=6000.0, price_limit=30.0, limit=5)
    item = out["data"]["conditional_buy"][0]

    assert out["data"]["strong_buy"] == []
    assert item["entry_setup"] == "not_triggered"
    assert "买点未触发" in item["downgrade_reasons"]
```

- [ ] **Step 2: Run new tests and verify failure or pass**

Run:

```bash
python3 -m pytest tests/test_market_data_service.py::test_v45_recent_candidate_goes_to_cooldown_watch_even_if_technically_buyable tests/test_market_data_service.py::test_v45_candidate_below_intraday_average_cannot_be_strong_buy -q
```

Expected: PASS if Task 2 fully handled these conditions; otherwise FAIL and continue.

- [ ] **Step 3: Fix helper logic if needed**

If below-VWAP still gets `小仓试错`, update `_candidate_recommendation_tier()` so `entry_setup == "not_triggered"` prevents `小仓试错` and `强推荐可买`.

If duplicate does not appear in `cooldown_watch`, ensure duplicate handling appends to `opportunity_pools["cooldown_watch"]` before bucket reassignment.

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_market_data_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/a_share_research/market_data_service.py tests/test_market_data_service.py
git commit -m "Tighten V4.5 candidate cooling and entry gates"
```

---

### Task 4: Update OpenAPI Action Schema

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/chatgpt_action_openapi.yaml`

- [ ] **Step 1: Update `getActionableCandidates` description**

Find operation `getActionableCandidates` and replace its response description with:

```yaml
          description: >
            返回 V4.5 机会漏斗候选。buy_now 只表示盘口和现金可执行；
            recommendation_tier 才决定强推荐可买/小仓试错/条件观察/剔除。
            GPT 必须优先使用 market_gate、sector_gate、stock_role、entry_setup、
            account_gate、opportunity_source、risk_reward_ratio、downgrade_reasons，
            不得只按技术分或买点距离推荐。
```

- [ ] **Step 2: Add response schema notes if schema is inline**

In the same response schema, ensure the description mentions these top-level fields:

```text
market_gate, sector_candidates, opportunity_pools, strong_buy, trial_buy, conditional_buy, rejected
```

If the schema is currently generic, keep it generic but expand the operation description. Do not overfit a huge OpenAPI schema if it risks parse errors.

- [ ] **Step 3: Validate YAML and OpenAPI parse**

Run:

```bash
python3 - <<'PY'
import yaml
data = yaml.safe_load(open("chatgpt_action_openapi.yaml", encoding="utf-8"))
assert data["openapi"].startswith("3.")
assert "/candidates/actionable" in data["paths"]
print(data["openapi"], len(data["paths"]))
PY
```

Expected: Prints OpenAPI version and path count without error.

- [ ] **Step 4: Commit**

```bash
git add chatgpt_action_openapi.yaml
git commit -m "Document V4.5 candidate action response"
```

---

### Task 5: Update GPT Instructions to V4.5

**Files:**
- Modify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/GPT_STOCK_TRADING_ASSISTANT_V4_2.md`

- [ ] **Step 1: Update title and candidate section**

Change the title to:

```markdown
# A股盘中交易参谋 GPT V4.5 精简指令
```

Replace section `## 十、候选股规则` with:

```markdown
## 十、候选股规则

选股采用 V4.5 机会漏斗，不再按“低价、技术分、离买点近”直接推荐。

顺序固定：市场能否进攻 -> 板块是否强 -> 个股在板块里的角色 -> 买点是否正在发生 -> 分时均价/量能/盘口是否确认 -> 风险收益比 -> 账户/T+1 是否可承受。

必须优先使用 `market_gate`、`sector_gate`、`stock_role`、`entry_setup`、`account_gate`、`opportunity_source`、`recommendation_tier`、`risk_reward_ratio`、`downgrade_reasons`。

`buy_now` 只表示可执行，不等于推荐。最终只按 `recommendation_tier` 表达：
- `强推荐可买`：板块强、角色清楚、买点触发、站上或守住分时均价、风险收益比合格、买后账户不被动。
- `小仓试错`：方向基本对，但板块、盘口、仓位或风险收益比有一个短板。
- `条件观察`：买点未触发、板块不完整、重复候选无新证据、需要等回踩或突破。
- `剔除`：板块失败、后排跟风、涨幅过高、低于分时均价未修复、风险收益比差、现金不足或 T+1 风险过高。

候选必须说明：为什么进入候选、所属板块、个股角色、当前买点是否触发、三日结构、当日分时、风险收益比、买入后账户变化、反证条件、最终等级。

旧候选和重复股票默认进冷却观察池；只有板块转强、站回均价、放量突破、盘口改善、公告催化等新证据，才可重新升级。
```

- [ ] **Step 2: Update character count**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
p=Path("GPT_STOCK_TRADING_ASSISTANT_V4_2.md")
text=p.read_text(encoding="utf-8")
print(len(text))
assert len(text) < 7000
PY
```

Expected: Prints a number under 7000.

- [ ] **Step 3: Commit**

```bash
git add GPT_STOCK_TRADING_ASSISTANT_V4_2.md
git commit -m "Update GPT instructions for V4.5 opportunity funnel"
```

---

### Task 6: Final Verification

**Files:**
- Verify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/tests/test_market_data_service.py`
- Verify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/chatgpt_action_openapi.yaml`
- Verify: `/Users/auroravelar/公路科学技术研究院/CodeX/A股研究/GPT_STOCK_TRADING_ASSISTANT_V4_2.md`

- [ ] **Step 1: Run full tests**

```bash
python3 -m pytest tests/test_market_data_service.py tests/test_portfolio.py -q
```

Expected: All tests pass.

- [ ] **Step 2: Validate OpenAPI**

```bash
python3 - <<'PY'
import yaml
data = yaml.safe_load(open("chatgpt_action_openapi.yaml", encoding="utf-8"))
assert data["openapi"].startswith("3.")
assert "getActionableCandidates" in str(data)
print("openapi", data["openapi"], "paths", len(data["paths"]))
PY
```

Expected: Prints OpenAPI version and path count.

- [ ] **Step 3: Confirm GPT prompt length**

```bash
python3 - <<'PY'
from pathlib import Path
text=Path("GPT_STOCK_TRADING_ASSISTANT_V4_2.md").read_text(encoding="utf-8")
print("chars", len(text))
assert len(text) < 7000
PY
```

Expected: `chars` under 7000.

- [ ] **Step 4: Check git status**

```bash
git status --short
```

Expected: no output.

- [ ] **Step 5: Provide handoff**

Tell the user:

```text
V4.5 机会漏斗已实现并验证。需要你用 GitHub Desktop Push origin，然后在 Render 手动部署最新提交；部署后把 chatgpt_action_openapi.yaml 复制到 GPT Actions，再把 GPT_STOCK_TRADING_ASSISTANT_V4_2.md 的内容复制到 GPT 指令。
```
