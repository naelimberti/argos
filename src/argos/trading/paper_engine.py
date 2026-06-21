"""
Paper Trading Engine — moteur de simulation de trades ARGOS.

Responsabilités :
  - Ouvrir un trade simulé si toutes les règles sont satisfaites
  - Surveiller les positions ouvertes (SL / TP / timeout)
  - Clôturer les positions et enregistrer les résultats
  - Calculer le P&L net (frais + spread + slippage)
  - Mettre à jour le portefeuille fictif

Ce module ne touche JAMAIS à de l'argent réel.
Toute tentative d'activation du mode réel est bloquée ici.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import yaml

from argos.database.models import (
    PaperTrade,
    RiskDecisionLog,
    TradeDirection,
    TradeResult,
    TradeStatus,
    RiskDecision,
)
from argos.database.repositories import (
    get_consecutive_losses,
    get_daily_pnl,
    get_open_positions,
    count_trades_today,
    save_paper_trade,
    save_risk_decision,
    update_paper_trade,
)
from argos.trading.portfolio import Portfolio
from argos.utils.logger import get_logger

logger = get_logger("paper_engine")


# ============================================================
# Paramètres d'un trade proposé
# ============================================================

@dataclass
class TradeRequest:
    """Ce que le Strategy Agent propose. Le PaperEngine valide et exécute."""

    asset: str                      # "BTC", "ETH"
    direction: TradeDirection
    entry_price: float              # prix d'entrée théorique
    stop_loss: float                # prix de stop-loss — OBLIGATOIRE
    take_profit: float              # prix de take-profit — OBLIGATOIRE
    position_size_eur: float        # montant à engager en EUR
    strategy_version: str = "strategy_v1_production"
    timeframe: Optional[str] = None
    confidence_score: float = 0.0
    reason_entry: Optional[str] = None
    signals_favorable: Optional[str] = None    # JSON string
    signals_unfavorable: Optional[str] = None  # JSON string


@dataclass
class TradeResult_:
    """Résultat retourné après ouverture ou refus d'un trade."""
    accepted: bool
    trade_id: Optional[int] = None
    rejection_reason: Optional[str] = None


# ============================================================
# Règles de risque chargées depuis la config
# ============================================================

_RULES_CACHE: dict = {}
_RULES_MTIME: float = 0.0


def _load_risk_rules() -> dict:
    """Lit risk_rules.yaml une seule fois, recharge si le fichier change sur disque."""
    global _RULES_CACHE, _RULES_MTIME
    import os
    try:
        mtime = os.path.getmtime("config/risk_rules.yaml")
        if mtime != _RULES_MTIME:
            with open("config/risk_rules.yaml", encoding="utf-8") as f:
                _RULES_CACHE = yaml.safe_load(f) or {}
            _RULES_MTIME = mtime
    except Exception:
        pass
    return _RULES_CACHE


def _get_paper_rules(rules: dict) -> dict:
    return rules.get("paper_trading", {})


def _get_market_filters(rules: dict) -> dict:
    return rules.get("market_filters", {})


def _get_fee_config(rules: dict) -> dict:
    return rules.get("fees", {})


# ============================================================
# Paper Trading Engine
# ============================================================

class PaperTradingEngine:
    """
    Moteur central de simulation.

    Usage typique (dans le scheduler) :
        engine = PaperTradingEngine(portfolio, session)
        result = engine.open_trade(trade_request)
        engine.monitor_open_positions(current_prices)
    """

    def __init__(self, portfolio: Portfolio) -> None:
        self.portfolio = portfolio
        self._guard_real_trading()

    def _guard_real_trading(self) -> None:
        """Bloque toute tentative d'activation du mode réel."""
        mode = os.getenv("TRADING_MODE", "paper").lower()
        real = os.getenv("ENABLE_REAL_TRADING", "false").lower()
        if mode != "paper" or real == "true":
            raise RuntimeError(
                "SECURITE : PaperTradingEngine refuse de demarrer en mode reel. "
                "Verifiez TRADING_MODE=paper et ENABLE_REAL_TRADING=false dans .env"
            )

    # ------------------------------------------------------------------
    # Ouverture d'un trade
    # ------------------------------------------------------------------

    def open_trade(self, request: TradeRequest, session) -> TradeResult_:
        """
        Tente d'ouvrir un trade simulé.

        Effectue toutes les vérifications de risque dans l'ordre.
        Si une règle est violée, le trade est refusé et enregistré.
        Retourne TradeResult_ avec accepted=True/False.
        """
        rules = _load_risk_rules()
        paper = _get_paper_rules(rules)

        # Vérifications dans l'ordre de criticité
        checks = [
            self._check_sl_tp_present(request),
            self._check_no_leverage(request, paper),
            self._check_single_position(session, paper),
            self._check_max_trades_per_day(session, paper),
            self._check_daily_loss_limit(session, paper),
            self._check_consecutive_losses(session, paper),
            self._check_position_size(request, paper),
            self._check_risk_reward(request, paper),
            self._check_capital_available(request),
        ]

        for rejection_reason, rule_name in checks:
            if rejection_reason:
                self._log_rejection(request, rejection_reason, rule_name, session)
                return TradeResult_(accepted=False, rejection_reason=rejection_reason)

        # Toutes les vérifications passées — on ouvre le trade
        trade = self._create_trade(request, rules)
        save_paper_trade(session, trade)

        logger.info(
            f"[TRADE OUVERT] {trade.asset} {trade.direction.value} "
            f"@ {trade.entry_price:,.2f} EUR | "
            f"SL={trade.stop_loss:,.2f} | TP={trade.take_profit:,.2f} | "
            f"Taille={trade.position_size_eur:.4f} EUR"
        )

        self._log_acceptance(request, trade, session)
        return TradeResult_(accepted=True, trade_id=trade.id)

    # ------------------------------------------------------------------
    # Surveillance des positions ouvertes
    # ------------------------------------------------------------------

    def monitor_open_positions(
        self,
        current_prices: dict[str, float],
        session,
        max_trade_duration_hours: int = 48,
    ) -> list[PaperTrade]:
        """
        Vérifie toutes les positions ouvertes contre les prix actuels.

        Clôture si :
          - stop-loss atteint
          - take-profit atteint
          - durée maximale dépassée (timeout)

        Retourne la liste des trades clôturés durant cet appel.
        """
        open_trades = get_open_positions(session)
        closed_now: list[PaperTrade] = []

        for trade in open_trades:
            price = current_prices.get(trade.asset)
            if price is None:
                logger.warning(f"Prix introuvable pour {trade.asset} — position ignorée.")
                continue

            closed = self._check_and_close(trade, price, session, max_trade_duration_hours)
            if closed:
                closed_now.append(trade)

        return closed_now

    # ------------------------------------------------------------------
    # Logique de clôture
    # ------------------------------------------------------------------

    def _check_and_close(
        self,
        trade: PaperTrade,
        current_price: float,
        session,
        max_hours: int,
    ) -> bool:
        """Retourne True si le trade a été clôturé."""
        now = datetime.utcnow()

        # Timeout
        entry = trade.entry_time
        if entry:
            entry_naive = entry.replace(tzinfo=None) if entry.tzinfo is not None else entry
            age_hours = (now - entry_naive).total_seconds() / 3600
            if age_hours >= max_hours:
                self._close_trade(trade, current_price, TradeResult.TIMEOUT, session)
                return True

        if trade.direction == TradeDirection.LONG:
            if current_price <= trade.stop_loss:
                self._close_trade(trade, trade.stop_loss, TradeResult.LOSS, session)
                return True
            if current_price >= trade.take_profit:
                self._close_trade(trade, trade.take_profit, TradeResult.WIN, session)
                return True
        else:  # SHORT
            if current_price >= trade.stop_loss:
                self._close_trade(trade, trade.stop_loss, TradeResult.LOSS, session)
                return True
            if current_price <= trade.take_profit:
                self._close_trade(trade, trade.take_profit, TradeResult.WIN, session)
                return True

        return False

    def _close_trade(
        self,
        trade: PaperTrade,
        exit_price: float,
        result: TradeResult,
        session,
    ) -> None:
        """Clôture un trade et met à jour le portefeuille."""
        now = datetime.utcnow()

        # P&L brut
        if trade.direction == TradeDirection.LONG:
            gross_pnl = (exit_price - trade.entry_price) / trade.entry_price * trade.position_size_eur
        else:
            gross_pnl = (trade.entry_price - exit_price) / trade.entry_price * trade.position_size_eur

        total_friction = (trade.fees_estimated or 0) + (trade.spread_estimated or 0) + (trade.slippage_estimated or 0)
        net_pnl = gross_pnl - total_friction
        return_pct = net_pnl / trade.position_size_eur * 100 if trade.position_size_eur else 0

        # Mise à jour du trade
        trade.exit_price = exit_price
        trade.exit_time = now
        trade.gross_pnl = round(gross_pnl, 6)
        trade.net_pnl = round(net_pnl, 6)
        trade.return_percent = round(return_pct, 4)
        trade.result = result
        trade.status = TradeStatus.CLOSED
        trade.reason_exit = result.value

        update_paper_trade(session, trade)

        # Mise à jour du portefeuille
        self.portfolio.apply_trade_result(
            net_pnl=net_pnl,
            fees=trade.fees_estimated or 0,
            won=(result == TradeResult.WIN),
        )

        emoji = "[WIN] " if result == TradeResult.WIN else "[LOSS]" if result == TradeResult.LOSS else "[TIME]"
        logger.info(
            f"{emoji} {trade.asset} {trade.direction.value} cloture "
            f"@ {exit_price:,.2f} EUR | "
            f"Brut={gross_pnl:+.4f} EUR | Net={net_pnl:+.4f} EUR | "
            f"Raison={result.value}"
        )

    # ------------------------------------------------------------------
    # Création du trade (après validation)
    # ------------------------------------------------------------------

    def _create_trade(self, request: TradeRequest, rules: dict) -> PaperTrade:
        """Construit l'objet PaperTrade avec frictions estimées."""
        fees_cfg = _get_fee_config(rules)

        taker_fee_pct = fees_cfg.get("taker_fee_percent", 0.10) / 100
        slippage_pct = fees_cfg.get("slippage_estimate_percent", 0.05) / 100
        spread_pct = fees_cfg.get("default_spread_percent", 0.10) / 100

        fees = request.position_size_eur * taker_fee_pct * 2        # entrée + sortie
        slippage = request.position_size_eur * slippage_pct
        spread = request.position_size_eur * spread_pct

        return PaperTrade(
            strategy_version=request.strategy_version,
            asset=request.asset,
            direction=request.direction,
            timeframe=request.timeframe,
            entry_price=request.entry_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            position_size_eur=request.position_size_eur,
            position_size_asset=request.position_size_eur / request.entry_price,
            fees_estimated=round(fees, 6),
            spread_estimated=round(spread, 6),
            slippage_estimated=round(slippage, 6),
            status=TradeStatus.OPEN,
            result=TradeResult.OPEN,
            confidence_score=request.confidence_score,
            reason_entry=request.reason_entry,
            signals_favorable=request.signals_favorable,
            signals_unfavorable=request.signals_unfavorable,
        )

    # ------------------------------------------------------------------
    # Vérifications de risque — chacune retourne (raison|None, nom_règle)
    # ------------------------------------------------------------------

    def _check_sl_tp_present(self, req: TradeRequest) -> tuple[Optional[str], str]:
        if not req.stop_loss or req.stop_loss <= 0:
            return "Stop-loss absent ou invalide — trade refuse", "stop_loss_required"
        if not req.take_profit or req.take_profit <= 0:
            return "Take-profit absent ou invalide — trade refuse", "take_profit_required"

        # Vérifier la cohérence direction / SL / TP
        if req.direction == TradeDirection.LONG:
            if req.stop_loss >= req.entry_price:
                return (
                    f"LONG : stop-loss ({req.stop_loss}) doit etre sous le prix d'entree ({req.entry_price})",
                    "sl_below_entry_long",
                )
            if req.take_profit <= req.entry_price:
                return (
                    f"LONG : take-profit ({req.take_profit}) doit etre au-dessus du prix d'entree ({req.entry_price})",
                    "tp_above_entry_long",
                )
        else:  # SHORT
            if req.stop_loss <= req.entry_price:
                return (
                    f"SHORT : stop-loss ({req.stop_loss}) doit etre au-dessus du prix d'entree ({req.entry_price})",
                    "sl_above_entry_short",
                )
            if req.take_profit >= req.entry_price:
                return (
                    f"SHORT : take-profit ({req.take_profit}) doit etre sous le prix d'entree ({req.entry_price})",
                    "tp_below_entry_short",
                )
        return None, ""

    def _check_no_leverage(self, req: TradeRequest, paper: dict) -> tuple[Optional[str], str]:
        if paper.get("allow_leverage", False):
            return None, ""
        if req.position_size_eur > self.portfolio.current_capital:
            return (
                f"Levier interdit : taille {req.position_size_eur:.4f} EUR > "
                f"capital {self.portfolio.current_capital:.4f} EUR",
                "allow_leverage",
            )
        return None, ""

    def _check_single_position(self, session, paper: dict) -> tuple[Optional[str], str]:
        max_pos = paper.get("max_open_positions", 1)
        open_pos = get_open_positions(session)
        if len(open_pos) >= max_pos:
            assets = [p.asset for p in open_pos]
            return (
                f"Position deja ouverte sur {assets} — {max_pos} positions max autorisees",
                "max_open_positions",
            )
        return None, ""

    def _check_max_trades_per_day(self, session, paper: dict) -> tuple[Optional[str], str]:
        max_trades = paper.get("max_trades_per_day", 5)
        today_count = count_trades_today(session)
        if today_count >= max_trades:
            return (
                f"Limite journaliere atteinte : {today_count}/{max_trades} trades aujourd'hui",
                "max_trades_per_day",
            )
        return None, ""

    def _check_daily_loss_limit(self, session, paper: dict) -> tuple[Optional[str], str]:
        max_loss_pct = paper.get("max_daily_loss_percent", 5.0)
        max_loss_eur = self.portfolio.initial_capital * max_loss_pct / 100
        daily_pnl = get_daily_pnl(session)
        if daily_pnl <= -max_loss_eur:
            return (
                f"Perte journaliere maximum atteinte : {daily_pnl:.4f} EUR "
                f"(limite={-max_loss_eur:.4f} EUR = {max_loss_pct}% du capital)",
                "max_daily_loss_percent",
            )
        return None, ""

    def _check_consecutive_losses(self, session, paper: dict) -> tuple[Optional[str], str]:
        max_consec = paper.get("max_consecutive_losses", 2)
        consec = get_consecutive_losses(session)
        if consec >= max_consec:
            cooldown = paper.get("cooldown_after_loss_minutes", 60)
            return (
                f"Arret apres {consec} pertes consecutives (max={max_consec}). "
                f"Cooldown de {cooldown} min.",
                "max_consecutive_losses",
            )
        return None, ""

    def _check_position_size(self, req: TradeRequest, paper: dict) -> tuple[Optional[str], str]:
        max_pct = paper.get("max_position_size_percent", 20.0)
        max_size = self.portfolio.current_capital * max_pct / 100
        if req.position_size_eur > round(max_size + 0.005, 2):  # tolérance arrondi centimes
            return (
                f"Taille de position trop grande : {req.position_size_eur:.4f} EUR > "
                f"{max_size:.4f} EUR ({max_pct}% du capital)",
                "max_position_size_percent",
            )
        return None, ""

    def _check_risk_reward(self, req: TradeRequest, paper: dict) -> tuple[Optional[str], str]:
        min_rr = paper.get("min_risk_reward_ratio", 2.0)

        if req.direction == TradeDirection.LONG:
            risk = req.entry_price - req.stop_loss
            reward = req.take_profit - req.entry_price
        else:
            risk = req.stop_loss - req.entry_price
            reward = req.entry_price - req.take_profit

        if risk <= 0:
            return "Risque nul ou negatif — calcul R/R impossible", "risk_reward_ratio"

        rr = reward / risk
        if rr < min_rr:
            return (
                f"Ratio risque/rendement insuffisant : {rr:.2f} < {min_rr} (minimum requis)",
                "min_risk_reward_ratio",
            )
        return None, ""

    def _check_capital_available(self, req: TradeRequest) -> tuple[Optional[str], str]:
        if req.position_size_eur <= 0:
            return "Taille de position nulle ou negative", "position_size_positive"
        if req.position_size_eur > self.portfolio.current_capital:
            return (
                f"Capital insuffisant : {req.position_size_eur:.4f} EUR demandes, "
                f"{self.portfolio.current_capital:.4f} EUR disponibles",
                "capital_available",
            )
        return None, ""

    # ------------------------------------------------------------------
    # Enregistrement des décisions Risk Manager
    # ------------------------------------------------------------------

    def _log_rejection(
        self,
        request: TradeRequest,
        reason: str,
        rule: str,
        session,
    ) -> None:
        log = RiskDecisionLog(
            asset=request.asset,
            direction=request.direction.value,
            proposed_entry=request.entry_price,
            proposed_stop_loss=request.stop_loss,
            proposed_take_profit=request.take_profit,
            decision=RiskDecision.REJECTED,
            reason=reason,
            rule_triggered=rule,
        )
        save_risk_decision(session, log)
        logger.warning(f"[REFUSE] {request.asset} {request.direction.value} — {reason}")

    def _log_acceptance(
        self,
        request: TradeRequest,
        trade: PaperTrade,
        session,
    ) -> None:
        log = RiskDecisionLog(
            asset=request.asset,
            direction=request.direction.value,
            proposed_entry=request.entry_price,
            proposed_stop_loss=request.stop_loss,
            proposed_take_profit=request.take_profit,
            trade_id=trade.id,
            decision=RiskDecision.APPROVED,
            reason="Toutes les regles de risque validees",
        )
        save_risk_decision(session, log)
