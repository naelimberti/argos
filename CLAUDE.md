# argos — bot paper trading BTC

Python · SQLite · CoinGecko API · Docker · Fly.io · pytest

## Structure src/argos/
database/ · dashboard/app.py · learning/ · market_data/ · strategies/ · trading/ · utils/logger.py · main.py · scheduler.py

## Config
config/settings.yaml · config/risk_rules.yaml · config/strategy_params.json

## Règles
- Tests pytest obligatoires pour tout nouveau module
- Paper trading uniquement (pas de vrais fonds)
- Logs via utils/logger.py
- Actif unique : BTC
