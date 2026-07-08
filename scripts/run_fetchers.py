#!/usr/bin/env python3
"""
GitHub Actions エントリーポイント。
Phase 1 フェッチャー（JPXオプション・空売り・SQカレンダー）を実行し、
GEXを計算してデータを data/ に保存する。

実行方法:
    python scripts/run_fetchers.py
    python scripts/run_fetchers.py --force   # キャッシュ無視で強制再取得
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_fetchers")

STATUS: dict[str, dict] = {}


def run(name: str, fn, *args, **kwargs):
    logger.info("▶ %s 開始", name)
    try:
        result = fn(*args, **kwargs)
        rows = len(result) if hasattr(result, "__len__") else "N/A"
        STATUS[name] = {"status": "ok", "rows": rows}
        logger.info("✓ %s 完了: %s 行", name, rows)
        return result
    except Exception as exc:
        STATUS[name] = {"status": "error", "error": str(exc)}
        logger.error("✗ %s 失敗: %s", name, exc, exc_info=True)
        return None


def main():
    parser = argparse.ArgumentParser(description="需給データ取得スクリプト")
    parser.add_argument("--force", action="store_true", help="キャッシュ無視で強制再取得")
    args = parser.parse_args()

    logger.info("=== 需給データ取得 開始 %s UTC ===", datetime.utcnow().isoformat())

    # ── B2: SQカレンダー（静的計算、常に成功） ────────────────────────────
    from processors.sq_calendar import SqCalendar
    cal = SqCalendar()
    run("sq_calendar", cal.save)

    # ── B1: JPXオプションデータ ────────────────────────────────────────────
    from fetchers.jpx_options import JpxOptionsFetcher
    options_fetcher = JpxOptionsFetcher()
    if args.force or options_fetcher.has_update():
        options_df = run("jpx_options", options_fetcher.fetch)
    else:
        logger.info("● jpx_options: 変化なし。スキップ")
        options_df = options_fetcher.load_latest()
        STATUS["jpx_options"] = {"status": "skipped (no update)"}

    # ── A3: 空売り比率 ─────────────────────────────────────────────────────
    from fetchers.jpx_short_selling import JpxShortSellingFetcher
    short_fetcher = JpxShortSellingFetcher()
    if args.force or short_fetcher.has_update():
        run("jpx_short_selling", short_fetcher.fetch)
    else:
        logger.info("● jpx_short_selling: 変化なし。スキップ")
        STATUS["jpx_short_selling"] = {"status": "skipped (no update)"}

    # ── GEX 計算 ───────────────────────────────────────────────────────────
    if options_df is not None and not options_df.empty:
        try:
            import yfinance as yf
            from processors.gex_calculator import compute_gex_profile

            logger.info("日経225スポット価格を取得中...")
            tk = yf.Ticker("^N225")
            hist = tk.history(period="2d")
            if hist.empty:
                raise ValueError("yfinance から ^N225 価格を取得できませんでした")
            spot = float(hist["Close"].iloc[-1])
            logger.info("スポット価格: ¥%,.0f", spot)

            gex_df = compute_gex_profile(options_df, spot)
            gex_dir = ROOT / "data" / "gex"
            gex_dir.mkdir(parents=True, exist_ok=True)
            gex_path = gex_dir / f"{date.today().isoformat()}.parquet"
            gex_df.to_parquet(gex_path, index=False)
            STATUS["gex_calculation"] = {
                "status": "ok",
                "rows": len(gex_df),
                "spot": spot,
            }
            logger.info("✓ GEX計算完了: %d行 → %s", len(gex_df), gex_path)
        except Exception as exc:
            STATUS["gex_calculation"] = {"status": "error", "error": str(exc)}
            logger.error("✗ GEX計算失敗: %s", exc, exc_info=True)
    else:
        STATUS["gex_calculation"] = {"status": "skipped (no options data)"}
        logger.warning("オプションデータがないため GEX 計算をスキップ")

    # ── ステータス保存 ─────────────────────────────────────────────────────
    status_path = ROOT / "data" / "run_status.json"
    run_result = {
        "run_at": datetime.utcnow().isoformat() + "Z",
        "fetchers": STATUS,
    }
    status_path.write_text(
        json.dumps(run_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("ステータス保存: %s", status_path)

    # ── サマリー ───────────────────────────────────────────────────────────
    ok = [k for k, v in STATUS.items() if v["status"] in ("ok", "skipped (no update)")]
    ng = [k for k, v in STATUS.items() if "error" in v["status"]]
    logger.info("=== 完了: 成功/スキップ %d件, エラー %d件 ===", len(ok), len(ng))
    if ng:
        logger.warning("エラーあり: %s", ng)
        # GitHub Actions では exit(1) すると Step 失敗になるが、
        # データ取得失敗は soft failure として exit(0) にとどめる
        # （完全に空のコミットを避けるため）
    sys.exit(0)


if __name__ == "__main__":
    main()
