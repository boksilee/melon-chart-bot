# src/config_loader.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List

def load_songs_config() -> Dict[str, Any]:
    """
    프로젝트 루트 기준으로 config/songs.json을 읽어 dict 반환.
    PyCharm/CLI 실행 경로가 달라도 __file__ 기준으로 안전하게 찾습니다.
    """
    here = Path(__file__).resolve()               # .../src/config_loader.py
    root = here.parent.parent                     # 프로젝트 루트
    cfg_path = root / "config" / "songs.json"
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)

def iter_targets(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return config.get("targets", [])
