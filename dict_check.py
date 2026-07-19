# -*- coding: utf-8 -*-
"""
국립국어원 '표준국어대사전' Open API를 이용한 단어 유효성 검사 및 뜻풀이 조회.

무료 API 키 발급: https://stdict.korean.go.kr/openapi/openApiInfo.do
발급받은 키는 환경변수 STDICT_API_KEY 로 설정하세요.
    export STDICT_API_KEY="발급받은키"      (윈도우 cmd는 set STDICT_API_KEY=발급받은키)
"""

import os
import requests
import xml.etree.ElementTree as ET

API_URL = "https://stdict.korean.go.kr/api/search.do"
API_KEY = os.environ.get("STDICT_API_KEY", "").strip()

_info_cache = {}   # word -> {"exists": bool, "definition": str|None, "pos": str|None}
_prefix_cache = {}  # (prefix, letter_e) -> list[(word, definition)]


class DictUnavailable(Exception):
    """사전 API 호출이 실패했을 때 발생"""
    pass


def _require_key():
    if not API_KEY:
        raise DictUnavailable(
            "STDICT_API_KEY 환경변수가 설정되어 있지 않습니다. "
            "https://stdict.korean.go.kr/openapi/openApiInfo.do 에서 무료 키를 발급받으세요."
        )


def _clean_headword(headword: str) -> str:
    """사전 표제어에 붙는 어깨번호/기호 등을 제거해 순수 단어만 남긴다."""
    cleaned = "".join(ch for ch in headword if not ch.isdigit()).strip()
    return cleaned.replace("^", "").replace("-", "")


def brief_definition(definition: str, max_len: int = 40) -> str:
    """뜻풀이를 짧게 요약(첫 문장 또는 max_len자)한다."""
    if not definition:
        return ""
    d = " ".join(definition.split())
    idx = d.find(".")
    if idx != -1 and idx < max_len:
        return d[: idx + 1]
    if len(d) > max_len:
        return d[:max_len].rstrip() + "…"
    return d


def get_word_info(word: str, timeout: float = 5.0) -> dict:
    """
    단어의 사전 등재 여부와 첫 번째 뜻풀이를 함께 조회한다.
    반환: {"exists": bool, "definition": str|None, "pos": str|None}
    """
    if word in _info_cache:
        return _info_cache[word]

    _require_key()

    params = {
        "key": API_KEY,
        "q": word,
        "req_type": "xml",
        "num": 10,
    }

    try:
        resp = requests.get(API_URL, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise DictUnavailable(f"사전 API 호출 실패: {e}")

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise DictUnavailable(f"사전 API 응답 파싱 실패: {e}")

    info = {"exists": False, "definition": None, "pos": None}
    for item in root.iter("item"):
        w_el = item.find("word")
        if w_el is None or not w_el.text:
            continue
        if _clean_headword(w_el.text.strip()) != word:
            continue
        info["exists"] = True
        pos_el = item.find("pos")
        if pos_el is not None and pos_el.text:
            info["pos"] = pos_el.text.strip()
        sense_el = item.find("sense")
        if sense_el is not None:
            def_el = sense_el.find("definition")
            if def_el is not None and def_el.text:
                info["definition"] = def_el.text.strip()
        break  # 첫 번째로 일치하는 표제어 정보만 사용

    _info_cache[word] = info
    return info


def is_valid_korean_word(word: str, timeout: float = 5.0) -> bool:
    """표준국어대사전에 등재된 단어인지 확인한다. (하위 호환용 래퍼)"""
    return get_word_info(word, timeout=timeout)["exists"]


def search_words_starting_with(prefix: str, letter_s: int = 2, letter_e: int = 4,
                                noun_only: bool = True, limit: int = 50,
                                timeout: float = 5.0):
    """
    특정 글자로 '시작'하는 표제어 목록을 (단어, 품사, 간략한뜻) 튜플 리스트로 반환한다.
    끝말잇기 AI가 다음에 낼 단어 후보를 찾을 때 사용한다.
    """
    cache_key = (prefix, letter_s, letter_e, noun_only)
    if cache_key in _prefix_cache:
        return _prefix_cache[cache_key]

    _require_key()

    params = {
        "key": API_KEY,
        "q": prefix,
        "req_type": "xml",
        "advanced": "y",
        "method": "start",
        "type1": "word",
        "letter_s": letter_s,
        "letter_e": letter_e,
        "num": min(max(limit, 10), 100),
    }
    if noun_only:
        params["pos"] = 1  # 명사만

    try:
        resp = requests.get(API_URL, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise DictUnavailable(f"사전 API 호출 실패: {e}")

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise DictUnavailable(f"사전 API 응답 파싱 실패: {e}")

    results = []
    seen = set()
    for item in root.iter("item"):
        w_el = item.find("word")
        if w_el is None or not w_el.text:
            continue
        word = _clean_headword(w_el.text.strip())
        if not word or word in seen or not word.startswith(prefix):
            continue
        seen.add(word)
        pos = None
        pos_el = item.find("pos")
        if pos_el is not None and pos_el.text:
            pos = pos_el.text.strip()
        definition = None
        sense_el = item.find("sense")
        if sense_el is not None:
            def_el = sense_el.find("definition")
            if def_el is not None and def_el.text:
                definition = def_el.text.strip()
        results.append((word, pos, definition))

    _prefix_cache[cache_key] = results
    return results


def format_word_line(word: str, pos: str = None, definition: str = None) -> str:
    """'단어 | 품사 | 간략한뜻' 형식의 표시용 문자열을 만든다."""
    p = pos if pos else "품사미상"
    d = brief_definition(definition) if definition else "뜻풀이 없음"
    return f"{word} | {p} | {d}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("사용법: python3 dict_check.py <단어>")
        sys.exit(1)
    w = sys.argv[1]
    try:
        info = get_word_info(w)
        print(f"'{w}' 등재 여부: {info['exists']}, 품사: {info['pos']}, 뜻: {info['definition']}")
    except DictUnavailable as e:
        print(f"[오류] {e}")
