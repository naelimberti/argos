"""
Dashboard ARGOS — Suivi paper trading en temps réel.

Lancement :
    streamlit run src/argos/dashboard/app.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlmodel import Session, select

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("TRADING_MODE", "paper")
os.environ.setdefault("ENABLE_REAL_TRADING", "false")

# Injecte les secrets Streamlit Cloud dans les variables d'environnement
try:
    if hasattr(st, "secrets"):
        for _k in ("DATABASE_URL", "DATABASE_PATH"):
            if _k in st.secrets and not os.environ.get(_k):
                os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

from argos.database.db import get_engine, init_database
from argos.database.models import (
    MarketSnapshot,
    PaperTrade,
    RiskDecisionLog,
    TradeResult,
    TradeStatus,
    RiskDecision,
)

# Crée les tables si elles n'existent pas (idempotent — OK pour Supabase et SQLite)
try:
    init_database()
except Exception:
    pass

# ============================================================
# Page config
# ============================================================

st.set_page_config(
    page_title="ARGOS — Trading Terminal",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# Auth gate — code d'accès
# ============================================================

def _check_auth() -> bool:
    """Vérifie le code d'accès. Retourne True si l'utilisateur est authentifié."""
    access_code = st.secrets.get("ACCESS_CODE", "argos2024")

    if st.session_state.get("authenticated"):
        return True

    st.markdown("""
    <style>
    html, body, [data-testid="stApp"] {
        background-color: #080B12 !important;
        color: #E2E8F0 !important;
        font-family: 'Inter', -apple-system, sans-serif !important;
    }
    [data-testid="stHeader"] { display: none !important; }
    </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.markdown("<br><br><br>", unsafe_allow_html=True)
        st.markdown("""
        <div style="text-align:center; margin-bottom:2rem;">
            <span style="font-size:2.5rem;">⬡</span>
            <h1 style="color:#E2E8F0; font-size:1.8rem; margin:0.5rem 0 0.2rem;">ARGOS</h1>
            <p style="color:#64748B; font-size:0.85rem; margin:0;">Trading Terminal · Paper Mode</p>
        </div>
        """, unsafe_allow_html=True)

        code = st.text_input("Code d'accès", type="password", placeholder="••••••••",
                             label_visibility="collapsed")
        if st.button("Accéder →", use_container_width=True):
            if code == access_code:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Code incorrect.")
    return False

if not _check_auth():
    st.stop()

# ============================================================
# Design System — CSS complet
# ============================================================

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');

/* ── Reset Streamlit ─────────────────────────────────── */
html, body, [data-testid="stApp"] {
    background-color: #080B12 !important;
    color: #E2E8F0 !important;
    font-family: 'Inter', -apple-system, sans-serif !important;
}
[data-testid="stHeader"] { display: none !important; }
[data-testid="stSidebar"] { background-color: #0D1117 !important; }
.block-container { padding: 1.5rem 2rem !important; max-width: 100% !important; }
[data-testid="stToolbar"] { display: none !important; }
.stDeployButton { display: none !important; }
footer { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }
section[data-testid="stSidebar"] > div { background: #0D1117 !important; }

/* ── Plotly tweaks ───────────────────────────────────── */
.js-plotly-plot { border-radius: 12px; }

/* ── Scrollbar ───────────────────────────────────────── */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: #0D1117; }
::-webkit-scrollbar-thumb { background: #1E2D3D; border-radius: 2px; }

/* ── Header ──────────────────────────────────────────── */
.argos-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 0 1.25rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    margin-bottom: 1.5rem;
}
.argos-logo {
    display: flex;
    align-items: center;
    gap: 12px;
}
.argos-logo-icon {
    width: 36px; height: 36px;
    background: linear-gradient(135deg, #10B981, #059669);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
    box-shadow: 0 0 24px rgba(16,185,129,0.35);
}
.argos-logo-text {
    font-size: 1.25rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    color: #F1F5F9;
}
.argos-logo-sub {
    font-size: 0.7rem;
    color: #475569;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin-top: 1px;
}
.argos-badges {
    display: flex;
    gap: 8px;
    align-items: center;
}
.badge {
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border: 1px solid;
}
.badge-paper {
    background: rgba(16,185,129,0.08);
    border-color: rgba(16,185,129,0.25);
    color: #10B981;
}
.badge-live {
    background: rgba(239,68,68,0.08);
    border-color: rgba(239,68,68,0.25);
    color: #EF4444;
    display: flex;
    align-items: center;
    gap: 5px;
}
.live-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: #EF4444;
    animation: blink 1.4s ease-in-out infinite;
}
@keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.2; }
}
.header-time {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: #475569;
    letter-spacing: 0.04em;
}

/* ── Section title ───────────────────────────────────── */
.section-label {
    font-size: 0.68rem;
    font-weight: 600;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin: 1.75rem 0 0.75rem 0;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-label::after {
    content: '';
    flex: 1;
    height: 1px;
    background: rgba(255,255,255,0.05);
}

/* ── KPI Cards ───────────────────────────────────────── */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 12px;
    margin-bottom: 0.5rem;
}
.kpi-card {
    background: #0D1117;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 14px;
    padding: 1rem 1.1rem;
    position: relative;
    overflow: hidden;
    transition: border-color 0.2s;
}
.kpi-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--accent-color, rgba(255,255,255,0.08));
    border-radius: 14px 14px 0 0;
}
.kpi-card.green  { --accent-color: linear-gradient(90deg, #10B981, #059669); }
.kpi-card.red    { --accent-color: linear-gradient(90deg, #EF4444, #DC2626); }
.kpi-card.blue   { --accent-color: linear-gradient(90deg, #3B82F6, #2563EB); }
.kpi-card.violet { --accent-color: linear-gradient(90deg, #8B5CF6, #7C3AED); }
.kpi-card.amber  { --accent-color: linear-gradient(90deg, #F59E0B, #D97706); }
.kpi-label {
    font-size: 0.67rem;
    font-weight: 600;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 0.5rem;
}
.kpi-value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.45rem;
    font-weight: 600;
    letter-spacing: -0.03em;
    line-height: 1;
    margin-bottom: 0.3rem;
}
.kpi-value.positive { color: #10B981; }
.kpi-value.negative { color: #EF4444; }
.kpi-value.neutral  { color: #E2E8F0; }
.kpi-value.muted    { color: #94A3B8; }
.kpi-sub {
    font-size: 0.68rem;
    color: #475569;
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: 0.02em;
}

/* ── Trade table ─────────────────────────────────────── */
[data-testid="stDataFrame"] {
    border-radius: 12px !important;
    overflow: hidden !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
}
[data-testid="stDataFrame"] iframe {
    background: #0D1117 !important;
}

/* ── Tabs ────────────────────────────────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background: #0D1117 !important;
    border-radius: 10px !important;
    padding: 4px !important;
    gap: 4px !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    background: transparent !important;
    color: #475569 !important;
    border-radius: 7px !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    padding: 6px 14px !important;
    border: none !important;
}
[data-testid="stTabs"] [aria-selected="true"] {
    background: rgba(255,255,255,0.07) !important;
    color: #E2E8F0 !important;
}
[data-baseweb="tab-panel"] {
    padding-top: 1rem !important;
}

/* ── Inputs ──────────────────────────────────────────── */
[data-testid="stSelectSlider"] > div > div {
    background: #0D1117 !important;
    border-color: rgba(255,255,255,0.08) !important;
}
[data-testid="stToggle"] label { color: #94A3B8 !important; font-size: 0.8rem !important; }

/* ── Expander ────────────────────────────────────────── */
[data-testid="stExpander"] {
    background: #0D1117 !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 12px !important;
}
[data-testid="stExpander"] summary {
    color: #94A3B8 !important;
    font-size: 0.8rem !important;
}

/* ── Info / caption ──────────────────────────────────── */
[data-testid="stCaptionContainer"] { color: #475569 !important; font-size: 0.75rem !important; }
.stAlert { border-radius: 10px !important; border: 1px solid rgba(255,255,255,0.06) !important; }

/* ── Empty state ─────────────────────────────────────── */
.empty-state {
    background: #0D1117;
    border: 1px dashed rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 2rem;
    text-align: center;
    color: #475569;
    font-size: 0.8rem;
}

/* ── Status row ──────────────────────────────────────── */
.status-row {
    display: flex;
    gap: 16px;
    align-items: center;
    margin-bottom: 1.5rem;
    flex-wrap: wrap;
}
.stat-pill {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
    padding: 5px 12px;
    font-size: 0.73rem;
    color: #94A3B8;
    font-family: 'JetBrains Mono', monospace;
    display: flex;
    gap: 6px;
    align-items: center;
}
.stat-pill span { color: #E2E8F0; font-weight: 600; }

/* ── Chart containers ────────────────────────────────── */
.chart-card {
    background: #0D1117;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 14px;
    padding: 1rem;
}
.chart-title {
    font-size: 0.72rem;
    font-weight: 600;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 0.5rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.chart-price {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.1rem;
    font-weight: 600;
    color: #E2E8F0;
}
.chart-change-pos { color: #10B981; font-size: 0.78rem; }
.chart-change-neg { color: #EF4444; font-size: 0.78rem; }

/* ── Positions ouvertes ──────────────────────────────── */
.open-pos-card {
    background: linear-gradient(135deg, rgba(16,185,129,0.06), rgba(16,185,129,0.02));
    border: 1px solid rgba(16,185,129,0.2);
    border-radius: 12px;
    padding: 0.9rem 1rem;
    margin-bottom: 8px;
}
.open-pos-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}
.pos-asset {
    font-weight: 700;
    font-size: 0.9rem;
    color: #E2E8F0;
    display: flex;
    align-items: center;
    gap: 8px;
}
.pos-dir {
    font-size: 0.65rem;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 5px;
    letter-spacing: 0.08em;
}
.pos-dir.long  { background: rgba(16,185,129,0.15); color: #10B981; border: 1px solid rgba(16,185,129,0.3); }
.pos-dir.short { background: rgba(239,68,68,0.12); color: #EF4444; border: 1px solid rgba(239,68,68,0.25); }
.pos-meta {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
}
.pos-field {
    display: flex;
    flex-direction: column;
    gap: 2px;
}
.pos-field-label { font-size: 0.62rem; color: #475569; text-transform: uppercase; letter-spacing: 0.08em; }
.pos-field-value { font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; color: #CBD5E1; font-weight: 500; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# Helpers DB
# ============================================================

def _get_engine():
    return get_engine()


def _session():
    return Session(_get_engine())


def _load_trades() -> pd.DataFrame:
    try:
        with _session() as s:
            rows = s.exec(select(PaperTrade).order_by(PaperTrade.entry_time)).all()
            if not rows:
                return pd.DataFrame()
            records = [r.model_dump() for r in rows]
    except Exception as e:
        st.warning(f"[DB] _load_trades error: {e} | URL: {os.environ.get('DATABASE_URL','NOT SET')[:40]}")
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ["entry_time", "exit_time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True)
    return df


def _load_risk_decisions() -> pd.DataFrame:
    with _session() as s:
        rows = s.exec(
            select(RiskDecisionLog).order_by(RiskDecisionLog.timestamp.desc()).limit(200)
        ).all()
        if not rows:
            return pd.DataFrame()
        records = [r.model_dump() for r in rows]
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _load_prices(asset: str, hours: int = 24) -> pd.DataFrame:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with _session() as s:
        rows = s.exec(
            select(MarketSnapshot)
            .where(MarketSnapshot.asset == asset, MarketSnapshot.timestamp >= since)
            .order_by(MarketSnapshot.timestamp)
        ).all()
        if not rows:
            return pd.DataFrame()
        records = [r.model_dump() for r in rows]
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _load_logs(n_lines: int = 80) -> list[str]:
    log_path = Path("logs/argos.log")
    if not log_path.exists():
        return ["[LOG] Aucun fichier de log trouvé."]
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n_lines:]]
    except Exception as e:
        return [f"[ERR] Erreur lecture logs : {e}"]


def _initial_capital() -> float:
    try:
        import yaml
        cfg_path = Path("config/risk_rules.yaml")
        if not cfg_path.exists():
            cfg_path = Path(__file__).parents[4] / "config" / "risk_rules.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        return float(cfg["paper_trading"]["initial_capital_eur"])
    except Exception:
        return 10.0


# ============================================================
# Calculs
# ============================================================

def _compute_metrics(df: pd.DataFrame, initial_capital: float) -> dict:
    closed = df[df["status"] == TradeStatus.CLOSED.value].copy() if not df.empty else pd.DataFrame()
    open_df = df[df["status"] == TradeStatus.OPEN.value].copy() if not df.empty else pd.DataFrame()

    total_pnl    = closed["net_pnl"].sum() if not closed.empty else 0.0
    current_cap  = initial_capital + total_pnl
    total_trades = len(closed)
    wins   = len(closed[closed["result"] == TradeResult.WIN.value])  if not closed.empty else 0
    losses = len(closed[closed["result"] == TradeResult.LOSS.value]) if not closed.empty else 0
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    total_fees = closed["fees_estimated"].sum() if not closed.empty else 0.0
    best  = closed["net_pnl"].max() if not closed.empty else 0.0
    worst = closed["net_pnl"].min() if not closed.empty else 0.0
    avg_pnl = closed["net_pnl"].mean() if not closed.empty else 0.0

    return {
        "current_capital": current_cap,
        "total_pnl":       total_pnl,
        "win_rate":        win_rate,
        "total_trades":    total_trades,
        "wins":            wins,
        "losses":          losses,
        "total_fees":      total_fees,
        "n_open":          len(open_df),
        "best_trade":      best,
        "worst_trade":     worst,
        "avg_pnl":         avg_pnl,
        "pnl_pct":         (total_pnl / initial_capital * 100) if initial_capital else 0.0,
    }


def _compute_capital_curve(df: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    if df.empty or "status" not in df.columns:
        return pd.DataFrame(columns=["time", "capital"])
    closed = df[df["status"] == TradeStatus.CLOSED.value].copy()
    if closed.empty:
        return pd.DataFrame(columns=["time", "capital"])
    closed = closed.dropna(subset=["exit_time"]).sort_values("exit_time")
    closed["cumulative_pnl"] = closed["net_pnl"].cumsum()
    closed["capital"] = initial_capital + closed["cumulative_pnl"]
    start = pd.DataFrame([{
        "time": closed["exit_time"].iloc[0] - timedelta(seconds=1),
        "capital": initial_capital,
    }])
    return pd.concat([
        start,
        closed.rename(columns={"exit_time": "time"})[["time", "capital"]],
    ], ignore_index=True)


def _compute_drawdown(curve: pd.DataFrame) -> pd.DataFrame:
    if curve.empty:
        return pd.DataFrame(columns=["time", "drawdown_pct"])
    curve = curve.copy()
    curve["peak"] = curve["capital"].cummax()
    curve["drawdown_pct"] = (curve["capital"] - curve["peak"]) / curve["peak"] * 100
    return curve[["time", "drawdown_pct"]]


# ============================================================
# Chart helpers
# ============================================================

_PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, sans-serif", color="#64748B", size=11),
    margin=dict(l=0, r=0, t=4, b=0),
    showlegend=False,
    hovermode="x unified",
    hoverlabel=dict(
        bgcolor="#1E293B",
        bordercolor="rgba(255,255,255,0.1)",
        font=dict(color="#E2E8F0", size=12, family="JetBrains Mono"),
    ),
)

_AXIS_STYLE = dict(
    gridcolor="rgba(255,255,255,0.04)",
    zerolinecolor="rgba(255,255,255,0.08)",
    tickfont=dict(size=10, family="JetBrains Mono"),
    linecolor="rgba(255,255,255,0.06)",
)


def _chart_capital(curve: pd.DataFrame, initial: float) -> go.Figure:
    fig = go.Figure()
    if curve.empty:
        return fig

    last = curve["capital"].iloc[-1]
    color = "#10B981" if last >= initial else "#EF4444"
    fill_color = "rgba(16,185,129,0.08)" if last >= initial else "rgba(239,68,68,0.08)"

    fig.add_hline(
        y=initial, line_dash="dot",
        line_color="rgba(255,255,255,0.12)", line_width=1,
    )
    fig.add_trace(go.Scatter(
        x=curve["time"], y=curve["capital"],
        mode="lines",
        line=dict(color=color, width=2, shape="spline", smoothing=0.8),
        fill="tozeroy", fillcolor=fill_color,
        hovertemplate="<b>%{x|%d/%m %H:%M}</b><br>Capital : %{y:.4f} €<extra></extra>",
    ))
    fig.update_layout(
        height=220,
        xaxis=dict(**_AXIS_STYLE, showgrid=False),
        yaxis=dict(**_AXIS_STYLE, ticksuffix=" €"),
        **_PLOTLY_LAYOUT,
    )
    return fig


def _chart_drawdown(dd: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if dd.empty:
        return fig
    fig.add_trace(go.Scatter(
        x=dd["time"], y=dd["drawdown_pct"],
        mode="lines",
        line=dict(color="#EF4444", width=1.5, shape="spline"),
        fill="tozeroy", fillcolor="rgba(239,68,68,0.07)",
        hovertemplate="<b>%{x|%d/%m %H:%M}</b><br>Drawdown : %{y:.2f}%<extra></extra>",
    ))
    fig.update_layout(
        height=160,
        xaxis=dict(**_AXIS_STYLE, showgrid=False),
        yaxis=dict(**_AXIS_STYLE, ticksuffix="%"),
        **_PLOTLY_LAYOUT,
    )
    return fig


def _chart_pnl_bars(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        return fig
    closed = df[df["status"] == TradeStatus.CLOSED.value].dropna(subset=["net_pnl", "exit_time"]).sort_values("exit_time")
    if closed.empty:
        return fig

    colors = [
        "#10B981" if r == TradeResult.WIN.value else
        "#EF4444" if r == TradeResult.LOSS.value else "#64748B"
        for r in closed["result"]
    ]
    fig.add_trace(go.Bar(
        x=list(range(len(closed))),
        y=closed["net_pnl"],
        marker=dict(
            color=colors,
            line=dict(width=0),
            opacity=0.85,
        ),
        hovertemplate="Trade #%{x}<br>P&L net : %{y:+.4f} €<extra></extra>",
    ))
    fig.add_hline(y=0, line_color="rgba(255,255,255,0.1)", line_width=1)
    fig.update_layout(
        height=180,
        xaxis=dict(**_AXIS_STYLE, showgrid=False, title=None),
        yaxis=dict(**_AXIS_STYLE, ticksuffix=" €"),
        bargap=0.3,
        **_PLOTLY_LAYOUT,
    )
    return fig


def _chart_price(df: pd.DataFrame, asset: str) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        return fig

    last  = df["price"].iloc[-1]
    first = df["price"].iloc[0]
    up    = last >= first
    color = "#10B981" if up else "#EF4444"
    fill  = "rgba(16,185,129,0.07)" if up else "rgba(239,68,68,0.07)"

    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["price"],
        mode="lines",
        line=dict(color=color, width=2, shape="spline", smoothing=0.6),
        fill="tozeroy", fillcolor=fill,
        hovertemplate=f"<b>%{{x|%d/%m %H:%M}}</b><br>{asset} : %{{y:,.2f}} €<extra></extra>",
    ))
    fig.update_layout(
        height=160,
        xaxis=dict(**_AXIS_STYLE, showgrid=False),
        yaxis=dict(**_AXIS_STYLE, ticksuffix=" €"),
        **_PLOTLY_LAYOUT,
    )
    return fig


def _chart_winrate_donut(wins: int, losses: int) -> go.Figure:
    other = max(0, losses)
    fig = go.Figure(go.Pie(
        values=[wins, other],
        labels=["Wins", "Losses"],
        hole=0.72,
        marker=dict(
            colors=["#10B981", "#EF4444"],
            line=dict(color="#080B12", width=3),
        ),
        textinfo="none",
        hovertemplate="%{label}: %{value}<extra></extra>",
    ))
    fig.update_layout(
        height=130,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


# ============================================================
# KPI Cards
# ============================================================

def _kpi_card(label: str, value: str, color: str, sub: str = "") -> str:
    val_class = "positive" if color == "green" else ("negative" if color == "red" else "neutral")
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    return f"""
    <div class="kpi-card {color}">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value {val_class}">{value}</div>
        {sub_html}
    </div>"""


def _render_kpis(m: dict, initial: float):
    pnl      = m["total_pnl"]
    pnl_pct  = m["pnl_pct"]
    cap      = m["current_capital"]
    wr       = m["win_rate"]
    pnl_col  = "green" if pnl >= 0 else "red"
    wr_col   = "green" if wr >= 60 else ("amber" if wr >= 45 else "red")

    cards = [
        ("Capital", f"{cap:.4f} €", "blue",   f"init {initial:.2f} €"),
        ("P&L net",  f"{pnl:+.4f} €", pnl_col, f"{pnl_pct:+.2f}%"),
        ("Win rate", f"{wr:.1f}%",     wr_col,  f"{m['wins']}W / {m['losses']}L"),
        ("Trades",   str(m["total_trades"]), "violet", "clôturés"),
        ("Ouverts",  str(m["n_open"]),       "amber",  "en cours"),
        ("Frais",    f"{m['total_fees']:.4f} €", "red", "simulés"),
    ]
    html = '<div class="kpi-grid">' + "".join(_kpi_card(*c) for c in cards) + '</div>'
    st.markdown(html, unsafe_allow_html=True)


# ============================================================
# Positions ouvertes
# ============================================================

def _render_open_positions(df: pd.DataFrame):
    open_df = df[df["status"] == TradeStatus.OPEN.value].copy() if not df.empty else pd.DataFrame()
    if open_df.empty:
        st.markdown('<div class="empty-state">⬡ Aucune position ouverte — le bot surveille le marché</div>', unsafe_allow_html=True)
        return

    for _, row in open_df.iterrows():
        dir_str = str(row.get("direction", "")).upper()
        dir_cls = "long" if dir_str == "LONG" else "short"
        entry_t = row["entry_time"].strftime("%d/%m %H:%M") if pd.notna(row.get("entry_time")) else "—"

        st.markdown(f"""
        <div class="open-pos-card">
            <div class="open-pos-header">
                <div class="pos-asset">
                    {row.get("asset", "—")}
                    <span class="pos-dir {dir_cls}">{dir_str}</span>
                </div>
                <div style="font-family:'JetBrains Mono',monospace;font-size:0.72rem;color:#475569">{entry_t}</div>
            </div>
            <div class="pos-meta">
                <div class="pos-field"><span class="pos-field-label">Entrée</span><span class="pos-field-value">{row.get("entry_price", 0):,.2f} €</span></div>
                <div class="pos-field"><span class="pos-field-label">SL</span><span class="pos-field-value" style="color:#EF4444">{row.get("stop_loss", 0):,.2f} €</span></div>
                <div class="pos-field"><span class="pos-field-label">TP</span><span class="pos-field-value" style="color:#10B981">{row.get("take_profit", 0):,.2f} €</span></div>
                <div class="pos-field"><span class="pos-field-label">Taille</span><span class="pos-field-value">{row.get("position_size_eur", 0):.2f} €</span></div>
                <div class="pos-field"><span class="pos-field-label">Confiance</span><span class="pos-field-value">{row.get("confidence_score", 0):.0%}</span></div>
            </div>
        </div>""", unsafe_allow_html=True)


# ============================================================
# Trades table
# ============================================================

def _render_trades_table(df: pd.DataFrame):
    closed = df[df["status"] == TradeStatus.CLOSED.value].copy() if not df.empty else pd.DataFrame()
    if closed.empty:
        st.markdown('<div class="empty-state">Aucun trade clôturé pour l\'instant.</div>', unsafe_allow_html=True)
        return

    display = closed.sort_values("exit_time", ascending=False).head(50)[[
        "id", "asset", "direction", "entry_time", "exit_time",
        "entry_price", "exit_price", "position_size_eur",
        "net_pnl", "return_percent", "result",
    ]].copy()

    display["entry_time"] = display["entry_time"].dt.strftime("%d/%m %H:%M")
    display["exit_time"]  = display["exit_time"].dt.strftime("%d/%m %H:%M")
    display["entry_price"]  = display["entry_price"].map(lambda x: f"{x:,.2f}")
    display["exit_price"]   = display["exit_price"].map(lambda x: f"{x:,.2f}" if pd.notna(x) else "—")
    display["net_pnl"]      = display["net_pnl"].map(lambda x: f"{x:+.4f}" if pd.notna(x) else "—")
    display["return_percent"] = display["return_percent"].map(lambda x: f"{x:+.2f}%" if pd.notna(x) else "—")

    display.columns = ["#", "Asset", "Dir", "Entrée", "Sortie",
                        "Prix entrée", "Prix sortie", "Taille €",
                        "Net P&L", "Rdt", "Résultat"]

    def _color(row):
        if row["Résultat"] == TradeResult.WIN.value:
            return ["background-color: rgba(16,185,129,0.07)"] * len(row)
        if row["Résultat"] == TradeResult.LOSS.value:
            return ["background-color: rgba(239,68,68,0.07)"] * len(row)
        return [""] * len(row)

    st.dataframe(
        display.style.apply(_color, axis=1),
        use_container_width=True,
        height=320,
        hide_index=True,
    )


# ============================================================
# Risk decisions
# ============================================================

def _render_risk_table(risk_df: pd.DataFrame):
    if risk_df.empty:
        st.markdown('<div class="empty-state">Aucune décision de risque enregistrée.</div>', unsafe_allow_html=True)
        return

    refused  = risk_df[risk_df["decision"] == RiskDecision.REJECTED.value]
    approved = risk_df[risk_df["decision"] == RiskDecision.APPROVED.value]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f'<div class="stat-pill" style="margin-bottom:8px">Refus <span>{len(refused)}</span></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="stat-pill" style="margin-bottom:8px">Approuvés <span>{len(approved)}</span></div>', unsafe_allow_html=True)

    if refused.empty:
        st.markdown('<div class="empty-state">Aucun trade refusé.</div>', unsafe_allow_html=True)
        return

    display = refused.head(40)[[
        "timestamp", "asset", "direction",
        "proposed_entry", "reason", "rule_triggered",
    ]].copy()
    display["timestamp"] = display["timestamp"].dt.strftime("%d/%m %H:%M")
    display["proposed_entry"] = display["proposed_entry"].map(
        lambda x: f"{x:,.2f}" if pd.notna(x) else "—"
    )
    display.columns = ["Heure", "Asset", "Dir", "Entrée", "Raison", "Règle"]
    st.dataframe(display, use_container_width=True, height=260, hide_index=True)


# ============================================================
# Layout principal
# ============================================================

def main():
    now_str = datetime.now().strftime("%d/%m/%Y  %H:%M:%S")

    # ── Header ───────────────────────────────────────────────
    st.markdown(f"""
    <div class="argos-header">
        <div class="argos-logo">
            <div class="argos-logo-icon">⬡</div>
            <div>
                <div class="argos-logo-text">ARGOS</div>
                <div class="argos-logo-sub">Trading Terminal</div>
            </div>
        </div>
        <div class="argos-badges">
            <div class="badge badge-live">
                <div class="live-dot"></div>
                LIVE
            </div>
            <div class="badge badge-paper">PAPER MODE</div>
            <div class="header-time">{now_str}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Données ───────────────────────────────────────────────
    initial_capital = _initial_capital()
    trades_df       = _load_trades()
    risk_df         = _load_risk_decisions()
    metrics         = _compute_metrics(trades_df, initial_capital)
    curve           = _compute_capital_curve(trades_df, initial_capital)
    dd              = _compute_drawdown(curve)

    # ── Status pills ──────────────────────────────────────────
    btc_df    = _load_prices("BTC", 1)
    btc_price = f"{btc_df['price'].iloc[-1]:,.0f} €" if not btc_df.empty else "—"

    st.markdown(f"""
    <div class="status-row">
        <div class="stat-pill">BTC <span>{btc_price}</span></div>
        <div class="stat-pill">Capital <span>{metrics["current_capital"]:.4f} €</span></div>
        <div class="stat-pill">Trades <span>{metrics["total_trades"]}</span></div>
        <div class="stat-pill">Win rate <span>{metrics["win_rate"]:.1f}%</span></div>
        <div class="stat-pill">Open <span>{metrics["n_open"]}</span></div>
    </div>
    """, unsafe_allow_html=True)

    # ── KPIs ──────────────────────────────────────────────────
    _render_kpis(metrics, initial_capital)

    # ── Tabs principaux ───────────────────────────────────────
    tab_perf, tab_prix, tab_trades, tab_risk, tab_logs = st.tabs([
        "📈  Performance", "₿  Marché", "🗂  Trades", "🛡  Risk", "📋  Logs",
    ])

    # ╔══════════════════════════════╗
    # ║  TAB PERFORMANCE             ║
    # ╚══════════════════════════════╝
    with tab_perf:
        col_cap, col_win = st.columns([3, 1])

        with col_cap:
            st.markdown('<div class="section-label">Courbe de capital</div>', unsafe_allow_html=True)
            if curve.empty:
                st.markdown('<div class="empty-state">La courbe apparaîtra après le premier trade clôturé.</div>', unsafe_allow_html=True)
            else:
                fig = _chart_capital(curve, initial_capital)
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        with col_win:
            st.markdown('<div class="section-label">Win / Loss</div>', unsafe_allow_html=True)
            if metrics["total_trades"] > 0:
                fig_donut = _chart_winrate_donut(metrics["wins"], metrics["losses"])
                st.plotly_chart(fig_donut, use_container_width=True, config={"displayModeBar": False})
                wr_color = "#10B981" if metrics["win_rate"] >= 60 else ("#F59E0B" if metrics["win_rate"] >= 45 else "#EF4444")
                st.markdown(f"""
                <div style="text-align:center;margin-top:-0.5rem">
                    <div style="font-family:'JetBrains Mono',monospace;font-size:1.6rem;font-weight:700;color:{wr_color}">{metrics['win_rate']:.0f}%</div>
                    <div style="font-size:0.68rem;color:#475569;letter-spacing:0.08em;text-transform:uppercase">win rate</div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown('<div class="empty-state">—</div>', unsafe_allow_html=True)

        # Drawdown
        st.markdown('<div class="section-label">Drawdown</div>', unsafe_allow_html=True)
        if dd.empty or dd["drawdown_pct"].min() == 0:
            st.markdown('<div class="empty-state">Aucun drawdown enregistré — capital intact.</div>', unsafe_allow_html=True)
        else:
            fig_dd = _chart_drawdown(dd)
            st.plotly_chart(fig_dd, use_container_width=True, config={"displayModeBar": False})

        # P&L bar
        st.markdown('<div class="section-label">P&L par trade</div>', unsafe_allow_html=True)
        if trades_df.empty:
            st.markdown('<div class="empty-state">Aucun trade pour l\'instant.</div>', unsafe_allow_html=True)
        else:
            fig_bars = _chart_pnl_bars(trades_df)
            st.plotly_chart(fig_bars, use_container_width=True, config={"displayModeBar": False})

        # Métriques avancées
        if metrics["total_trades"] > 0:
            st.markdown('<div class="section-label">Statistiques avancées</div>', unsafe_allow_html=True)
            a1, a2, a3, a4 = st.columns(4)
            a1.metric("Meilleur trade", f"{metrics['best_trade']:+.4f} €")
            a2.metric("Pire trade",     f"{metrics['worst_trade']:+.4f} €")
            a3.metric("P&L moyen",      f"{metrics['avg_pnl']:+.4f} €")
            a4.metric("Perf. totale",   f"{metrics['pnl_pct']:+.2f}%")

        # Positions ouvertes
        st.markdown('<div class="section-label">Positions ouvertes</div>', unsafe_allow_html=True)
        _render_open_positions(trades_df)

    # ╔══════════════════════════════╗
    # ║  TAB MARCHÉ                  ║
    # ╚══════════════════════════════╝
    with tab_prix:
        price_hours = st.select_slider(
            "Fenêtre temporelle",
            options=[1, 4, 12, 24, 48, 72],
            value=24,
            format_func=lambda x: f"{x}h",
        )
        btc_df_full = _load_prices("BTC", price_hours)

        if not btc_df_full.empty:
            last_p  = btc_df_full["price"].iloc[-1]
            first_p = btc_df_full["price"].iloc[0]
            chg     = (last_p - first_p) / first_p * 100
            chg_cls = "chart-change-pos" if chg >= 0 else "chart-change-neg"

            st.markdown(f"""
            <div class="chart-title">
                <span>BTC / EUR</span>
                <div style="display:flex;align-items:center;gap:12px">
                    <span class="chart-price">{last_p:,.2f} €</span>
                    <span class="{chg_cls}">{chg:+.2f}%</span>
                </div>
            </div>""", unsafe_allow_html=True)

            fig_btc = _chart_price(btc_df_full, "BTC")
            st.plotly_chart(fig_btc, use_container_width=True, config={"displayModeBar": False})

            # Volume si dispo
            if "volume_24h" in btc_df_full.columns and btc_df_full["volume_24h"].notna().any():
                last_vol = btc_df_full["volume_24h"].dropna().iloc[-1]
                st.markdown(f'<div class="stat-pill" style="display:inline-flex">Volume 24h <span>{last_vol/1e9:.1f}B €</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="empty-state">Aucune donnée de marché disponible.</div>', unsafe_allow_html=True)

    # ╔══════════════════════════════╗
    # ║  TAB TRADES                  ║
    # ╚══════════════════════════════╝
    with tab_trades:
        st.markdown('<div class="section-label">Historique — 50 derniers trades</div>', unsafe_allow_html=True)
        _render_trades_table(trades_df)

    # ╔══════════════════════════════╗
    # ║  TAB RISK                    ║
    # ╚══════════════════════════════╝
    with tab_risk:
        st.markdown('<div class="section-label">Décisions du risk manager</div>', unsafe_allow_html=True)
        _render_risk_table(risk_df)

    # ╔══════════════════════════════╗
    # ║  TAB LOGS                    ║
    # ╚══════════════════════════════╝
    with tab_logs:
        col_l, col_r = st.columns([4, 1])
        with col_l:
            st.markdown('<div class="section-label">Logs système</div>', unsafe_allow_html=True)
        with col_r:
            n_lines = st.select_slider("Lignes", options=[40, 80, 120, 200], value=80, label_visibility="collapsed")

        lines = _load_logs(n_lines)
        st.code("\n".join(lines), language=None)

    # ── Footer ────────────────────────────────────────────────
    st.markdown(f"""
    <div style="margin-top:2rem;padding-top:1rem;border-top:1px solid rgba(255,255,255,0.04);
                display:flex;justify-content:space-between;align-items:center">
        <div style="font-size:0.68rem;color:#2D3748;font-family:'JetBrains Mono',monospace">
            ARGOS v2.0 · paper-only · {os.environ.get("DATABASE_PATH","data/argos.db")}
        </div>
        <div style="font-size:0.68rem;color:#2D3748">
            {'<span style="color:#10B981">●</span> Secure — aucun argent réel'}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Auto-refresh (si activé via URL param)
    if st.query_params.get("refresh") == "1":
        st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
