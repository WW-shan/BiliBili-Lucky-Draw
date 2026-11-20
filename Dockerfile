FROM python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d

# 设置工作目录为项目目录
WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app
RUN mkdir -p /app/Log
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 运行docker run命令
CMD ["python", "main.py"]
