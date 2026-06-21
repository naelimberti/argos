"""
Fixtures partagées pour tous les tests ARGOS.
Utilise une base SQLite en mémoire — jamais la base de production.
"""

import os

import pytest
from sqlmodel import Session, SQLModel, create_engine

# Forcer le mode paper et une DB en mémoire avant tout import
os.environ["TRADING_MODE"] = "paper"
os.environ["ENABLE_REAL_TRADING"] = "false"
os.environ["DATABASE_PATH"] = ":memory:"
os.environ["LOG_LEVEL"] = "WARNING"


@pytest.fixture(scope="function")
def engine():
    """Moteur SQLite en mémoire, recréé pour chaque test."""
    from argos.database import models  # noqa: F401 — enregistre les modèles

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def session(engine):
    """Session SQLite propre pour chaque test."""
    with Session(engine) as s:
        yield s
