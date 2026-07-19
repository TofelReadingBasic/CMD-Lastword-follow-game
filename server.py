# -*- coding: utf-8 -*-
"""
끝말잇기 온라인 게임 - 서버

실행:
    export STDICT_API_KEY="발급받은키"
    python3 server.py [포트]   (기본 포트: 5555)

여러 명이 client.py로 접속한 뒤, 아무 플레이어나 채팅창에 /start 를 입력하면
게임이 시작됩니다. 제한시간 안에 규칙에 맞는 단어를 입력하지 못하거나
틀린 단어를 내면 탈락하며, 마지막까지 남은 한 명이 승리합니다.
"""

import socket
import threading
import json
import sys
import time

from hangul_utils import is_valid_word_format, can_link
from dict_check import get_word_info, format_word_line, DictUnavailable
from ui_utils import Spinner

HOST = "0.0.0.0"
DEFAULT_PORT = 5555
TURN_TIME_LIMIT = 15  # 초


class Player:
    def __init__(self, name, conn, addr):
        self.name = name
        self.conn = conn
        self.addr = addr
        self.alive = True
        self.file = conn.makefile("r", encoding="utf-8")

    def send(self, obj):
        try:
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            self.conn.sendall(data)
        except OSError:
            pass


class GameServer:
    def __init__(self, port):
        self.port = port
        self.players = {}          # name -> Player
        self.order = []            # 참가 순서(턴 순서)
        self.lock = threading.RLock()
        self.used_words = set()
        self.last_word = None
        self.state = "WAITING"     # WAITING / PLAYING / FINISHED
        self.turn_index = 0
        self.turn_timer = None
        self.server_sock = None

    # ---------- 브로드캐스트 ----------
    def broadcast(self, obj, exclude=None):
        with self.lock:
            for name, p in self.players.items():
                if name == exclude:
                    continue
                p.send(obj)

    def system_msg(self, msg):
        print(f"[SERVER] {msg}")
        self.broadcast({"type": "system", "msg": msg})

    # ---------- 접속 처리 ----------
    def start(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((HOST, self.port))
        self.server_sock.listen()
        print(f"[SERVER] 끝말잇기 서버 시작됨: 0.0.0.0:{self.port}")
        print(f"[SERVER] 참가자들이 접속하면 아무나 /start 를 입력해 게임을 시작하세요.")
        try:
            while True:
                conn, addr = self.server_sock.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\n[SERVER] 종료합니다.")
        finally:
            self.server_sock.close()

    def handle_client(self, conn, addr):
        f = conn.makefile("r", encoding="utf-8")
        try:
            first_line = f.readline()
            if not first_line:
                conn.close()
                return
            join_msg = json.loads(first_line)
            name = join_msg.get("name", "").strip()
        except (json.JSONDecodeError, ValueError):
            conn.close()
            return

        if not name:
            name = f"플레이어{addr[1]}"

        with self.lock:
            if name in self.players or self.state != "WAITING":
                reason = "이미 사용 중인 닉네임입니다." if name in self.players else "게임이 이미 진행 중입니다."
                conn.sendall((json.dumps({"type": "join_rejected", "reason": reason}, ensure_ascii=False) + "\n").encode())
                conn.close()
                return
            player = Player(name, conn, addr)
            self.players[name] = player
            self.order.append(name)

        player.send({"type": "welcome", "msg": f"{name}님, 서버에 접속했습니다. 다른 참가자를 기다려 주세요."})
        self.system_msg(f"{name}님이 입장했습니다. (현재 {len(self.players)}명)")
        self.broadcast_player_list()

        try:
            while True:
                line = player.file.readline()
                if not line:
                    break
                self.handle_message(player, line)
        except (ConnectionResetError, OSError):
            pass
        finally:
            self.remove_player(name)

    def broadcast_player_list(self):
        self.broadcast({"type": "players", "list": list(self.players.keys())})

    def remove_player(self, name):
        with self.lock:
            if name not in self.players:
                return
            del self.players[name]
            if name in self.order:
                self.order.remove(name)
        self.system_msg(f"{name}님이 퇴장했습니다.")
        self.broadcast_player_list()
        if self.state == "PLAYING":
            self.check_game_over_or_continue(disconnected_name=name)

    # ---------- 메시지 처리 ----------
    def handle_message(self, player, line):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        mtype = msg.get("type")

        if mtype == "chat":
            text = msg.get("text", "")
            if text.strip() == "/start":
                self.try_start_game()
            else:
                self.broadcast({"type": "chat", "from": player.name, "text": text})
        elif mtype == "word":
            self.handle_word(player, msg.get("word", "").strip())

    def try_start_game(self):
        with self.lock:
            if self.state != "WAITING":
                return
            if len(self.players) < 2:
                self.system_msg("최소 2명 이상이어야 게임을 시작할 수 있습니다.")
                return
            self.state = "PLAYING"
            self.used_words.clear()
            self.last_word = None
            self.turn_index = 0
        self.system_msg("게임을 시작합니다! 첫 참가자부터 순서대로 진행됩니다.")
        self.broadcast({"type": "game_start", "order": self.order[:]})
        self.next_turn()

    def current_player_name(self):
        if not self.order:
            return None
        return self.order[self.turn_index % len(self.order)]

    def next_turn(self):
        with self.lock:
            if self.state != "PLAYING":
                return
            if not self.order:
                return
            self.turn_index %= len(self.order)
            name = self.order[self.turn_index]

        self.broadcast({
            "type": "turn",
            "player": name,
            "last_word": self.last_word,
            "time_limit": TURN_TIME_LIMIT,
        })
        self.system_msg(f"{name}님 차례입니다. ({TURN_TIME_LIMIT}초 제한)")

        if self.turn_timer:
            self.turn_timer.cancel()
        self.turn_timer = threading.Timer(TURN_TIME_LIMIT, self.handle_timeout, args=(name,))
        self.turn_timer.daemon = True
        self.turn_timer.start()

    def handle_timeout(self, name):
        with self.lock:
            if self.state != "PLAYING" or self.current_player_name() != name:
                return
        self.system_msg(f"{name}님이 시간 초과로 탈락했습니다.")
        self.eliminate(name)

    def eliminate(self, name):
        with self.lock:
            if name in self.order:
                self.order.remove(name)
            if name in self.players:
                self.players[name].alive = False
        self.broadcast({"type": "eliminated", "player": name})
        self.check_game_over_or_continue()

    def check_game_over_or_continue(self, disconnected_name=None):
        with self.lock:
            if self.state != "PLAYING":
                return
            if len(self.order) <= 1:
                winner = self.order[0] if self.order else None
                self.state = "FINISHED"
                if self.turn_timer:
                    self.turn_timer.cancel()
                self.broadcast({"type": "game_over", "winner": winner})
                self.system_msg(f"게임 종료! 승자: {winner if winner else '없음'}")
                self.reset_for_rematch()
                return
            # turn_index가 범위를 벗어나지 않도록 보정 (본인이 탈락한 경우 다음 사람으로)
            self.turn_index %= len(self.order)
        self.next_turn()

    def reset_for_rematch(self):
        # 잠시 후 대기 상태로 돌아가 재시작 가능하게 함
        def _reset():
            time.sleep(3)
            with self.lock:
                self.state = "WAITING"
                self.order = list(self.players.keys())
                self.used_words.clear()
                self.last_word = None
                self.turn_index = 0
            self.system_msg("대기 상태로 돌아왔습니다. /start 입력 시 재시작할 수 있습니다.")
            self.broadcast_player_list()
        threading.Thread(target=_reset, daemon=True).start()

    def handle_word(self, player, word):
        with self.lock:
            if self.state != "PLAYING":
                player.send({"type": "result", "valid": False, "reason": "게임이 진행 중이 아닙니다.", "word": word})
                return
            if self.current_player_name() != player.name:
                player.send({"type": "result", "valid": False, "reason": "당신의 차례가 아닙니다.", "word": word})
                return

        valid, reason, pos, definition = self.validate_word(word)

        if not valid:
            self.broadcast({"type": "result", "valid": False, "reason": reason, "word": word, "player": player.name})
            self.system_msg(f"{player.name}님의 '{word}' 은(는) 무효 처리되었습니다: {reason} -> 탈락")
            self.eliminate(player.name)
            return

        with self.lock:
            self.used_words.add(word)
            self.last_word = word
            self.turn_index = (self.turn_index + 1) % max(len(self.order), 1)

        if self.turn_timer:
            self.turn_timer.cancel()

        self.broadcast({"type": "result", "valid": True, "word": word, "player": player.name,
                         "pos": pos, "definition": definition})
        self.system_msg(f"{player.name}: {format_word_line(word, pos, definition)}")
        self.next_turn()

    def validate_word(self, word):
        if not is_valid_word_format(word):
            return False, "한글 2글자 이상으로만 입력해야 합니다.", None, None
        if word in self.used_words:
            return False, "이미 사용된 단어입니다.", None, None
        if self.last_word and not can_link(self.last_word, word):
            return False, f"'{self.last_word[-1]}'(으)로 시작하는 단어가 아닙니다.", None, None
        try:
            with Spinner("사전 확인 중"):
                info = get_word_info(word)
        except DictUnavailable as e:
            return False, f"사전 확인 불가: {e}", None, None
        if not info["exists"]:
            return False, "표준국어대사전에 등재되지 않은 단어입니다.", None, None
        return True, "", info.get("pos"), info.get("definition")


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print("포트 번호는 숫자여야 합니다.")
            sys.exit(1)
    server = GameServer(port)
    server.start()


if __name__ == "__main__":
    main()
