# 小红书模块

本模块包含本地小红书 Agent：`local_rednote_agent/`，为旅行 Agent 提供小红书笔记与攻略的实时检索能力。

## 工具功能

| 工具函数 | 说明 |
|----------|------|
| `search_notes(keyword)` | 按关键词搜索小红书笔记（如"北京故宫攻略"） |
| `get_note_detail(note_id)` | 获取指定笔记的详细内容与评论 |

## 使用方式

在后端 `backend/agent/tools/xhs_tools.py` 中封装调用：

```python
from scripts.xiaohongshu.local_rednote_agent import ...  # 参考 local_rednote_agent 文档
```

## 环境配置

请参考 `local_rednote_agent` 文档，将所需环境变量添加至项目根目录的 `.env` 文件。

## 状态

- [x] `local_rednote_agent` 接入后端工具层
- [ ] 工具函数测试
