"""
GEX (Gamma Exposure) / DEX (Delta Exposure) / Charm / Vanna 計算 (B1処理層)

【GEX の解釈】
  GEX > 0（正）: ディーラーがネット・ロング・ガンマ
               → 上昇時に売り、下落時に買うため値動きが抑制される（レンジ相場）
  GEX < 0（負）: ディーラーがネット・ショート・ガンマ
               → 上昇時に買い増し、下落時に売り増すため値動きが加速する

【ガンマ・フリップ】
  権利行使価格別の累積 GEX がゼロになる水準。
  現在値がガンマ・フリップの上にあれば正GEX環境（安定）、
  下にあれば負GEX環境（ボラ拡大リスク）。

【使用前提】
  ディーラーは全建玉の反対側に立つ（公開建玉 ≒ ディーラーが売った数量）
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "gex"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 日経225オプションの契約倍率（1枚あたり 1,000 円/ポイント）
NIKKEI_MULTIPLIER = 1_000

# ミニオプションの倍率（100円/ポイント）— 将来対応用
NIKKEI_MINI_MULTIPLIER = 100


# ── Black-Scholes Greeks ────────────────────────────────────────────────────

def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes ガンマ（コール/プット共通）。"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    return float(norm.pdf(d1) / (S * sigma * sqrt_T))


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, cp: str) -> float:
    """Black-Scholes デルタ。cp: 'C' or 'P'。"""
    if T <= 0:
        if cp == "C":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    if sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    if cp == "C":
        return float(norm.cdf(d1))
    return float(norm.cdf(d1) - 1)


def bs_vanna(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Vanna = ∂Delta/∂σ = ∂²V/(∂S ∂σ)。"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return float(-norm.pdf(d1) * d2 / sigma)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, cp: str) -> float:
    """Black-Scholes オプション価格（コール/プット）。"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        intrinsic = max(S - K, 0.0) if cp == "C" else max(K - S, 0.0)
        return intrinsic
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    disc = np.exp(-r * T)
    if cp == "C":
        return float(S * norm.cdf(d1) - K * disc * norm.cdf(d2))
    return float(K * disc * norm.cdf(-d2) - S * norm.cdf(-d1))


def implied_vol(
    S: float, K: float, T: float, r: float, market_price: float, cp: str,
    lo: float = 0.001, hi: float = 5.0
) -> float | None:
    """
    市場価格から Black-Scholes インプライドボラティリティを逆算（brentq 法）。
    解が見つからない場合は None を返す。
    """
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(S - K, 0.0) if cp == "C" else max(K - S, 0.0)
    if market_price <= intrinsic:
        return None
    try:
        vol = brentq(
            lambda sigma: bs_price(S, K, T, r, sigma, cp) - market_price,
            lo, hi, xtol=1e-6, maxiter=100
        )
        return float(vol)
    except (ValueError, RuntimeError):
        return None


def bs_charm(S: float, K: float, T: float, r: float, sigma: float, cp: str) -> float:
    """Charm = ∂Delta/∂t（デルタの時間減衰）。"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    charm_c = -norm.pdf(d1) * (2 * r * T - d2 * sigma * sqrt_T) / (2 * T * sigma * sqrt_T)
    if cp == "C":
        return float(charm_c)
    return float(charm_c)  # コール/プットで符号は同じ（デルタの絶対値の変化率）


# ── GEX プロファイル計算 ─────────────────────────────────────────────────────

def compute_gex_profile(
    options_df: pd.DataFrame,
    spot: float,
    risk_free_rate: float = 0.001,
    multiplier: int = NIKKEI_MULTIPLIER,
) -> pd.DataFrame:
    """
    オプション建玉データから GEX/DEX/Charm/Vanna プロファイルを計算。

    Parameters
    ----------
    options_df : 日経225オプション建玉データ（jpx_options fetcher の出力）
    spot : 現在の日経225スポット価格
    risk_free_rate : 無リスク金利（日本の短期金利、約0.1%）
    multiplier : 契約倍率

    Returns
    -------
    DataFrame with columns: strike, expiry, option_type, open_interest,
        gamma, delta, vanna, charm, gex, dex, vanna_exposure, charm_exposure
    """
    if options_df.empty:
        logger.warning("入力 DataFrame が空です")
        return pd.DataFrame()

    df = options_df.copy()

    # 必須列チェック
    required = ["strike", "option_type", "open_interest"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.error("必須列が不足: %s", missing)
        return pd.DataFrame()

    # 残存日数（年単位）
    today = pd.Timestamp.today().normalize()
    if "expiry" in df.columns and df["expiry"].notna().any():
        df["T"] = (pd.to_datetime(df["expiry"]) - today).dt.days / 365.0
        df["T"] = df["T"].clip(lower=1 / 365)  # 最低1日
    else:
        # 限月がなければ当月第2金曜（直近SQ）を仮定
        from processors.sq_calendar import _nth_weekday
        now = today.date()
        assumed_expiry = _nth_weekday(now.year, now.month, 4, 2)
        days = max((assumed_expiry - now).days, 1)
        df["T"] = days / 365.0
        logger.warning("限月情報なし。SQ日 %s を仮定（%.0f 日）", assumed_expiry, days)

    # IV 計算の優先順位:
    #   1. iv 列（%表記）が存在する → 直接使用
    #   2. settlement 列が存在する → Black-Scholes 逆算
    #   3. どちらもなければ → デフォルト σ=0.20
    if "iv" in df.columns and df["iv"].notna().any():
        df["iv_dec"] = df["iv"] / 100.0
    elif "settlement" in df.columns and df["settlement"].notna().any():
        logger.info("settlement 価格から IV を逆算します")
        def _calc_iv(row: pd.Series) -> float:
            if pd.isna(row["settlement"]) or row["settlement"] <= 0:
                return 0.20
            vol = implied_vol(
                S=spot,
                K=float(row["strike"]),
                T=float(row["T"]),
                r=risk_free_rate,
                market_price=float(row["settlement"]),
                cp=str(row["option_type"]),
            )
            # IV の現実的な範囲: 1%〜200%。範囲外は 20% をデフォルト
            if vol is None or not (0.01 <= vol <= 2.0):
                return 0.20
            return vol
        df["iv_dec"] = df.apply(_calc_iv, axis=1)
    else:
        logger.warning("IV・settlement 列なし。デフォルト σ=0.20 を使用")
        df["iv_dec"] = 0.20

    # ガンマが既にある場合はそのまま使用、なければ B-S 計算
    if "gamma" not in df.columns or df["gamma"].isna().all():
        logger.info("ガンマを Black-Scholes で計算します")
        df["gamma"] = df.apply(
            lambda r: bs_gamma(spot, r["strike"], r["T"], risk_free_rate, r["iv_dec"])
            if pd.notna(r["strike"]) and pd.notna(r["iv_dec"])
            else 0.0,
            axis=1,
        )
    else:
        df["gamma"] = df["gamma"].fillna(0.0)

    # デルタ計算（B-S）
    if "delta" not in df.columns or df["delta"].isna().all():
        df["delta"] = df.apply(
            lambda r: bs_delta(
                spot, r["strike"], r["T"], risk_free_rate, r["iv_dec"], r["option_type"]
            )
            if pd.notna(r["strike"]) and pd.notna(r["iv_dec"])
            else 0.0,
            axis=1,
        )
    else:
        df["delta"] = df["delta"].fillna(0.0)

    # Vanna / Charm
    df["vanna"] = df.apply(
        lambda r: bs_vanna(spot, r["strike"], r["T"], risk_free_rate, r["iv_dec"])
        if pd.notna(r["strike"]) and pd.notna(r["iv_dec"])
        else 0.0,
        axis=1,
    )
    df["charm"] = df.apply(
        lambda r: bs_charm(
            spot, r["strike"], r["T"], risk_free_rate, r["iv_dec"], r["option_type"]
        )
        if pd.notna(r["strike"]) and pd.notna(r["iv_dec"])
        else 0.0,
        axis=1,
    )

    oi = df["open_interest"].fillna(0.0)

    # GEX = OI × Gamma × Multiplier × Spot² × 0.01
    # コール: +, プット: -（ディーラー視点でコール売りが正ガンマ環境に相当）
    raw_gex = oi * df["gamma"] * multiplier * (spot**2) * 0.01
    df["gex"] = np.where(df["option_type"] == "C", raw_gex, -raw_gex)

    # DEX = OI × Delta × Multiplier × Spot
    df["dex"] = oi * df["delta"] * multiplier * spot

    # Vanna エクスポージャー = OI × Vanna × Multiplier × Spot
    df["vanna_exposure"] = oi * df["vanna"] * multiplier * spot

    # Charm エクスポージャー = OI × Charm × Multiplier
    df["charm_exposure"] = oi * df["charm"] * multiplier

    return df.reset_index(drop=True)


def gex_by_strike(gex_df: pd.DataFrame) -> pd.DataFrame:
    """権利行使価格別にGEXを集計（プロファイルチャート用）。"""
    if gex_df.empty:
        return pd.DataFrame()
    agg = (
        gex_df.groupby("strike", as_index=False)
        .agg(
            net_gex=("gex", "sum"),
            call_gex=("gex", lambda x: x[gex_df.loc[x.index, "option_type"] == "C"].sum()),
            put_gex=("gex", lambda x: x[gex_df.loc[x.index, "option_type"] == "P"].sum()),
            total_oi=("open_interest", "sum"),
            call_oi=("open_interest", lambda x: x[gex_df.loc[x.index, "option_type"] == "C"].sum()),
            put_oi=("open_interest", lambda x: x[gex_df.loc[x.index, "option_type"] == "P"].sum()),
            net_dex=("dex", "sum"),
            net_charm=("charm_exposure", "sum"),
            net_vanna=("vanna_exposure", "sum"),
        )
        .sort_values("strike")
        .reset_index(drop=True)
    )
    # 累積GEX（低いストライクから積み上げ）
    agg["cumulative_gex"] = agg["net_gex"].cumsum()
    return agg


def find_gamma_flip(strike_gex: pd.DataFrame) -> float | None:
    """
    ガンマ・フリップ水準（累積GEXがゼロになるストライク）を線形補間で推定。
    """
    if strike_gex.empty or "cumulative_gex" not in strike_gex.columns:
        return None
    cum = strike_gex["cumulative_gex"].values
    strikes = strike_gex["strike"].values
    # 符号が反転する箇所を探す
    for i in range(len(cum) - 1):
        if cum[i] * cum[i + 1] <= 0:
            # 線形補間
            s0, s1 = strikes[i], strikes[i + 1]
            g0, g1 = cum[i], cum[i + 1]
            if g1 - g0 != 0:
                return float(s0 + (0 - g0) * (s1 - s0) / (g1 - g0))
    return None


def total_gex_summary(gex_df: pd.DataFrame, spot: float) -> dict:
    """GEX全体サマリーを辞書で返す。"""
    if gex_df.empty:
        return {"total_gex": None, "gamma_flip": None, "gex_environment": "不明"}

    by_strike = gex_by_strike(gex_df)
    total = float(gex_df["gex"].sum())
    gamma_flip = find_gamma_flip(by_strike)

    if gamma_flip is not None:
        above_flip = spot >= gamma_flip
        env = "正GEX（安定・レンジ傾向）" if above_flip else "負GEX（ボラ拡大警戒）"
    elif total >= 0:
        env = "正GEX（安定・レンジ傾向）"
    else:
        env = "負GEX（ボラ拡大警戒）"

    return {
        "total_gex": total,
        "gamma_flip": gamma_flip,
        "gex_environment": env,
        "total_dex": float(gex_df["dex"].sum()),
        "spot": spot,
    }
