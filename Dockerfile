FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3000 \
    GUNICORN_TIMEOUT=1800

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg supervisor \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /storage \
    && chmod +x start.sh

EXPOSE 3000

CMD ["./start.sh"]
