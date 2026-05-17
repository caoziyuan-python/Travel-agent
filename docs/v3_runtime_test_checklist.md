# Travel Agent V3 运行测试清单

这份清单用于确认当前 V3 后端、CLI、结构化行程流程是否能正常运行。

## 1. 环境检查

从项目根目录执行：

```bash
cd /Users/liuyidi/Downloads/Travel-Agent
source .venv/bin/activate
pip install -r backend/requirements.txt
```

`.env` 至少需要：

```bash
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=...
AMAP_API_KEY=...
MAX_TOOL_ROUNDS=8
OPENAI_TIMEOUT_SECONDS=60
CONTEXT_RECENT_MESSAGES=12
CONTEXT_SUMMARY_TRIGGER_MESSAGES=20
CONTEXT_STORE=memory
PRICE_CURRENCY=CNY
```

如果要测 GPT-5.4，先确认 Azure OpenAI 里已经创建了对应 deployment，再设置：

```bash
AZURE_OPENAI_DEPLOYMENT=gpt-5.4
```

## 2. 一键离线检查

离线检查不依赖真实 Azure / 高德接口，主要验证 V3 编排和语法：

```bash
python3 scripts/run_v3_checks.py
```

通过标准：

```text
[PASS] V3 orchestrator tests
[PASS] V3 syntax compile
Summary: 0 failed
```

如果提示缺依赖，重新执行：

```bash
pip install -r backend/requirements.txt
```

## 3. 后端 API 启动

从 `backend/` 目录启动：

```bash
cd backend
uvicorn main:app --reload --port 8000
```

打开：

```text
http://127.0.0.1:8000/docs
```

通过标准：Swagger 页面能打开，并能看到 `/api/v1/chat`、`/weather`、`/pois`、`/flights`、`/hotels`。

## 4. 一键 API 冒烟测试

保持后端运行，另开一个终端从项目根目录执行：

```bash
source .venv/bin/activate
python3 scripts/run_v3_checks.py --api
```

该命令会检查：

- `/health`
- `/docs`
- `/api/v1/weather`
- `/api/v1/pois`
- `/api/v1/flights`
- `/api/v1/hotels`
- `/api/v1/chat` 缺字段反问
- `/api/v1/chat` 完整行程生成
- `/api/v1/chat` 多轮修改

通过标准：`Summary: 0 failed`。外部服务失败通常会显示为 warning；例如天气失败优先查 `AMAP_API_KEY`，行程生成失败优先查 Azure endpoint/key/deployment。若模型响应很慢，可临时设置 `OPENAI_TIMEOUT_SECONDS=20` 做 fallback 验证。

## 5. 手动接口测试

天气：

```bash
curl "http://127.0.0.1:8000/api/v1/weather?city_code=110000"
```

POI：

```bash
curl "http://127.0.0.1:8000/api/v1/pois?keywords=故宫&city=北京"
```

机票：

```bash
curl "http://127.0.0.1:8000/api/v1/flights?origin=北京&destination=成都&days=7&top_n=5"
```

酒店：

```bash
curl "http://127.0.0.1:8000/api/v1/hotels?check_in=2026-05-01&keyword=故宫&top_n=5"
```

缺字段反问：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我规划北京行程"}'
```

完整行程：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我规划北京3天历史和美食行程，预算3000元，5月1日出发"}'
```

多轮修改：使用上一条返回的 `session_id`。

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"上一步的session_id","message":"把第二天换成室内景点，少走路"}'
```

## 6. CLI 测试

从项目根目录执行：

```bash
source .venv/bin/activate
python backend/cli.py --no-xhs --no-extract
```

建议输入：

```text
规划北京3日游，预算3000，喜欢历史和美食
```

通过标准：能返回回答，工具调用不无限重复。401/404 通常是 Azure key、endpoint 或 deployment 问题；天气失败通常是 `AMAP_API_KEY` 或高德接口问题。

## 7. 当前已知缺口

目前还没有实现：

```text
POST /api/v1/itinerary
PATCH /api/v1/itinerary/{id}
```

前端当前应先接 `/api/v1/chat` 返回的 `itinerary` 字段来渲染结构化行程。
