"""
Tests du Learning Agent ARGOS.

Chaque test vÃ©rifie qu'un type de trade produit la bonne classification,
la bonne leÃ§on, et la bonne qualitÃ© de signal.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["TRADING_MODE"] = "paper"
os.environ["ENABLE_REAL_TRADING"] = "false"

from argos.database.models import (
    LearningNote,
    PaperTrade,
    TradeCategory,
    TradeDirection,
    TradeResult,
    TradeStatus,
)
from argos.learning.learning_agent import LearningAgent, analyze_closed_trades, _update_position_sizing


# ============================================================
# Fixtures helpers
# ============================================================

def _make_trade(**overrides) -> PaperTrade:
    """Construit un PaperTrade clÃ´turÃ© minimal pour les tests."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=1,
        strategy_version="strategy_v1_production",
        asset="BTC",
        direction=TradeDirection.LONG,
        entry_time=now - timedelta(hours=4),
        exit_time=now,
        status=TradeStatus.CLOSED,
        entry_price=55_000.0,
        exit_price=59_000.0,
        stop_loss=53_000.0,
        take_profit=59_000.0,
        position_size_eur=2.0,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        gross_pnl=0.1455,
        net_pnl=0.1385,
        return_percent=6.925,
        result=TradeResult.WIN,
        confidence_score=0.82,
        reason_entry="Test setup",
    )
    defaults.update(overrides)
    return PaperTrade(**defaults)


class _MockSession:
    """Session factice qui capture les objets ajoutÃ©s sans toucher la DB."""

    def __init__(self):
        self.flushed = []

    def add(self, obj):
        self.flushed.append(obj)

    def flush(self):
        pass


# ============================================================
# VÃ©rification de la structure de l'analyse
# ============================================================

def test_analysis_returns_learning_note():
    trade = _make_trade()
    session = _MockSession()
    agent = LearningAgent()
    note = agent.analyze(trade, session)
    assert note is not None
    assert isinstance(note, LearningNote)


def test_analysis_sets_trade_category():
    trade = _make_trade()
    session = _MockSession()
    agent = LearningAgent()
    agent.analyze(trade, session)
    assert trade.category is not None


def test_analysis_sets_lesson_learned():
    trade = _make_trade()
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.lesson_learned
    assert len(trade.lesson_learned) > 20


def test_analysis_ignored_if_not_closed():
    trade = _make_trade(status=TradeStatus.OPEN, result=TradeResult.OPEN)
    session = _MockSession()
    note = LearningAgent().analyze(trade, session)
    assert note is None


def test_analysis_ignored_if_result_is_open():
    trade = _make_trade(status=TradeStatus.CLOSED, result=TradeResult.OPEN)
    session = _MockSession()
    note = LearningAgent().analyze(trade, session)
    assert note is None


# ============================================================
# Classification â€” WIN
# ============================================================

def test_clean_win_high_confidence_good_rr():
    """LONG gagnant : confiance 82%, R/R 2.0 â†’ CLEAN_WIN."""
    trade = _make_trade(
        result=TradeResult.WIN,
        confidence_score=0.82,
        gross_pnl=0.1455,
        net_pnl=0.1385,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.CLEAN_WIN


def test_lucky_win_low_confidence():
    """Victoire avec confiance faible â†’ LUCKY_WIN."""
    trade = _make_trade(
        result=TradeResult.WIN,
        confidence_score=0.60,
        gross_pnl=0.08,
        net_pnl=0.07,
        fees_estimated=0.003,
        spread_estimated=0.002,
        slippage_estimated=0.001,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.LUCKY_WIN


def test_lucky_win_high_friction_ratio():
    """Victoire oÃ¹ les frais ont absorbÃ© 50%+ du profit brut â†’ LUCKY_WIN."""
    trade = _make_trade(
        result=TradeResult.WIN,
        confidence_score=0.80,
        gross_pnl=0.010,   # profit brut trÃ¨s faible
        net_pnl=0.005,
        fees_estimated=0.003,
        spread_estimated=0.001,
        slippage_estimated=0.001,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.LUCKY_WIN


# ============================================================
# Classification â€” LOSS
# ============================================================

def test_normal_loss():
    """Perte classique dans les limites â€” NORMAL_LOSS."""
    trade = _make_trade(
        result=TradeResult.LOSS,
        exit_price=53_000.0,   # SL touchÃ©
        gross_pnl=-0.0727,
        net_pnl=-0.0797,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        stop_loss=53_000.0,
        take_profit=59_000.0,
        confidence_score=0.80,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.NORMAL_LOSS


def test_avoidable_loss_tight_sl():
    """Stop-loss trop serrÃ© (0.5%) â†’ AVOIDABLE_LOSS."""
    trade = _make_trade(
        result=TradeResult.LOSS,
        entry_price=55_000.0,
        stop_loss=54_725.0,   # 0.5% sous l'entrÃ©e
        take_profit=59_000.0,
        exit_price=54_725.0,
        gross_pnl=-0.01,
        net_pnl=-0.017,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        confidence_score=0.80,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.AVOIDABLE_LOSS


def test_avoidable_loss_low_confidence_with_unfavorable_signals():
    """Confiance faible + signaux dÃ©favorables connus â†’ AVOIDABLE_LOSS."""
    trade = _make_trade(
        result=TradeResult.LOSS,
        confidence_score=0.65,
        signals_unfavorable='["rsi_overbought", "ma_bearish"]',
        gross_pnl=-0.05,
        net_pnl=-0.057,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        stop_loss=53_000.0,   # SL normal (>1%)
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.AVOIDABLE_LOSS


def test_destroyed_by_fees():
    """Profit brut positif â†’ perte nette Ã  cause des frais â†’ DESTROYED_BY_FEES."""
    trade = _make_trade(
        result=TradeResult.LOSS,
        gross_pnl=0.002,     # brut lÃ©gÃ¨rement positif
        net_pnl=-0.005,      # net nÃ©gatif Ã  cause des frais
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        stop_loss=53_000.0,
        take_profit=59_000.0,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.DESTROYED_BY_FEES


def test_destroyed_by_spread():
    """Spread a absorbÃ© la quasi-totalitÃ© du profit brut â†’ DESTROYED_BY_SPREAD."""
    trade = _make_trade(
        result=TradeResult.LOSS,
        gross_pnl=0.001,
        net_pnl=-0.006,
        fees_estimated=0.001,
        spread_estimated=0.005,   # spread dominant
        slippage_estimated=0.001,
        stop_loss=53_000.0,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.DESTROYED_BY_SPREAD


# ============================================================
# Classification â€” TIMEOUT
# ============================================================

def test_timeout_classified_as_avoidable():
    """Trade clÃ´turÃ© par timeout â†’ AVOIDABLE_LOSS."""
    trade = _make_trade(
        result=TradeResult.TIMEOUT,
        exit_price=55_500.0,   # prix entre SL et TP
        gross_pnl=0.018,
        net_pnl=0.011,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        entry_time=datetime.now(timezone.utc) - timedelta(hours=50),
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.AVOIDABLE_LOSS


# ============================================================
# QualitÃ© du signal
# ============================================================

def test_signal_quality_good_for_clean_win():
    trade = _make_trade(confidence_score=0.82)
    session = _MockSession()
    note = LearningAgent().analyze(trade, session)
    assert note.signal_quality == "good"


def test_signal_quality_neutral_for_normal_loss():
    trade = _make_trade(
        result=TradeResult.LOSS,
        exit_price=53_000.0,
        gross_pnl=-0.0727,
        net_pnl=-0.0797,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        confidence_score=0.80,
    )
    session = _MockSession()
    note = LearningAgent().analyze(trade, session)
    assert note.signal_quality == "neutral"


def test_signal_quality_bad_for_avoidable_loss():
    trade = _make_trade(
        result=TradeResult.LOSS,
        confidence_score=0.65,
        signals_unfavorable='["bearish"]',
        gross_pnl=-0.05,
        net_pnl=-0.057,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        stop_loss=53_000.0,
    )
    session = _MockSession()
    note = LearningAgent().analyze(trade, session)
    assert note.signal_quality == "bad"


# ============================================================
# AmÃ©lioration proposÃ©e
# ============================================================

def test_clean_win_has_improvement():
    trade = _make_trade(confidence_score=0.82)
    session = _MockSession()
    note = LearningAgent().analyze(trade, session)
    assert note.improvement_proposal is not None


def test_normal_loss_has_no_improvement():
    """Perte normale : pas d'amÃ©lioration Ã  proposer."""
    trade = _make_trade(
        result=TradeResult.LOSS,
        exit_price=53_000.0,
        gross_pnl=-0.0727,
        net_pnl=-0.0797,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        confidence_score=0.80,
    )
    session = _MockSession()
    note = LearningAgent().analyze(trade, session)
    assert note.improvement_proposal is None


def test_destroyed_by_fees_has_improvement():
    trade = _make_trade(
        result=TradeResult.LOSS,
        gross_pnl=0.002,
        net_pnl=-0.005,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
    )
    session = _MockSession()
    note = LearningAgent().analyze(trade, session)
    assert note.improvement_proposal is not None
    assert "%" in note.improvement_proposal


# ============================================================
# Erreur principale
# ============================================================

def test_clean_win_has_no_main_error():
    trade = _make_trade(confidence_score=0.82)
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.main_error is None


def test_avoidable_loss_has_main_error():
    trade = _make_trade(
        result=TradeResult.LOSS,
        stop_loss=54_725.0,
        exit_price=54_725.0,
        gross_pnl=-0.01,
        net_pnl=-0.017,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
        confidence_score=0.80,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.main_error is not None
    assert len(trade.main_error) > 5


# ============================================================
# SHORT trades
# ============================================================

def test_short_win_clean():
    """SHORT gagnant avec bons paramÃ¨tres â†’ CLEAN_WIN."""
    trade = _make_trade(
        asset="BTC",
        direction=TradeDirection.SHORT,
        entry_price=55_000.0,
        stop_loss=57_000.0,
        take_profit=49_500.0,
        exit_price=2_520.0,
        result=TradeResult.WIN,
        confidence_score=0.78,
        gross_pnl=0.20,
        net_pnl=0.193,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.CLEAN_WIN


def test_short_loss_normal():
    """SHORT perdant, SL touchÃ© â†’ NORMAL_LOSS."""
    trade = _make_trade(
        asset="BTC",
        direction=TradeDirection.SHORT,
        entry_price=55_000.0,
        stop_loss=57_000.0,
        take_profit=49_500.0,
        exit_price=2_900.0,
        result=TradeResult.LOSS,
        confidence_score=0.78,
        gross_pnl=-0.0714,
        net_pnl=-0.0784,
        fees_estimated=0.004,
        spread_estimated=0.002,
        slippage_estimated=0.001,
    )
    session = _MockSession()
    LearningAgent().analyze(trade, session)
    assert trade.category == TradeCategory.NORMAL_LOSS


# ============================================================
# Fonction utilitaire analyze_closed_trades
# ============================================================

def test_analyze_closed_trades_empty():
    session = _MockSession()
    notes = analyze_closed_trades([], session)
    assert notes == []


def test_analyze_closed_trades_multiple():
    session = _MockSession()
    trades = [
        _make_trade(id=1, result=TradeResult.WIN, confidence_score=0.82),
        _make_trade(
            id=2, result=TradeResult.LOSS,
            exit_price=53_000.0, gross_pnl=-0.0727, net_pnl=-0.0797,
            fees_estimated=0.004, spread_estimated=0.002, slippage_estimated=0.001,
        ),
    ]
    notes = analyze_closed_trades(trades, session)
    assert len(notes) == 2


def test_analyze_closed_trades_skips_open():
    session = _MockSession()
    trades = [
        _make_trade(id=1, status=TradeStatus.OPEN, result=TradeResult.OPEN),
        _make_trade(id=2, result=TradeResult.WIN, confidence_score=0.82),
    ]
    notes = analyze_closed_trades(trades, session)
    assert len(notes) == 1


def test_analyze_closed_trades_handles_error_gracefully():
    """Une erreur sur un trade ne doit pas interrompre les autres."""
    session = _MockSession()

    bad_trade = PaperTrade(
        id=99,
        strategy_version="v1",
        asset="BTC",
        direction=TradeDirection.LONG,
        entry_price=0.0,   # prix invalide â†’ division par zÃ©ro potentielle
        stop_loss=0.0,
        take_profit=0.0,
        position_size_eur=0.0,
        result=TradeResult.LOSS,
        status=TradeStatus.CLOSED,
        entry_time=datetime.now(timezone.utc) - timedelta(hours=1),
        exit_time=datetime.now(timezone.utc),
        gross_pnl=0.0,
        net_pnl=0.0,
        fees_estimated=0.0,
        spread_estimated=0.0,
        slippage_estimated=0.0,
    )
    good_trade = _make_trade(id=100, result=TradeResult.WIN, confidence_score=0.82)

    notes = analyze_closed_trades([bad_trade, good_trade], session)
    # Le bon trade doit toujours Ãªtre analysÃ©
    assert any(n.trade_id == 100 for n in notes)


# ============================================================
# Position Sizer automatique
# ============================================================

def test_position_sizer_increases_on_high_win_rate(session, tmp_path, monkeypatch):
    """Win rate > 65% â†’ taille de position augmente."""
    from argos.database.repositories import save_paper_trade
    from argos.learning import learning_agent as la

    params_file = tmp_path / "strategy_params.json"
    params_file.write_text(json.dumps({"position_size_pct": 0.15}), encoding="utf-8")
    monkeypatch.setattr(la, "_PARAMS_FILE", params_file)

    base = datetime.utcnow().replace(second=0, microsecond=0)
    for i in range(10):
        t = PaperTrade(
            strategy_version="v3", asset="BTC",
            direction=TradeDirection.LONG, entry_price=50000.0,
            stop_loss=49000.0, take_profit=52000.0, position_size_eur=2.0,
            status=TradeStatus.CLOSED, result=TradeResult.WIN,
            exit_time=base + timedelta(minutes=i), net_pnl=0.05,
        )
        save_paper_trade(session, t)
    session.commit()

    _update_position_sizing(session)

    new_size = json.loads(params_file.read_text())["position_size_pct"]
    assert new_size > 0.15


def test_position_sizer_decreases_on_low_win_rate(session, tmp_path, monkeypatch):
    """Win rate < 45% â†’ taille de position diminue."""
    from argos.database.repositories import save_paper_trade
    from argos.learning import learning_agent as la

    params_file = tmp_path / "strategy_params.json"
    params_file.write_text(json.dumps({"position_size_pct": 0.25}), encoding="utf-8")
    monkeypatch.setattr(la, "_PARAMS_FILE", params_file)

    base = datetime.utcnow().replace(second=0, microsecond=0)
    for i in range(10):
        result = TradeResult.WIN if i < 3 else TradeResult.LOSS
        t = PaperTrade(
            strategy_version="v3", asset="BTC",
            direction=TradeDirection.LONG, entry_price=50000.0,
            stop_loss=49000.0, take_profit=52000.0, position_size_eur=2.0,
            status=TradeStatus.CLOSED, result=result,
            exit_time=base + timedelta(minutes=i), net_pnl=0.05 if result == TradeResult.WIN else -0.03,
        )
        save_paper_trade(session, t)
    session.commit()

    _update_position_sizing(session)

    new_size = json.loads(params_file.read_text())["position_size_pct"]
    assert new_size < 0.25


def test_position_sizer_respects_bounds(session, tmp_path, monkeypatch):
    """La taille reste dans [10%, 35%] mÃªme avec beaucoup de wins."""
    from argos.database.repositories import save_paper_trade
    from argos.learning import learning_agent as la

    params_file = tmp_path / "strategy_params.json"
    params_file.write_text(json.dumps({"position_size_pct": 0.34}), encoding="utf-8")
    monkeypatch.setattr(la, "_PARAMS_FILE", params_file)

    base = datetime.utcnow().replace(second=0, microsecond=0)
    for i in range(20):
        t = PaperTrade(
            strategy_version="v3", asset="BTC",
            direction=TradeDirection.LONG, entry_price=50000.0,
            stop_loss=49000.0, take_profit=52000.0, position_size_eur=2.0,
            status=TradeStatus.CLOSED, result=TradeResult.WIN,
            exit_time=base + timedelta(minutes=i), net_pnl=0.05,
        )
        save_paper_trade(session, t)
    session.commit()

    _update_position_sizing(session)

    new_size = json.loads(params_file.read_text())["position_size_pct"]
    assert new_size <= 0.35

