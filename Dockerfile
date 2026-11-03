# Multi-arch базовый образ: linux/amd64 и linux/arm64 (Raspberry Pi 4/5)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Работаем от root: ./data монтируется с хоста и может принадлежать root,
# если Docker создал папку сам. Наружу контейнер ничего не публикует.
VOLUME ["/data"]
CMD ["python", "-m", "app.main"]
