"""
Logger centralisé pour ARGOS.

Tous les modules importent get_logger() depuis ici.
Les logs sont écrits en console ET dans logs/argos.log (rotation automatique).
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger nommé, configuré une seule fois."""
    return logging.getLogger(f"argos.{name}")


def setup_logging() -> None:
    """Configure le système de logging global. À appeler une seule fois au démarrage."""
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    log_path = Path(os.getenv("LOG_PATH", "logs/argos.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger("argos")
    root.setLevel(log_level)

    # Évite d'ajouter plusieurs handlers si appelé plusieurs fois
    if root.handlers:
        return

    # Console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # Fichier rotatif (max 10 Mo × 5 fichiers)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
