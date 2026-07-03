# V4.5 Candidate Opportunity Funnel Design

## 背景

当前 V4.4 已经把 `buy_now` 和真正推荐分开，并增加了 `strong_buy`、`trial_buy`、`conditional_buy`。这解决了“可执行不等于推荐”的一部分问题，但选股仍然容易出现三个缺陷：

- 候选来源仍偏粗糙，容易从低价主板快照里挑出“离买点近”的票，而不是从当日强板块和资金方向中找机会。
- 推荐容易重复，旧候选如果没有新的实时证据，不应该再次包装成新机会。
- 买点判断偏机械，常把技术点位、低价、一手成本、涨幅不高等单项条件误当成可以买。

V4.5 的目标是把选股从“候选排序”升级为“机会漏斗”：先判断市场和板块，再判断个股角色、分时确认、风险收益比和账户约束，最后才给推荐等级。

## 目标

V4.5 必须做到：

- 扩大选股视野，但收紧最终推荐。
- 优先推荐当下可以买、买点正在发生、买后账户仍可控的票。
- 减少重复推荐，旧候选只有出现新的实时证据才可升级。
- 避免推荐已经大幅上涨、接近日内高点、风险收益比差、板块不清或后排跟风的票。
- 持仓股和新候选必须统一放进市场、板块、账户约束中判断。
- GPT 回复必须解释“为什么它进入候选、为什么现在能/不能买、错了怎么办”，而不是只给结论。

## 非目标

V4.5 不追求：

- Level-2 十档盘口、委托队列、逐笔委托。
- 自动下单或自动交易。
- 用单一模型预测上涨概率。
- 把所有技术分析规则写死。规则负责兜底和降级，GPT 仍需结合实时盘面解释。

## 推荐总流程

选股必须按以下顺序执行：

```text
市场状态
-> 强弱板块
-> 候选来源池
-> 个股板块角色
-> 三日走势和当日分时
-> 买点是否正在发生
-> 盘口和量能确认
-> 风险收益比
-> 账户/T+1/仓位约束
-> 推荐等级
```

不能先有股票再找理由。若市场或板块数据不可用，所有买入结论自动降级。

## 候选来源池

V4.5 新增候选来源类型 `opportunity_source`：

### 主线核心池

来源于当日强板块或资金流入方向中的核心票。优先寻找：

- 板块龙头。
- 中军趋势股。
- 板块成交额核心。
- 站稳均价且不是日内高位乱冲的票。

适用结论：可进入 `强推荐可买` 或 `小仓试错`。

### 低吸修复池

来源于强板块里回踩不破的票。必须满足：

- 所属板块仍在前排或资金未明显流出。
- 个股接近分时均价、三日支撑、MA20 或平台支撑。
- 回踩缩量或跌不动。
- 再次拉起时有量能确认。

适用结论：多数为 `小仓试错`，只有板块和盘口都强才可升为 `强推荐可买`。

### 弱转强池

来源于早盘弱、盘中重新转强的票。必须满足：

- 重新站回分时均价。
- 重新站回关键压力或成本密集区。
- 近 5/15/30 分钟方向转强。
- 板块同步修复或个股明显强于板块。

适用结论：多数为 `小仓试错` 或 `条件观察`。

### 卖出后二次转强池

来源于用户近期卖出、减仓、卖飞或曾经持有的票。不能因为卖过就排除，也不能因为后悔就追回。

只有出现以下新证据才可升级：

- 板块重新转强。
- 个股站回分时均价和关键压力。
- 放量突破前高或重要均线。
- 盘口卖压明显下降。
- 出现新的公告、业绩或题材催化。

适用结论：默认 `条件观察`，证据充分时可升为 `小仓试错`。

### 冷却观察池

来源于近期反复推荐过但未触发新证据的股票。

默认处理：

- 不进入 `strong_buy`。
- 不作为新推荐重复输出。
- 只在观察列表中说明“旧候选，暂无新证据”。

## 个股角色识别

每个候选都要输出 `stock_role`：

- `leader`：龙头，板块涨停或涨幅/成交/辨识度领先。
- `trend_core`：中军趋势股，成交额大、走势稳定、代表板块方向。
- `low_position_repair`：低位修复，位置不高但开始跟随板块。
- `turnaround`：弱转强，盘中从弱转强。
- `follower`：后排跟风。
- `unknown`：角色不可确认。

推荐限制：

- `leader`、`trend_core`、`low_position_repair`、`turnaround` 可以进入推荐漏斗。
- `follower` 默认不能强推荐。
- `unknown` 在板块数据不完整时最多 `条件观察`。

## 买点确认

候选不能只因为“离买点近”而推荐。必须输出 `entry_setup`：

- `vwap_reclaim`：重新站回分时均价。
- `vwap_pullback_hold`：回踩分时均价不破。
- `breakout_confirmed`：放量突破关键压力。
- `support_reversal`：支撑位企稳反转。
- `trend_continuation`：趋势股缩量回踩后继续上行。
- `not_triggered`：买点未触发。

硬性要求：

- 现价低于分时均价且没有重新站回时，不能强推荐。
- 买点距离现价超过 3% 时，不能写现在买。
- 日内涨幅过高、接近日内高点时，必须判断是否追高。
- 买点若来源不足，必须标记 `level_source_status=weak`，不得作为硬交易线。

## 风险收益比

每个候选必须计算：

- `entry_price_reference`
- `intraday_failure_line`
- `daily_hard_stop_line`
- `first_target`
- `second_target`
- `downside_pct`
- `upside_pct`
- `risk_reward_ratio`

推荐限制：

- `risk_reward_ratio < 1.2`：剔除或条件观察。
- `1.2 <= risk_reward_ratio < 1.5`：最多小仓试错。
- `risk_reward_ratio >= 1.5`：允许进入强推荐候选，但仍需通过板块、分时、账户约束。
- `downside_pct > 8%`：默认降级，除非是极小仓观察。

## 板块门槛

新增 `sector_gate`：

```json
{
  "status": "pass / weak_pass / fail / unknown",
  "industry": "",
  "concepts": [],
  "board_rank": null,
  "board_change_pct": null,
  "board_up_ratio": null,
  "leader_strength": "strong / mixed / weak / unknown",
  "stock_vs_board": "stronger / in_line / weaker / unknown",
  "reason": ""
}
```

门槛规则：

- `pass`：板块强，个股同步或强于板块。
- `weak_pass`：板块不是最强，但有资金或修复迹象，个股强于板块。
- `fail`：板块弱、个股弱于板块、后排跟风。
- `unknown`：板块数据缺失或映射失败。

推荐限制：

- `sector_gate=fail`：不能推荐买入。
- `sector_gate=unknown`：最多条件观察。
- `sector_gate=weak_pass`：最多小仓试错。

## 账户门槛

新增 `account_gate`：

```json
{
  "can_buy_lot": true,
  "min_lot_cost": 0,
  "cash_after_buy": 0,
  "stock_position_after_buy_pct": 0,
  "same_sector_exposure_after_buy_pct": 0,
  "t_plus_1_risk": "low / medium / high",
  "decision": "pass / weak_pass / fail",
  "reason": ""
}
```

规则：

- 现金不足买一手：`fail`。
- 买后仓位超过用户进攻上限且没有同步减弱仓：`weak_pass` 或 `fail`。
- 买后同板块暴露过重：降级。
- 当天已买入多只锁仓股：降级。
- 尾盘买入：提高 T+1 风险等级。

用户偏进攻，正常市场下可接受 65%-75% 仓位；弱势或板块不清时应降到 50%-60% 或更低。

## 推荐等级

最终输出 `recommendation_tier`：

### 强推荐可买

必须同时满足：

- 市场不是防守盘。
- 板块门槛 `pass`。
- 个股角色是 `leader`、`trend_core`、`low_position_repair` 或强证据 `turnaround`。
- 买点已经触发，且现价仍在合理买区。
- 现价站上或回踩守住分时均价。
- 风险收益比 >= 1.5。
- 买后账户不被动。
- 不是近期重复推荐，或有明确新证据。

### 小仓试错

适用于：

- 方向正确但有一个短板。
- 板块 `weak_pass`。
- 风险收益比 1.2-1.5。
- 买后仓位略高但仍可控。
- 弱转强刚出现，仍需确认。

### 条件观察

适用于：

- 买点未触发。
- 需要等回踩、等突破、等站回均价。
- 板块数据 partial/unknown。
- 近期重复推荐但无新证据。
- 角色不清。

### 剔除

适用于：

- 非主板、ST、退市风险、新股异常波动。
- 现金不足。
- 涨停封板不可追。
- 涨幅过高且接近日内高点。
- 低于分时均价且没有修复。
- 板块失败。
- 风险收益比差。
- 后排跟风。

## GPT 输出要求

候选推荐必须按以下格式解释：

```text
为什么进入候选：
所属板块：
板块强弱和个股角色：
当前买点是否已经触发：
三日结构：
当日分时：
风险收益比：
买入后账户变化：
反证条件：
最终等级：
```

GPT 不允许只输出“可以买”。必须说明如果错了在哪里认错、如果对了看哪里、为什么不是追高、为什么不是重复推荐。

## 服务端接口建议

可以在现有 `getActionableCandidates` 上扩展，或新增 `getOpportunityCandidates`。

推荐新增返回字段：

```json
{
  "market_gate": {},
  "sector_candidates": [],
  "opportunity_pools": {
    "mainline_core": [],
    "pullback_repair": [],
    "turnaround": [],
    "sold_reclaim": [],
    "cooldown_watch": []
  },
  "strong_buy": [],
  "trial_buy": [],
  "conditional_buy": [],
  "rejected": [],
  "selection_policy": ""
}
```

每只股票至少包含：

```json
{
  "code": "",
  "name": "",
  "opportunity_source": "",
  "stock_role": "",
  "entry_setup": "",
  "sector_gate": {},
  "account_gate": {},
  "risk_reward_ratio": null,
  "downside_pct": null,
  "upside_pct": null,
  "recommendation_tier": "",
  "recommendation_reasons": [],
  "downgrade_reasons": [],
  "anti_repeat_status": "",
  "new_evidence": []
}
```

## 测试计划

新增测试覆盖：

- 强板块核心股、买点触发、风险收益比合格时进入 `strong_buy`。
- 板块数据缺失时，即使技术分高，也只能 `conditional_buy`。
- 近期重复推荐且无新证据时进入 `cooldown_watch`。
- 卖出后二次转强有新证据时进入 `sold_reclaim`，但默认最多小仓试错。
- 日内涨幅高且接近日内高点时剔除或观察，不能强推荐。
- 低于分时均价且未修复时不能推荐买入。
- 买后仓位超过进攻上限且未减弱仓时降级。
- XD/除权股没有额外确认时降级。
- 后排跟风股不能进入 `strong_buy`。

## 开放问题

- 板块核心股识别第一版可先用板块涨幅、板块排名、成交额、涨停数量、个股涨幅相对板块估算；后续再引入更细的角色模型。
- 卖出后二次转强需要依赖复盘账本或用户手动输入历史交易，第一版可用近期推荐/持仓变动近似。
- 如果接口耗时过长，V4.5 可以先限制深度核验数量：先广扫选出 30-50 只，再深挖 5-10 只。

## 验收标准

V4.5 完成后，一次午间或盘中选股回复应满足：

- 不把 `buy_now` 原样当推荐。
- 至少说明候选来自哪个机会池。
- 至少说明板块门槛和个股角色。
- 至少说明买点是否正在发生。
- 至少说明风险收益比和失败线来源。
- 至少说明买后仓位变化。
- 重复股票没有新证据不再反复推荐。
- 最终推荐少而精，可以没有强推荐。
