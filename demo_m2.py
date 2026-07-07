from backend.client import DeepSeekBackend

tools = [{
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "返回当前时间。用户询问现在几点、当前时间时使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]

backend = DeepSeekBackend()
resp = backend.chat(
    [{"role": "user", "content": "现在几点？"}],
    tools=tools,
)
print(resp)