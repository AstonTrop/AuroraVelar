# A股云端行情服务部署说明

这个服务用于给 ChatGPT Plus 的 Custom GPT Actions 提供实时 A 股行情接口。

## 本地运行

```bash
pip install -r requirements.txt
python scripts/run_market_data_server.py
```

访问：

```bash
curl http://127.0.0.1:8000/health
curl "http://127.0.0.1:8000/stock/quotes?codes=000725,600667,600879"
```

## Render 部署

1. 把本项目推到 GitHub。
2. 登录 Render。
3. New -> Blueprint，选择本仓库。
4. Render 会读取 `render.yaml`。
5. 部署完成后获得 HTTPS 地址，例如：

```text
https://a-share-market-data-service.onrender.com
```

## 接入 Custom GPT Actions

1. 打开 ChatGPT -> Explore GPTs -> Create。
2. 进入 Configure -> Actions。
3. 点击 Create new action。
4. Authentication 选择 None。
5. Schema 粘贴 `chatgpt_action_openapi.yaml` 的完整内容。
6. 点击 Test，优先测试 `health`、`getStockQuotes`、`getMarketSnapshot`。

## 对 GPT 的使用提示

```text
你做 A 股分析前，必须先调用 Actions 获取：
1. /market/snapshot
2. /boards/hot
3. /stock/quotes
4. /stock/bidask
5. /stock/technical
6. /candidates/actionable

所有结论必须标注 freshness。如果 freshness 是 unavailable 或 stale_cache，不允许输出强买入建议。
```
