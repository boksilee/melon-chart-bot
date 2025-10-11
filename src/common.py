# src/common.py
import re
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

def norm(s: str) -> str:
    """대소문자/공백/괄호/슬래시/하이픈 등 제거/정규화"""
    s = s.lower().strip()
    s = re.sub(r"[\s\-\(\)\[\]\{\}/\\]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s

def make_session(headers: dict, retries: int = 3, backoff: float = 0.3) -> requests.Session:
    """리트라이 포함 requests 세션 생성"""
    s = requests.Session()
    r = Retry(total=retries, backoff_factor=backoff, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=r))
    s.headers.update(headers)
    return s

def any_match(q: str, candidates: list[str]) -> bool:
    """부분 일치: q in cand or cand in q 가 하나라도 True면 매칭"""
    from .common import norm as _n  # 순환 import 방지용 로컬 import
    qn = _n(q)
    for c in candidates:
        cn = _n(c)
        if qn in cn or cn in qn:
            return True
    return False


