"""
Connexion SQLite et initialisation de la base de données ARGOS.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

from argos.utils.logger import get_logger, setup_logging

setup_logging()
logger = get_logger("database")


def _get_database_url() -> str:
    db_path = Path(os.getenv("DATABASE_PATH", "data/argos.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


# Moteur SQLite — une seule instance pour tout le processus
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = _get_database_url()
        _engine = create_engine(
            url,
            echo=False,                    # mettre True pour déboguer les requêtes SQL
            connect_args={"check_same_thread": False},  # nécessaire avec SQLite + threads
        )
        logger.debug(f"Moteur SQLite créé : {url}")
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager pour obtenir une session SQLite.

    Usage :
        with get_session() as session:
            session.add(...)
            session.commit()
    """
    with Session(get_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def init_database() -> None:
    """Crée toutes les tables si elles n'existent pas encore.

    Appelé par : python -m argos.main init-db
    Idempotent : peut être appelé plusieurs fois sans risque.
    """
    # Importer les modèles pour que SQLModel les enregistre
    from argos.database import models  # noqa: F401

    engine = get_engine()
    SQLModel.metadata.create_all(engine)

    db_path = os.getenv("DATABASE_PATH", "data/argos.db")
    logger.info(f"Base de données initialisée : {db_path}")
    logger.info(
        "Tables créées : market_snapshots, signals, paper_trades, "
        "risk_decisions, learning_notes, strategy_versions"
    )

    _seed_default_strategy(engine)


def _seed_default_strategy(engine) -> None:
    """Insère la stratégie de production par défaut si elle n'existe pas encore."""
    from sqlmodel import Session, select
    from argos.database.models import StrategyVersion, StrategyStatus

    with Session(engine) as session:
        existing = session.exec(
            select(StrategyVersion).where(StrategyVersion.name == "strategy_v1_production")
        ).first()

        if existing:
            return

        strategy = StrategyVersion(
            name="strategy_v1_production",
            version="1.0.0",
            status=StrategyStatus.PRODUCTION,
            description=(
                "Stratégie initiale simple. "
                "Signaux : RSI + moyennes mobiles + volume. "
                "Règle : plusieurs signaux alignés obligatoires."
            ),
            parameters='{"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70, '
                       '"ma_fast": 20, "ma_slow": 50, "min_volume_ratio": 1.2}',
        )
        session.add(strategy)
        session.commit()
        logger.info("Stratégie par défaut 'strategy_v1_production' enregistrée.")
