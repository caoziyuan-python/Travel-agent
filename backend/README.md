# 后端技术文档

## FastAPI 是什么

FastAPI 是 Python 的现代异步 Web 框架：
- **自动生成 Swagger 文档**：启动后访问 `/docs` 即可获得可交互的 API 文档
- **原生 async/await**：适合同时调用 LLM 和多个外部 API 的并发场景
- **Pydantic 数据校验**：请求/响应结构自动验证，减少运行时错误

---

## 系统架构

```
前端 (frontend/)
    │ HTTP
    ▼
FastAPI (main.py)  ←── lifespan: 启动时连接 rednote-mcp
    │
    ▼  /api/v1/...
路由层 (api/routes.py)
    │
    ▼
Travel Agent (agent/travel_agent.py)
    │  Azure OpenAI tool calling（原生，无 LangChain）
    │
    ├── amap_tools.py ──► 高德 REST API
    │   get_weather / search_poi
    │   get_transit_route / get_driving_route / get_walking_route
    │
    └── xhs_tools.py ──► MCP 协议 ──► rednote-mcp (Node.js/npm)
        RedNoteMCPClient              └─► 小红书（Playwright 浏览器）
        (复用 scripts/xiaohongshu/local_rednote_agent/)
```

### local_rednote_agent 的位置与集成方式

```
Travel-Agent/               ← 项目根目录
├── scripts/
│   └── xiaohongshu/
│       └── local_rednote_agent/  ← 独立 CLI + 可复用 MCP 客户端
│           └── chat_with_rednote.py
│               └── RedNoteMCPClient  ← 核心类
└── backend/
    └── agent/tools/xhs_tools.py
        └── 通过 sys.path + 包路径导入 RedNoteMCPClient（无需 pip install）
```

---

## 目录结构

```
backend/
├── main.py                      # FastAPI 入口，lifespan 管理 MCP 连接
├── requirements.txt
├── api/
│   └── routes.py                # 所有前端可用接口（/api/v1/...）
├── agent/
│   ├── travel_agent.py          # Agent 核心：Azure OpenAI + 工具调度
│   └── tools/
│       ├── amap_tools.py        # 高德工具（async + OpenAI 工具定义）
│       └── xhs_tools.py        # 小红书工具（封装 RedNoteMCPClient）
└── utils/
    └── config.py                # 所有环境变量
```

---

## API 接口（前端接入点）

所有接口前缀：`/api/v1`

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/chat` | **主对话接口**，支持多轮上下文 |
| `DELETE` | `/chat/{session_id}` | 重置会话 |
| `GET` | `/weather` | 实时天气（直连高德） |
| `GET` | `/pois` | POI 搜索（直连高德） |
| `GET` | `/route` | 路线规划（公交/驾车/打车/步行，直连高德） |
| `GET` | `/taxi` | 打车路线与费用预估（直连高德） |
| `GET` | `/notes` | 小红书攻略搜索（直连 XHS） |
| `GET` | `/tools` | 查看已加载的 XHS 工具列表 |

### POST /api/v1/chat 示例

```json
// Request
{
  "message": "推荐故宫附近的午餐，然后告诉我怎么坐地铁去颐和园",
  "session_id": ""
}

// Response
{
  "reply": "...",
  "tools_called": ["search_poi", "get_transit_route"],
  "session_id": "自动生成的 UUID"
}
```

> `session_id` 留空时自动生成新会话；后续请求带上同一个 `session_id` 即可保持上下文。

---

## 环境变量

在项目根目录或 `backend/` 目录创建 `.env`：

```env
# Azure OpenAI（必填）
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your_key
# Azure OpenAI 这里填“部署名”。如果部署名与模型名一致，可使用 gpt-5.4。
AZURE_OPENAI_DEPLOYMENT=gpt-5.4

# 高德地图（必填）
AMAP_API_KEY=your_amap_key

# rednote-mcp（可选，默认值如下）
MCP_COMMAND=npx
MCP_ARGS=rednote-mcp --stdio

# Agent 行为（可选）
AGENT_TEMPERATURE=0.2
MAX_TOOL_ROUNDS=8
```

---

## 本地运行

### 前置条件

```bash
# 1. 安装 Node.js + rednote-mcp 并登录小红书
npm install -g rednote-mcp
npx playwright install
rednote-mcp init   # 会打开浏览器，扫码登录

# 2. 安装 Python 依赖
cd backend
pip install -r requirements.txt
```

### 启动

```bash
cd backend
uvicorn main:app --reload --port 8000
```

访问 `http://localhost:8000/docs` 查看 Swagger 文档。

> **注意**：如果 rednote-mcp 未安装或未登录，启动时会打印警告，
> 小红书功能不可用，但高德相关功能（天气、路线、POI）仍正常工作。

---

## 数据流

```
1. 前端 POST /api/v1/chat  →  { message, session_id }
2. routes.py 获取或创建会话（内存 dict）
3. travel_agent.run_agent(messages) 开始推理循环
4. Azure OpenAI 决定调用哪些工具
5. amap_tools / xhs_tools 并发执行（asyncio）
6. 工具结果注入对话历史，继续下一轮推理
7. 无工具调用时返回最终文本
8. 前端收到 { reply, tools_called, session_id }
```
