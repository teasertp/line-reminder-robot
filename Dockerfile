# 使用 Python 3.12 slim 版作為基底映像
FROM python:3.12-slim

# 讓 Python 輸出不會被緩存
ENV PYTHONUNBUFFERED=1

# 安裝必要的系統套件
RUN apt-get update && apt-get install -y \
    gcc \
    build-essential \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 設定工作目錄
WORKDIR /app

# 複製 requirements.txt 並安裝依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製其他所有檔案
COPY . .

# 啟動指令
CMD ["python", "bot.py"]
