"""
SQ・OPEXカレンダー生成 (D2)

日本SQ: 毎月第2金曜（メジャーSQ = 3,6,9,12月; マイナーSQ = その他）
米国OPEX: 毎月第3金曜（トリプルウィッチング = 3,6,9,12月）
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "sq_calendar"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """year/month の第n weekday（0=月曜, 4=金曜）を返す。"""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    first_occurrence = first + timedelta(days=delta)
    return first_occurrence + timedelta(weeks=n - 1)


def jp_sq_dates(year: int) -> list[dict]:
    """year 内の日本SQ日リストを返す。"""
    results = []
    for month in range(1, 13):
        sq_date = _nth_weekday(year, month, 4, 2)  # 第2金曜
        sq_type = "major_sq" if month in (3, 6, 9, 12) else "minor_sq"
        label = (
            f"メジャーSQ ({year}/{month:02d})"
            if sq_type == "major_sq"
            else f"マイナーSQ ({year}/{month:02d})"
        )
        results.append({
            "date": sq_date,
            "type": sq_type,
            "market": "JP",
            "label": label,
        })
    return results


def us_opex_dates(year: int) -> list[dict]:
    """year 内の米国OPEX日リストを返す（第3金曜）。"""
    results = []
    for month in range(1, 13):
        opex_date = _nth_weekday(year, month, 4, 3)  # 第3金曜
        is_triple = month in (3, 6, 9, 12)
        label = (
            f"トリプルウィッチング ({year}/{month:02d})"
            if is_triple
            else f"US OPEX ({year}/{month:02d})"
        )
        results.append({
            "date": opex_date,
            "type": "triple_witching" if is_triple else "opex",
            "market": "US",
            "label": label,
        })
    return results


class SqCalendar:
    """SQ/OPEXカレンダーの生成・保存・照会。"""

    def get_events(
        self,
        reference: date | None = None,
        lookahead_days: int = 90,
    ) -> pd.DataFrame:
        """reference から lookahead_days 日以内のイベント一覧を返す。"""
        if reference is None:
            reference = date.today()

        end_date = reference + timedelta(days=lookahead_days)
        records: list[dict] = []

        for year in range(reference.year, end_date.year + 1):
            for rec in jp_sq_dates(year) + us_opex_dates(year):
                if reference <= rec["date"] <= end_date:
                    records.append(rec)

        if not records:
            return pd.DataFrame(
                columns=["date", "type", "market", "label", "days_until"]
            )

        df = pd.DataFrame(records)
        df = df.sort_values("date").reset_index(drop=True)
        today = pd.Timestamp.today().normalize()
        df["days_until"] = (
            pd.to_datetime(df["date"]) - today
        ).dt.days.astype(int)
        return df

    def next_jp_sq(self, reference: date | None = None) -> dict | None:
        """最も近い日本SQの情報を返す。"""
        events = self.get_events(reference, lookahead_days=60)
        jp = events[events["market"] == "JP"]
        if jp.empty:
            return None
        row = jp.iloc[0]
        return {
            "date": row["date"],
            "type": row["type"],
            "days_until": int(row["days_until"]),
            "label": row["label"],
            "is_major": row["type"] == "major_sq",
        }

    def is_sq_week(self, reference: date | None = None) -> bool:
        """今週SQがあるか（残り0〜4営業日以内）。"""
        sq = self.next_jp_sq(reference)
        return sq is not None and 0 <= sq["days_until"] <= 6

    def is_month_end_period(
        self, reference: date | None = None, buffer_biz_days: int = 5
    ) -> bool:
        """
        月末リバランス期間判定。
        月の最後の buffer_biz_days 営業日に入っていれば True。
        年金・バランスファンドのリバランス売買が集中する。
        """
        if reference is None:
            reference = date.today()

        last_day_num = calendar.monthrange(reference.year, reference.month)[1]
        month_end = date(reference.year, reference.month, last_day_num)

        # 月末から逆算して buffer_biz_days 番目の営業日を探す
        biz_count = 0
        check = month_end
        threshold = month_end
        while check >= date(reference.year, reference.month, 1):
            if check.weekday() < 5:  # 月〜金
                biz_count += 1
                if biz_count == buffer_biz_days:
                    threshold = check
                    break
            check -= timedelta(days=1)

        return reference >= threshold

    def save(self) -> pd.DataFrame:
        """1年分のカレンダーを parquet に保存。"""
        df = self.get_events(lookahead_days=365)
        path = DATA_DIR / "upcoming_events.parquet"
        df.to_parquet(path, index=False)
        logger.info("SQカレンダー保存: %d件 → %s", len(df), path)
        return df
