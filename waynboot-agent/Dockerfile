# waynboot-agent 运行镜像
#
# ⚠️ 本机没有 Docker，这个文件**未实测**（按标准写法写的，首次构建时留意）。用法：
#   docker build -t waynboot-agent .
#   docker run --rm -p 8300:8300 \
#     -e AGENT_MALL_BASE_URL=http://host.docker.internal:82 \
#     -e AGENT_REDIS_URL=redis://host.docker.internal:6379/0 \
#     -e AGENT_LLM_API_KEY=sk-xxx \
#     waynboot-agent
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先只拷依赖描述再装，利用 Docker 层缓存：改代码不会重装依赖
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# 不用 root 跑服务（容器内最小权限原则）
RUN useradd --create-home --shell /usr/sbin/nologin agent && chown -R agent:agent /app
USER agent

EXPOSE 8300

# 单进程即可水平扩副本：会话/幂等/限流/用量状态都在 Redis 里，进程本身无状态
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
