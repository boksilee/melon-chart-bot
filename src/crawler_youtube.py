# src/crawler_youtube.py
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

import requests

YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"

# 다양한 유튜브 URL 패턴에서 11자리 영상 ID를 뽑아내는 정규식들
_YT_PATTERNS = [
    r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[^\w-]|$)",   # watch?v=, youtu.be/, embed/, shorts/ 등 대응
    r"^([0-9A-Za-z_-]{11})$"                       # 이미 11자리 ID만 들어온 경우
]


# ──────────────────────────────────────────────────────────────────────────────
# ID/URL 처리
# ──────────────────────────────────────────────────────────────────────────────
def extract_video_id(url_or_id: str) -> Optional[str]:
    """유튜브 URL 또는 11자리 ID에서 영상 ID를 추출."""
    if not url_or_id:
        return None
    s = url_or_id.strip()
    for pat in _YT_PATTERNS:
        m = re.search(pat, s)
        if m:
            return m.group(1)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# API 호출 (단건 / 벌크)
# ──────────────────────────────────────────────────────────────────────────────
def _get_api_key(explicit_key: Optional[str] = None) -> Optional[str]:
    return explicit_key or os.getenv("YOUTUBE_API_KEY")

def get_video_stats_single(video_id: str, api_key: Optional[str] = None) -> Optional[Dict]:
    """
    단일 영상 ID로 조회수 등 통계를 가져옵니다.
    반환 예: {"id":"...", "viewCount":123, "likeCount":456, "commentCount":7, "title":"...", "publishedAt":"..."}
    """
    key = _get_api_key(api_key)
    if not key:
        print("[YouTube] YOUTUBE_API_KEY not set. Skip single.")
        return None

    params = {
        "id": video_id,
        "part": "snippet,statistics",
        "key": key,
        "hl": "ko",
    }
    r = requests.get(YOUTUBE_API_URL, params=params, timeout=15)
    if r.status_code != 200:
        print(f"[YouTube] API error(single): {r.status_code} {r.text[:200]}")
        return None
    js = r.json()
    items = js.get("items", [])
    if not items:
        return None

    it = items[0]
    stats = it.get("statistics", {})
    snip = it.get("snippet", {})
    return {
        "id": it.get("id"),
        "title": snip.get("title"),
        "publishedAt": snip.get("publishedAt"),
        "viewCount": _safe_int(stats.get("viewCount")),
        "likeCount": _safe_int(stats.get("likeCount")),
        "commentCount": _safe_int(stats.get("commentCount")),
    }

def get_video_stats_bulk(video_ids: List[str], api_key: Optional[str] = None) -> Dict[str, Dict]:
    """
    여러 영상 ID를 한 번에(최대 50개/요청) 조회. 반환: {video_id: stats_dict}
    """
    key = _get_api_key(api_key)
    if not key:
        print("[YouTube] YOUTUBE_API_KEY not set. Skip bulk.")
        return {}

    # 최대 50개씩 청크로 나눠 호출
    out: Dict[str, Dict] = {}
    for chunk in _chunks(video_ids, 50):
        params = {
            "id": ",".join(chunk),
            "part": "snippet,statistics",
            "key": key,
            "hl": "ko",
        }
        r = requests.get(YOUTUBE_API_URL, params=params, timeout=20)
        if r.status_code != 200:
            print(f"[YouTube] API error(bulk): {r.status_code} {r.text[:200]}")
            continue
        js = r.json()
        for it in js.get("items", []):
            vid = it.get("id")
            stats = it.get("statistics", {})
            snip = it.get("snippet", {})
            out[vid] = {
                "id": vid,
                "title": snip.get("title"),
                "publishedAt": snip.get("publishedAt"),
                "viewCount": _safe_int(stats.get("viewCount")),
                "likeCount": _safe_int(stats.get("likeCount")),
                "commentCount": _safe_int(stats.get("commentCount")),
            }
    return out


# ──────────────────────────────────────────────────────────────────────────────
# songs.json과 연동하는 헬퍼
# ──────────────────────────────────────────────────────────────────────────────
def collect_video_ids_from_targets(targets: List[Dict]) -> List[Tuple[str, str, str, str]]:
    """
    targets에서 youtube.url 또는 youtube.id가 있는 항목을 모아
    [(key, title, artist, video_id), ...] 형태로 반환.
    key는 "title@@artist".lower() (히스토리/매칭용)
    """
    rows: List[Tuple[str, str, str, str]] = []
    for t in targets:
        y = t.get("youtube")
        if not y:
            continue
        raw = y.get("id") or y.get("url") or ""
        vid = extract_video_id(raw)
        if not vid:
            continue
        title = (t.get("title") or "").strip()
        artist = (t.get("artist") or "").strip()
        key = f"{title}@@{artist}".lower().strip()
        rows.append((key, title, artist, vid))
    return rows

def get_youtube_views_for_targets(targets: List[Dict], api_key: Optional[str] = None) -> List[Dict]:
    """
    targets 중 유튜브 필드가 있는 항목들의 조회수를 일괄 조회.
    반환: [{"key":..., "title":..., "artist":..., "video_id":..., "viewCount":int|None}, ...]
    """
    rows = collect_video_ids_from_targets(targets)
    if not rows:
        return []

    # 벌크 조회
    vids = [r[3] for r in rows]
    stat_map = get_video_stats_bulk(vids, api_key=api_key)

    out: List[Dict] = []
    for key, title, artist, vid in rows:
        stat = stat_map.get(vid)
        out.append({
            "key": key,
            "title": title,
            "artist": artist,
            "video_id": vid,
            "viewCount": (stat or {}).get("viewCount"),
            "yt_title": (stat or {}).get("title"),
            "publishedAt": (stat or {}).get("publishedAt"),
        })
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────────────────────────────────────
def _chunks(seq: List[str], n: int) -> List[List[str]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]

def _safe_int(x) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# 단독 테스트 실행 (선택)
#   - 워크플로/로컬 둘 다 PYTHONPATH로 src를 잡는다면,
#     아래와 같이 간단 테스트 가능:
#     python -m crawler_youtube  (또는 PYTHONPATH=. python -m crawler_youtube)
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 로컬 단독 테스트(선택): config_loader를 통해 targets 읽고 유튜브 있는 항목만 조회
    try:
        from config_loader import load_songs_config, iter_targets  # PYTHONPATH=. 기준
    except Exception:
        print("[YouTube] config_loader import 실패 (PYTHONPATH 설정 확인)")
        raise

    cfg = load_songs_config()
    targets = list(iter_targets(cfg))
    results = get_youtube_views_for_targets(targets)
    print(f"[YouTube] {len(results)} items")
    for r in results:
        print(r)
