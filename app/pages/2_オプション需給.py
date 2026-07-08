"""
オプション需給 — GEX/DEXプロファイル・ガンマフリップ・IV期間構造
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
import yfinance as yf

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from processors.gex_calculator import (
    gex_by_strike,
    find_gamma_flip,
    total_gex_summary,
)
from processors.sq_calendar import SqCalendar

st.set_page_config(page_title="オプション需給", page_icon="⚡", layout="wide")
st.title("⚡ オプション需給（GEX / DEX / Charm / Vanna）")
st.caption(f"基準日: {date.today().strftime('%Y年%m月%d日')}")


@st.cache_data(ttl=900)
def load_gex_data():
    gex_dir = ROOT / "data" / "gex"
    files = sorted(gex_dir.glob("*.parquet"))
    if not files:
        return None, None
    latest = pd.read_parquet(files[-1])
    return latest, files[-1].stem  # DataFrame, date string


@st.cache_data(ttl=900)
def load_options_data():
    opt_dir = ROOT / "data" / "jpx_options"
    files = sorted(opt_dir.glob("*.parquet"))
    if not files:
        return None
    return pd.read_parquet(files[-1])


@st.cache_data(ttl=300)
def get_spot():
    try:
        tk = yf.Ticker("^N225")
        hist = tk.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


# ── データ読み込み ────────────────────────────────────────────────────────
gex_df, gex_date = load_gex_data()
spot = get_spot()

if gex_df is None or gex_df.empty:
    st.error(
        "GEXデータが見つかりません。  \n"
        "`scripts/run_fetchers.py` を実行するか、GitHub Actions を確認してください。"
    )
    st.stop()

if spot is None:
    st.warning("日経225スポット価格の取得に失敗。yfinance を確認してください。")
    spot = 0.0

st.info(f"データ取得日: **{gex_date}**　｜　日経225現在値: **¥{spot:,.0f}**")

# ── GEX プロファイル ───────────────────────────────────────────────────────
st.subheader("📊 GEX プロファイル（権利行使価格別）")

by_strike = gex_by_strike(gex_df)
gamma_flip = find_gamma_flip(by_strike)
summary = total_gex_summary(gex_df, spot) if spot else {}

# フィルタ: 現在値 ± 15% の範囲に絞る（視認性向上）
if spot > 0:
    lower = spot * 0.85
    upper = spot * 1.15
    view = by_strike[(by_strike["strike"] >= lower) & (by_strike["strike"] <= upper)]
else:
    view = by_strike

if view.empty:
    st.warning("表示範囲内にデータがありません")
else:
    fig = go.Figure()

    # コール GEX（上向き棒グラフ）
    fig.add_trace(go.Bar(
        x=view["strike"],
        y=view["call_gex"] / 1e9,
        name="コール GEX",
        marker_color="rgba(0, 200, 100, 0.7)",
    ))

    # プット GEX（下向き棒グラフ）
    fig.add_trace(go.Bar(
        x=view["strike"],
        y=view["put_gex"] / 1e9,
        name="プット GEX",
        marker_color="rgba(255, 60, 60, 0.7)",
    ))

    # 現在値の縦線
    if spot > 0:
        fig.add_vline(
            x=spot, line_dash="solid", line_color="#ffffff",
            annotation_text=f"現在値 ¥{spot:,.0f}",
            annotation_position="top right",
            annotation_font_color="#ffffff",
        )

    # ガンマ・フリップラインの縦線
    if gamma_flip:
        fig.add_vline(
            x=gamma_flip, line_dash="dash", line_color="#ffcc00",
            annotation_text=f"γ-Flip ¥{gamma_flip:,.0f}",
            annotation_position="top left",
            annotation_font_color="#ffcc00",
        )

    fig.update_layout(
        barmode="relative",
        title="GEX プロファイル（兆円）",
        xaxis_title="権利行使価格",
        yaxis_title="GEX（兆円）",
        template="plotly_dark",
        legend=dict(orientation="h", y=1.1),
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True)

# ── サマリーメトリクス ─────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
total_gex = summary.get("total_gex")
with col1:
    st.metric(
        "合計 GEX",
        f"¥{total_gex/1e9:+.2f}兆" if total_gex is not None else "N/A",
        delta=summary.get("gex_environment", ""),
        delta_color="normal" if (total_gex or 0) >= 0 else "inverse",
    )
with col2:
    st.metric(
        "ガンマ・フリップ",
        f"¥{gamma_flip:,.0f}" if gamma_flip else "N/A",
        delta=f"現在値まで {(spot - gamma_flip)/gamma_flip*100:+.1f}%" if gamma_flip and spot else "",
    )
with col3:
    total_dex = summary.get("total_dex")
    st.metric(
        "合計 DEX",
        f"¥{total_dex/1e9:+.2f}兆" if total_dex is not None else "N/A",
    )
with col4:
    cal = SqCalendar()
    sq = cal.next_jp_sq()
    st.metric(
        "次回SQ",
        sq["label"] if sq else "N/A",
        delta=f"残り {sq['days_until']} 日" if sq else "",
    )

st.markdown("---")

# ── DEX プロファイル ───────────────────────────────────────────────────────
st.subheader("📈 DEX プロファイル（デルタ・エクスポージャー）")
st.caption("DEX > 0: デルタヘッジの買い需要、DEX < 0: 売り圧力")

if not view.empty:
    fig_dex = go.Figure()
    fig_dex.add_trace(go.Bar(
        x=view["strike"],
        y=view["net_dex"] / 1e9,
        name="純 DEX",
        marker_color=view["net_dex"].apply(
            lambda v: "rgba(0,180,90,0.7)" if v >= 0 else "rgba(255,60,60,0.7)"
        ),
    ))
    if spot > 0:
        fig_dex.add_vline(x=spot, line_dash="solid", line_color="#ffffff")
    fig_dex.update_layout(
        title="DEX プロファイル（兆円）",
        xaxis_title="権利行使価格",
        yaxis_title="DEX（兆円）",
        template="plotly_dark",
        height=350,
    )
    st.plotly_chart(fig_dex, use_container_width=True)

# ── Charm / Vanna ─────────────────────────────────────────────────────────
st.subheader("🔀 Charm / Vanna エクスポージャー")
col_c, col_v = st.columns(2)

with col_c:
    st.caption("**Charm**: 時間経過でデルタが変化する度合い（SQ近接日に重要）")
    if "net_charm" in view.columns and not view.empty:
        fig_charm = px.bar(
            view, x="strike", y="net_charm",
            color="net_charm", color_continuous_scale="RdYlGn",
            title="Charm エクスポージャー",
            template="plotly_dark",
        )
        fig_charm.update_layout(height=320, coloraxis_showscale=False)
        if spot > 0:
            fig_charm.add_vline(x=spot, line_dash="solid", line_color="#ffffff")
        st.plotly_chart(fig_charm, use_container_width=True)

with col_v:
    st.caption("**Vanna**: IVが変化したときのデルタ変化（VIX急変時に重要）")
    if "net_vanna" in view.columns and not view.empty:
        fig_vanna = px.bar(
            view, x="strike", y="net_vanna",
            color="net_vanna", color_continuous_scale="RdYlGn",
            title="Vanna エクスポージャー",
            template="plotly_dark",
        )
        fig_vanna.update_layout(height=320, coloraxis_showscale=False)
        if spot > 0:
            fig_vanna.add_vline(x=spot, line_dash="solid", line_color="#ffffff")
        st.plotly_chart(fig_vanna, use_container_width=True)

# ── P/C 比率 ──────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📉 Put/Call 比率（建玉ベース）")

opt_df = load_options_data()
if opt_df is not None and "option_type" in opt_df.columns and "open_interest" in opt_df.columns:
    call_oi = opt_df[opt_df["option_type"] == "C"]["open_interest"].sum()
    put_oi = opt_df[opt_df["option_type"] == "P"]["open_interest"].sum()
    pc_ratio = put_oi / call_oi if call_oi > 0 else None

    c1, c2, c3 = st.columns(3)
    c1.metric("コール建玉合計", f"{call_oi:,.0f}" if call_oi else "N/A")
    c2.metric("プット建玉合計", f"{put_oi:,.0f}" if put_oi else "N/A")
    if pc_ratio is not None:
        sentiment = (
            "🟢 コール優位（強気傾向）" if pc_ratio < 0.8 else
            "🔴 プット優位（ヘッジ需要・弱気傾向）" if pc_ratio > 1.2 else
            "🟡 中立"
        )
        c3.metric("P/C 比率", f"{pc_ratio:.2f}", delta=sentiment)
else:
    st.info("P/C 比率の計算にはオプション建玉データが必要です。")

st.markdown("---")
st.caption(
    "⚠️ GEX はディーラーがオプション建玉の反対側に立つという仮定に基づく近似です。"
    "公開建玉から個人投資家・機関投資家・ディーラーのポジションを区別することはできません。"
)
