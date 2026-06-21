FROM python:3.12-slim

# Dépendances système
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances Python d'abord (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code source
COPY . .

# Dossiers persistants
RUN mkdir -p /data /app/logs /app/exports

# Variables d'environnement par défaut
ENV PYTHONPATH=/app/src
ENV TRADING_MODE=paper
ENV ENABLE_REAL_TRADING=false
ENV DATABASE_PATH=/data/argos.db
ENV LOG_LEVEL=INFO
ENV PYTHONUNBUFFERED=1

# Initialise la DB au build (sera écrasée par le volume persistant en prod)
RUN python -m argos.main init-db || true

EXPOSE 8501

CMD ["supervisord", "-c", "/app/supervisord.conf", "-n"]
