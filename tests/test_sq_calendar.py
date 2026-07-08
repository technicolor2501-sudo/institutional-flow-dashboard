"""SQカレンダーのユニットテスト。"""
from datetime import date
import pytest
from processors.sq_calendar import SqCalendar, jp_sq_dates, us_opex_dates, _nth_weekday


class TestNthWeekday:
    def test_second_friday_july_2026(self):
        """2026年7月の第2金曜 = 7月10日。"""
        result = _nth_weekday(2026, 7, 4, 2)
        assert result == date(2026, 7, 10)

    def test_second_friday_sep_2026(self):
        """2026年9月の第2金曜 = 9月11日（メジャーSQ）。"""
        result = _nth_weekday(2026, 9, 4, 2)
        assert result == date(2026, 9, 11)

    def test_third_friday_july_2026(self):
        """2026年7月の第3金曜 = 7月17日（US OPEX）。"""
        result = _nth_weekday(2026, 7, 4, 3)
        assert result == date(2026, 7, 17)


class TestJpSqDates:
    def test_2026_has_12_sq(self):
        """2026年は12回のSQがある。"""
        events = jp_sq_dates(2026)
        assert len(events) == 12

    def test_major_sq_months(self):
        """メジャーSQ = 3, 6, 9, 12月。"""
        events = jp_sq_dates(2026)
        major = [e for e in events if e["type"] == "major_sq"]
        months = sorted([e["date"].month for e in major])
        assert months == [3, 6, 9, 12]

    def test_july_2026_minor_sq(self):
        """2026年7月は マイナーSQ、日付は7月10日。"""
        events = jp_sq_dates(2026)
        july = next(e for e in events if e["date"].month == 7)
        assert july["type"] == "minor_sq"
        assert july["date"] == date(2026, 7, 10)

    def test_sep_2026_major_sq(self):
        """2026年9月は メジャーSQ、日付は9月11日。"""
        events = jp_sq_dates(2026)
        sep = next(e for e in events if e["date"].month == 9)
        assert sep["type"] == "major_sq"
        assert sep["date"] == date(2026, 9, 11)


class TestUsOpexDates:
    def test_2026_has_12_opex(self):
        """2026年は12回のOPEXがある。"""
        events = us_opex_dates(2026)
        assert len(events) == 12

    def test_july_2026_opex_date(self):
        """2026年7月のUS OPEX = 7月17日（第3金曜）。"""
        events = us_opex_dates(2026)
        july = next(e for e in events if e["date"].month == 7)
        assert july["date"] == date(2026, 7, 17)
        assert july["type"] == "opex"

    def test_triple_witching_months(self):
        """トリプルウィッチング = 3, 6, 9, 12月。"""
        events = us_opex_dates(2026)
        triple = [e for e in events if e["type"] == "triple_witching"]
        months = sorted([e["date"].month for e in triple])
        assert months == [3, 6, 9, 12]


class TestSqCalendar:
    """SqCalendar クラスのテスト。"""

    def setup_method(self):
        self.cal = SqCalendar()

    def test_is_sq_week_on_sq_date(self):
        """SQ当日（2026-07-10）は is_sq_week() == True。"""
        assert self.cal.is_sq_week(date(2026, 7, 10)) is True

    def test_is_sq_week_on_tuesday(self):
        """SQ直前の火曜（2026-07-07）も SQ週 → True。"""
        assert self.cal.is_sq_week(date(2026, 7, 7)) is True

    def test_is_sq_week_on_monday_before_sq(self):
        """SQ週の月曜（2026-07-06）も SQ週 → True。"""
        assert self.cal.is_sq_week(date(2026, 7, 6)) is True

    def test_is_sq_week_after_sq(self):
        """SQ翌週の月曜（2026-07-13）は SQ週ではない → False。"""
        assert self.cal.is_sq_week(date(2026, 7, 13)) is False

    def test_is_month_end_period_last_5_biz_days(self):
        """2026-07-27〜2026-07-31 は月末期間 → True（7月末5営業日）。"""
        for d in [date(2026, 7, 27), date(2026, 7, 28), date(2026, 7, 29),
                  date(2026, 7, 30), date(2026, 7, 31)]:
            assert self.cal.is_month_end_period(d) is True, f"{d} should be month-end period"

    def test_is_month_end_period_early_month(self):
        """月初（2026-07-01）は月末期間ではない → False。"""
        assert self.cal.is_month_end_period(date(2026, 7, 1)) is False

    def test_next_jp_sq_from_sq_week(self):
        """SQ週の月曜から next_jp_sq() は当月SQを返す。"""
        sq = self.cal.next_jp_sq(date(2026, 7, 6))
        assert sq is not None
        assert sq["date"] == date(2026, 7, 10)
        assert sq["is_major"] is False  # 7月はマイナー

    def test_get_events_includes_both_jp_and_us(self):
        """get_events は JP と US の両方を含む。"""
        events = self.cal.get_events(date(2026, 7, 1), lookahead_days=30)
        assert "JP" in events["market"].values
        assert "US" in events["market"].values

    def test_days_until_non_negative_from_today(self):
        """today 以降のイベントは days_until >= 0。"""
        events = self.cal.get_events(lookahead_days=90)
        assert (events["days_until"] >= 0).all()
