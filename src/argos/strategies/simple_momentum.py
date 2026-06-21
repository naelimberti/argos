"""
Stratégie Momentum — ARGOS v3 (bot 10 000 €).

Améliorations v3 :
  1. Filtre de tendance haute timeframe  — on ne trade que dans la direction
     de la SMA 8h. Un LONG pendant une baisse macro = refusé.
  2. Historique étendu  — signal calculé sur 32 snapshots (8h), tendance
     confirmée sur 96 snapshots (24h) quand disponibles.
  3. Détection de régime de marché — 4 régimes : NORMAL / TRENDING /
     RANGING / DANGER. En DANGER (crash ou spike), aucun trade.
     En RANGING (marché plat), seuil momentum doublé.

Principe cost-reducer appliqué au capital :
  - Zéro trade sans confirmation macro → préserve le capital
  - Signal le plus fort uniquement → pas de dispersion
  - DANGER bloqué immédiatement → coupe court les séries de pertes
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from argos.database.models import MarketSnapshot, TradeDirection
from argos.database.repositories import (
    get_market_snapshots,
    get_open_positions,
    load_portfolio_from_db,
)
from argos.trading.paper_engine import TradeRequest
from argos.utils.logger import get_logger

logger = get_logger("strategy.momentum")

# ============================================================
# Fenêtres temporelles (snapshots = intervalles de 15 min)
# ============================================================

SIGNAL_WINDOW     = 8    # 2h  — momentum rapide
TREND_WINDOW      = 16   # 4h  — tendance moyen terme (plus réactif)
LONG_TREND_WINDOW = 96   # 24h — macro tendance
MIN_SNAPSHOTS     = 4    # minimum absolu pour générer un signal

STRATEGY_NAME     = "strategy_v4"
POSITION_SIZE_PCT = 0.15   # taille de base — surchargée par les params directionnels
RR_TARGET         = 2.2    # R/R cible

RSI_PERIOD        = 14     # RSI standard (14 × 15min = 3.5h)
RSI_LONG_MAX      = 58     # ne pas LONG si RSI > 58 (overbought)
RSI_SHORT_MIN     = 42     # ne pas SHORT si RSI < 42 (oversold)
VOLUME_CONFIRM_RATIO = 0.75  # volume minimum vs moyenne 10 snapshots (dégrade gracieusement)

# ============================================================
# Profils de volatilité par asset
# ============================================================

ASSET_PROFILE: dict[str, dict] = {
    "BTC": {"min_sl": 1.0, "max_sl": 3.0, "momentum_threshold": 0.10},
}
DEFAULT_PROFILE = {"min_sl": 1.0, "max_sl": 3.0, "momentum_threshold": 0.10}

# ============================================================
# Régimes de marché
# ============================================================

class MarketRegime(Enum):
    NORMAL       = "normal"       # conditions standard → trade normalement
    TRENDING     = "trending"     # tendance forte → bonus de confiance
    RANGING      = "ranging"      # marché plat → seuil doublé, trades rares
    DANGER       = "danger"       # crash ou spike → aucun trade


def detect_regime(prices: list[float], asset: str) -> MarketRegime:
    """
    Analyse les 8 derniers snapshots (2h) pour classifier le régime.

    DANGER  : variation > 4% en 1h (4 snapshots) — crash ou spike
    TRENDING: ≥ 75% des mouvements dans la même direction sur 2h
    RANGING : volatilité 2h < 0.4%
    NORMAL  : tout le reste
    """
    if len(prices) < 4:
        return MarketRegime.NORMAL

    recent = prices[-min(8, len(prices)):]

    # --- DANGER : mouvement brutal sur 1h ---
    last4 = prices[-4:]
    move_1h = (last4[-1] - last4[0]) / last4[0] * 100
    if abs(move_1h) > 4.0:
        logger.warning(
            f"[REGIME] {asset} DANGER — mouvement {move_1h:+.2f}% sur 1h"
        )
        return MarketRegime.DANGER

    # --- Directions des mouvements ---
    moves = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
    if not moves:
        return MarketRegime.NORMAL

    up   = sum(1 for m in moves if m > 0)
    down = sum(1 for m in moves if m < 0)
    ratio = max(up, down) / len(moves)

    # --- TRENDING : 75%+ dans la même direction ---
    if ratio >= 0.75:
        return MarketRegime.TRENDING

    # --- RANGING : amplitude totale < 0.4% ---
    amplitude = (max(recent) - min(recent)) / min(recent) * 100
    if amplitude < 0.4:
        return MarketRegime.RANGING

    return MarketRegime.NORMAL


# ============================================================
# Filtre de tendance haute timeframe
# ============================================================

def check_trend_alignment(
    prices: list[float],
    direction: TradeDirection,
    asset: str,
) -> tuple[bool, str]:
    """
    Vérifie que le trade est dans le sens de la tendance 8h.
    Retourne (True, "") si aligné, (False, raison) si contre-tendance.
    Dégradation gracieuse : si pas assez de données → autorisé par défaut.
    """
    window = min(len(prices), TREND_WINDOW)
    if window < SIGNAL_WINDOW * 2:
        return False, f"historique insuffisant ({window} snapshots < {SIGNAL_WINDOW * 2}) — trade bloqué par prudence"

    sma_trend = sum(prices[-window:]) / window
    current   = prices[-1]
    gap_pct   = (current - sma_trend) / sma_trend * 100

    trend_up = current > sma_trend

    if direction == TradeDirection.LONG and not trend_up:
        return False, f"LONG contre tendance 8h (prix {gap_pct:+.2f}% sous SMA{window})"
    if direction == TradeDirection.SHORT and trend_up:
        return False, f"SHORT contre tendance 8h (prix {gap_pct:+.2f}% au-dessus SMA{window})"

    return True, ""


# ============================================================
# Calculs de base
# ============================================================

def _compute_momentum(prices: list[float], window: int) -> float:
    if len(prices) < window:
        return 0.0
    sma = sum(prices[-window:]) / window
    return (prices[-1] - sma) / sma * 100


def _compute_rsi(prices: list[float], period: int = RSI_PERIOD) -> Optional[float]:
    """RSI(14) standard. Retourne None si données insuffisantes."""
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains  = [d for d in recent if d > 0]
    losses = [abs(d) for d in recent if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs  = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _compute_volatility(prices: list[float], asset: str) -> float:
    """ATR approximé sur la fenêtre disponible (préfère 24h, accepte moins)."""
    profile = ASSET_PROFILE.get(asset, DEFAULT_PROFILE)
    if len(prices) < 2:
        return profile["min_sl"]
    # Utiliser jusqu'à 96 snapshots (24h) pour un ATR representatif
    window = min(len(prices), LONG_TREND_WINDOW)
    moves  = [abs(prices[i] / prices[i - 1] - 1) * 100 for i in range(max(1, len(prices) - window), len(prices))]
    sl_pct = (sum(moves) / len(moves)) * 2.5
    return max(profile["min_sl"], min(profile["max_sl"], sl_pct))


def _check_volume(snapshots: list, n_ref: int = 10) -> tuple[bool, str]:
    """
    Vérifie que le volume actuel n'est pas anormalement faible.
    Retourne (True, "") si OK ou si données manquantes (dégrade gracieusement).
    """
    volumes = [s.volume_24h for s in snapshots if s.volume_24h is not None]
    if len(volumes) < n_ref + 1:
        return True, ""  # pas assez de données → pas de filtre
    avg_vol = sum(volumes[-n_ref - 1:-1]) / n_ref
    current = volumes[-1]
    if avg_vol <= 0:
        return True, ""
    ratio = current / avg_vol
    if ratio < VOLUME_CONFIRM_RATIO:
        return False, f"volume faible ({ratio:.2f}× la moyenne) — signal peu fiable"
    return True, ""


def _check_macro_bias(prices: list[float], direction: TradeDirection) -> tuple[float, str]:
    """
    Calcule le biais macro via la SMA long terme disponible.
    Retourne (facteur_taille, note) — facteur < 1 réduit la position.
    Les SHORTs contre une tendance haussière forte sont réduits de 50%.
    """
    if len(prices) < LONG_TREND_WINDOW:
        return 1.0, ""
    sma_long = sum(prices[-LONG_TREND_WINDOW:]) / LONG_TREND_WINDOW
    current  = prices[-1]
    gap_pct  = (current - sma_long) / sma_long * 100

    if direction == TradeDirection.SHORT and gap_pct > 2.0:
        # SHORT contre une tendance haussière forte → réduction 50%
        return 0.50, f"macro bullish ({gap_pct:+.1f}% > SMA24h) — SHORT réduit à 50%"
    if direction == TradeDirection.LONG and gap_pct < -2.0:
        # LONG contre une tendance baissière forte → réduction 30%
        return 0.70, f"macro bearish ({gap_pct:+.1f}% < SMA24h) — LONG réduit à 70%"
    return 1.0, ""


def _compute_confidence(
    momentum_pct: float,
    n_snapshots: int,
    threshold: float,
    regime: MarketRegime,
    rsi: Optional[float],
    direction: TradeDirection,
) -> float:
    base           = 0.70
    momentum_bonus = min(abs(momentum_pct) / threshold * 0.08, 0.12)
    data_bonus     = min((n_snapshots - MIN_SNAPSHOTS) / (SIGNAL_WINDOW - MIN_SNAPSHOTS) * 0.05, 0.05)
    regime_bonus   = 0.05 if regime == MarketRegime.TRENDING else 0.0

    # Bonus RSI : signal aligné avec une zone technique favorable
    rsi_bonus = 0.0
    if rsi is not None:
        if direction == TradeDirection.LONG and rsi < 40:
            rsi_bonus = 0.06  # survente → setup LONG de qualité
        elif direction == TradeDirection.SHORT and rsi > 60:
            rsi_bonus = 0.06  # surachat → setup SHORT de qualité

    return min(0.92, base + momentum_bonus + data_bonus + regime_bonus + rsi_bonus)


# ============================================================
# Signal
# ============================================================

PARAMS_FILE = Path("config/strategy_params.json")


def _load_dynamic_params() -> dict:
    try:
        if PARAMS_FILE.exists():
            return json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


@dataclass
class MomentumSignal:
    asset: str
    direction: TradeDirection
    momentum_pct: float
    volatility_pct: float
    sma: float
    current_price: float
    confidence: float
    n_snapshots: int
    score: float
    regime: MarketRegime
    rsi: Optional[float]
    macro_size_factor: float


def generate_signal(
    asset: str,
    snapshots: list[MarketSnapshot],
    dynamic_params: dict,
) -> Optional[MomentumSignal]:
    """
    Pipeline v4 : régime → volume → momentum → RSI → tendance → macro bias → signal.
    Chaque filtre peut bloquer le signal ou réduire la taille de position.
    """
    if len(snapshots) < MIN_SNAPSHOTS:
        return None

    prices        = [s.price for s in snapshots]
    current_price = prices[-1]
    n             = len(prices)

    # 1. Régime de marché
    regime = detect_regime(prices, asset)
    if regime == MarketRegime.DANGER:
        logger.info(f"[SKIP] {asset} — régime DANGER, aucun trade")
        return None

    # 2. Volume — confirmation que le marché est actif
    vol_ok, vol_reason = _check_volume(snapshots)
    if not vol_ok:
        logger.info(f"[SKIP] {asset} — {vol_reason}")
        return None

    # 3. Seuil adapté au régime
    profile   = ASSET_PROFILE.get(asset, DEFAULT_PROFILE)
    base_thr  = dynamic_params.get(f"{asset}_momentum_threshold",
                                   profile["momentum_threshold"])
    threshold = base_thr * 2.0 if regime == MarketRegime.RANGING else base_thr

    # 4. Momentum sur la fenêtre rapide
    window   = min(n, SIGNAL_WINDOW)
    momentum = _compute_momentum(prices, window)
    sma      = sum(prices[-window:]) / window

    if abs(momentum) < threshold:
        logger.debug(
            f"{asset} : momentum {momentum:+.3f}% < seuil {threshold:.3f}% "
            f"[{regime.value}] — pas de signal"
        )
        return None

    direction = TradeDirection.LONG if momentum > 0 else TradeDirection.SHORT

    # 5. Filtre RSI — évite les entrées en zone extrême contre nous
    rsi = _compute_rsi(prices)
    if rsi is not None:
        if direction == TradeDirection.LONG and rsi > RSI_LONG_MAX:
            logger.info(f"[SKIP] {asset} LONG — RSI {rsi:.0f} > {RSI_LONG_MAX} (overbought)")
            return None
        if direction == TradeDirection.SHORT and rsi < RSI_SHORT_MIN:
            logger.info(f"[SKIP] {asset} SHORT — RSI {rsi:.0f} < {RSI_SHORT_MIN} (oversold)")
            return None

    # 6. Filtre de tendance haute timeframe
    aligned, reason = check_trend_alignment(prices, direction, asset)
    if not aligned:
        logger.info(f"[SKIP] {asset} {direction.value} — {reason}")
        return None

    # 7. Biais macro — réduit la taille si contre la tendance 24h
    macro_factor, macro_note = _check_macro_bias(prices, direction)
    if macro_note:
        logger.info(f"[MACRO] {asset} {direction.value} — {macro_note}")

    # 8. Métriques finales
    volatility = _compute_volatility(prices, asset)  # 24h ATR
    confidence = _compute_confidence(momentum, n, threshold, regime, rsi, direction)
    score      = abs(momentum) / threshold * confidence * macro_factor

    rsi_str = f"RSI={rsi:.0f}" if rsi is not None else "RSI=n/a"
    logger.info(
        f"[SIGNAL] {asset} {direction.value} | "
        f"mom={momentum:+.3f}% | {rsi_str} | vol={volatility:.2f}% | "
        f"conf={confidence:.0%} | score={score:.3f} | macro×{macro_factor:.2f} | "
        f"régime={regime.value} | prix={current_price:,.2f} EUR"
    )

    return MomentumSignal(
        asset=asset,
        direction=direction,
        momentum_pct=momentum,
        volatility_pct=volatility,
        sma=sma,
        current_price=current_price,
        confidence=confidence,
        n_snapshots=n,
        score=score,
        regime=regime,
        rsi=rsi,
        macro_size_factor=macro_factor,
    )


def signal_to_trade_request(
    signal: MomentumSignal,
    capital_eur: float = 10.0,
    dynamic_params: dict | None = None,
) -> TradeRequest:
    price    = signal.current_price
    dp       = dynamic_params or {}
    dir_key  = "long_position_size_pct" if signal.direction == TradeDirection.LONG else "short_position_size_pct"
    base_pct = dp.get(dir_key, dp.get("position_size_pct", POSITION_SIZE_PCT))
    size_pct = round(base_pct * signal.macro_size_factor, 3)
    position_size_eur = round(capital_eur * size_pct, 2)
    sl_pct            = signal.volatility_pct / 100
    tp_pct            = sl_pct * RR_TARGET

    if signal.direction == TradeDirection.LONG:
        stop_loss   = round(price * (1 - sl_pct), 6)
        take_profit = round(price * (1 + tp_pct), 6)
    else:
        stop_loss   = round(price * (1 + sl_pct), 6)
        take_profit = round(price * (1 - tp_pct), 6)

    favorable   = json.dumps({
        "momentum_pct":    round(signal.momentum_pct, 4),
        "score":           round(signal.score, 4),
        "regime":          signal.regime.value,
        "rsi":             round(signal.rsi, 1) if signal.rsi is not None else None,
        "macro_factor":    round(signal.macro_size_factor, 2),
    })
    unfavorable = json.dumps({"volatility_pct": round(signal.volatility_pct, 4)})

    return TradeRequest(
        asset=signal.asset,
        direction=signal.direction,
        entry_price=price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size_eur=position_size_eur,
        strategy_version=STRATEGY_NAME,
        confidence_score=signal.confidence,
        reason_entry=(
            f"[{signal.regime.value.upper()}] "
            f"Mom={signal.momentum_pct:+.3f}% "
            f"RSI={signal.rsi:.0f} " if signal.rsi else f"Mom={signal.momentum_pct:+.3f}% "
            f"score={signal.score:.3f} | "
            f"SL={sl_pct*100:.2f}% TP={tp_pct*100:.2f}% | "
            f"size={size_pct:.0%}×capital"
        ),
        signals_favorable=favorable,
        signals_unfavorable=unfavorable,
    )


# ============================================================
# Point d'entrée du scheduler
# ============================================================

def run_strategy(
    assets: list[str],
    session,
    max_positions: int = 3,
    portfolio=None,
) -> list[TradeRequest]:
    """
    1. Exclut les assets déjà en position (pas de doublons)
    2. Pour chaque asset libre : régime → tendance → momentum
    3. Classe par score, soumet uniquement les N meilleurs (slots libres)
    """
    dynamic_params  = _load_dynamic_params()
    if portfolio is None:
        portfolio = load_portfolio_from_db(session)
    capital = portfolio.current_capital

    open_positions  = get_open_positions(session)
    occupied_assets = {p.asset for p in open_positions}  # 1 position max par actif
    free_slots      = max_positions - len(open_positions)

    if free_slots <= 0:
        logger.debug("[STRATEGY] Aucun slot libre")
        return []

    signals: list[MomentumSignal] = []
    for asset in assets:
        if asset in occupied_assets:
            logger.debug(f"[STRATEGY] {asset} déjà en position — ignoré")
            continue
        try:
            # Charge jusqu'à 24h d'historique (LONG_TREND_WINDOW snapshots)
            snapshots = get_market_snapshots(session, asset, limit=LONG_TREND_WINDOW + 4)
            if not snapshots:
                continue
            signal = generate_signal(asset, snapshots, dynamic_params)
            if signal:
                signals.append(signal)
        except Exception as exc:
            logger.error(f"[STRATEGY] Erreur {asset} : {exc}", exc_info=True)

    if not signals:
        return []

    signals.sort(key=lambda s: s.score, reverse=True)
    top = signals[:free_slots]

    logger.info(
        f"[STRATEGY] {len(signals)} signal(s) | {free_slots} slot(s) → "
        f"soumission : " + ", ".join(f"{s.asset}({s.score:.2f})" for s in top)
    )

    size_pct = dynamic_params.get("position_size_pct", POSITION_SIZE_PCT)
    logger.debug(f"[STRATEGY] Taille de position : {size_pct:.0%} du capital ({capital * size_pct:.2f}€)")
    return [signal_to_trade_request(s, capital_eur=capital, dynamic_params=dynamic_params) for s in top]
