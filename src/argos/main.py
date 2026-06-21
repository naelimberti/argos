"""
ARGOS — Point d'entrée principal (CLI).

Usage :
    python -m argos.main init-db
    python -m argos.main run-paper
    python -m argos.main status
    python -m argos.main weekly-report
    python -m argos.main backtest --strategy strategy_v1 --asset BTC
    python -m argos.main export-logs
"""

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

# Charger .env avant tout import interne
load_dotenv()


def _check_real_trading_guard() -> None:
    """Refuse de démarrer si une configuration dangereuse est détectée."""
    import os

    trading_mode = os.getenv("TRADING_MODE", "paper").lower()
    enable_real = os.getenv("ENABLE_REAL_TRADING", "false").lower()

    if trading_mode == "real" or enable_real == "true":
        click.echo(
            "\n[ARGOS] ERREUR DE SÉCURITÉ : trading réel détecté dans la configuration.\n"
            "  TRADING_MODE doit être 'paper'.\n"
            "  ENABLE_REAL_TRADING doit être 'false'.\n"
            "\nARGOS refuse de démarrer. Corrigez votre fichier .env.\n",
            err=True,
        )
        sys.exit(1)


@click.group()
@click.version_option(version="0.1.0", prog_name="ARGOS")
def cli() -> None:
    """ARGOS — Autonomous Risk-Governed Optimization System.

    Système multi-agents de paper trading et d'apprentissage continu.
    Mode paper trading uniquement par défaut.
    """


@cli.command("init-db")
def init_db() -> None:
    """Initialise la base de données SQLite et crée toutes les tables."""
    _check_real_trading_guard()

    click.echo("[ARGOS] Initialisation de la base de données...")

    try:
        from argos.database.db import init_database

        init_database()
        click.echo("[ARGOS] Base de données initialisée avec succès.")
        click.echo(f"[ARGOS] Fichier : {_get_db_path()}")
    except Exception as e:
        click.echo(f"[ARGOS] Erreur lors de l'initialisation : {e}", err=True)
        sys.exit(1)


@cli.command("run-paper")
def run_paper() -> None:
    """Lance le bot en mode paper trading continu (boucle autonome)."""
    _check_real_trading_guard()

    click.echo("[ARGOS] Démarrage en mode paper trading...")
    click.echo("[ARGOS] Appuyez sur CTRL+C pour arrêter proprement.\n")

    try:
        from argos.scheduler import start_scheduler

        start_scheduler()
    except KeyboardInterrupt:
        click.echo("\n[ARGOS] Arrêt demandé. Fermeture propre en cours...")
    except Exception as e:
        click.echo(f"[ARGOS] Erreur critique : {e}", err=True)
        sys.exit(1)


@cli.command("status")
def status() -> None:
    """Affiche l'état actuel du système (capital, positions, derniers trades)."""
    _check_real_trading_guard()

    click.echo("[ARGOS] Récupération du statut...")

    try:
        from argos.database.db import get_session
        from argos.database.repositories import (
            get_open_positions,
            get_portfolio_summary,
            get_recent_trades,
            get_last_market_snapshot,
        )

        with get_session() as session:
            summary = get_portfolio_summary(session)
            positions = get_open_positions(session)
            recent_trades = get_recent_trades(session, limit=5)
            btc_snap = get_last_market_snapshot(session, "BTC")
            eth_snap = get_last_market_snapshot(session, "ETH")

            # Extraire les valeurs pendant que la session est ouverte
            n_positions = len(positions)
            btc_info = (btc_snap.price, str(btc_snap.timestamp)) if btc_snap else None
            eth_info = (eth_snap.price, str(eth_snap.timestamp)) if eth_snap else None
            trades_info = [
                (t.asset, str(t.direction), t.net_pnl or 0.0, str(t.result))
                for t in recent_trades
            ]

        click.echo("\n" + "=" * 50)
        click.echo("  ARGOS — Statut du système")
        click.echo("=" * 50)

        click.echo(f"\n  Mode           : PAPER TRADING")
        click.echo(f"  Capital fictif : {summary.get('capital_eur', 0):.2f} €")
        click.echo(f"  P&L total      : {summary.get('total_pnl', 0):+.2f} €")
        click.echo(f"  Trades total   : {summary.get('total_trades', 0)}")
        click.echo(f"  Win rate       : {summary.get('win_rate', 0):.1f}%")
        click.echo(f"  Positions ouv. : {n_positions}")

        if btc_info:
            click.echo(f"\n  BTC : {btc_info[0]:,.0f} €  ({btc_info[1]})")
        if eth_info:
            click.echo(f"  ETH : {eth_info[0]:,.0f} €  ({eth_info[1]})")

        if trades_info:
            click.echo("\n  Derniers trades :")
            for asset, direction, net_pnl, result in trades_info:
                sign = "+" if net_pnl >= 0 else ""
                click.echo(f"    {asset} {direction:<5} {sign}{net_pnl:.4f} €  [{result}]")

        click.echo("\n" + "=" * 50)

    except Exception as e:
        click.echo(f"[ARGOS] Impossible de récupérer le statut : {e}", err=True)
        click.echo("[ARGOS] Avez-vous lancé 'python -m argos.main init-db' ?", err=True)
        sys.exit(1)


@cli.command("weekly-report")
def weekly_report() -> None:
    """Génère le rapport hebdomadaire d'apprentissage (Markdown + CSV)."""
    _check_real_trading_guard()

    click.echo("[ARGOS] Génération du rapport hebdomadaire...")

    try:
        from argos.learning.weekly_report import generate_weekly_report

        report_path = generate_weekly_report()
        click.echo(f"[ARGOS] Rapport généré : {report_path}")
    except Exception as e:
        click.echo(f"[ARGOS] Erreur lors de la génération du rapport : {e}", err=True)
        sys.exit(1)


@cli.command("backtest")
@click.option(
    "--strategy",
    required=True,
    help="Nom de la stratégie à tester (ex: strategy_v1)",
)
@click.option(
    "--asset",
    required=True,
    type=click.Choice(["BTC", "ETH", "SOL"], case_sensitive=False),
    help="Actif à backtester",
)
@click.option(
    "--days",
    default=90,
    show_default=True,
    help="Nombre de jours d'historique à utiliser",
)
def backtest(strategy: str, asset: str, days: int) -> None:
    """Backteste une stratégie sur données historiques."""
    _check_real_trading_guard()

    click.echo(f"[ARGOS] Backtesting : {strategy} sur {asset} ({days} jours)...")

    try:
        from argos.backtesting.backtester import run_backtest

        result = run_backtest(strategy_name=strategy, asset=asset.upper(), days=days)

        click.echo("\n" + "=" * 50)
        click.echo(f"  Résultats — {strategy} / {asset}")
        click.echo("=" * 50)
        click.echo(f"  Trades        : {result.total_trades}")
        click.echo(f"  Win rate      : {result.win_rate:.1f}%")
        click.echo(f"  Profit factor : {result.profit_factor:.2f}")
        click.echo(f"  Rendement     : {result.return_percent:+.2f}%")
        click.echo(f"  Max drawdown  : {result.max_drawdown_percent:.2f}%")
        click.echo(f"  Après frais   : {result.net_return_percent:+.2f}%")
        click.echo("=" * 50)

    except Exception as e:
        click.echo(f"[ARGOS] Erreur lors du backtesting : {e}", err=True)
        sys.exit(1)


@cli.command("dashboard")
def dashboard() -> None:
    """Lance le dashboard Streamlit de monitoring."""
    _check_real_trading_guard()

    import subprocess

    dashboard_path = Path(__file__).parent / "dashboard" / "app.py"
    click.echo(f"[ARGOS] Lancement du dashboard : {dashboard_path}")
    click.echo("[ARGOS] Ouvrez http://localhost:8501 dans votre navigateur.")

    try:
        subprocess.run(
            ["streamlit", "run", str(dashboard_path)],
            check=True,
        )
    except KeyboardInterrupt:
        click.echo("\n[ARGOS] Dashboard arrêté.")
    except Exception as e:
        click.echo(f"[ARGOS] Erreur : {e}", err=True)
        sys.exit(1)


@cli.command("export-logs")
@click.option(
    "--output",
    default=None,
    help="Dossier de destination (défaut : exports/)",
)
def export_logs(output: str | None) -> None:
    """Exporte les trades, signaux et décisions en CSV."""
    _check_real_trading_guard()

    click.echo("[ARGOS] Export des données...")

    try:
        from argos.database.repositories import export_all_to_csv
        import os

        dest = output or os.getenv("EXPORTS_PATH", "exports")
        files = export_all_to_csv(Path(dest))

        click.echo(f"[ARGOS] {len(files)} fichiers exportés dans '{dest}' :")
        for f in files:
            click.echo(f"  - {f}")
    except Exception as e:
        click.echo(f"[ARGOS] Erreur lors de l'export : {e}", err=True)
        sys.exit(1)


def _get_db_path() -> str:
    import os

    return os.getenv("DATABASE_PATH", "data/argos.db")


if __name__ == "__main__":
    cli()
