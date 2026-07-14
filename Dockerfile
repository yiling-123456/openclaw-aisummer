# mini-OpenClaw — Claude Code 式命令行 AI 智能体
# 构建: docker build -t openclaw .
# 运行: docker run -e DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY openclaw "你的任务"

FROM python:3.11-slim

LABEL org.opencontainers.image.title="mini-OpenClaw"
LABEL org.opencontainers.image.description="A Claude Code-style CLI AI agent"

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*
ARG NPM_REGISTRY=https://registry.npmmirror.com

RUN npm config set registry "$NPM_REGISTRY" \
    && npm install -g @modelcontextprotocol/server-filesystem \
    && npm cache clean --force \
    && command -v mcp-server-filesystem
# 工作目录
WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .

RUN pip install --no-cache-dir --default-timeout=60 -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 复制项目源码
COPY . .

# 环境变量默认值
ENV DEEPSEEK_BASE_URL=https://api.deepseek.com
ENV DEEPSEEK_MODEL=deepseek-chat
# DEEPSEEK_API_KEY 需要在运行时注入

# 自检（可跳过缓存，用 --build-arg SKIP_SELFCHECK=1 跳过）
ARG SKIP_SELFCHECK=0
RUN if [ "$SKIP_SELFCHECK" != "1" ]; then python -m agent.cli --selfcheck; fi

ENTRYPOINT ["python", "-m", "agent.cli"]
