"""
Streamlit AppTest スモークテスト。
ページが例外なしでロードでき、主要コンポーネントが描画されることを検証する。
データが存在しなくてもグレースフル・デグラデーション（空データ表示）することを確認。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from streamlit.testing.v1 import AppTest
    STREAMLIT_TESTING = True
except ImportError:
    STREAMLIT_TESTING = False


def _all_text(at) -> str:
    """AppTest の全テキスト要素を連結した文字列を返す。ElementList は + 非対応なので個別に収集。"""
    parts = []
    for el_list in [at.title, at.subheader, at.markdown, at.info, at.warning, at.error, at.caption]:
        try:
            parts.extend(str(e.value) for e in el_list)
        except Exception:
            pass
    return " ".join(parts)


@pytest.mark.skipif(not STREAMLIT_TESTING, reason="streamlit.testing.v1 not available")
class TestDashboardHomePage:
    """ホームページのスモークテスト。"""

    def test_home_loads_without_exception(self):
        """ホームページが例外なしでロードされること。"""
        at = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
        at.run()
        assert not at.exception, f"例外発生: {at.exception}"

    def test_home_has_title(self):
        """タイトルが存在すること。"""
        at = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
        at.run()
        assert len(at.title) > 0, "タイトルが見つかりません"
        assert "需給" in at.title[0].value, f"タイトルに '需給' が含まれていません: {at.title[0].value}"

    def test_home_shows_phase1_info(self):
        """Phase 1 の説明が表示されること。"""
        at = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
        at.run()
        text = _all_text(at)
        assert "Phase 1" in text or "GEX" in text, f"Phase1/GEX の説明がありません。テキスト: {text[:300]}"


@pytest.mark.skipif(not STREAMLIT_TESTING, reason="streamlit.testing.v1 not available")
class TestSummaryPage:
    """今日の需給サマリーページのスモークテスト。"""

    def test_summary_page_loads(self):
        """サマリーページが例外なしでロードされること。"""
        at = AppTest.from_file(
            str(ROOT / "app" / "pages" / "1_今日の需給サマリー.py"),
            default_timeout=60,
        )
        at.run()
        assert not at.exception, f"例外発生: {at.exception}"

    def test_summary_shows_sq_section(self):
        """SQ情報セクションが存在すること。"""
        at = AppTest.from_file(
            str(ROOT / "app" / "pages" / "1_今日の需給サマリー.py"),
            default_timeout=60,
        )
        at.run()
        text = _all_text(at)
        assert "SQ" in text, f"SQ 情報セクションが見つかりません。テキスト: {text[:300]}"

    def test_summary_shows_gex_section(self):
        """GEX環境セクションが存在すること。"""
        at = AppTest.from_file(
            str(ROOT / "app" / "pages" / "1_今日の需給サマリー.py"),
            default_timeout=60,
        )
        at.run()
        text = _all_text(at)
        assert "GEX" in text, f"GEX セクションが見つかりません。テキスト: {text[:300]}"


@pytest.mark.skipif(not STREAMLIT_TESTING, reason="streamlit.testing.v1 not available")
class TestOptionsPage:
    """オプション需給ページのスモークテスト。"""

    def test_options_page_loads(self):
        """
        オプション需給ページがロードされること。
        GEXデータなし → エラー表示 + st.stop() は許容（exception != StopException）。
        """
        at = AppTest.from_file(
            str(ROOT / "app" / "pages" / "2_オプション需給.py"),
            default_timeout=60,
        )
        at.run()
        if at.exception:
            exc_type = type(at.exception).__name__
            assert "Stop" in exc_type or "StopException" in exc_type, (
                f"予期しない例外: {exc_type}: {at.exception}"
            )

    def test_options_page_has_title(self):
        """タイトルが存在すること。"""
        at = AppTest.from_file(
            str(ROOT / "app" / "pages" / "2_オプション需給.py"),
            default_timeout=60,
        )
        at.run()
        assert len(at.title) > 0, "タイトルが見つかりません"
