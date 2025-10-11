# src/crawler_genie.py
from __future__ import annotations

import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

import re
import time
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

from config_loader import load_songs_config, iter_targets

BASE_URL = "https://www.genie.co.kr/chart/top200"

# 브라우저 흉내내기
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.genie.co.kr/",
    "Connection": "keep-alive",
}


def _make_session(timeout: int = 15, total_retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    """requests 세션(리트라이 포함) 생성"""
    session = requests.Session()
    retries = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(DEFAULT_HEADERS)
    return session


def build_genie_url(ymd: str, hh: int, page: int) -> str:
    """Genie Top200 URL 생성 (예: ditc=D&ymd=20251011&hh=16&rtm=Y&pg=1)"""
    qs = {
        "ditc": "D",
        "ymd": ymd,
        "hh": str(hh),
        "rtm": "Y",
        "pg": str(page),
    }
    return f"{BASE_URL}?{urlencode(qs)}"


def fetch_genie_html(ymd: str, hh: int, page: int) -> str:
    """Genie Top200 특정 페이지 HTML 가져오기"""
    url = build_genie_url(ymd, hh, page)
    s = _make_session()
    time.sleep(0.2)
    resp = s.get(url, timeout=15)
    if resp.status_code != 200:
        time.sleep(0.5)
        resp = s.get(url, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def parse_genie(html: str) -> List[Dict]:
    """
    Genie Top200 파싱
    - 행: tr.list
    - 순위: td.number (앞쪽 숫자)
    - 제목: a.title.ellipsis
    - 가수: a.artist.ellipsis
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr.list")
    items: List[Dict] = []

    for tr in rows:
        rank_el = tr.select_one("td.number")
        title_el = tr.select_one("a.title.ellipsis")
        artist_el = tr.select_one("a.artist.ellipsis")

        if not (rank_el and title_el and artist_el):
            continue

        # "1 상승" 같은 문자열에서 숫자만 추출
        rank_text = rank_el.get_text(" ", strip=True).split()[0]
        try:
            rank = int(rank_text)
        except ValueError:
            continue

        title = title_el.get_text(" ", strip=True)
        artist = artist_el.get_text(" ", strip=True)

        items.append(
            {
                "rank": rank,
                "title": title,
                "artists": [artist],
            }
        )

    items.sort(key=lambda x: x["rank"])
    return items


def get_genie_top200_items(ymd: Optional[str] = None, hh: Optional[int] = None, max_page: int = 4) -> List[Dict]:
    """
    지정한 날짜/시간 기준으로 pg=1..max_page(기본 4)까지 모두 수집. (총 200곡)
    - ymd/hh를 생략하면 현재 시각 기준으로 자동 계산
    반환: [{"rank": int, "title": str, "artists": [str, ...]}, ...]
    """
    if ymd is None or hh is None:
        now = datetime.now()
        ymd = now.strftime("%Y%m%d") if ymd is None else ymd
        hh = now.hour if hh is None else hh

    all_items: List[Dict] = []
    for pg in range(1, max_page + 1):
        html = fetch_genie_html(ymd, hh, pg)
        page_items = parse_genie(html)
        all_items.extend(page_items)

    # rank를 키로 중복 제거
    dedup = {it["rank"]: it for it in all_items}
    return [dedup[k] for k in sorted(dedup.keys())]


# ── 멜론과 동일한 검색 유틸 ────────────────────────────────────────────────────────
def find_rank_by_title_artist(items: List[Dict], title: str, artist: str | None = None) -> Optional[int]:
    """
    제목/가수로 순위 반환(부분 일치 허용, 대소문자/공백/괄호 관대)
    """
    def _norm(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"[\s\-\(\)\[\]\{\}/\\]+", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s

    q_title = _norm(title)
    q_artist = _norm(artist) if artist else None

    for item in items:
        t = _norm(item.get("title", ""))
        a_list = [_norm(a) for a in item.get("artists", [])]

        title_ok = (q_title in t) or (t in q_title)

        artist_ok = True
        if q_artist:
            artist_ok = any(q_artist in a or a in q_artist for a in a_list)

        if title_ok and artist_ok:
            return item.get("rank")
    return None


def find_rank_by_title_artist_with_alias(
    items: List[Dict],
    title: str,
    artist: Optional[str] = None,
    title_aliases: Optional[List[str]] = None,
    artist_aliases: Optional[List[str]] = None,
) -> Optional[int]:
    """
    별칭(영문/한글 표기 등)을 함께 고려한 검색
    """
    def _norm(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"[\s\-\(\)\[\]\{\}/\\]+", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s

    q_titles = [title] + (title_aliases or [])
    q_artists = ([artist] if artist else []) + (artist_aliases or [])

    q_titles_n = [_norm(t) for t in q_titles if t]
    q_artists_n = [_norm(a) for a in q_artists if a]

    for item in items:
        t = _norm(item.get("title", ""))
        a_list = [_norm(a) for a in item.get("artists", [])]

        title_match = any(qt in t or t in qt for qt in q_titles_n)

        artist_match = True
        if q_artists_n:
            artist_match = any(q in a or a in q for q in q_artists_n for a in a_list)

        if title_match and artist_match:
            return item.get("rank")
    return None
# ────────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    # 1) 지니 Top200 수집 (기본: 현재 시각 기준 ymd/hh 자동)
    items = get_genie_top200_items(max_page=4)

    # 2) 설정 파일에서 타깃 목록을 읽어 순회
    cfg = load_songs_config()
    targets = iter_targets(cfg)

    for t in targets:
        title = t.get("title", "")
        artist = t.get("artist", "")
        aliases = t.get("aliases", {})
        title_aliases = aliases.get("title", [])
        artist_aliases = aliases.get("artist", [])
        platforms = t.get("platforms", [])

        # 지니만 체크
        if "genie" not in platforms:
            continue

        rank = find_rank_by_title_artist_with_alias(
            items,
            title=title,
            artist=artist,
            title_aliases=title_aliases,
            artist_aliases=artist_aliases,
        )

        if rank is not None:
            print(f"[GENIE] '{artist}' - '{title}' 현재 순위: {rank}")
        else:
            print(f"[GENIE] '{artist}' - '{title}' 를 Top200에서 찾지 못했습니다.")
