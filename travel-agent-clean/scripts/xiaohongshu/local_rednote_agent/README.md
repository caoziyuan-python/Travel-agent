# RedNote 终端聊天助手

通过终端与小红书内容交互的本地 Agent，使用 Azure OpenAI + rednote-mcp。

## 快速开始

### 1. 安装依赖

```bash
# 安装 Node.js 和 npm
# 安装 Python 3.10+

# 安装 Playwright
npx playwright install

# 全局安装 rednote-mcp 并登录
npm install -g rednote-mcp
rednote-mcp init
```

### 2. 配置环境

复制 `.env` 并填入你的 Azure OpenAI 信息：

```bash
# 编辑 .env 文件，填写以下配置
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your_api_key
AZURE_OPENAI_DEPLOYMENT=your_deployment_name
```

### 3. 创建虚拟环境并安装 Python 依赖

Windows PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. 运行

```bash
python chat_with_rednote.py
```

## 使用示例

```text
You> 帮我搜索几篇关于露营的小红书笔记
You> 打开第一篇笔记看看内容
```

输入 `exit` 或 `quit` 退出。

## 故障排除

- **找不到 npx**: 安装 Node.js 并确保在 PATH 中
- **没有列出工具**: 运行 `rednote-mcp init` 重新登录
- **工具调用失败**: 检查 `~/.mcp/rednote/cookies.json` 是否存在

## 文件说明

- `chat_with_rednote.py` - 主程序
- `.env` - 环境配置
- `requirements.txt` - Python 依赖
