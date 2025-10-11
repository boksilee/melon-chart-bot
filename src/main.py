# src/main.py
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import json
from typing import Dict, List, Optional, Tuple

from config_loader import load_songs_config, iter_targets
from crawler_melon import get_melon_hot100_items, find_rank_by_title_artist_with_alias as find_melon_rank
from crawler_genie import get_genie_top200_items, find_rank_by_title_artist_with_alias as find_genie_rank
from notifier import slack_post

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "last_results"
DATA_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_FILES = {
    "melon": DATA_DIR / "melon.json",
    "genie": DATA_DIR / "genie.json",
}


# ──────────────────────────────────────────────────────────────────────────────
# 유틸: 이전/현재 순위 로드·저장
# ──────────────────────────────────────────────────────────────────────────────
def _result_key(title: str, artist: str) -> str:
    return f"{title}@@{artist}".lower().strip()


def _load_prev(platform: str) -> Dict[str, int]:
    p = HISTORY_FILES[platform]
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_curr(platform: str, mapping: Dict[str, int]) -> None:
    p = HISTORY_FILES[platform]
    with p.open("w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# 변동 이모지 계산 (상승/하락/유지/진입/재진입/🚫)
# ──────────────────────────────────────────────────────────────────────────────
def change_emoji(prev: Optional[int], curr: Optional[int], had_history_file: bool) -> Tuple[str, str]:
    """
    반환: (emoji, label)
    - 🚫 : 미진입 or 탈락 (둘 다 동일)
    - 🆕 : 최초 진입 (이전 기록 없음)
    - 🔁 : 재진입 (이전 기록 있었고, 미진입이었다가 이번에 진입)
    - 🔺 : 상승
    - 🔻 : 하락
    - ➖ : 유지
    """
    # 미진입 케이스 (curr None)
    if curr is None:
        return ("🚫", "미진입")

    # 최초 진입/재진입 판단
    if prev is None:
        if had_history_file:
            return ("🔁", "재진입")
        else:
            return ("🆕", "진입")

    # 둘 다 순위가 있을 때
    if curr < prev:
        return ("🔺", "상승")
    if curr > prev:
        return ("🔻", "하락")
    return ("➖", "유지")


# ──────────────────────────────────────────────────────────────────────────────
# 실행본
# ──────────────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n🚀 [음원 차트 스크래핑 시작] ({now})\n")

    cfg = load_songs_config()
    targets = list(iter_targets(cfg))

    # 플랫폼별 타깃
    melon_targets = [t for t in targets if "melon" in t.get("platforms", [])]
    genie_targets = [t for t in targets if "genie" in t.get("platforms", [])]

    # 현재 차트 수집
    melon_items = get_melon_hot100_items() if melon_targets else []
    genie_items = get_genie_top200_items(max_page=4) if genie_targets else []

    # 이전 결과 로드
    melon_prev_map = _load_prev("melon")
    genie_prev_map = _load_prev("genie")
    melon_had_file = HISTORY_FILES["melon"].exists()
    genie_had_file = HISTORY_FILES["genie"].exists()

    # 현재 결과 맵 작성 (저장용)
    melon_curr_map: Dict[str, int] = {}
    genie_curr_map: Dict[str, int] = {}

    combined_blocks = []
    tweet_lines_all = []

    for t in targets:
        title = t.get("title", "")
        artist = t.get("artist", "")
        aliases = t.get("aliases", {})
        title_aliases = aliases.get("title", [])
        artist_aliases = aliases.get("artist", [])

        key = _result_key(title, artist)

        # 멜론 순위 조회
        melon_rank = None
        if "melon" in t.get("platforms", []):
            melon_rank = find_melon_rank(
                melon_items,
                title=title,
                artist=artist,
                title_aliases=title_aliases,
                artist_aliases=artist_aliases,
            )
            melon_curr_map[key] = melon_rank if melon_rank is not None else -1

        # 지니 순위 조회
        genie_rank = None
        if "genie" in t.get("platforms", []):
            genie_rank = find_genie_rank(
                genie_items,
                title=title,
                artist=artist,
                title_aliases=title_aliases,
                artist_aliases=artist_aliases,
            )
            genie_curr_map[key] = genie_rank if genie_rank is not None else -1

        # 이전 순위
        melon_prev = melon_prev_map.get(key)
        genie_prev = genie_prev_map.get(key)
        if melon_prev == -1:
            melon_prev = None
        if genie_prev == -1:
            genie_prev = None

        # 변동 이모지
        melon_emo, melon_label = change_emoji(melon_prev, melon_rank, had_history_file=melon_had_file)
        genie_emo, genie_label = change_emoji(genie_prev, genie_rank, had_history_file=genie_had_file)

        # 순위 텍스트
        melon_rank_txt = f"{melon_rank} 위" if melon_rank is not None else "미진입"
        genie_rank_txt = f"{genie_rank} 위" if genie_rank is not None else "미진입"

        # 콘솔 출력
        print(f"🎵 {title} - {artist}")
        if "melon" in t.get("platforms", []):
            print(f"   멜론 : {melon_rank_txt}  {melon_emo}({melon_label})")
        if "genie" in t.get("platforms", []):
            print(f"   지니 : {genie_rank_txt}  {genie_emo}({genie_label})")
        print("")

        # Slack 섹션
        section_text = f"*{title}* - *{artist}*\n"
        if "melon" in t.get("platforms", []):
            section_text += f"• 멜론 : *{melon_rank_txt}*  {melon_emo}({melon_label})\n"
        if "genie" in t.get("platforms", []):
            section_text += f"• 지니 : *{genie_rank_txt}*  {genie_emo}({genie_label})"
        combined_blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section_text}})
        combined_blocks.append({"type": "divider"})

        # 트위터용
        tweet_block = f"{title} - {artist}\n멜론 : {melon_rank_txt} {melon_emo}\n지니 : {genie_rank_txt} {genie_emo}"
        tweet_lines_all.append(tweet_block)

    # 히스토리 저장
    if melon_targets:
        _save_curr("melon", melon_curr_map)
    if genie_targets:
        _save_curr("genie", genie_curr_map)

    # Slack 메시지 조립
    header_text = f"[차트 업데이트] {now}"
    blocks: List[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "멜론/지니 결과를 모아 한 번에 전송"}]},
        {"type": "divider"},
    ]
    blocks.extend(combined_blocks[:-1] if combined_blocks and combined_blocks[-1].get("type") == "divider" else combined_blocks)

    # 트윗 버튼
    tweet_text = "\n\n".join(tweet_lines_all)
    from urllib.parse import quote
    tweet_url = f"https://twitter.com/intent/tweet?text={quote(tweet_text)}"
    actions = [
        {"type": "button", "text": {"type": "plain_text", "text": "X(트위터) 작성"}, "url": tweet_url},
        {"type": "button", "text": {"type": "plain_text", "text": "멜론 차트"}, "url": "https://www.melon.com/chart/index.htm"},
        {"type": "button", "text": {"type": "plain_text", "text": "지니 차트"}, "url": "https://www.genie.co.kr/chart/top200"},
    ]
    blocks.append({"type": "actions", "elements": actions})

    # Slack 전송
    text_fallback = header_text + "\n\n" + "\n\n".join(tweet_lines_all)
    try:
        slack_post(text_fallback, blocks=blocks)
        print("✅ [Slack] 전송 완료")
    except Exception as e:
        print(f"❌ [Slack] 전송 실패: {e}")

    print("✅ [완료]")


if __name__ == "__main__":
    main()
