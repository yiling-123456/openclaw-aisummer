# backend/ — 大模型后端

## 架构

```
AgentLoop ──► DeepSeekBackend.chat(messages, tools) ──► DeepSeek API
                  │
                  ├─ API 兼容：OpenAI /v1/chat/completions
                  ├─ 消息格式转换：_to_openai_messages()
                  └─ 响应归一化：_normalize()
```

## DeepSeekBackend

通过 OpenAI 兼容接口调用 DeepSeek API，使用原生 function-calling。

### 归一化接口约定

```python
chat(messages, tools) -> {
    "role": "assistant",
    "content": str,
    "tool_calls": [{"id": str, "name": str, "arguments": dict}],
    "usage": {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int},
}
```

### 为什么选择 DeepSeek

- **成本**：约 ¥0.5/百万 token，比 GPT-4 便宜 ~50 倍
- **Function calling 质量**：DeepSeek-V3 在工具调用评测中得分接近 GPT-4
- **兼容性**：完全兼容 OpenAI SDK，换个 base_url 即用
- **中文能力**：对教师评价等中文领域任务更友好

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | (必填) | API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 地址 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型 |

### 安全

- API key 通过环境变量传入，不硬编码
- 错误响应截断至 500 字符，防止敏感信息泄露
- HTTPS 加密传输

## FakeBackend

离线开发的占位后端。实现相同接口，用规则模拟工具调用。用于：
- 自检（`--selfcheck`）
- 未配 API key 时的骨架测试
- 消融实验的可控环境

## 已弃用：server.py

原计划本地部署 GLM 模型（server.py），后改为直接调 DeepSeek API 的 client.py。server.py 保留但不接入。
