FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY gateway/ ./gateway/
COPY orders/ ./orders/

EXPOSE 8000 8001

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
