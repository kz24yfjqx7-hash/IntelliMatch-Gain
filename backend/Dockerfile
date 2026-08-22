# 多架构构建：不硬编码 --platform，由 buildx 的 --platform 决定基础镜像架构
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 只装运行期需要的东西，不装 build-essential/gcc：
# 需要编译工具才能安装的包，一律视为不合格依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY . .

EXPOSE 8000

# 树莓派内存上限 300MB：单进程 uvicorn，不用 gunicorn 多 worker
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
