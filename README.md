# ARGOS — Autonomous Risk-Governed Optimization System

Système multi-agents de paper trading, d'analyse de marché et d'apprentissage continu.

**Objectif principal :** apprendre l'automatisation, le backtesting et la gestion du risque — pas gagner de l'argent.

> Le mode paper trading est actif par défaut. Aucun trading réel n'est possible sans activation manuelle explicite.

---

## Principe fondamental

> Mieux vaut rater une opportunité que prendre un trade mal compris.

Le bot observe souvent, mais trade rarement. Le capital est protégé avant toute recherche de performance.

---

## Installation (Windows)

```powershell
cd argos
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m argos.main init-db
python -m argos.main run-paper
```

## Installation (Linux / macOS)

```bash
cd argos
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m argos.main init-db
python -m argos.main run-paper
```

---

## Commandes

| Commande | Description |
|---|---|
| `python -m argos.main init-db` | Initialise la base de données SQLite |
| `python -m argos.main run-paper` | Lance le bot en paper trading continu |
| `python -m argos.main status` | Affiche l'état du système |
| `python -m argos.main weekly-report` | Génère le rapport hebdomadaire |
| `python -m argos.main backtest --strategy strategy_v1 --asset BTC` | Backteste une stratégie |
| `python -m argos.main export-logs` | Exporte les données en CSV |
| `streamlit run src/argos/dashboard/app.py` | Lance le dashboard |

---

## Architecture

```
Scheduler (toutes les 15 min)
    └── MarketScanner    → signaux de marché
    └── StrategyAgent    → fiche de trade structurée
    └── RiskManager      → valide ou bloque le trade
    └── PaperEngine      → simule l'exécution
    └── LearningAgent    → analyse le résultat
    └── CriticAgent      → challenge la stratégie
```

---

## Marché ciblé

- BTC/EUR et ETH/EUR uniquement au départ
- Pas de levier, pas de futures, pas de tokens illiquides
- Stratégie simple : RSI + moyennes mobiles + volume

---

## Sécurité

- Paper trading activé par défaut (`TRADING_MODE=paper`)
- Trading réel désactivé par défaut (`ENABLE_REAL_TRADING=false`)
- Aucune clé API stockée dans le code
- Toutes les clés dans `.env` (exclu de Git)
- Stop-loss et take-profit obligatoires sur chaque trade
- Arrêt automatique après 2 pertes consécutives

---

## Passage en réel — conditions minimales

Le système ne passera jamais en réel avant :

- [ ] 3 mois de paper trading
- [ ] 300 trades simulés
- [ ] Performance positive après frais
- [ ] Drawdown maîtrisé
- [ ] Dashboard fonctionnel
- [ ] Validation manuelle finale

---

## Roadmap

| Week-end | Objectif |
|---|---|
| 1 | Fondations, config, sécurité, base de données |
| 2 | Données de marché (CoinGecko) |
| 3 | Market Scanner Agent |
| 4 | Strategy Agent v1 (RSI + MA + volume) |
| 5 | Risk Manager Agent |
| 6 | Paper Trading Engine |
| 7 | Scheduler autonome |
| 8 | Dashboard Streamlit |
| 9 | Learning Engine + Critic Agent |
| 10 | Backtesting + versioning des stratégies |

---

## Avertissement

ARGOS est un projet d'apprentissage technique. Il ne garantit aucun gain financier.
Avec un capital de 10 €, la perte maximale possible est 10 €.
Le vrai rendement est la compétence acquise.
