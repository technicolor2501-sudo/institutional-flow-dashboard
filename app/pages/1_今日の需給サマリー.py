"""
今日の需給サマリー — 信号機形式でフラグを一覧表示
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
import yfinance as yf

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from fetchers.jpx_short_selling import JpxShortSellingFetcher
from processors.gex_calculator import total_gex_summary, gex_by_strike, find_gamma_flip
from processors.sq_calendar import SqCalendar

st.set_page_config(page_title="今日の需給サマリー", page_icon="🚦", layout="wide")
st.title("🚦 今日の需給サマリー")
st.caption(f"基準日: {date.today().strftime('%Y年%m月%d日')}")


def signal(condition: bool | None, label_true: str, label_false: str, label_none: str = "データなし") -> str:
    """条件に応じた信号アイコン＋ラベルを返す。"""
    if condition is None:
        return f"⬜ {label_none}"
    return f"🟢 {label_true}" if condition else f"🔴 {label_false}"


def warn_signal(condition: bool | None, label_warn: str, label_ok: str) -> str:
    """警戒なら赤、問題なしなら緑。"""
    if condition is None:
        return "⬜ データなし"
    return f"🔴 {label_warn}" if condition else f"🟢 {label_ok}"


# ── データ読み込み ────────────────────────────────────────────────────────
@st.cache_data(ttl=900)
def load_data():
    cal = SqCalendar()
    sq_info = cal.next_jp_sq()
    is_sq_week = cal.is_sq_week()
    is_month_end = cal.is_month_end_period()

    # 空売り比率
    short_fetcher = JpxShortSellingFetcher()
    short_info = short_fetcher.latest_ratio()

    # 日経225スポット（yfinance）
    spot = None
    try:
        tk = yf.Ticker("^N225")
        hist = tk.history(period="2d")
        if not hist.empty:
            spot = float(hist["Close"].iloc[-1])
    except Exception:
        pass

    # GEXデータ読み込み
    gex_files = sorted((ROOT / "data" / "gex").glob("*.parquet"))
    gex_summary = None
    gamma_flip = None
    if gex_files:
        try:
            gex_df = pd.read_parquet(gex_files[-1])
            if spot and not gex_df.empty:
                gex_summary = total_gex_summary(gex_df, spot)
                by_strike = gex_by_strike(gex_df)
                gamma_flip = find_gamma_flip(by_strike)
        except Exception:
            pass

    return {
        "sq_info": sq_info,
        "is_sq_week": is_sq_week,
        "is_month_end": is_month_end,
        "short_info": short_info,
        "spot": spot,
        "gex_summary": gex_summary,
        "gamma_flip": gamma_flip,
    }


with st.spinner("データ読み込み中..."):
    data = load_data()

# ── シグナルカード ─────────────────────────────────────────────────────────
st.subheader("📌 本日のフラグ一覧")

col1, col2, col3 = st.columns(3)

# SQ 情報
with col1:
    sq = data["sq_info"]
    if sq:
        sq_label = f"SQ週（残り{sq['days_until']}日、{sq['label']}）"
        no_sq_label = f"SQ週ではない（次回 {sq['label']} まで {sq['days_until']} 日）"
        st.markdown(f"**🗓 SQ週フラグ**")
        st.info(signal(data["is_sq_week"], sq_label, no_sq_label))
        if data["is_sq_week"]:
            st.warning("SQ週はポジション解消フローが出やすい。特にメジャーSQ週は要注意。")
    else:
        st.markdown("**🗓 SQ情報**")
        st.warning("⬜ SQ情報なし")

# 月末リバランス
with col2:
    st.markdown("**📅 月末リバランス期間**")
    st.info(
        signal(
            data["is_month_end"],
            "月末リバランス期間（最終5営業日）",
            "月末リバランス期間外",
        )
    )
    if data["is_month_end"]:
        st.warning("年金・バランスファンドのリバランス売買が集中しやすい。")

# 空売り比率
with col3:
    st.markdown("**📉 空売り比率（プライム市場）**")
    si = data["short_info"]
    if si and si.get("short_ratio") is not None:
        ratio = si["short_ratio"]
        ratio_str = f"{ratio:.1f}%"
        if ratio > 45:
            label = f"🔴 高水準 {ratio_str}（踏み上げリスク）"
        elif ratio < 35:
            label = f"🟢 低水準 {ratio_str}（買い安心感）"
        else:
            label = f"🟡 通常水準 {ratio_str}"
        st.info(label)
        st.caption(f"取引日: {si['trade_date']}")
    elif si:
        # PDFソースは対総出来高比率を含まない → 構成比を表示
        reg = si.get("regulated_pct", 0)
        unreg = si.get("unregulated_pct", 0)
        margin = si.get("margin_pct", 0)
        total_k = si.get("total_short_k", 0)
        st.info(
            f"🟡 空売り量: {total_k:,.0f}千株  \n"
            f"内訳 規制あり {reg:.1f}% / 規制なし {unreg:.1f}% / 信用 {margin:.1f}%"
        )
        st.caption(
            f"取引日: {si.get('trade_date', '不明')}  ※JPX日次集計PDF（対総出来高比率は非掲載）"
        )
    else:
        st.warning("⬜ データなし（取得ページを確認）")

st.markdown("---")

# ── GEX環境 ────────────────────────────────────────────────────────────────
st.subheader("⚡ オプション需給環境（GEX）")
col4, col5 = st.columns(2)

with col4:
    gs = data["gex_summary"]
    if gs and gs.get("total_gex") is not None:
        total = gs["total_gex"]
        env = gs["gex_environment"]
        is_positive = total >= 0
        env_label = "正GEX — 値動き抑制（レンジ傾向）" if is_positive else "負GEX — ボラ拡大警戒"
        gex_bn = total / 1e9
        st.metric("合計 GEX（兆円換算）", f"¥{gex_bn:+.2f}兆", delta=env_label)
        if is_positive:
            st.success("ディーラーはロング・ガンマ環境。上昇時は売り、下落時は買いが出やすく値動きが抑制される。")
        else:
            st.error("ディーラーはショート・ガンマ環境。上昇・下落が加速しやすい。")
    else:
        st.warning("⬜ GEXデータなし")

with col5:
    spot = data["spot"]
    gf = data["gamma_flip"]
    if spot and gf:
        diff = spot - gf
        pct = diff / gf * 100
        above = spot > gf
        label = "スポットがフリップより上（正GEX圏）" if above else "スポットがフリップより下（負GEX圏）"
        st.metric(
            f"ガンマ・フリップ水準",
            f"¥{gf:,.0f}",
            delta=f"現在値 ¥{spot:,.0f}（フリップから {pct:+.1f}%）",
            delta_color="normal" if above else "inverse",
        )
        if above:
            st.success(label)
        else:
            st.error(label)
    elif spot:
        st.metric("日経225現在値", f"¥{spot:,.0f}")
        st.warning("ガンマ・フリップ計算にはオプションデータが必要です")
    else:
        st.warning("⬜ 価格データなし")

st.markdown("---")

# ── 曜日フロー傾向（静的テーブル、D4 の先行表示）─────────────────────────
st.subheader("📆 曜日別フロー傾向（経験則・参考）")
st.caption("週次データ（A1/A2）蓄積後に統計化予定。現在は経験則のみ。")

weekday_guide = pd.DataFrame({
    "曜日": ["月", "火", "水", "木", "金"],
    "海外勢フロー傾向": ["様子見", "やや買い", "フラット", "週次集計公表", "週末手仕舞い売り"],
    "信託銀行（年金）": ["月初買い多め", "フラット", "フラット", "フラット", "リバランス調整"],
    "注意イベント": ["先週末CME日経", "信用残公表", "-", "投資部門別・COT", "SQ清算（SQ週）"],
})
st.dataframe(weekday_guide, use_container_width=True, hide_index=True)

st.caption(
    "⚠️ 週次データ（投資部門別・COT）は前週分の遅行情報。リアルタイムの機関フローではありません。"
)
