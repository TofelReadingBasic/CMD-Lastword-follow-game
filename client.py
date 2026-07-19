# -*- coding: utf-8 -*-
"""
끝말잇기 온라인 게임 - 클라이언트

실행:
    python3 client.py <서버주소> [포트]   (기본 포트: 5555)

접속 후 닉네임을 입력하면 대기실에 들어갑니다.
아무 참가자나 채팅창에 /start 를 입력하면 게임이 시작됩니다.
자신의 차례가 되면 제한시간 안에 단어를 입력하세요.
"""

import socket
import threading
import json
import sys

DEFAULT_PORT = 5555

my_turn = threading.Event()
current_last_word = [None]


def receiver_loop(sock):
    f = sock.makefile("r", encoding="utf-8")
    my_name = client_state["name"]
    while True:
        line = f.readline()
        if not line:
            print("\n[알림] 서버와의 연결이 끊어졌습니다.")
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle_server_message(msg, my_name)


def handle_server_message(msg, my_name):
    mtype = msg.get("type")

    if mtype == "welcome":
        print(f"[서버] {msg['msg']}")
    elif mtype == "join_rejected":
        print(f"[입장 거부] {msg['reason']}")
        sys.exit(1)
    elif mtype == "system":
        print(f"[시스템] {msg['msg']}")
    elif mtype == "players":
        names = ", ".join(msg["list"])
        print(f"[참가자 목록] {names}")
    elif mtype == "chat":
        print(f"[{msg['from']}] {msg['text']}")
    elif mtype == "game_start":
        order = " -> ".join(msg["order"])
        print(f"\n=== 게임 시작! 순서: {order} ===\n")
    elif mtype == "turn":
        current_last_word[0] = msg.get("last_word")
        if msg["player"] == my_name:
            hint = f" (이전 단어: {current_last_word[0]}, '{current_last_word[0][-1]}'(으)로 시작)" if current_last_word[0] else " (첫 단어이므로 자유롭게 입력)"
            print(f"\n>>> 당신의 차례입니다!{hint} 제한시간 {msg['time_limit']}초")
            my_turn.set()
        else:
            print(f"\n[{msg['player']}]님의 차례입니다. (제한시간 {msg['time_limit']}초)")
            my_turn.clear()
    elif mtype == "result":
        if msg.get("valid"):
            word = msg["word"]
            pos = msg.get("pos") or "품사미상"
            definition = msg.get("definition")
            if definition:
                short = definition[:40] + ("…" if len(definition) > 40 else "")
            else:
                short = "뜻풀이 없음"
            print(f"[정답] {msg.get('player')}: {word} | {pos} | {short}")
        else:
            who = msg.get("player", "")
            print(f"[오답] {who} '{msg['word']}' -> {msg['reason']}")
        my_turn.clear()
    elif mtype == "eliminated":
        print(f"[탈락] {msg['player']}님이 탈락했습니다.")
    elif mtype == "game_over":
        winner = msg.get("winner")
        print(f"\n★★★ 게임 종료! 우승자: {winner} ★★★\n")
    else:
        pass


def send_json(sock, obj):
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    sock.sendall(data)


client_state = {"name": None}


def main():
    if len(sys.argv) < 2:
        print("사용법: python3 client.py <서버주소> [포트]")
        sys.exit(1)

    server_addr = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT

    name = input("닉네임을 입력하세요: ").strip()
    if not name:
        name = "익명"
    client_state["name"] = name

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((server_addr, port))
    except OSError as e:
        print(f"[오류] 서버에 접속할 수 없습니다: {e}")
        sys.exit(1)

    send_json(sock, {"name": name})

    t = threading.Thread(target=receiver_loop, args=(sock,), daemon=True)
    t.start()

    print("접속 완료! 채팅을 하려면 그냥 입력하고, 게임을 시작하려면 /start 를 입력하세요.")
    print("자신의 차례가 되면 단어만 입력해서 보내면 됩니다.")

    try:
        while True:
            text = input()
            if not text:
                continue
            if my_turn.is_set():
                send_json(sock, {"type": "word", "word": text.strip()})
                my_turn.clear()
            else:
                send_json(sock, {"type": "chat", "text": text})
    except (KeyboardInterrupt, EOFError):
        print("\n연결을 종료합니다.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
