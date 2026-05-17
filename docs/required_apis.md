# 项目所需 API 与外部调用对齐清单

本文件按当前代码实现整理项目实际依赖的外部 API、服务和环境变量，覆盖 `backend/` 与 `scripts/` 中已存在的调用。

## 1. 必需环境变量

### 1.1 后端主 Agent 必需

| 环境变量 | 用途 | 代码位置 |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI 服务地址，用于模型对话与 tool calling | `backend/utils/config.py`, `backend/agent/travel_agent.py` |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API Key | `backend/utils/config.py`, `backend/agent/travel_agent.py` |
| `AZURE_OPENAI_DEPLOYMENT` | Azure OpenAI 模型部署名；部署指向 GPT-5.4 时可填 `gpt-5.4` | `backend/utils/config.py`, `backend/agent/travel_agent.py` |
| `AMAP_API_KEY` | 高德地图 API Key，用于天气、POI、路线查询 | `backend/utils/config.py`, `backend/agent/tools/amap_tools.py` |

### 1.2 小红书 MCP 相关

| 环境变量 | 用途 | 是否必须 | 代码位置 |
| --- | --- | --- | --- |
| `MCP_COMMAND` | 启动 MCP 服务的命令，默认 `npx` | 小红书功能需要 | `backend/utils/config.py`, `backend/main.py`, `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py` |
| `MCP_ARGS` | 启动参数，默认 `rednote-mcp --stdio` | 小红书功能需要 | `backend/utils/config.py`, `backend/main.py`, `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py` |

### 1.3 运行参数

| 环境变量 | 用途 | 是否外部 API 凭据 | 代码位置 |
| --- | --- | --- | --- |
| `AGENT_TEMPERATURE` | 控制模型温度 | 否 | `backend/utils/config.py`, `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py` |
| `MAX_TOOL_ROUNDS` | 限制最大工具调用轮数 | 否 | `backend/utils/config.py`, `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py` |
| `EXTRA_SYSTEM_PROMPT` | 给本地小红书 Agent 追加系统提示词 | 否 | `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py` |

## 2. 代码里实际调用的外部 API / 服务

### 2.1 Azure OpenAI

用途：
- 负责主对话推理
- 决定是否发起 tool calling
- 在小红书本地 agent 示例中也用于驱动工具调用

实际调用方式：
- SDK: `openai.AsyncOpenAI`
- 方法: `client.chat.completions.create(...)`

代码位置：
- `backend/agent/travel_agent.py`
- `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py`

依赖配置：
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT`

说明：
- 代码当前使用 `base_url = {AZURE_OPENAI_ENDPOINT}/openai/v1/`
- 然后通过 `chat.completions.create(...)` 调用模型
- `model` 参数读取 `AZURE_OPENAI_DEPLOYMENT`；在 Azure OpenAI 中通常是部署名，不一定等同于模型 ID

### 2.2 高德地图 REST API

用途：
- 天气查询
- POI 搜索
- 路线规划
- 离线知识库数据采集

统一依赖配置：
- `AMAP_API_KEY`

#### 2.2.1 后端 Agent 工具层实际调用

代码位置：
- `backend/agent/tools/amap_tools.py`

| 本地函数 / 工具名 | 调用的高德接口 | 用途 |
| --- | --- | --- |
| `get_weather` | `GET https://restapi.amap.com/v3/weather/weatherInfo` | 查询天气 |
| `search_poi` | `GET https://restapi.amap.com/v3/assistant/inputtips` | 搜索景点/餐厅/酒店等 POI |
| `get_transit_route` | `GET https://restapi.amap.com/v3/direction/transit/integrated` | 查询公交/地铁路线 |
| `get_driving_route` | `GET https://restapi.amap.com/v3/direction/driving` | 查询驾车路线 |
| `get_walking_route` | `GET https://restapi.amap.com/v3/direction/walking` | 查询步行路线 |

这些函数同时也是主 Agent 暴露给模型的 function calling 工具：
- `get_weather`
- `search_poi`
- `get_transit_route`
- `get_driving_route`
- `get_walking_route`

相关代码位置：
- 工具定义：`backend/agent/tools/amap_tools.py`
- 调用入口：`backend/agent/travel_agent.py`

#### 2.2.2 Amap 脚本工具层实际调用

代码位置：
- `scripts/Amap_API/src/agent_tools/amap_tools.py`

| 本地函数 | 调用的高德接口 | 用途 |
| --- | --- | --- |
| `get_weather` | `GET https://restapi.amap.com/v3/weather/weatherInfo` | 动态天气查询 |
| `get_transit_route` | `GET https://restapi.amap.com/v3/direction/transit/integrated` | 动态公交路线查询 |
| `get_driving_route` | `GET https://restapi.amap.com/v3/direction/driving` | 动态驾车路线查询 |
| `get_walking_route` | `GET https://restapi.amap.com/v3/direction/walking` | 动态步行路线查询 |
| `get_input_tips` | `GET https://restapi.amap.com/v3/assistant/inputtips` | 自动补全 / POI 输入建议 |

#### 2.2.3 Amap 离线采集管道实际调用

代码位置：
- `scripts/Amap_API/src/kb_builder/collector.py`

| 采集逻辑 | 调用的高德接口 | 用途 |
| --- | --- | --- |
| `collect_pois_by_keywords` | `GET https://restapi.amap.com/v3/place/text` | 按关键词批量采集 POI |
| `collect_poi_details` | `GET https://restapi.amap.com/v3/place/detail` | 拉取 POI 详情 |
| `collect_around_facilities` | `GET https://restapi.amap.com/v3/place/around` | 查询 POI 周边设施 |
| `collect_static_routes` | `GET https://restapi.amap.com/v3/direction/driving` | 采集驾车路线矩阵 |
| `collect_static_routes` | `GET https://restapi.amap.com/v3/direction/transit/integrated` | 采集公交路线矩阵 |
| `collect_static_routes` | `GET https://restapi.amap.com/v3/direction/walking` | 采集步行路线矩阵 |
| `collect_static_routes` | `GET https://restapi.amap.com/v4/direction/bicycling` | 采集骑行路线矩阵 |

## 3. 小红书相关调用

### 3.1 当前代码不是直接调用小红书官方开放 API

当前实现方式是：
- Python 代码启动本地 MCP 服务
- 通过 MCP 协议获取工具列表并调用工具
- 实际工具服务默认由 `rednote-mcp` 提供

代码位置：
- `backend/agent/tools/xhs_tools.py`
- `backend/main.py`
- `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py`

### 3.2 实际外部依赖

| 依赖项 | 类型 | 作用 |
| --- | --- | --- |
| `rednote-mcp` | 本地 MCP 工具服务 | 暴露小红书相关工具 |
| `npx rednote-mcp --stdio` | 本地进程启动方式 | 供 Python 侧通过 stdio 建立 MCP 会话 |

### 3.3 代码中的 MCP 调用形式

| 调用方法 | 含义 | 代码位置 |
| --- | --- | --- |
| `list_tools()` | 读取 MCP 暴露的小红书工具列表 | `backend/agent/tools/xhs_tools.py`, `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py` |
| `call_tool(tool_name, arguments)` | 调用指定小红书工具 | `backend/agent/tools/xhs_tools.py`, `scripts/xiaohongshu/local_rednote_agent/chat_with_rednote.py` |

说明：
- 小红书工具名不是写死在后端代码里的
- 工具列表启动时动态加载
- 常见可能包括 `search`、`get_note`、`get_comments`
- 以 MCP 服务实际暴露结果为准

## 4. 主后端里实际存在的 function calling 工具

主 Agent 代码位置：
- `backend/agent/travel_agent.py`

模型可调用的工具来源：
- 固定高德工具：`backend/agent/tools/amap_tools.py`
- 动态小红书工具：`backend/agent/tools/xhs_tools.py`

### 4.1 固定工具

| 工具名 | 来源 | 外部依赖 |
| --- | --- | --- |
| `get_weather` | 高德工具层 | 高德天气 API |
| `search_poi` | 高德工具层 | 高德 POI / inputtips API |
| `get_transit_route` | 高德工具层 | 高德公交路线 API |
| `get_driving_route` | 高德工具层 | 高德驾车路线 API |
| `get_walking_route` | 高德工具层 | 高德步行路线 API |

### 4.2 动态工具

| 工具来源 | 外部依赖 | 是否固定名称 |
| --- | --- | --- |
| `rednote-mcp` 动态返回的工具列表 | 本地 MCP 服务 + 小红书相关能力 | 否 |

## 5. 对齐结论

如果只运行主后端聊天能力，至少需要：
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT`
- `AMAP_API_KEY`

如果还要启用小红书能力，还需要：
- 本机可运行 `npx`
- 已安装并可启动 `rednote-mcp`
- `MCP_COMMAND`
- `MCP_ARGS`

如果还要运行 `scripts/Amap_API` 离线采集管道，还需要：
- `AMAP_API_KEY`

## 6. 当前代码涉及的全部外部接口汇总

### Azure OpenAI

- Azure OpenAI Chat Completions

### 高德地图

- `https://restapi.amap.com/v3/weather/weatherInfo`
- `https://restapi.amap.com/v3/assistant/inputtips`
- `https://restapi.amap.com/v3/direction/transit/integrated`
- `https://restapi.amap.com/v3/direction/driving`
- `https://restapi.amap.com/v3/direction/walking`
- `https://restapi.amap.com/v3/place/text`
- `https://restapi.amap.com/v3/place/detail`
- `https://restapi.amap.com/v3/place/around`
- `https://restapi.amap.com/v4/direction/bicycling`

### 小红书相关

- `rednote-mcp` 通过 MCP stdio 提供的动态工具调用
