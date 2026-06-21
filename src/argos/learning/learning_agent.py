"""
Learning Agent ARGOS.

Analyse chaque trade clôturé et produit :
  1. Une classification (TradeCategory) — ce qui s'est vraiment passé
  2. Une explication lisible — pourquoi ce résultat
  3. Une leçon — ce qu'on en retient
  4. Une proposition d'amélioration — comment éviter cet échec / reproduire ce succès

Toutes les analyses sont déterministes et basées uniquement sur les données du trade.
Le LearningAgent ne génère jamais de signaux ni de trades.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from argos.database.models import (
    LearningNote,
    PaperTrade,
    TradeCategory,
    TradeDirection,
    TradeResult,
    TradeStatus,
)
from argos.database.repositories import save_learning_note, update_paper_trade
from argos.utils.logger import get_logger

logger = get_logger("learning_agent")


# ============================================================
# Résultat d'une analyse
# ============================================================

@dataclass
class TradeAnalysis:
    """Résultat complet de l'analyse d'un trade."""
    trade_id: int
    category: TradeCategory
    main_error: Optional[str]          # Court (1 ligne) — ce qui a failli
    lesson: str                        # Ce qu'on retient
    improvement: Optional[str]         # Comment faire mieux
    signal_quality: str                # "good" | "neutral" | "bad"
    key_metrics: dict                  # Métriques calculées pendant l'analyse


# ============================================================
# Learning Agent
# ============================================================

class LearningAgent:
    """
    Analyse un trade clôturé et produit une note d'apprentissage.

    Usage :
        agent = LearningAgent()
        note = agent.analyze(trade, session)
    """

    def analyze(self, trade: PaperTrade, session) -> Optional[LearningNote]:
        """
        Analyse un trade clôturé, enrichit ses champs de bilan,
        et enregistre une LearningNote en base.

        Retourne None si le trade n'est pas clôturé ou si les données sont insuffisantes.
        """
        if trade.status != TradeStatus.CLOSED:
            logger.debug(f"Trade #{trade.id} non clôturé — analyse ignorée.")
            return None

        if trade.result not in (
            TradeResult.WIN, TradeResult.LOSS,
            TradeResult.TIMEOUT, TradeResult.BREAKEVEN,
        ):
            logger.debug(f"Trade #{trade.id} résultat {trade.result} — analyse ignorée.")
            return None

        analysis = self._run_analysis(trade)
        self._update_trade_fields(trade, analysis, session)
        note = self._save_note(trade, analysis, session)

        logger.info(
            f"[LEARNING] Trade #{trade.id} — {analysis.category.value} | "
            f"Signal: {analysis.signal_quality} | "
            f"{analysis.lesson[:80]}..."
            if len(analysis.lesson) > 80 else
            f"[LEARNING] Trade #{trade.id} — {analysis.category.value} | "
            f"Signal: {analysis.signal_quality} | {analysis.lesson}"
        )

        return note

    # ------------------------------------------------------------------
    # Analyse principale
    # ------------------------------------------------------------------

    def _run_analysis(self, trade: PaperTrade) -> TradeAnalysis:
        metrics = self._compute_metrics(trade)
        category = self._classify(trade, metrics)
        main_error = self._describe_error(trade, category, metrics)
        lesson = self._write_lesson(trade, category, metrics)
        improvement = self._write_improvement(trade, category, metrics)
        signal_quality = self._assess_signal_quality(trade, category, metrics)

        return TradeAnalysis(
            trade_id=trade.id,
            category=category,
            main_error=main_error,
            lesson=lesson,
            improvement=improvement,
            signal_quality=signal_quality,
            key_metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Calcul des métriques du trade
    # ------------------------------------------------------------------

    def _compute_metrics(self, trade: PaperTrade) -> dict:
        entry = trade.entry_price or 1.0
        sl = trade.stop_loss or 0.0
        tp = trade.take_profit or 0.0
        exit_price = trade.exit_price or entry
        size = trade.position_size_eur or 0.0
        gross = trade.gross_pnl or 0.0
        net = trade.net_pnl or 0.0
        fees = trade.fees_estimated or 0.0
        spread = trade.spread_estimated or 0.0
        slippage = trade.slippage_estimated or 0.0
        friction = fees + spread + slippage
        conf = trade.confidence_score or 0.0

        # R/R attendu à l'entrée
        if trade.direction == TradeDirection.LONG:
            risk_price = entry - sl
            reward_price = tp - entry
            move_pct = (exit_price - entry) / entry * 100
        else:
            risk_price = sl - entry
            reward_price = entry - tp
            move_pct = (entry - exit_price) / entry * 100

        rr_planned = reward_price / risk_price if risk_price > 0 else 0.0
        sl_distance_pct = risk_price / entry * 100 if entry > 0 else 0.0
        tp_distance_pct = reward_price / entry * 100 if entry > 0 else 0.0

        # R/R réalisé (fraction du TP atteinte)
        rr_achieved = move_pct / tp_distance_pct if tp_distance_pct > 0 else 0.0

        # Friction relative
        friction_pct_of_size = friction / size * 100 if size > 0 else 0.0
        friction_vs_gross = abs(friction / gross) if gross != 0 else float("inf")

        # Durée du trade
        if trade.exit_time and trade.entry_time:
            entry_time = trade.entry_time
            exit_time = trade.exit_time
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            if exit_time.tzinfo is None:
                exit_time = exit_time.replace(tzinfo=timezone.utc)
            duration_hours = (exit_time - entry_time).total_seconds() / 3600
        else:
            duration_hours = 0.0

        return {
            "gross_pnl": gross,
            "net_pnl": net,
            "friction": friction,
            "fees": fees,
            "spread": spread,
            "slippage": slippage,
            "friction_pct_of_size": friction_pct_of_size,
            "friction_vs_gross": friction_vs_gross,
            "rr_planned": rr_planned,
            "rr_achieved": rr_achieved,
            "sl_distance_pct": sl_distance_pct,
            "tp_distance_pct": tp_distance_pct,
            "move_pct": move_pct,
            "confidence": conf,
            "duration_hours": duration_hours,
            "size_eur": size,
        }

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify(self, trade: PaperTrade, m: dict) -> TradeCategory:
        result = trade.result

        if result == TradeResult.WIN:
            return self._classify_win(trade, m)

        if result == TradeResult.TIMEOUT:
            return TradeCategory.AVOIDABLE_LOSS

        if result in (TradeResult.LOSS, TradeResult.BREAKEVEN):
            return self._classify_loss(trade, m)

        return TradeCategory.NORMAL_LOSS

    def _classify_win(self, trade: PaperTrade, m: dict) -> TradeCategory:
        # Fees ont consommé plus de 40% du profit brut → victoire coûteuse
        if m["friction_vs_gross"] > 0.4 and m["gross_pnl"] > 0:
            return TradeCategory.LUCKY_WIN

        # Confiance faible → signal douteux, résultat chanceux
        if m["confidence"] < 0.72:
            return TradeCategory.LUCKY_WIN

        # TP atteint trop vite (moins de 20% de la durée attendue) → lucky
        if m["sl_distance_pct"] > 0 and m["duration_hours"] < 1.0 and m["rr_achieved"] >= 1.0:
            return TradeCategory.CLEAN_WIN  # rapide mais propre

        # R/R planifié respecté et confiance suffisante → victoire propre
        if m["rr_planned"] >= 2.0 and m["confidence"] >= 0.75:
            return TradeCategory.CLEAN_WIN

        return TradeCategory.LUCKY_WIN

    def _classify_loss(self, trade: PaperTrade, m: dict) -> TradeCategory:
        # Profit brut positif ou nul, mais net négatif → frictions responsables
        if m["friction"] > 0 and m["gross_pnl"] >= 0 and m["net_pnl"] < 0:
            # Identifier le contributeur dominant
            if m["spread"] > m["fees"] + m["slippage"]:
                return TradeCategory.DESTROYED_BY_SPREAD
            return TradeCategory.DESTROYED_BY_FEES

        # Le spread seul a absorbé la quasi-totalité du mouvement
        if m["spread"] > 0 and m["gross_pnl"] >= -m["spread"] * 1.5 and m["net_pnl"] < 0:
            return TradeCategory.DESTROYED_BY_SPREAD

        # Stop-loss trop serré (< 1%) → stop chassé par le bruit de marché
        if 0 < m["sl_distance_pct"] < 1.0:
            return TradeCategory.AVOIDABLE_LOSS

        # Perte évitable si le trade a été ignoré malgré des signaux défavorables connus
        if trade.signals_unfavorable and m["confidence"] < 0.72:
            return TradeCategory.AVOIDABLE_LOSS

        return TradeCategory.NORMAL_LOSS

    # ------------------------------------------------------------------
    # Explication de l'erreur
    # ------------------------------------------------------------------

    def _describe_error(
        self, trade: PaperTrade, category: TradeCategory, m: dict
    ) -> Optional[str]:
        if category == TradeCategory.CLEAN_WIN:
            return None  # Pas d'erreur

        errors = {
            TradeCategory.LUCKY_WIN: (
                f"Signal de confiance faible ({m['confidence']:.0%}) — "
                f"la victoire doit etre reproduite avec plus de conviction"
                if m["confidence"] < 0.75 else
                f"Frais eleves ({m['friction_pct_of_size']:.2f}% de la position) "
                f"ont greve le profit brut de {m['friction_vs_gross']:.0%}"
            ),
            TradeCategory.NORMAL_LOSS: (
                f"SL touche apres un mouvement de {abs(m['move_pct']):.2f}% "
                f"contre la position — perte dans les limites prevues "
                f"({m['sl_distance_pct']:.2f}% de risque)"
            ),
            TradeCategory.AVOIDABLE_LOSS: (
                f"SL trop serre ({m['sl_distance_pct']:.2f}%) — "
                f"stoppe par le bruit de marche avant le vrai mouvement"
                if m["sl_distance_pct"] < 1.0 else
                f"Trade maintenu {m['duration_hours']:.1f}h sans atteindre SL ni TP — "
                f"cloture forcee par timeout"
                if trade.result == TradeResult.TIMEOUT else
                f"Signaux defavorables ignores (confiance {m['confidence']:.0%})"
            ),
            TradeCategory.DESTROYED_BY_FEES: (
                f"Frais totaux ({m['friction']:.4f} EUR = {m['friction_pct_of_size']:.2f}% "
                f"de la position) ont converti un P&L brut de {m['gross_pnl']:+.4f} EUR "
                f"en perte nette de {m['net_pnl']:+.4f} EUR"
            ),
            TradeCategory.DESTROYED_BY_SPREAD: (
                f"Spread estime ({m['spread']:.4f} EUR) a absorbe la quasi-totalite "
                f"du profit brut ({m['gross_pnl']:+.4f} EUR)"
            ),
        }
        return errors.get(category)

    # ------------------------------------------------------------------
    # Leçon
    # ------------------------------------------------------------------

    def _write_lesson(
        self, trade: PaperTrade, category: TradeCategory, m: dict
    ) -> str:
        asset = trade.asset
        dir_str = trade.direction.value

        lessons = {
            TradeCategory.CLEAN_WIN: (
                f"{asset} {dir_str} : trade execute selon le plan. "
                f"R/R prevu={m['rr_planned']:.2f}, confiance={m['confidence']:.0%}. "
                f"Gain net={m['net_pnl']:+.4f} EUR. "
                f"Reproduire ce profil de setup a l'avenir."
            ),
            TradeCategory.LUCKY_WIN: (
                f"{asset} {dir_str} : victoire obtenue malgre un signal de qualite moyenne. "
                f"Confiance={m['confidence']:.0%}, frais={m['friction_pct_of_size']:.2f}% de la position. "
                f"Ne pas sur-ponderer ce trade dans l'evaluation de la strategie — "
                f"il ne serait pas reproductible systematiquement."
            ),
            TradeCategory.NORMAL_LOSS: (
                f"{asset} {dir_str} : perte normale dans le cadre du systeme. "
                f"SL a {m['sl_distance_pct']:.2f}%, TP a {m['tp_distance_pct']:.2f}%, "
                f"R/R prevu={m['rr_planned']:.2f}. "
                f"Le marche s'est deplace de {abs(m['move_pct']):.2f}% dans le mauvais sens. "
                f"Aucune regle violee — accepter la perte et passer au prochain setup."
            ),
            TradeCategory.AVOIDABLE_LOSS: (
                f"{asset} {dir_str} : perte qui aurait pu etre evitee. "
                + (
                    f"Stop-loss trop serre ({m['sl_distance_pct']:.2f}%) — "
                    f"le marche a besoin de plus d'espace pour respirer avant de bouger."
                    if m["sl_distance_pct"] < 1.0 else
                    f"Trade ouvert {m['duration_hours']:.1f}h sans resolution — "
                    f"envisager un TP partiel ou une sortie manuelle apres 24h."
                    if trade.result == TradeResult.TIMEOUT else
                    f"Signaux contradictoires ignores. "
                    f"Confiance {m['confidence']:.0%} en dessous du seuil recommande."
                )
            ),
            TradeCategory.DESTROYED_BY_FEES: (
                f"{asset} {dir_str} : les frais de transaction ({m['friction']:.4f} EUR) "
                f"representent {m['friction_pct_of_size']:.2f}% de la position — "
                f"trop eleves pour un mouvement aussi faible ({abs(m['move_pct']):.2f}%). "
                f"Augmenter la cible de TP ou la taille de position pour amortir les frais. "
                f"Breakeven minimum = {m['friction_pct_of_size']:.2f}% de mouvement favorable."
            ),
            TradeCategory.DESTROYED_BY_SPREAD: (
                f"{asset} {dir_str} : spread estime ({m['spread']:.4f} EUR) "
                f"disproportionne par rapport au mouvement capture ({abs(m['move_pct']):.2f}%). "
                f"Eviter les trades sur des marchés a faible liquidite ou a fort spread. "
                f"Verifier max_spread_percent dans risk_rules.yaml."
            ),
        }

        return lessons.get(category, f"Aucune lecon identifiee pour {category.value}.")

    # ------------------------------------------------------------------
    # Proposition d'amélioration
    # ------------------------------------------------------------------

    def _write_improvement(
        self, trade: PaperTrade, category: TradeCategory, m: dict
    ) -> Optional[str]:
        improvements = {
            TradeCategory.CLEAN_WIN: (
                "Identifier les conditions de marche similaires et documenter "
                "ce setup comme reference pour la strategie."
            ),
            TradeCategory.LUCKY_WIN: (
                f"Relever le seuil minimal de confiance a 0.75. "
                f"Filtrer les setups ou les frais depassent 0.25% de la position."
            ),
            TradeCategory.NORMAL_LOSS: None,  # Rien à améliorer — perte acceptée
            TradeCategory.AVOIDABLE_LOSS: (
                f"Elargir le stop-loss a au moins 1.5% du prix d'entree "
                f"pour eviter les chasses de stop par le bruit de marche."
                if m["sl_distance_pct"] < 1.0 else
                f"Ajouter une regle de sortie partielle apres 24h si le prix "
                f"n'a pas avance d'au moins 50% vers le TP."
                if trade.result == TradeResult.TIMEOUT else
                f"Renforcer le filtre de confiance minimale (0.75) et verifier "
                f"systematiquement les signaux defavorables avant d'entrer."
            ),
            TradeCategory.DESTROYED_BY_FEES: (
                f"Regle : ne pas ouvrir un trade si le mouvement minimal "
                f"pour couvrir les frais depasse 0.3% du prix d'entree. "
                f"Ici seuil de rentabilite = {m['friction_pct_of_size']:.3f}%."
            ),
            TradeCategory.DESTROYED_BY_SPREAD: (
                "Ajouter un filtre max_spread_percent strict (0.15%) dans risk_rules.yaml. "
                "Privilegier les periodes de forte liquidite (09h-17h UTC)."
            ),
        }
        return improvements.get(category)

    # ------------------------------------------------------------------
    # Qualité du signal
    # ------------------------------------------------------------------

    def _assess_signal_quality(
        self, trade: PaperTrade, category: TradeCategory, m: dict
    ) -> str:
        if category == TradeCategory.CLEAN_WIN:
            return "good"
        if category in (TradeCategory.NORMAL_LOSS,):
            return "neutral"  # signal correct mais marché défavorable
        if category in (
            TradeCategory.LUCKY_WIN,
            TradeCategory.AVOIDABLE_LOSS,
            TradeCategory.DESTROYED_BY_FEES,
            TradeCategory.DESTROYED_BY_SPREAD,
        ):
            return "bad"
        return "neutral"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _update_trade_fields(
        self, trade: PaperTrade, analysis: TradeAnalysis, session
    ) -> None:
        trade.category = analysis.category
        trade.main_error = analysis.main_error
        trade.lesson_learned = analysis.lesson
        trade.improvement_proposed = analysis.improvement
        update_paper_trade(session, trade)

    def _save_note(
        self, trade: PaperTrade, analysis: TradeAnalysis, session
    ) -> LearningNote:
        note = LearningNote(
            trade_id=trade.id,
            category=analysis.category,
            lesson=analysis.lesson,
            improvement_proposal=analysis.improvement,
            signal_quality=analysis.signal_quality,
            accepted_or_rejected="pending",
            market_context=json.dumps(analysis.key_metrics, default=str),
        )
        save_learning_note(session, note)
        return note


# ============================================================
# Feedback → strategy_params.json
# ============================================================

_PARAMS_FILE = Path("config/strategy_params.json")

# Pondérations d'ajustement du seuil momentum selon la catégorie du trade
_THRESHOLD_ADJUSTMENTS: dict[str, float] = {
    "CLEAN_WIN":          -0.005,   # signal fort → abaisser légèrement le seuil
    "LUCKY_WIN":          +0.010,   # signal douteux → être plus sélectif
    "NORMAL_LOSS":         0.000,   # perte normale → rien à changer
    "AVOIDABLE_LOSS":     +0.015,   # signal trop permissif → resserrer
    "DESTROYED_BY_FEES":  +0.010,   # mouvement trop faible → threshold plus haut
    "DESTROYED_BY_SPREAD":+0.010,
}

# Bornes absolues des seuils par asset
_THRESHOLD_BOUNDS: dict[str, tuple[float, float]] = {
    "BTC": (0.08, 0.40),
}
_DEFAULT_BOUNDS = (0.08, 0.40)

# Poids de lissage exponentiel (0 = jamais apprend, 1 = mémorise chaque trade)
_EMA_ALPHA = 0.15


def _update_strategy_params(notes: list) -> None:
    """
    Met à jour config/strategy_params.json à partir des notes d'apprentissage.
    Ajuste le seuil momentum par asset via EMA.
    """
    if not notes:
        return

    # Charger l'état courant
    try:
        params: dict = json.loads(_PARAMS_FILE.read_text(encoding="utf-8")) if _PARAMS_FILE.exists() else {}
    except Exception:
        params = {}

    changes: list[str] = []

    for note in notes:
        asset    = note.trade_id  # sera corrigé via join — on utilise le champ category
        category = note.category.value if hasattr(note.category, "value") else str(note.category)
        adj      = _THRESHOLD_ADJUSTMENTS.get(category, 0.0)
        if adj == 0.0:
            continue

        # Récupérer l'asset depuis market_context si disponible
        try:
            ctx   = json.loads(note.market_context or "{}")
            asset = ctx.get("asset", "UNKNOWN")
        except Exception:
            asset = "UNKNOWN"

        if asset == "UNKNOWN":
            continue

        key          = f"{asset}_momentum_threshold"
        lo, hi       = _THRESHOLD_BOUNDS.get(asset, _DEFAULT_BOUNDS)
        current      = params.get(key, (lo + hi) / 2)
        updated      = current * (1 - _EMA_ALPHA) + (current + adj) * _EMA_ALPHA
        updated      = round(max(lo, min(hi, updated)), 4)

        if updated != current:
            params[key] = updated
            changes.append(f"{asset}: {current:.4f} → {updated:.4f} ({category})")

    if changes:
        _PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PARAMS_FILE.write_text(json.dumps(params, indent=2), encoding="utf-8")
        logger.info(f"[LEARNING] Paramètres stratégie mis à jour : {' | '.join(changes)}")


# ============================================================
# Auto-ajustement de la taille de position
# ============================================================

_POSITION_SIZE_DEFAULT = 0.15   # point de départ si aucun historique
_POSITION_SIZE_MIN     = 0.10   # plancher absolu (10% du capital)
_POSITION_SIZE_MAX     = 0.25   # plafond absolu (25% — 35% = trop dangereux en bull run)

# Nombre de trades récents analysés pour décider
_SIZING_WINDOW = 20


def _compute_directional_delta(trades_dir: list, label: str) -> tuple[float, str]:
    """Retourne (delta, reason) pour une direction donnée."""
    if len(trades_dir) < 3:
        return 0.0, f"{label}: données insuffisantes"
    wins = sum(1 for t in trades_dir if t.result == TradeResult.WIN)
    wr = wins / len(trades_dir)
    exp = sum(t.net_pnl or 0.0 for t in trades_dir) / len(trades_dir)
    if wr >= 0.65 and exp > 0:
        return +0.02, f"{label} wr={wr:.0%} > 65% → hausse"
    elif wr >= 0.55 and exp > 0:
        return +0.01, f"{label} wr={wr:.0%} 55-65% → légère hausse"
    elif wr >= 0.45:
        return 0.0, f"{label} wr={wr:.0%} stable"
    elif wr >= 0.35:
        return -0.03, f"{label} wr={wr:.0%} < 45% → réduction"
    else:
        return -0.05, f"{label} wr={wr:.0%} < 35% → urgence"


def _update_position_sizing(session) -> None:
    """
    Ajuste LONG_position_size_pct et SHORT_position_size_pct séparément
    dans strategy_params.json, basé sur les 20 derniers trades par direction.
    """
    from sqlmodel import select

    try:
        trades = list(session.exec(
            select(PaperTrade)
            .where(PaperTrade.status == TradeStatus.CLOSED)
            .order_by(PaperTrade.exit_time.desc())  # type: ignore
            .limit(_SIZING_WINDOW * 2)
        ).all())
    except Exception:
        return

    if len(trades) < 5:
        return

    longs  = [t for t in trades if t.direction == TradeDirection.LONG][:_SIZING_WINDOW]
    shorts = [t for t in trades if t.direction == TradeDirection.SHORT][:_SIZING_WINDOW]

    try:
        params: dict = json.loads(_PARAMS_FILE.read_text(encoding="utf-8")) if _PARAMS_FILE.exists() else {}
    except Exception:
        params = {}

    changes: list[str] = []

    for key, trades_dir, label in [
        ("long_position_size_pct",  longs,  "LONG"),
        ("short_position_size_pct", shorts, "SHORT"),
    ]:
        current = params.get(key, _POSITION_SIZE_DEFAULT)
        delta, reason = _compute_directional_delta(trades_dir, label)
        if delta == 0.0:
            continue
        new_size = round(max(_POSITION_SIZE_MIN, min(_POSITION_SIZE_MAX, current + delta)), 3)
        if new_size != current:
            params[key] = new_size
            changes.append(f"{reason} | {current:.0%} → {new_size:.0%}")

    # position_size_pct global = moyenne pondérée (SHORT dominant si plus de trades)
    long_pct  = params.get("long_position_size_pct",  _POSITION_SIZE_DEFAULT)
    short_pct = params.get("short_position_size_pct", _POSITION_SIZE_DEFAULT)
    n_long, n_short = len(longs), len(shorts)
    total = n_long + n_short
    if total > 0:
        weighted = (long_pct * n_long + short_pct * n_short) / total
        params["position_size_pct"] = round(max(_POSITION_SIZE_MIN, min(_POSITION_SIZE_MAX, weighted)), 3)

    if changes:
        _PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PARAMS_FILE.write_text(json.dumps(params, indent=2), encoding="utf-8")
        logger.info(f"[POSITION SIZER] {' | '.join(changes)}")


# ============================================================
# Fonction utilitaire appelée par le scheduler
# ============================================================

def analyze_closed_trades(
    closed_trades: list[PaperTrade],
    session,
) -> list[LearningNote]:
    """
    Analyse une liste de trades clôturés, enregistre les notes,
    puis ajuste les paramètres dynamiques de la stratégie.
    """
    if not closed_trades:
        return []

    agent = LearningAgent()
    notes: list[LearningNote] = []

    for trade in closed_trades:
        try:
            note = agent.analyze(trade, session)
            if note:
                # Injecter l'asset dans market_context pour le feedback
                try:
                    ctx = json.loads(note.market_context or "{}")
                    ctx["asset"] = trade.asset
                    note.market_context = json.dumps(ctx, default=str)
                except Exception:
                    pass
                notes.append(note)
        except Exception as exc:
            logger.error(
                f"[LEARNING] Erreur analyse trade #{trade.id} : {exc}",
                exc_info=True,
            )

    if notes:
        logger.info(f"[LEARNING] {len(notes)} note(s) d'apprentissage enregistree(s).")
        _update_strategy_params(notes)
        _update_position_sizing(session)

    return notes
