FROM python:3.9

WORKDIR /app

# 先复制requirements文件单独安装依赖（利用Docker缓存层）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 然后复制其他文件
COPY . .

# 明确指定端口
EXPOSE 5000

# 使用gunicorn替代waitress（更稳定）
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "main:app"]
