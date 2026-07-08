"""
JPX 日経225オプション日次データ取得 (B1) — 改訂版

データソース:
  1. 月次一覧JSON: /automation/markets/statistics-derivatives/daily/json/daily_report_monthlylist.json
  2. 日次JSON:     /automation/markets/statistics-derivatives/daily/json/daily_report_{YYYYMM}.json
  3. OSE日次ZIPファイル: Daily_Report_OSE_{YYYYMMDD}.zip
  4. ZIP内の siop_dyr_{YYYYMMDD}.pdf (Stock Index Options 日次報告)

siop PDF に日経225オプション全ストライクの建玉・清算値が記載されている。
robots.txt は Disallow: 空（全パスクロール許可）。

注記:
  - 最終取引日 (Last Trading Day) の翌日が SQ 日（権利行使日）
  - 清算値が 1.00 の深く OTM なオプションは IV の精度が低い
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pdfplumber

from fetchers.base import BaseFetcher, fetch_with_retry

logger = logging.getLogger(__name__)

BASE = "https://www.jpx.co.jp"
MONTHLY_LIST_URL = (
    f"{BASE}/automation/markets/statistics-derivatives/daily/json/"
    "daily_report_monthlylist.json"
)

# Put/Callセクション識別テキスト（日本語+英語、大文字小文字を正規化して照合）
_PUT_MARKERS = ("putoptions", "put options", "プットオプション")
_CALL_MARKERS = ("calloptions", "call options", "コールオプション")

# 行パターン: YYYYMM MM.DD NNN,NNN CODE ...
_ROW_RE = re.compile(
    r"^(\d{6})\s+(\d{2}\.\d{2})\s+([\d,]{4,})\s+(\d{6,})\s+(.*)"
)


class JpxOptionsFetcher(BaseFetcher):
    """
    OSE日次報告ZIPから日経225オプション全建玉を取得。
    GEX/DEX 計算に必要な Strike, OI, Settlement を抽出する。
    """

    name = "jpx_options"

    def fetch(self) -> pd.DataFrame:
        logger.info("JPX オプションデータ取得開始")

        # Step1: 月次一覧
        r = fetch_with_retry(MONTHLY_LIST_URL, self._session)
        monthly = r.json()
        latest_month = monthly["TableDatas"][0]["Month"]  # "202607"

        # Step2: 日次JSON
        daily_url = (
            f"{BASE}/automation/markets/statistics-derivatives/daily/json/"
            f"daily_report_{latest_month}.json"
        )
        r2 = fetch_with_retry(daily_url, self._session)
        daily_data = r2.json()
        rows = daily_data.get("TableDatas", [])
        if not rows:
            raise RuntimeError("日次JSONにデータがありません")

        # Step3: 最新日のOseAll ZIPを取得
        latest_row = next((row for row in rows if row.get("OseAll") not in (None, "-")), None)
        if not latest_row:
            raise RuntimeError("OseAllリンクが見つかりません")
        ose_path = latest_row["OseAll"]
        trade_date = latest_row["TradeDate"]

        zip_url = f"{BASE}{ose_path}"
        logger.info("OSE ZIP ダウンロード: %s (取引日: %s)", zip_url, trade_date)
        r3 = fetch_with_retry(zip_url, self._session, timeout=120)

        # 冪等性チェック
        content_hash = self._content_hash(r3.content)
        parquet_path = self.data_dir / f"{trade_date}.parquet"
        if self._meta.get("last_hash") == content_hash and parquet_path.exists():
            logger.info("内容変化なし（ハッシュ一致）。スキップ")
            return pd.read_parquet(parquet_path)

        # Step4: ZIP展開 → siop PDF 解析
        df = self._parse_ose_zip(r3.content, trade_date)
        if df.empty:
            logger.warning("parseрезульте пустой DataFrame. PDF構造を確認してください")
            self._meta["last_status"] = "empty"
            self._save_meta()
            return df

        # Step5: 保存
        df.to_parquet(parquet_path, index=False)
        self._meta.update({
            "last_updated": pd.Timestamp.now().isoformat(),
            "last_hash": content_hash,
            "last_url": zip_url,
            "last_status": "ok",
            "rows": len(df),
            "trade_date": trade_date,
        })
        self._save_meta()
        logger.info("保存完了: %d行 (取引日 %s) → %s", len(df), trade_date, parquet_path)
        return df

    # ── ZIP / PDF 解析 ─────────────────────────────────────────────────────

    def _parse_ose_zip(self, zip_content: bytes, trade_date: str) -> pd.DataFrame:
        with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
            siop_fname = next(
                (f for f in zf.namelist() if "siop_dyr_" in f and "flex" not in f),
                None,
            )
            if not siop_fname:
                logger.error("ZIPに siop_dyr_*.pdf が見つかりません。ファイル一覧: %s", zf.namelist())
                return pd.DataFrame()
            logger.info("siop PDF: %s (%d bytes)", siop_fname, zf.getinfo(siop_fname).file_size)
            pdf_content = zf.read(siop_fname)

        return self._parse_siop_pdf(pdf_content, trade_date)

    def _parse_siop_pdf(self, pdf_content: bytes, trade_date: str) -> pd.DataFrame:
        """
        siop_dyr PDF からオプション建玉を全ストライク分抽出。

        PDF 行フォーマット（日経225オプション）:
          YYYYMM  MM.DD  Strike  Code  [価格フィールド...]  Settlement  [Exercised]  OI
        """
        records: list[dict] = []
        current_type: str | None = None  # 'C' or 'P'

        with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                for line in text.split("\n"):
                    line_stripped = line.strip()
                    low = line_stripped.lower()

                    # Put/Call セクション検知
                    # ヘッダー免責事項行（〜の場合、置換）との誤検知を防ぐため、
                    # "putoptions"/"calloptions" が含まれる短い行に限定する
                    if len(line_stripped) < 80:
                        if any(m in low for m in _PUT_MARKERS):
                            current_type = "P"
                            continue
                        if any(m in low for m in _CALL_MARKERS):
                            current_type = "C"
                            continue

                    if current_type is None:
                        continue

                    # データ行のマッチ
                    m = _ROW_RE.match(line_stripped)
                    if not m:
                        continue

                    month_code = m.group(1)  # e.g., "202607"
                    last_day_str = m.group(2)  # e.g., "07.09"
                    strike_str = m.group(3)  # e.g., "20,000"
                    rest = m.group(5)  # 残りフィールド（価格・数量・OI）

                    strike = float(strike_str.replace(",", ""))

                    # 全数値を抽出（・や空白を除去）
                    nums = re.findall(r"[\d,]+\.?\d*", rest)
                    # 0 除去（ゴミデータ）し float に変換
                    nums_f = []
                    for n in nums:
                        try:
                            v = float(n.replace(",", ""))
                            nums_f.append(v)
                        except ValueError:
                            pass

                    if not nums_f:
                        continue

                    oi = nums_f[-1]  # 最後の数値 = 建玉数量

                    # 清算値 = OI 直前の数値
                    # ただしOI直前が 整数の"Contracts Exercised"の場合もあるため、
                    # 小数点を含む数値か直前3値の中で最小かつ妥当な範囲のものを選ぶ
                    settlement: float | None = None
                    if len(nums_f) >= 2:
                        for candidate in reversed(nums_f[:-1]):
                            # 清算値は通常 0.01〜999999 の範囲
                            if 0.01 <= candidate <= 9_999_999:
                                settlement = candidate
                                break

                    # 満期日の計算: 最終取引日の翌日 = SQ日
                    try:
                        year = int(month_code[:4])
                        ltd_month = int(last_day_str.split(".")[0])
                        ltd_day = int(last_day_str.split(".")[1])
                        # month_code が 202607 で last_day が 07.09 → July 9
                        last_trade = date(year, ltd_month, ltd_day)
                        expiry = last_trade + timedelta(days=1)  # SQ = 翌日
                    except ValueError:
                        expiry = None

                    records.append({
                        "trade_date": trade_date,
                        "option_type": current_type,
                        "strike": strike,
                        "expiry": expiry,
                        "open_interest": oi,
                        "settlement": settlement,
                    })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df["fetch_date"] = date.today().isoformat()

        # 重複除去（同一ストライク・同月・CP の最初のレコードを採用）
        df = df.drop_duplicates(subset=["option_type", "strike", "expiry"]).reset_index(drop=True)

        # 型変換
        numeric_cols = ["strike", "open_interest", "settlement"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce")
        df = df[df["open_interest"] > 0].reset_index(drop=True)  # 建玉ゼロ除去

        logger.info(
            "解析完了: コール%d行, プット%d行, ストライク範囲 %.0f〜%.0f",
            (df["option_type"] == "C").sum(),
            (df["option_type"] == "P").sum(),
            df["strike"].min(),
            df["strike"].max(),
        )
        return df
