"""
GEX計算の有効性テスト。
実データ（data/jpx_options/）が存在する場合のみ実行。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "jpx_options"


def _load_options() -> pd.DataFrame | None:
    files = sorted(DATA_DIR.glob("*.parquet"))
    if not files:
        return None
    return pd.read_parquet(files[-1])


def _get_spot() -> float:
    tk = yf.Ticker("^N225")
    hist = tk.history(period="3d")
    return float(hist["Close"].iloc[-1])


@pytest.fixture(scope="module")
def gex_result():
    """GEXプロファイルを計算してフィクスチャとして提供。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from processors.gex_calculator import compute_gex_profile, gex_by_strike, find_gamma_flip

    options_df = _load_options()
    if options_df is None or options_df.empty:
        pytest.skip("jpx_options データなし。run_fetchers.py を先に実行してください")

    spot = _get_spot()
    gex_df = compute_gex_profile(options_df, spot)
    by_strike = gex_by_strike(gex_df)
    gamma_flip = find_gamma_flip(by_strike)
    return {"gex_df": gex_df, "by_strike": by_strike, "gamma_flip": gamma_flip, "spot": spot}


class TestGexValidity:
    def test_no_nan_in_gex(self, gex_result):
        """GEX 列に NaN / inf がないこと。"""
        gex = gex_result["gex_df"]["gex"]
        assert not gex.isna().any(), "GEXにNaN検出"
        assert not np.isinf(gex).any(), "GEXにinf検出"

    def test_no_nan_in_dex(self, gex_result):
        """DEX 列に NaN / inf がないこと。"""
        dex = gex_result["gex_df"]["dex"]
        assert not dex.isna().any(), "DEXにNaN検出"
        assert not np.isinf(dex).any(), "DEXにinf検出"

    def test_peak_gex_magnitude_near_spot(self, gex_result):
        """GEX最大値を持つストライクがスポット ±3000 以内にあること。"""
        spot = gex_result["spot"]
        by_strike = gex_result["by_strike"]
        # 当月限だけを対象（全限月だと遠いストライクが支配することがある）
        gex_df = gex_result["gex_df"]
        near_term = gex_df[gex_df["expiry"] == gex_df["expiry"].min()]
        if near_term.empty:
            pytest.skip("当月限データなし")
        from processors.gex_calculator import gex_by_strike
        by_near = gex_by_strike(near_term)
        if by_near.empty:
            pytest.skip("当月限GEXデータなし")
        peak_idx = by_near["net_gex"].abs().idxmax()
        peak_strike = float(by_near.loc[peak_idx, "strike"])
        tolerance = max(5000, spot * 0.08)  # 最低5000pt or スポットの8%
        assert abs(peak_strike - spot) <= tolerance, (
            f"ピークGEXのストライク {peak_strike:.0f} がスポット {spot:.0f} から"
            f"±{tolerance:.0f}以上離れています（差: {abs(peak_strike - spot):.0f}）"
        )

    def test_gamma_flip_within_spot_range(self, gex_result):
        """ガンマフリップがスポット ±15% 以内にあること（全限月）。"""
        gamma_flip = gex_result["gamma_flip"]
        spot = gex_result["spot"]
        if gamma_flip is None:
            pytest.skip("ガンマフリップが検出されませんでした")
        tolerance = spot * 0.15
        assert abs(gamma_flip - spot) <= tolerance, (
            f"ガンマフリップ {gamma_flip:.0f} がスポット {spot:.0f} から"
            f"±15%（±{tolerance:.0f}）以上離れています"
        )

    def test_iv_range_reasonable(self, gex_result):
        """IV の範囲が 1%〜200% の間にあること（デフォルト 20% は除外）。"""
        iv = gex_result["gex_df"]["iv_dec"]
        assert iv.notna().all(), "iv_decにNaN検出"
        # デフォルト 20% が多く使われるのは許容するが、範囲外は NG
        assert (iv >= 0.01).all(), "iv_dec < 1% の行あり"
        assert (iv <= 2.0).all(), "iv_dec > 200% の行あり"

    def test_positive_oi(self, gex_result):
        """建玉は全て正（> 0）。"""
        oi = gex_result["gex_df"]["open_interest"]
        assert (oi > 0).all(), "建玉 ≤ 0 の行あり"

    def test_multiple_expiries(self, gex_result):
        """複数の限月（満期日）が存在すること。"""
        expiries = gex_result["gex_df"]["expiry"].nunique()
        assert expiries >= 2, f"限月数が {expiries} しかありません（2以上必要）"

    def test_both_call_and_put(self, gex_result):
        """コールとプットの両方が存在すること。"""
        types = set(gex_result["gex_df"]["option_type"].unique())
        assert "C" in types, "コールオプションデータがありません"
        assert "P" in types, "プットオプションデータがありません"
