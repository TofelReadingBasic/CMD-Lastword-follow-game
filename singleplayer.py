# -*- coding: utf-8 -*-
"""
끝말잇기 - 혼자서 컴퓨터(AI)와 대결하는 버전

실행 (Windows cmd):
    set STDICT_API_KEY=발급받은키
    python singleplayer.py
"""

import os
import random
import sys

from hangul_utils import is_valid_word_format, can_link, dueum_alternatives
from dict_check import get_word_info, search_words_starting_with, format_word_line, DictUnavailable
from ui_utils import Spinner

# ==========================================
# [설정 데이터] 게임 환경 및 난이도 정의
# ==========================================

# 난이도별 설정 규격 정의 (모든 모드 최소 글자 수는 2글자로 통일)
# ai_max가 0이면 사전 글자 수 상한 제한 없이 전수 조사를 진행함을 의미합니다.
DIFFICULTY_SETTINGS = {
    "1": {"name": "이지 (Easy)", "player_min": 2, "ai_max": 3, "is_nightmare": False},
    "2": {"name": "하드 (Hard)", "player_min": 2, "ai_max": 5, "is_nightmare": False},
    "3": {"name": "익스트림 (Extreme)", "player_min": 2, "ai_max": 0, "is_nightmare": False},
    "32604": {"name": "나이트메어 (Nightmare)", "player_min": 2, "ai_max": 0, "is_nightmare": True},
}

# AI가 선공일 때 무작위로 선택할 시작 글자 후보군
OPENING_SEEDS = ["가", "나", "다", "바", "사", "자", "하", "고", "구", "미", "보", "지"]


# ==========================================
# [핵심 로직] 단어 탐색 및 검증 함수
# ==========================================

def get_all_valid_candidates(last_word, used, config):
    """현재 매치 규칙 하에 제출 가능한 사전 내 모든 유효 단어 풀을 검색 및 정리합니다.
    반환: [(word, pos, definition), ...]"""
    if last_word is None:
        starts = OPENING_SEEDS[:]
    else:
        starts = list(dueum_alternatives(last_word[-1]))

    random.shuffle(starts)
    pool = []

    # ai_max가 0(무제한)일 때 API에 0을 그대로 넘기면 0건 검색이 되므로,
    # 사전에서 사실상 무제한으로 취급되는 99자 상한값으로 치환한다.
    max_len = 99 if config["ai_max"] == 0 else config["ai_max"]

    for s in starts:
        try:
            candidates = search_words_starting_with(s, letter_s=config["player_min"], letter_e=max_len)
        except DictUnavailable as e:
            print(f"[오류] 사전 API 조회 실패: {e}")
            return []

        for word, pos, definition in candidates:
            if word in used:
                continue
            if not is_valid_word_format(word) or len(word) < config["player_min"]:
                continue
            if last_word and not can_link(last_word, word):
                continue
            pool.append((word, pos, definition))

    return pool


def ai_choose_word(last_word, used, config):
    """지정된 난이도 규칙에 맞춰 AI가 낼 단어를 선택해 반환한다. (word, pos, definition) 또는 None"""
    message = "AI가 단어를 고르는 중"
    if config["ai_max"] == 0 and not config["is_nightmare"]:
        message = "AI가 사전 전체를 뒤지는 중 (익스트림)"

    with Spinner(message):
        pool = get_all_valid_candidates(last_word, used, config)

    if not pool:
        return None
    return random.choice(pool)


def validate_human_word(word, last_word, used, config):
    """유저가 입력한 단어를 규칙에 맞춰 검증한다. 반환: (valid, reason, pos, definition)"""
    if not is_valid_word_format(word):
        return False, "한글로만 입력해야 합니다.", None, None

    if len(word) < config["player_min"]:
        return False, f"최소 {config['player_min']}글자 이상이어야 합니다.", None, None

    if word in used:
        return False, "이미 사용된 단어입니다.", None, None

    if last_word and not can_link(last_word, word):
        return False, f"'{last_word[-1]}'(으)로 시작하는 단어가 아닙니다.", None, None

    try:
        with Spinner("사전 확인 중"):
            info = get_word_info(word)
    except DictUnavailable as e:
        return False, f"사전 확인 불가: {e}", None, None

    if not info["exists"]:
        return False, "표준국어대사전에 등재되지 않은 단어입니다.", None, None

    return True, "", info.get("pos"), info.get("definition")


def show_hints(last_word, used, config):
    """유저가 패배했을 때 낼 수 있었던 정답 예시 3개를 안내한다."""
    print("\n[힌트] 아쉬워요! 이런 단어들을 낼 수 있었습니다:")
    with Spinner("힌트를 찾는 중"):
        candidates = get_all_valid_candidates(last_word, used, config)

    if not candidates:
        print("   - (사전에 더 이상 이을 수 있는 단어가 없었습니다!)")
        return

    hints = random.sample(candidates, min(len(candidates), 3))
    for word, pos, definition in hints:
        print(f"   -> {format_word_line(word, pos, definition)}")


# ==========================================
# [메인 루프] 게임 실행 프로세스 컨트롤러
# ==========================================

def main():
    if not os.environ.get("STDICT_API_KEY", "").strip():
        print("[안내] STDICT_API_KEY 환경변수가 설정되어 있지 않습니다.")
        print("       https://stdict.korean.go.kr/openapi/openApiInfo.do 에서 무료 키를 발급받아 등록해 주세요.")
        print("       (Windows cmd) set STDICT_API_KEY=발급받은키")
        sys.exit(1)

    print("=== 끝말잇기 (혼자서 컴퓨터와 대결) ===")
    print("규칙: 표준국어대사전 등재 명사 인정, 중복 단어 불가, 두음법칙 자동 적용")
    print("게임 중 '/quit' 을 입력하면 언제든 종료할 수 있습니다.\n")

    print("난이도를 선택하세요:")
    print("1) 이지 (Easy)     - AI가 쉬운 단어(3글자 이하) 위주로 방어")
    print("2) 하드 (Hard)     - AI가 긴 단어(5글자 이하)까지 사용하여 압박")
    print("3) 익스트림 (Extreme) - [어려움] AI가 길이 제한 없이 사전의 모든 단어를 동원")

    diff_choice = input("선택 (1~3, 기본값 2): ").strip()
    if diff_choice not in DIFFICULTY_SETTINGS:
        diff_choice = "2"

    config = DIFFICULTY_SETTINGS[diff_choice]
    print(f"\n[설정] {config['name']} 모드로 게임을 시작합니다.")

    first = input("먼저 시작하시겠어요? (y = 내가 먼저 / n = AI가 먼저): ").strip().lower()
    my_turn = not first.startswith("n")

    used = set()
    last_word = None
    round_no = 1

    while True:
        print(f"\n--- {round_no}라운드 ---")

        if my_turn:
            # --- [플레이어 턴] ---
            if last_word:
                hint = f" (이전 단어: '{last_word}', '{last_word[-1]}'(으)로 시작, 최소 {config['player_min']}글자)"
            else:
                hint = f" (첫 단어, 자유롭게 입력, 최소 {config['player_min']}글자)"

            word = input(f"당신의 차례입니다{hint}\n> ").strip()

            if word == "/quit":
                print("게임을 종료합니다.")
                return

            valid, reason, pos, definition = validate_human_word(word, last_word, used, config)

            if not valid:
                print(f"[오답] '{word}' -> {reason}")
                print(f"\n*** AI 승리! (당신이 {round_no}라운드에서 탈락) ***")
                show_hints(last_word, used, config)
                return

            used.add(word)
            last_word = word
            print(f"[정답] 당신: {format_word_line(word, pos, definition)}")
        else:
            # --- [AI 컴퓨터 턴] ---
            choice = ai_choose_word(last_word, used, config)

            if choice is None:
                print(f"\n*** 당신의 승리! (AI가 더 이상 이을 단어를 찾지 못했습니다) ***")
                return

            word, pos, definition = choice
            used.add(word)
            last_word = word
            print(f"[AI] {format_word_line(word, pos, definition)}")

        my_turn = not my_turn
        round_no += 1


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n게임을 종료합니다.")
