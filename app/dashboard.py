"""
需給フロー・ダッシュボード — ホームページ

Streamlit Community Cloud でホストし、GitHubリポジトリの data/ から
コミット済みデータを読み取る。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

# プロジェクトルートをパスに追加（ローカル実行用）
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

st.set_page_config(
    page_title="需給フロー・ダッシュボード",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── スタイル ──────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .metric-card {
        background: #1e1e2e;
        border-radius: 10px;
        padding: 16px 20px;
        margin: 4px 0;
    }
    .signal-green  { color: #00ff88; font-size: 1.4em; }
    .signal-yellow { color: #ffcc00; font-size: 1.4em; }
    .signal-red    { color: #ff4466; font-size: 1.4em; }
    .freshness-ok  { color: #00cc77; font-size: 0.8em; }
    .freshness-err { color: #ff4444; font-size: 0.8em; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── ヘッダー ──────────────────────────────────────────────────────────────
st.title("📊 需給フロー・ダッシュボード")
st.caption("機関投資家が参照する公開需給データを自動取得・可視化")

st.info(
    "**Phase 1 MVP** — 対象: 日経225オプション GEX/DEX・空売り比率・SQカレンダー  \n"
    "Phase 2 以降: 投資部門別フロー (A1/A2)・COT (C1)・VIX・ネット流動性を追加予定",
    icon="ℹ️",
)

# ── 実行ステータス ────────────────────────────────────────────────────────
status_path = ROOT / "data" / "run_status.json"
with st.expander("🔄 最終データ取得ステータス", expanded=False):
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        st.write(f"**実行日時 (UTC):** {status.get('run_at', '不明')}")
        cols = st.columns(len(status.get("fetchers", {})) or 1)
        for idx, (name, info) in enumerate(status.get("fetchers", {}).items()):
            with cols[idx % len(cols)]:
                ok = info.get("status") == "ok"
                icon = "✅" if ok else "❌"
                rows = info.get("rows", "")
                st.metric(
                    label=f"{icon} {name}",
                    value="正常" if ok else "エラー",
                    delta=f"{rows}行" if rows else info.get("error", ""),
                )
    else:
        st.warning("まだデータ取得が実行されていません。README の初期セットアップを確認してください。")

# ── ナビゲーションガイド ──────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 ページ一覧")

col1, col2 = st.columns(2)
with col1:
    st.markdown(
        """
        **1️⃣ 今日の需給サマリー**
        - SQ週フラグ / 月末リバランス期間
        - GEX 環境（正 or 負）
        - ガンマ・フリップ vs 現在値
        - 空売り比率
        """
    )
with col2:
    st.markdown(
        """
        **2️⃣ オプション需給**
        - GEX プロファイル（権利行使価格別）
        - ガンマ・フリップ水準
        - DEX / Vanna / Charm の推移
        - P/C 比率
        """
    )

# ── 免責事項 ──────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "⚠️ **免責事項**: 本ツールは公開データからの情報可視化であり、投資助言ではありません。"
    "週次データは数日遅行します。実際の機関フロー（信託銀行内部・証券会社顧客フロー等）は取得不可のため、"
    "本ツールは公開データからの近似・再現です。投資判断はご自身の責任で行ってください。"
)
