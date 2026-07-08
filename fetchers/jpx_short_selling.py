"""
JPX 空売り集計（日次）データ取得 (A3) — 改訂版

データソース:
  PDF: https://www.jpx.co.jp/markets/statistics-equities/short-selling/
       t13vrt000001izub-att/{YYMMDD}-m.pdf
  URL日付形式: YY = year[-2:]、MMDD = "{month:02d}{day:02d}"
  例: 2026-07-07 → 260707-m.pdf

robots.txt チェック: Disallow: 空（全パス許可）

取得データ:
  - 市場別（プライム / スタンダード / グロース）
  - 規制あり空売り (a) / 規制なし空売り (b) / 信用取引売 (c) / 合計 (d)
  - 各カテゴリの構成比 (a/d, b/d, c/d)

注意: 本データは「空売り量の内訳」であり、
      総出来高に対する空売り比率（通常35〜45%）とは異なる。
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, timedelta

import pandas as pd
import pdfplumber

from fetchers.base import BaseFetcher, fetch_with_retry

logger = logging.getLogger(__name__)

BASE = "https://www.jpx.co.jp"
PDF_URL_TEMPLATE = (
    f"{BASE}/markets/statistics-equities/short-selling/"
    "t13vrt000001izub-att/{datestr}-m.pdf"
)

# 市場名マッピング（PDF内テキスト → 英語キー）
_MARKET_MAP = {
    "プライム": "prime",
    "スタンダード": "standard",
    "グロース": "growth",
    "東証全体": "total",
    "Prime": "prime",
    "Standard": "standard",
    "Growth": "growth",
}

# データ行パターン: 年月日 + 4組の（数量 パーセント%）+ 合計
_DATA_ROW_RE = re.compile(
    r"(\d{4})年(\d{1,2})月(\d{1,2})日"        # 日付
    r"\s+([\d,]+)\s+([\d.]+)%"                # (a) 規制あり と a/d%
    r"\s+([\d,]+)\s+([\d.]+)%"                # (b) 規制なし と b/d%
    r"\s+([\d,]+)\s+([\d.]+)%"                # (c) 信用取引売 と c/d%
    r"\s+([\d,]+)"                             # 合計(d)
)

_NUMBERS_RE = re.compile(r"[\d,]+")


class JpxShortSellingFetcher(BaseFetcher):
    """
    JPX空売り集計PDFから日次の空売り量内訳を取得。
    最大14日間（週末除く）遡って最新PDFを探索する。
    """

    name = "jpx_short_selling"

    def fetch(self) -> pd.DataFrame:
        logger.info("JPX 空売りデータ取得開始")

        pdf_url, trade_date_str = self._find_latest_pdf()
        if pdf_url is None:
            raise RuntimeError("最新の空売り集計PDFが見つかりません（14日遡及済み）")

        # 冪等性チェック
        if self._meta.get("last_url") == pdf_url:
            existing = self.load_latest()
            if existing is not None and not existing.empty:
                logger.info("空売りデータ変化なし。スキップ")
                return existing

        logger.info("PDF ダウンロード: %s", pdf_url)
        r = fetch_with_retry(pdf_url, self._session)

        content_hash = self._content_hash(r.content)
        if self._meta.get("last_hash") == content_hash:
            existing = self.load_latest()
            return existing if existing is not None else pd.DataFrame()

        df = self._parse_pdf(r.content)
        if df.empty:
            logger.warning("PDF解析結果が空。PDF構造を確認してください: %s", pdf_url)
            self._meta["last_status"] = "empty"
            self._save_meta()
            return df

        parquet_path = self.data_dir / f"{trade_date_str}.parquet"
        df.to_parquet(parquet_path, index=False)
        self._meta.update({
            "last_updated": pd.Timestamp.now().isoformat(),
            "last_hash": content_hash,
            "last_url": pdf_url,
            "last_status": "ok",
            "rows": len(df),
            "trade_date": trade_date_str,
        })
        self._save_meta()
        logger.info("保存完了: %d行 (取引日 %s) → %s", len(df), trade_date_str, parquet_path)
        return df

    # ── PDF URL 探索 ───────────────────────────────────────────────────────

    def _find_latest_pdf(self, lookback_days: int = 14) -> tuple[str | None, str | None]:
        """直近14日間（週末除く）を遡ってPDFを探す。"""
        today = date.today()
        for delta in range(lookback_days):
            d = today - timedelta(days=delta)
            if d.weekday() >= 5:  # 土日スキップ
                continue
            yy = str(d.year)[2:]
            datestr = f"{yy}{d.month:02d}{d.day:02d}"
            url = PDF_URL_TEMPLATE.format(datestr=datestr)
            try:
                resp = self._session.head(url, timeout=10)
                if resp.status_code == 200:
                    logger.info("空売りPDF発見: %s (取引日: %s)", url, d.isoformat())
                    return url, d.isoformat()
            except Exception:
                pass
        return None, None

    # ── PDF 解析 ───────────────────────────────────────────────────────────

    def _parse_pdf(self, pdf_content: bytes) -> pd.DataFrame:
        """
        空売り集計PDFからデータを抽出。

        PDF構造:
          空売り集計(日次)
          2026/7/7
          (プライム) プライム市場  [単位：千株]
          ...ヘッダ行...
          2026年7月7日  7,833,713  63.3%  3,477,175  28.1%  1,057,512  8.6%  12,368,400
        """
        records = []
        current_market = "prime"

        with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    # 市場名更新
                    for jp_name, en_key in _MARKET_MAP.items():
                        if jp_name in line:
                            current_market = en_key
                            break

                    # データ行マッチ（正規表現ベース）
                    m = _DATA_ROW_RE.search(line)
                    if m:
                        year = int(m.group(1))
                        month = int(m.group(2))
                        day = int(m.group(3))
                        records.append({
                            "trade_date": date(year, month, day).isoformat(),
                            "market": current_market,
                            "regulated_short": _parse_num(m.group(4)),
                            "regulated_pct": float(m.group(5)),
                            "unregulated_short": _parse_num(m.group(6)),
                            "unregulated_pct": float(m.group(7)),
                            "margin_short": _parse_num(m.group(8)),
                            "margin_pct": float(m.group(9)),
                            "total_short": _parse_num(m.group(10)),
                            "unit": "thousand_shares",
                        })

        # テキスト抽出失敗時のフォールバック
        if not records:
            records = self._extract_via_tables(pdf_content)

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df["fetch_date"] = date.today().isoformat()
        df = df.drop_duplicates(subset=["trade_date", "market"]).reset_index(drop=True)
        return df

    def _extract_via_tables(self, pdf_content: bytes) -> list[dict]:
        """pdfplumber テーブル抽出によるフォールバック。"""
        records = []
        current_market = "prime"
        with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    for row in table:
                        if not row or all(c is None for c in row):
                            continue
                        row_text = " ".join(str(c or "") for c in row)
                        for jp_name, en_key in _MARKET_MAP.items():
                            if jp_name in row_text:
                                current_market = en_key
                        m = _DATA_ROW_RE.search(row_text)
                        if m:
                            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                            records.append({
                                "trade_date": date(year, month, day).isoformat(),
                                "market": current_market,
                                "regulated_short": _parse_num(m.group(4)),
                                "regulated_pct": float(m.group(5)),
                                "unregulated_short": _parse_num(m.group(6)),
                                "unregulated_pct": float(m.group(7)),
                                "margin_short": _parse_num(m.group(8)),
                                "margin_pct": float(m.group(9)),
                                "total_short": _parse_num(m.group(10)),
                                "unit": "thousand_shares",
                            })
        return records

    # ── ダッシュボード向けヘルパー ────────────────────────────────────────

    def latest_summary(self) -> dict | None:
        """最新のプライム市場空売りサマリーを返す。"""
        df = self.load_latest()
        if df is None or df.empty:
            return None
        prime_df = df[df["market"] == "prime"]
        row = prime_df.iloc[-1] if not prime_df.empty else df.iloc[-1]
        return {
            "trade_date": str(row.get("trade_date", "")),
            "market": str(row.get("market", "prime")),
            "total_short_k": row.get("total_short", 0),
            "regulated_pct": row.get("regulated_pct", 0),
            "unregulated_pct": row.get("unregulated_pct", 0),
            "margin_pct": row.get("margin_pct", 0),
            "unit": "thousand_shares",
        }

    def latest_ratio(self) -> dict | None:
        """
        後方互換メソッド。
        従来の「空売り比率」（対総出来高）は本データソースでは計算不可。
        代わりに空売り構成比データを返す。dashboard 側で null チェックが必要。
        """
        summary = self.latest_summary()
        if summary is None:
            return None
        return {
            "trade_date": summary["trade_date"],
            "market": summary["market"],
            "short_ratio": None,  # 対総出来高比率は取得不可
            "total_short_k": summary["total_short_k"],
            "regulated_pct": summary["regulated_pct"],
            "unregulated_pct": summary["unregulated_pct"],
            "margin_pct": summary["margin_pct"],
        }


def _parse_num(s: str) -> float:
    return float(s.replace(",", ""))
