# src/notifier.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# ────────────────────────────────────────────────────────────────────────────────
# 환경설정
# ────────────────────────────────────────────────────────────────────────────────
load_dotenv()
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

if not SLACK_WEBHOOK_URL:
    # 사용 시점에 예외로 처리하기 위해 None 허용, 여기서는 경고만
    print("[notifier] 경고: SLACK_WEBHOOK_URL 이 설정되지 않았습니다 (.env 확인).")

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "last_results"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 플랫폼별 공식 차트 링크 (버튼용)
PLATFORM_LINKS = {
    "melon": "https://www.melon.com/chart/index.htm",
    "genie": "https://www.genie.co.kr/chart/top200",
}

# ────────────────────────────────────────────────────────────────────────────────
# Slack 전송 기본기
# ────────────────────────────────────────────────────────────────────────────────
def slack_post(text: str, blocks: Optional[List[dict]] = None) -> None:
    if not SLACK_WEBHOOK_URL:
        raise RuntimeError("SLACK_WEBHOOK_URL is not set")
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
    try:
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Slack post failed: {resp.status_code} {resp.text}") from e


# ────────────────────────────────────────────────────────────────────────────────
# 순위 변화 계산 유틸
# ────────────────────────────────────────────────────────────────────────────────
def _delta_emoji(prev: Optional[int], curr: Optional[int]) -> str:
    """
    순위 변화 이모지:
      - 신규 진입: 🆕
      - 상승: 🔺
      - 하락: 🔻
      - 유지/변화없음: ➖
      - 이탈(이전엔 있었는데 현재 없음): 🚫  (리스트 생성 시 별도 처리)
    """
    if prev is None and curr is not None:
        return "🆕"
    if prev is not None and curr is not None:
        if curr < prev:
            return "🔺"
        elif curr > prev:
            return "🔻"
        else:
            return "➖"
    return "➖"


def _result_key(title: str, artist: str) -> str:
    # 곡 식별 키 (간단 조합)
    return f"{title}@@{artist}".lower().strip()


def _load_prev(platform: str) -> Dict[str, int]:
    path = DATA_DIR / f"{platform}.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_curr(platform: str, mapping: Dict[str, int]) -> None:
    path = DATA_DIR / f"{platform}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


# ────────────────────────────────────────────────────────────────────────────────
# 블록 빌더
# ────────────────────────────────────────────────────────────────────────────────
def _build_row_text(title: str, artist: str, rank: Optional[int], prev: Optional[int]) -> str:
    emo = _delta_emoji(prev, rank)
    rank_txt = f"{rank}위" if rank is not None else "미진입"
    prev_txt = f"(prev {prev})" if prev is not None else ""
    return f"{emo} *{artist}* — *{title}* · *{rank_txt}* {prev_txt}".strip()


def _build_blocks(platform: str,
                  header_text: str,
                  rows: List[Tuple[str, str, Optional[int], Optional[int]]],
                  tweet_preset: Optional[str] = None) -> List[dict]:
    """
    rows: list of (title, artist, curr_rank, prev_rank)
    """
    blocks: List[dict] = []

    # 헤더
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": header_text}
    })

    # 본문 (각 곡 한 줄)
    for title, artist, curr, prev in rows:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": _build_row_text(title, artist, curr, prev)}})

    # 버튼 (플랫폼 링크 + (선택) X 작성)
    actions: List[dict] = [{
        "type": "button",
        "text": {"type": "plain_text", "text": f"열기: {platform.capitalize()} 차트"},
        "url": PLATFORM_LINKS.get(platform, "https://google.com")
    }]

    if tweet_preset:
        from urllib.parse import quote
        url = f"https://twitter.com/intent/tweet?text={quote(tweet_preset)}"
        actions.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "X(트위터) 작성"},
            "url": url
        })

    blocks.append({"type": "actions", "elements": actions})
    return blocks


# ────────────────────────────────────────────────────────────────────────────────
# 공개 API
# ────────────────────────────────────────────────────────────────────────────────
def notify_platform_ranks(platform: str,
                          results: List[Dict],
                          title: str = "음원 차트 업데이트",
                          save_history: bool = True,
                          tweet_preset: Optional[str] = None) -> None:
    """
    플랫폼별 랭크 결과를 슬랙으로 전송.
    results 포맷: [{"title": "...", "artists": ["..."], "rank": 12}, ...]
                  또는 main에서 만든 축약 리스트 [{"title": "...", "artist": "...", "rank": 12}]
    - 이전 결과와 비교하여 이모지(🔺🔻🆕🚫/➖) 표시
    - save_history=True 이면 data/last_results/{platform}.json 에 저장
    """
    # 현재 결과를 title+artist 키로 매핑
    curr_map: Dict[str, int] = {}
    rows: List[Tuple[str, str, Optional[int], Optional[int]]] = []

    # 입력 표준화
    for it in results:
        title_ = it.get("title", "").strip()
        artist_list = it.get("artists") or [it.get("artist", "").strip()]
        artist_ = (artist_list[0] if artist_list else "").strip()
        rank_ = it.get("rank")
        k = _result_key(title_, artist_)
        curr_map[k] = rank_ if rank_ is not None else -1  # 미진입은 -1로 저장(보관용)
    prev_map = _load_prev(platform)

    # 표시용 rows (현재 목록 기준)
    for it in results:
        title_ = it.get("title", "").strip()
        artist_list = it.get("artists") or [it.get("artist", "").strip()]
        artist_ = (artist_list[0] if artist_list else "").strip()
        rank_ = it.get("rank")
        k = _result_key(title_, artist_)
        prev_rank = prev_map.get(k)
        if prev_rank == -1:
            prev_rank = None  # 과거에도 미진입이면 None 취급
        rows.append((title_, artist_, rank_, prev_rank))

    # 이탈(과거엔 있었는데 현재 없음)도 보여주고 싶다면 여기서 rows에 추가 가능
    # for k, prev_rank in prev_map.items():
    #     if k not in curr_map and prev_rank not in (None, -1):
    #         t, a = k.split("@@")
    #         rows.append((t, a, None, prev_rank))

    header = f"[{platform.capitalize()}] {title}"
    blocks = _build_blocks(platform, header, rows, tweet_preset=tweet_preset)
    text_fallback = header + "\n" + "\n".join(
        _build_row_text(t, a, c, p) for t, a, c, p in rows
    )

    slack_post(text_fallback, blocks=blocks)

    if save_history:
        _save_curr(platform, curr_map)
