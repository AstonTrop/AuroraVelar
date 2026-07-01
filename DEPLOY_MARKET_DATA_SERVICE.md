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
2. 中文界面进入 `配置`。
3. `说明` 粘贴 `GPT_STOCK_ANALYSIS_INSTRUCTIONS.md` 的完整内容。
4. 找到 `操作`，点击 `创建新操作` 或 `添加操作`。
5. `身份验证` 选择 `无`。
6. `架构` 粘贴 `chatgpt_action_openapi.yaml` 的完整内容。
7. `隐私政策` 填写：

```text
https://a-share-market-data-service.onrender.com/privacy
```

8. 点击测试，优先测试 `health`、`getStockQuotes`、`getMarketSnapshot`。

## 对 GPT 的使用提示

```text
GPT 的说明请使用 GPT_STOCK_ANALYSIS_INSTRUCTIONS.md。
Actions 架构请使用 chatgpt_action_openapi.yaml。
```
