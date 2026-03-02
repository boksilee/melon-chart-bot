# src/main.py
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import json
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config_loader import load_songs_config, iter_targets
from crawler_melon import get_melon_hot100_items, find_rank_by_title_artist_with_alias as find_melon_rank
from crawler_genie import get_genie_top200_items, find_rank_by_title_artist_with_alias as find_genie_rank
from crawler_youtube import get_youtube_views_for_targets
from notifier import slack_post

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "last_results"
DATA_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_FILES = {
    "melon": DATA_DIR / "melon.json",
    "genie": DATA_DIR / "genie.json",
    # 유튜브는 증감/이모지 안 쓰지만, 미래 확장 대비해 저장은 유지 가능
    "youtube": DATA_DIR / "youtube.json",
}

# ──────────────────────────────────────────────────────────────────────────────
# 유틸: 이전/현재 로드·저장
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
# 변동 이모지 (순위용만 유지)
# ──────────────────────────────────────────────────────────────────────────────
def change_emoji(prev: Optional[int], curr: Optional[int], had_history_file: bool) -> Tuple[str, str]:
    if curr is None:
        return ("🚫", "미진입")
    if prev is None:
        return ("🔁", "재진입") if had_history_file else ("🆕", "진입")
    if curr < prev:
        return ("🔺", "상승")
    if curr > prev:
        return ("🔻", "하락")
    return ("➖", "유지")

# ──────────────────────────────────────────────────────────────────────────────
# 실행본
# ──────────────────────────────────────────────────────────────────────────────
def main():
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    now = now_kst.strftime("%Y-%m-%d %H:%M")  # Slack 헤더용(그대로 사용)
    #tweet_header = f"{now_kst.month}월 {now_kst.day}일 {now_kst.hour}시 기준"
    tweet_header = f"{now_kst.month}월 {now_kst.day}일 MV 조회수"
    print(f"\n🚀 [음원 차트 스크래핑 시작] ({now})\n")

    cfg = load_songs_config()
    targets = list(iter_targets(cfg))

    # 플랫폼별 타깃
    melon_targets = [t for t in targets if "melon" in t.get("platforms", [])]
    genie_targets = [t for t in targets if "genie" in t.get("platforms", [])]

    # 현재 차트 수집
    melon_items = get_melon_hot100_items() if melon_targets else []
    genie_items = get_genie_top200_items(max_page=4) if genie_targets else []

    # 유튜브(벌크) 수집: songs.json 중 youtube 필드 있는 타깃만
    yt_rows = get_youtube_views_for_targets(targets)  # [{"key","title","artist","video_id","viewCount",...}]
    yt_map = {r["key"]: r for r in yt_rows}

    # 이전 결과 로드 (순위용)
    melon_prev_map = _load_prev("melon")
    genie_prev_map = _load_prev("genie")
    melon_had_file = HISTORY_FILES["melon"].exists()
    genie_had_file = HISTORY_FILES["genie"].exists()

    # 현재 결과 맵 (저장용)
    melon_curr_map: Dict[str, int] = {}
    genie_curr_map: Dict[str, int] = {}
    yt_curr_map: Dict[str, int] = {}  # 숫자만 저장 (증감/이모지 사용 안 함)

    combined_blocks: List[dict] = []
    tweet_lines_all: List[str] = []

    for t in targets:
        title = t.get("title", "")
        artist = t.get("artist", "")
        aliases = t.get("aliases", {})
        title_aliases = aliases.get("title", [])
        artist_aliases = aliases.get("artist", [])

        key = _result_key(title, artist)

        # ── 멜론 순위 (플랫폼 있으면만 표시)
        melon_rank = None
        melon_rank_txt = None
        melon_line = None
        melon_emo = None
        melon_label = None


        if "melon" in t.get("platforms", []):
            melon_rank = find_melon_rank(
                melon_items, title=title, artist=artist,
                title_aliases=title_aliases, artist_aliases=artist_aliases
            )
            melon_curr_map[key] = melon_rank if melon_rank is not None else -1

            prev = melon_prev_map.get(key)
            if prev == -1:
                prev = None
            melon_emo, melon_label = change_emoji(prev, melon_rank, had_history_file=melon_had_file)
            melon_rank_txt = f"{melon_rank} 위" if melon_rank is not None else "미진입"
            melon_line = f"• 멜론 : *{melon_rank_txt}*  {melon_emo}({melon_label})"

        # ── 지니 순위 (플랫폼 있으면만 표시)
        genie_rank = None
        genie_rank_txt = None
        genie_line = None
        genie_emo = None
        genie_label = None
        if "genie" in t.get("platforms", []):
            genie_rank = find_genie_rank(
                genie_items, title=title, artist=artist,
                title_aliases=title_aliases, artist_aliases=artist_aliases
            )
            genie_curr_map[key] = genie_rank if genie_rank is not None else -1

            prev = genie_prev_map.get(key)
            if prev == -1:
                prev = None
            genie_emo, genie_label = change_emoji(prev, genie_rank, had_history_file=genie_had_file)
            genie_rank_txt = f"{genie_rank} 위" if genie_rank is not None else "미진입"
            genie_line = f"• 지니 : *{genie_rank_txt}*  {genie_emo}({genie_label})"

        # ── 유튜브 조회수 (songs.json에 youtube가 있을 때만, ‘숫자만’ 표시)
        yt_line = None
        yt_view = None
        curr_yt = yt_map.get(key)
        if curr_yt:
            yt_view = curr_yt.get("viewCount")
            if yt_view is not None:
                yt_curr_map[key] = int(yt_view)
                yt_line = f"• 유튜브 : *{yt_view:,}회*"   # ← 증감/이모지/신규 표시 없이 ‘숫자만’

        # ── 콘솔 출력
        print(f"🎵 {title} - {artist}")
        if melon_line:
            print("   " + melon_line.replace("• ", ""))
        if genie_line:
            print("   " + genie_line.replace("• ", ""))
        if yt_line:
            print("   " + yt_line.replace("• ", ""))
        print("")

        # ── Slack 섹션 (없는 플랫폼/유튜브는 줄 자체 생략)
        lines = [l for l in (melon_line, genie_line, yt_line) if l]
        section_text = f"*{title}* - *{artist}*"
        if lines:
            section_text += "\n" + "\n".join(lines)
        combined_blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section_text}})
        combined_blocks.append({"type": "divider"})

        title_emoji = ""
        if title.lower() == "hunter":
            title_emoji = "🧟 "  # 예: 헌터
        elif title.lower() == "pleasure shop":
            title_emoji = "🍸"  # 예: 가솔린

        # yt_view는 위에서 이미 계산된 상태라고 가정
        if yt_view is not None:
            # 타이틀 - 조회수 형태로 1줄 만들기
            first_line = f"{title_emoji}{title} - {yt_view:,}회"
        else:
            # 조회수 없으면 타이틀만
            first_line = f"{title_emoji}{title}" if title_emoji else f"{title}"

        tweet_block_lines = [first_line]

        # 멜론 / 지니 줄 추가
        if melon_rank_txt is not None:
            tweet_block_lines.append(f"멜론 : {melon_rank_txt} {melon_emo}")
        if genie_rank_txt is not None:
            tweet_block_lines.append(f"지니 : {genie_rank_txt} {genie_emo}")


        if yt_view is not None:
            target = 10000000
            # 10M 으로 기준 변경
            if  yt_view < target: #if 14000000 <= yt_view < target:
                remain = target - yt_view
                tweet_block_lines.append(f"      10M까지 {remain:,}회")
        tweet_lines_all.append("\n".join(tweet_block_lines))

    # 히스토리 저장 (유튜브는 값만 기록)
    if melon_targets:
        _save_curr("melon", melon_curr_map)
    if genie_targets:
        _save_curr("genie", genie_curr_map)
    if yt_curr_map:
        _save_curr("youtube", yt_curr_map)

    # Slack 메시지 조립
    header_text = f"[차트 업데이트] {now}"
    blocks: List[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "멜론/지니 + 유튜브 결과를 모아 한 번에 전송"}]},
        {"type": "divider"},
    ]
    blocks.extend(combined_blocks[:-1] if combined_blocks and combined_blocks[-1].get("type") == "divider" else combined_blocks)

    # 트윗 버튼
    tweet_text = tweet_header + "\n\n" + "\n\n".join(tweet_lines_all)
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
