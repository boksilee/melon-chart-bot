

# src/crawler_melon.py
from __future__ import annotations

import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)


import re
import time
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

from pathlib import Path
from typing import List, Dict, Optional
from config_loader import load_songs_config, iter_targets
from common import norm, any_match

HOT100_URL = "https://www.melon.com/chart/hot100/index.htm"

# 브라우저 흉내내기 (멜론은 UA/리퍼러/언어 헤더 없으면 406/차단되는 경우가 많음)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.melon.com/chart/index.htm",
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


def fetch_hot100_html(url: str = HOT100_URL) -> str:
    """
    멜론 Hot100 HTML을 가져옴.
    차단 우회를 위해 헤더/리트라이/약간의 지연을 둠.
    """
    s = _make_session()
    # 멜론은 가끔 첫 요청에 빈/차단 페이지를 줄 수 있어 약간의 지연을 줌
    time.sleep(0.2)
    resp = s.get(url, timeout=15)
    # 406(또는 기타 상태)일 때 한 번 더 시도
    if resp.status_code != 200:
        time.sleep(0.5)
        resp = s.get(url, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def parse_hot100(html: str) -> List[Dict]:
    """
    멜론 차트 테이블 파싱.
    - 일반적으로 tr.lst50 / tr.lst100 행으로 노출
    - 순위: span.rank
    - 제목: div.ellipsis.rank01 a
    - 아티스트: div.ellipsis.rank02 a
    """
    soup = BeautifulSoup(html, "html.parser")

    rows = soup.select("tr.lst50, tr.lst100")
    # 혹시 셀렉터가 바뀐 경우 대비: tbody 내 모든 tr
    if not rows:
        rows = soup.select("tbody tr")

    items = []
    for tr in rows:
        # rank
        rank_el = tr.select_one("span.rank")
        # 제목
        title_el = tr.select_one("div.ellipsis.rank01 a")
        # 아티스트(여러명일 수 있어 a 태그 모두)
        artist_els = tr.select("div.ellipsis.rank02 a")

        # 보강: 다른 마크업 대응
        if not title_el:
            title_el = tr.select_one("div.rank01 a, div.ellipsis a")
        if not rank_el:
            # 숫자만 들어있는 다른 셀을 보조로 찾기
            cand = tr.select_one("span, div, td")
            if cand and cand.get_text(strip=True).isdigit():
                class Dummy: text = cand.get_text(strip=True)
                rank_el = Dummy()

        rank = None
        title = None
        artists = []

        if rank_el:
            # '1', '2' 같은 숫자만 남기기
            m = re.search(r"\d+", rank_el.get_text(" ", strip=True))
            if m:
                rank = int(m.group(0))

        if title_el:
            title = title_el.get_text(" ", strip=True)

        if artist_els:
            artists = [a.get_text(" ", strip=True) for a in artist_els]
        else:
            # 아티스트 셀렉터 백업
            artist_cell = tr.select_one("div.ellipsis.rank02")
            if artist_cell:
                artists = [artist_cell.get_text(" ", strip=True)]

        if rank and title:
            items.append(
                {
                    "rank": rank,
                    "title": title,
                    "artists": artists,
                }
            )

    # 순위 오름차순 정렬(보장용)
    items.sort(key=lambda x: x["rank"])
    return items


from typing import Optional

def find_rank_by_title_artist(items: list[dict], title: str, artist: str | None = None) -> Optional[int]:
    """
    제목(title)과 아티스트(artist)를 함께 고려해 순위를 반환.
    - title은 필수, artist는 선택(미지정 시 제목만으로 검색)
    - 비교 시 대소문자/공백/괄호/특수문자에 관대(부분 일치 허용)
    - items: parse_hot100() 결과 리스트
    """
    import re

    def norm(s: str) -> str:
        s = s.lower().strip()
        # 공백/괄호/하이픈 등 잡스러운 문자들을 한 칸 공백으로 정리
        s = re.sub(r"[\s\-\(\)\[\]\{\}/\\]+", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s

    q_title = norm(title)
    q_artist = norm(artist) if artist else None

    for item in items:
        t = norm(item.get("title", ""))
        # 여러 아티스트(피처링 등) 지원
        a_list = [norm(a) for a in item.get("artists", [])]

        title_match = (q_title in t) or (t in q_title)

        artist_match = True
        if q_artist:
            # 하나라도 걸리면 매칭 인정
            artist_match = any(q_artist in a or a in q_artist for a in a_list)

        if title_match and artist_match:
            return item.get("rank")

    return None


def find_rank_by_title_artist_with_alias(
    items: list[dict],
    title: str,
    artist: Optional[str] = None,
    title_aliases: Optional[list[str]] = None,
    artist_aliases: Optional[list[str]] = None,
) -> Optional[int]:
    """
    기존 find_rank_by_title_artist를 확장.
    - title/artist가 우선
    - 없으면 aliases와의 부분 일치도 허용
    """
    import re

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


def find_rank_by_title(items: List[Dict], query_title: str) -> Optional[int]:
    """
    제목으로 순위를 찾는다.
    - 대소문자/공백/하이픈/괄호/특수문자에 비교적 관대한 부분일치
    """
    def norm(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"[\s\-\(\)\[\]\{\}]+", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s

    q = norm(query_title)
    for item in items:
        t = norm(item["title"])
        if q in t or t in q:
            return item["rank"]
    return None



def get_melon_hot100_items() -> list[dict]:
    html = fetch_hot100_html()
    return parse_hot100(html)


if __name__ == "__main__":
    # 1) 멜론 HOT100 크롤링
    items = get_melon_hot100_items()

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

        # 멜론만 체크(지니는 추후 genie_crawler.py에서 재사용)
        if "melon" not in platforms:
            continue

        rank = find_rank_by_title_artist_with_alias(
            items,
            title=title,
            artist=artist,
            title_aliases=title_aliases,
            artist_aliases=artist_aliases,
        )

        if rank is not None:
            print(f"[MELON] '{artist}' - '{title}' 현재 순위: {rank}")
        else:
            print(f"[MELON] '{artist}' - '{title}' 를 Hot100에서 찾지 못했습니다.")
