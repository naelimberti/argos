"""
Scheduler autonome d'ARGOS.

Toutes les X minutes (défaut : 15) :
  1. Récupère les prix BTC et ETH via CoinGecko
  2. Enregistre les snapshots en base
  3. (semaines suivantes) Scanner → Stratégie → Risque → Paper Trading → Learning

Garanties :
  - Un seul cycle actif à la fois (verrou threading)
  - Les erreurs n'arrêtent jamais la boucle (sauf seuil d'urgence)
  - Compteur d'erreurs consécutives → arrêt d'urgence si dépassé
  - Provider CoinGecko instancié une seule fois (pas de reconnexion inutile)
  - Arrêt propre sur CTRL+C avec résumé de session
  - Logs APScheduler silencieux (on contrôle notre propre format)
"""

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from argos.database.db import get_session
from argos.database.repositories import load_portfolio_from_db, save_market_snapshot
from argos.learning.learning_agent import analyze_closed_trades
from argos.market_data.base_provider import ProviderError
from argos.market_data.coingecko import CoinGeckoProvider
from argos.market_data.normalizer import price_data_to_snapshot
from argos.strategies.simple_momentum import run_strategy
from argos.trading.paper_engine import PaperTradingEngine, _load_risk_rules
from argos.utils.logger import get_logger, setup_logging

load_dotenv()
setup_logging()
logger = get_logger("scheduler")

# APScheduler est verbeux par défaut — on le réduit au silence
logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ============================================================
# État global de la session
# ============================================================

@dataclass
class SessionStats:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_cycles: int = 0
    successful_cycles: int = 0
    failed_cycles: int = 0
    consecutive_errors: int = 0
    last_success_at: datetime | None = None
    last_prices: dict[str, float] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.total_cycles == 0:
            return 0.0
        return self.successful_cycles / self.total_cycles * 100

    @property
    def uptime_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds() / 60


_stats = SessionStats()
_cycle_lock = threading.Lock()
_provider = None  # singleton CoinGecko — initialisé une seule fois


# ============================================================
# Chargement de la configuration
# ============================================================

_CONFIG_CACHE: dict = {}
_CONFIG_MTIME: float = 0.0


def _load_config() -> dict:
    """Lit settings.yaml une seule fois, recharge uniquement si le fichier change."""
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        mtime = os.path.getmtime("config/settings.yaml")
        if mtime != _CONFIG_MTIME:
            with open("config/settings.yaml", encoding="utf-8") as f:
                _CONFIG_CACHE = yaml.safe_load(f) or {}
            _CONFIG_MTIME = mtime
    except FileNotFoundError:
        logger.warning("config/settings.yaml introuvable — valeurs par défaut utilisées.")
    except Exception as e:
        logger.error(f"Erreur lecture config : {e}")
    return _CONFIG_CACHE


def _get_enabled_assets(config: dict) -> list[str]:
    return [a["symbol"] for a in config.get("assets", []) if a.get("enabled", False)]


def _get_interval(config: dict) -> int:
    env_val = os.getenv("SCAN_INTERVAL_MINUTES")
    if env_val:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass
    return int(config.get("scheduler", {}).get("scan_interval_minutes", 15))


def _get_max_errors(config: dict) -> int:
    return int(config.get("scheduler", {}).get("max_consecutive_errors", 5))


# ============================================================
# Cycle principal
# ============================================================

def run_cycle() -> None:
    """Point d'entrée du cycle. Appelé par APScheduler toutes les X minutes."""
    global _stats

    if not _cycle_lock.acquire(blocking=False):
        logger.warning("⚠  Cycle précédent encore en cours — cycle ignoré.")
        return

    _stats.total_cycles += 1
    cycle_num = _stats.total_cycles

    try:
        _execute_cycle(cycle_num)
        _stats.successful_cycles += 1
        _stats.consecutive_errors = 0
        _stats.last_success_at = datetime.now(timezone.utc)

    except Exception as e:
        _stats.failed_cycles += 1
        _stats.consecutive_errors += 1
        logger.error(
            f"✗ Cycle #{cycle_num} échoué "
            f"({_stats.consecutive_errors} erreur(s) consécutive(s)) : {e}"
        )

        config = _load_config()
        max_errors = _get_max_errors(config)
        if _stats.consecutive_errors >= max_errors:
            logger.critical(
                f"ARRÊT D'URGENCE — {_stats.consecutive_errors} erreurs consécutives "
                f"(seuil : {max_errors}). Vérifiez la connexion et les logs."
            )
            raise SystemExit(1)

        next_retry = _get_interval(config)
        logger.info(f"Prochain essai dans {next_retry} minute(s).")

    finally:
        _cycle_lock.release()


def _execute_cycle(cycle_num: int) -> None:
    """Logique interne d'un cycle. Séparée pour faciliter les tests."""
    global _provider, _stats

    config = _load_config()
    enabled_assets = _get_enabled_assets(config)

    if not enabled_assets:
        logger.warning("Aucun actif activé dans config/settings.yaml.")
        return

    started_at = datetime.now(timezone.utc)

    _log_cycle_start(cycle_num, enabled_assets)

    # Provider singleton — créé une seule fois par session
    if _provider is None:
        _provider = CoinGeckoProvider()
        logger.debug("Provider CoinGecko initialisé.")

    # --- Étape 1 : récupération des prix ---
    try:
        prices = _provider.get_prices(enabled_assets)
    except ProviderError as e:
        logger.error(f"  Échec récupération des prix : {e}")
        raise

    # --- Étape 2 : stockage des snapshots ---
    with get_session() as session:
        for price_data in prices:
            snapshot = price_data_to_snapshot(price_data)
            save_market_snapshot(session, snapshot)

    # Mise à jour des prix en mémoire pour le résumé de session
    for p in prices:
        _stats.last_prices[p.asset] = p.price

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()

    _log_cycle_end(cycle_num, prices, elapsed)

    # --- Pipeline complet : Stratégie → PaperEngine → Learning ---
    with get_session() as session:
        _run_trading_pipeline(session, enabled_assets, prices)


# ============================================================
# Intégration Paper Engine + Learning Agent (Week-end 4+)
# ============================================================

def _run_trading_pipeline(session, enabled_assets: list[str], prices: list) -> None:
    """
    Pipeline complet d'un cycle de trading :

    1. Reconstruction du portefeuille depuis la DB
    2. Surveillance et clôture des positions ouvertes (SL / TP / timeout)
    3. Learning Agent sur les trades clôturés
    4. Génération de signaux momentum
    5. Soumission des TradeRequests au PaperEngine

    Une seule session est utilisée pour tout le pipeline.
    """
    current_prices = {p.asset: p.price for p in prices}
    if not current_prices:
        return

    # 1. Portefeuille reconstruit depuis l'historique DB (une seule fois pour tout le pipeline)
    portfolio = load_portfolio_from_db(session)
    engine = PaperTradingEngine(portfolio)

    # 2. Surveillance des positions existantes
    closed_trades = engine.monitor_open_positions(current_prices, session)

    # 3. Learning Agent
    if closed_trades:
        logger.info(f"[PIPELINE] {len(closed_trades)} cloture(s) — analyse en cours...")
        notes = analyze_closed_trades(closed_trades, session)
        logger.info(f"[PIPELINE] {len(notes)} lecon(s) enregistree(s).")

    # 4. Génération des signaux — réutilise les règles déjà cachées
    risk_rules = _load_risk_rules()  # cache mtime-based, pas de lecture disque
    max_pos = risk_rules.get("paper_trading", {}).get("max_open_positions", 3)
    trade_requests = run_strategy(
        enabled_assets, session,
        max_positions=max_pos,
        portfolio=portfolio,      # évite un 2e load_portfolio_from_db dans run_strategy
    )

    if not trade_requests:
        logger.debug("[PIPELINE] Aucun signal ce cycle.")
        return

    # 5. Soumission au PaperEngine
    for request in trade_requests:
        result = engine.open_trade(request, session)
        if result.accepted:
            logger.info(
                f"[PIPELINE] Trade ouvert : {request.asset} {request.direction.value} "
                f"@ {request.entry_price:,.2f} EUR | id={result.trade_id}"
            )
        else:
            logger.info(
                f"[PIPELINE] Trade refuse : {request.asset} — {result.rejection_reason}"
            )


# ============================================================
# Logs structurés
# ============================================================

def _log_cycle_start(cycle_num: int, assets: list[str]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info(f"+-- Cycle #{cycle_num:04d} -- {now} --------------------")
    logger.info(f"|   Actifs : {', '.join(assets)}")


def _log_cycle_end(cycle_num: int, prices: list, elapsed: float) -> None:
    for p in prices:
        change = f"{p.price_change_24h:+.2f}%" if p.price_change_24h is not None else "n/a"
        vol = f"{p.volatility_estimate:.2f}%" if p.volatility_estimate is not None else "n/a"
        logger.info(f"|   {p.asset:<4} {p.price:>10,.2f} EUR   24h: {change:<9}  vol: {vol}")
    logger.info(
        f"+-- OK en {elapsed:.1f}s  "
        f"[succes: {_stats.successful_cycles} / cycles: {_stats.total_cycles}]"
    )


def _log_session_summary() -> None:
    uptime = _stats.uptime_minutes
    logger.info("=" * 55)
    logger.info("  ARGOS — Résumé de session")
    logger.info("=" * 55)
    logger.info(f"  Durée           : {uptime:.0f} min")
    logger.info(f"  Cycles total    : {_stats.total_cycles}")
    logger.info(f"  Cycles réussis  : {_stats.successful_cycles}")
    logger.info(f"  Cycles échoués  : {_stats.failed_cycles}")
    logger.info(f"  Taux de succès  : {_stats.success_rate:.1f}%")
    if _stats.last_prices:
        logger.info("  Derniers prix   :")
        for asset, price in _stats.last_prices.items():
            logger.info(f"    {asset} : {price:,.2f} EUR")
    if _stats.last_success_at:
        logger.info(f"  Dernier succès  : {_stats.last_success_at.strftime('%H:%M:%S UTC')}")
    logger.info("=" * 55)


# ============================================================
# Point d'entrée public
# ============================================================

def start_scheduler() -> None:
    """Lance la boucle autonome. Bloque jusqu'à CTRL+C."""
    config = _load_config()
    interval = _get_interval(config)
    enabled = _get_enabled_assets(config)

    logger.info("=" * 55)
    logger.info("  ARGOS — Démarrage du scheduler")
    logger.info(f"  Mode          : PAPER TRADING")
    logger.info(f"  Intervalle    : {interval} min")
    logger.info(f"  Provider      : CoinGecko (gratuit, sans clé)")
    logger.info(f"  Actifs        : {enabled}")
    logger.info(f"  Max erreurs   : {_get_max_errors(config)} avant arrêt d'urgence")
    logger.info("=" * 55)

    # Premier cycle immédiatement au démarrage
    logger.info("Lancement du cycle initial...")
    run_cycle()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        func=run_cycle,
        trigger=IntervalTrigger(minutes=interval),
        id="argos_main_cycle",
        name="ARGOS — collecte de marché",
        max_instances=1,
        coalesce=True,           # ignore les cycles manqués pendant un ralentissement
        misfire_grace_time=60,   # tolère 60s de retard avant de considérer un cycle manqué
    )

    try:
        logger.info(f"Scheduler actif. Prochain cycle dans {interval} min. CTRL+C pour arrêter.")
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Arrêt du scheduler...")
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        _log_session_summary()
        logger.info("ARGOS arrêté proprement.")
