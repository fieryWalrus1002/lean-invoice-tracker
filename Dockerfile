FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    fonts-liberation \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
