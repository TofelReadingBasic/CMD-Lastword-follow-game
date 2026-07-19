# -*- coding: utf-8 -*-
"""
한글 분해/조합 및 끝말잇기 규칙(두음법칙) 유틸리티
"""

CHO = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']
JUNG = ['ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅗ','ㅘ','ㅙ','ㅚ','ㅛ','ㅜ','ㅝ','ㅞ','ㅟ','ㅠ','ㅡ','ㅢ','ㅣ']
JONG = ['','ㄱ','ㄲ','ㄳ','ㄴ','ㄵ','ㄶ','ㄷ','ㄹ','ㄺ','ㄻ','ㄼ','ㄽ','ㄾ','ㄿ','ㅀ','ㅁ','ㅂ','ㅄ','ㅅ','ㅆ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']

# 두음법칙이 적용되는 'ㅣ'계 이중모음 (야,여,예,요,유,얘,이)
Y_VOWELS = {'ㅑ', 'ㅒ', 'ㅕ', 'ㅖ', 'ㅛ', 'ㅠ', 'ㅣ'}

SBASE = 0xAC00
LCOUNT, VCOUNT, TCOUNT = 19, 21, 28


def is_hangul_syllable(ch: str) -> bool:
    return len(ch) == 1 and 0xAC00 <= ord(ch) <= 0xD7A3


def decompose(ch: str):
    """완성형 한글 한 글자를 (초성, 중성, 종성)으로 분해"""
    if not is_hangul_syllable(ch):
        return None
    code = ord(ch) - SBASE
    cho = code // (VCOUNT * TCOUNT)
    jung = (code % (VCOUNT * TCOUNT)) // TCOUNT
    jong = code % TCOUNT
    return CHO[cho], JUNG[jung], JONG[jong]


def compose(cho: str, jung: str, jong: str = '') -> str:
    """초성/중성/종성을 완성형 한글 한 글자로 조합"""
    ci = CHO.index(cho)
    vi = JUNG.index(jung)
    ti = JONG.index(jong)
    code = SBASE + (ci * VCOUNT + vi) * TCOUNT + ti
    return chr(code)


def dueum_alternatives(syllable: str):
    """
    특정 음절이 '단어의 맨 앞'에 올 때 두음법칙으로 바뀔 수 있는
    표기들을 모두 반환한다 (자기 자신 포함).
    예: '량' -> {'량', '양'}, '라' -> {'라', '나'}, '녀' -> {'녀', '여'}
    """
    parts = decompose(syllable)
    if parts is None:
        return {syllable}

    cho, jung, jong = parts
    alts = {syllable}

    if cho == 'ㄹ':
        if jung in Y_VOWELS:
            alts.add(compose('ㅇ', jung, jong))
        else:
            alts.add(compose('ㄴ', jung, jong))
    elif cho == 'ㄴ':
        if jung in Y_VOWELS:
            alts.add(compose('ㅇ', jung, jong))

    return alts


def is_valid_word_format(word: str) -> bool:
    """공백 없이 순수 한글 2글자 이상인지 확인"""
    if not word or len(word) < 2:
        return False
    return all(is_hangul_syllable(ch) for ch in word)


def can_link(prev_word: str, next_word: str) -> bool:
    """끝말잇기 규칙(두음법칙 포함)에 따라 next_word가 prev_word 뒤에 올 수 있는지 확인"""
    if not prev_word or not next_word:
        return False
    last = prev_word[-1]
    first = next_word[0]
    return first in dueum_alternatives(last)


if __name__ == '__main__':
    # 간단한 테스트
    assert dueum_alternatives('량') == {'량', '양'}
    assert dueum_alternatives('라') == {'라', '나'}
    assert dueum_alternatives('녀') == {'녀', '여'}
    assert can_link('경제', '제주도')
    assert can_link('사랑', '낭만')     # 랑 -> 두음법칙 대체음 '낭'
    assert not can_link('사과', '바나나')
    print('hangul_utils self-test OK')
