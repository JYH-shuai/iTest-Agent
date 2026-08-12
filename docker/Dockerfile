# iTest-Agent 服务镜像
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    ITEST_KB_DIR=/app/chroma_db

WORKDIR /app

# 系统依赖：weasyprint 渲染 + playwright 浏览器
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libffi-dev \
    libjpeg62-turbo \
    libopenjp2-7 \
    fonts-noto-cjk \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Playwright Chromium（执行环境）
RUN python -m playwright install --with-deps chromium

# 项目代码
COPY . .

EXPOSE 8000 8001 8002

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
