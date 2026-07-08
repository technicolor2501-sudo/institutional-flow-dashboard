"""
共通フェッチャー基底クラス。
各ソース固有フェッチャーはこのクラスを継承し fetch() を実装する。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

_HEADERS = {
    "User-Agent": (
        "InstitutionalFlowDashboard/1.0 "
        "(educational open-source project; https://github.com/your-repo)"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_robots_cache: dict[str, RobotFileParser] = {}


def _check_robots(url: str) -> bool:
    """robots.txt に従いクロール可否を返す（True=OK）。"""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in _robots_cache:
        rp = RobotFileParser()
        rp.set_url(f"{origin}/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None  # type: ignore[assignment]
        _robots_cache[origin] = rp  # type: ignore[assignment]
    rp = _robots_cache[origin]
    if rp is None:
        return True
    return rp.can_fetch(_HEADERS["User-Agent"], url)


def fetch_with_retry(
    url: str,
    session: requests.Session,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    timeout: int = 30,
    respect_robots: bool = True,
) -> requests.Response:
    """指数バックオフ付きリトライ。robots.txt チェックあり。"""
    if respect_robots and not _check_robots(url):
        raise PermissionError(f"robots.txt が {url} のクロールを禁止しています")

    for attempt in range(max_retries):
        try:
            time.sleep(1.0)  # 最低1秒間隔（レート制限）
            resp = session.get(url, headers=_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == max_retries - 1:
                raise
            wait = backoff_base**attempt
            logger.warning(
                "試行 %d/%d 失敗 (%s): %s。%.1f 秒後にリトライ...",
                attempt + 1, max_retries, url, exc, wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"{url} の取得に {max_retries} 回失敗しました")


class BaseFetcher(ABC):
    """全フェッチャーの共通インターフェース。"""

    name: str = "base"

    def __init__(self) -> None:
        self.data_dir = DATA_DIR / self.name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.data_dir / "_meta.json"
        self._meta: dict = self._load_meta()
        self._session = requests.Session()

    # ── メタデータ管理 ─────────────────────────────────────────────────────

    def _load_meta(self) -> dict:
        if self._meta_path.exists():
            try:
                return json.loads(self._meta_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_meta(self) -> None:
        self._meta_path.write_text(
            json.dumps(self._meta, default=str, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _content_hash(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    # ── 公開インターフェース ───────────────────────────────────────────────

    def last_updated(self) -> datetime | None:
        ts = self._meta.get("last_updated")
        return datetime.fromisoformat(ts) if ts else None

    def has_update(self) -> bool:
        """新しいデータが存在するかを軽量チェック（Last-Modified / ETag）。"""
        last_url = self._meta.get("last_url")
        if not last_url:
            return True
        try:
            resp = self._session.head(
                last_url, headers=_HEADERS, timeout=10, allow_redirects=True
            )
            server_etag = resp.headers.get("ETag")
            server_lm = resp.headers.get("Last-Modified")
            if server_etag and server_etag == self._meta.get("etag"):
                return False
            if server_lm and server_lm == self._meta.get("last_modified"):
                return False
            self._meta["etag"] = server_etag
            self._meta["last_modified"] = server_lm
            return True
        except Exception:
            return True

    @abstractmethod
    def fetch(self) -> pd.DataFrame:
        """データを取得・保存し DataFrame を返す。冪等設計。"""

    # ── データ読み込みヘルパー ────────────────────────────────────────────

    def latest_parquet(self) -> Path | None:
        files = sorted(self.data_dir.glob("*.parquet"))
        return files[-1] if files else None

    def load_latest(self) -> pd.DataFrame | None:
        p = self.latest_parquet()
        return pd.read_parquet(p) if p else None

    def load_history(self, days: int = 90) -> pd.DataFrame:
        files = sorted(self.data_dir.glob("*.parquet"))[-days:]
        if not files:
            return pd.DataFrame()
        return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    # ── データ鮮度バッジ用 ────────────────────────────────────────────────

    def freshness_info(self) -> dict:
        lu = self.last_updated()
        return {
            "name": self.name,
            "last_updated": lu.isoformat() if lu else None,
            "status": self._meta.get("last_status", "unknown"),
            "rows": self._meta.get("rows", 0),
        }
