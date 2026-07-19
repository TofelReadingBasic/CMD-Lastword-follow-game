# -*- coding: utf-8 -*-
"""
끝말잇기 - 완전 P2P (중앙 서버 없음) 버전

모든 참가자가 동일한 이 스크립트를 실행합니다. 별도의 서버 프로세스가 없고,
LAN 안에서 UDP 브로드캐스트로 서로를 자동으로 찾은 뒤, 참가자들끼리 TCP로
서로 직접(mesh) 연결되어 게임을 진행합니다.

실행:
    export STDICT_API_KEY="발급받은키"
    python3 peer.py --name 철수

옵션:
    --name          닉네임 (미입력 시 실행 중 입력받음)
    --tcp-port      내가 다른 피어의 접속을 받을 포트 (기본: 자동 랜덤)
    --discovery-port UDP 탐색 포트 (기본: 55201, 모든 참가자가 동일해야 함)

사용법:
    1. 참가자 전원이 각자 컴퓨터에서 위 명령을 실행합니다 (같은 LAN이어야 함).
    2. /list 로 자동 발견된 참가자 목록을 확인합니다.
    3. 아무나 /start 를 입력하면 그 시점에 발견된 참가자 전원으로 게임이 시작됩니다.
    4. 본인 차례가 되면 제한시간 안에 단어를 입력합니다.

주의: 신뢰 기반 캐주얼 게임입니다. 각 참가자는 '자신이 낸 단어'에 대해서만
사전 검증을 직접 수행하고 그 결과를 모두에게 알리는 방식이라, 친구끼리
가볍게 즐기는 용도에 적합합니다(치팅 방지용 중앙 심판은 없습니다).
"""

import argparse
import json
import socket
import sys
import threading
import time
import uuid

from hangul_utils import is_valid_word_format, can_link
from dict_check import get_word_info, format_word_line, DictUnavailable
from ui_utils import Spinner

DISCOVERY_PORT_DEFAULT = 55201
TURN_TIME_LIMIT = 15
PRESENCE_INTERVAL = 1.5


class Peer:
    def __init__(self, name, tcp_port, discovery_port):
        self.name = name
        self.id = uuid.uuid4().hex
        self.tcp_port = tcp_port
        self.discovery_port = discovery_port

        self.lock = threading.RLock()
        self.discovered = {}       # name -> {"ip":..., "port":..., "last_seen":...}
        self.connections = {}      # name -> {"sock":..., "file":...}
        self.addr_book = {}        # name -> (ip, port)  (게임 시작 시 확정된 주소록)

        self.mode = "LOBBY"        # LOBBY / PLAYING / FINISHED
        self.members = []          # 턴 순서 (이름 리스트, 전원)
        self.alive = []            # 현재 생존자만 (턴 순서 유지)
        self.used_words = set()
        self.last_word = None
        self.turn_index = 0
        self.turn_timer = None
        self.turn_token = 0        # 타이머 유효성 확인용

        self.tcp_sock = None

    # ---------------- 출력 ----------------
    def log(self, msg):
        print(msg)

    # ---------------- UDP 탐색 ----------------
    def start_discovery(self):
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        udp_sock.bind(("", self.discovery_port))
        self.udp_sock = udp_sock

        threading.Thread(target=self._udp_listen_loop, daemon=True).start()
        threading.Thread(target=self._udp_broadcast_loop, daemon=True).start()

    def _udp_listen_loop(self):
        while True:
            try:
                data, addr = self.udp_sock.recvfrom(4096)
            except OSError:
                return
            try:
                msg = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            if msg.get("id") == self.id:
                continue  # 자기 자신이 보낸 패킷 무시

            mtype = msg.get("type")
            if mtype == "presence":
                name = msg.get("name")
                port = msg.get("tcp_port")
                with self.lock:
                    is_new = name not in self.discovered
                    self.discovered[name] = {"ip": addr[0], "port": port, "last_seen": time.time()}
                if is_new:
                    self.log(f"[탐색] '{name}' 발견됨 ({addr[0]}:{port})")
            elif mtype == "game_start":
                self._on_game_start_broadcast(msg)

    def _udp_broadcast_loop(self):
        bsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bsock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = {
            "type": "presence",
            "id": self.id,
            "name": self.name,
            "tcp_port": self.tcp_port,
        }
        data = (json.dumps(payload, ensure_ascii=False)).encode("utf-8")
        while True:
            try:
                bsock.sendto(data, ("255.255.255.255", self.discovery_port))
            except OSError:
                pass
            time.sleep(PRESENCE_INTERVAL)

    def broadcast_udp_raw(self, payload, repeats=5, interval=0.2):
        bsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bsock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for _ in range(repeats):
            try:
                bsock.sendto(data, ("255.255.255.255", self.discovery_port))
            except OSError:
                pass
            time.sleep(interval)

    # ---------------- TCP 리스너 (다른 피어의 접속을 받음) ----------------
    def start_tcp_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", self.tcp_port))
        s.listen()
        self.tcp_sock = s
        actual_port = s.getsockname()[1]
        self.tcp_port = actual_port
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                conn, addr = self.tcp_sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle_incoming, args=(conn, addr), daemon=True).start()

    def _handle_incoming(self, conn, addr):
        f = conn.makefile("r", encoding="utf-8")
        first = f.readline()
        if not first:
            conn.close()
            return
        try:
            hello = json.loads(first)
        except json.JSONDecodeError:
            conn.close()
            return
        peer_name = hello.get("name")
        if not peer_name:
            conn.close()
            return
        with self.lock:
            self.connections[peer_name] = {"sock": conn, "file": f}
        self.log(f"[연결] '{peer_name}'와(과) 직접 연결되었습니다.")
        self._reader_loop(peer_name, f)

    def _connect_out(self, peer_name, ip, port):
        with self.lock:
            if peer_name in self.connections:
                return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((ip, port))
            s.settimeout(None)
        except OSError as e:
            self.log(f"[오류] '{peer_name}'({ip}:{port}) 연결 실패: {e}")
            return
        f = s.makefile("r", encoding="utf-8")
        hello = {"name": self.name}
        s.sendall((json.dumps(hello, ensure_ascii=False) + "\n").encode("utf-8"))
        with self.lock:
            self.connections[peer_name] = {"sock": s, "file": f}
        self.log(f"[연결] '{peer_name}'에게 직접 연결했습니다.")
        threading.Thread(target=self._reader_loop, args=(peer_name, f), daemon=True).start()

    def _reader_loop(self, peer_name, f):
        while True:
            line = f.readline()
            if not line:
                self.log(f"[연결 종료] '{peer_name}' 연결이 끊어졌습니다.")
                with self.lock:
                    self.connections.pop(peer_name, None)
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle_peer_message(peer_name, msg)

    def send_to(self, peer_name, obj):
        with self.lock:
            entry = self.connections.get(peer_name)
        if not entry:
            return
        try:
            entry["sock"].sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def broadcast_mesh(self, obj):
        with self.lock:
            names = list(self.connections.keys())
        for n in names:
            self.send_to(n, obj)

    # ---------------- 게임 시작 ----------------
    def cmd_start(self):
        with self.lock:
            if self.mode == "PLAYING":
                self.log("[안내] 이미 게임이 진행 중입니다.")
                return
            snapshot = dict(self.discovered)
        if not snapshot:
            self.log("[안내] 아직 발견된 다른 참가자가 없습니다. 잠시 기다려 주세요.")
            return

        members_info = [{"name": self.name, "ip": "self", "port": self.tcp_port}]
        for name, info in snapshot.items():
            members_info.append({"name": name, "ip": info["ip"], "port": info["port"]})
        members_info.sort(key=lambda m: m["name"])

        payload = {
            "type": "game_start",
            "id": self.id,
            "members": members_info,
        }
        self.log(f"[시작] 다음 {len(members_info)}명으로 게임을 시작합니다: "
                  f"{', '.join(m['name'] for m in members_info)}")
        # 나 자신도 동일한 members_info로 즉시 시작 처리
        self._apply_game_start(members_info)
        # 다른 참가자들에게도 알림 (UDP 브로드캐스트, 신뢰성 위해 여러 번 전송)
        threading.Thread(target=self.broadcast_udp_raw, args=(payload,), daemon=True).start()

    def _on_game_start_broadcast(self, msg):
        members_info = msg.get("members", [])
        self._apply_game_start(members_info)

    def _apply_game_start(self, members_info):
        with self.lock:
            if self.mode == "PLAYING":
                return  # 이미 시작됨 (중복 브로드캐스트 무시)
            names = [m["name"] for m in members_info]
            self.members = names
            self.alive = names[:]
            self.used_words = set()
            self.last_word = None
            self.turn_index = 0
            self.mode = "PLAYING"

        # 주소록 저장 + 나보다 이름이 사전순으로 작은 상대에게만 내가 먼저 연결
        for m in members_info:
            if m["name"] == self.name:
                continue
            ip = m["ip"]
            port = m["port"]
            if ip == "self":
                continue
            self.addr_book[m["name"]] = (ip, port)
            if self.name < m["name"]:
                threading.Thread(target=self._connect_out, args=(m["name"], ip, port), daemon=True).start()

        self.log(f"\n=== 게임 시작! 참가자: {', '.join(self.members)} ===\n")
        # 연결이 어느 정도 맺어질 시간을 준 뒤 턴 진행 (연결 자체는 비동기)
        threading.Timer(1.0, self._begin_turns).start()

    def _begin_turns(self):
        with self.lock:
            if self.mode != "PLAYING":
                return
        self._advance_and_announce_turn()

    # ---------------- 턴 진행 ----------------
    def _current_player(self):
        with self.lock:
            if not self.alive:
                return None
            self.turn_index %= len(self.alive)
            return self.alive[self.turn_index]

    def _advance_and_announce_turn(self):
        with self.lock:
            if self.mode != "PLAYING":
                return
            if len(self.alive) <= 1:
                self._finish_game()
                return
            player = self._current_player()
            self.turn_token += 1
            token = self.turn_token

        is_me = (player == self.name)
        if is_me:
            hint = f" (이전 단어: '{self.last_word}', '{self.last_word[-1]}'(으)로 시작)" if self.last_word else " (첫 단어, 자유롭게 입력)"
            self.log(f"\n>>> 당신의 차례입니다!{hint} 제한시간 {TURN_TIME_LIMIT}초")
        else:
            self.log(f"\n[{player}]님의 차례입니다. (제한시간 {TURN_TIME_LIMIT}초)")

        if self.turn_timer:
            self.turn_timer.cancel()
        self.turn_timer = threading.Timer(TURN_TIME_LIMIT, self._on_timeout, args=(player, token))
        self.turn_timer.daemon = True
        self.turn_timer.start()

    def _on_timeout(self, player, token):
        with self.lock:
            if self.mode != "PLAYING" or self.turn_token != token:
                return
        self.log(f"[시간초과] '{player}'님이 시간 안에 답하지 못했습니다.")
        self._eliminate(player, broadcast=True)

    def _eliminate(self, player, broadcast):
        with self.lock:
            if player not in self.alive:
                return
            self.alive.remove(player)
            if self.turn_index >= len(self.alive) and self.alive:
                self.turn_index %= len(self.alive)
        self.log(f"[탈락] '{player}'님이 탈락했습니다. (남은 인원: {len(self.alive)})")
        if broadcast:
            self.broadcast_mesh({"type": "eliminate", "player": player})
        with self.lock:
            if len(self.alive) <= 1:
                self._finish_game()
                return
        self._advance_and_announce_turn()

    def _finish_game(self):
        with self.lock:
            if self.mode == "FINISHED":
                return
            self.mode = "FINISHED"
            winner = self.alive[0] if self.alive else None
            if self.turn_timer:
                self.turn_timer.cancel()
        self.log(f"\n★★★ 게임 종료! 우승자: {winner if winner else '없음'} ★★★")
        self.log("[안내] /start 를 다시 입력하면 재시작할 수 있습니다.\n")

    # ---------------- 단어 제출 ----------------
    def submit_word(self, word):
        with self.lock:
            if self.mode != "PLAYING":
                self.log("[안내] 게임이 진행 중이 아닙니다.")
                return
            if self._current_player() != self.name:
                self.log("[안내] 당신의 차례가 아닙니다.")
                return
            last_word = self.last_word
            used = set(self.used_words)

        valid, reason, pos, definition = self._validate(word, last_word, used)
        result = {
            "type": "result", "player": self.name, "word": word,
            "valid": valid, "reason": reason, "pos": pos, "definition": definition,
        }
        self._apply_result(result)          # 나 자신도 동일하게 처리
        self.broadcast_mesh(result)         # 다른 모든 피어에게 결과 전파

    def _validate(self, word, last_word, used):
        if not is_valid_word_format(word):
            return False, "한글 2글자 이상으로만 입력해야 합니다.", None, None
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

    def _apply_result(self, result):
        player = result["player"]
        word = result["word"]
        valid = result["valid"]
        with self.lock:
            if self.mode != "PLAYING":
                return
            if self._current_player() != player:
                return  # 이미 처리된(순서가 지난) 결과는 무시

            if valid:
                self.used_words.add(word)
                self.last_word = word
                self.turn_index = (self.turn_index + 1) % max(len(self.alive), 1)

        if valid:
            self.log(f"[정답] {player}: {format_word_line(word, result.get('pos'), result.get('definition'))}")
            if self.turn_timer:
                self.turn_timer.cancel()
            self._advance_and_announce_turn()
        else:
            self.log(f"[오답] {player}: '{word}' -> {result.get('reason')}")
            if self.turn_timer:
                self.turn_timer.cancel()
            self._eliminate(player, broadcast=False)

    # ---------------- 피어 메시지 처리 ----------------
    def _handle_peer_message(self, from_name, msg):
        mtype = msg.get("type")
        if mtype == "result":
            self._apply_result(msg)
        elif mtype == "eliminate":
            self._eliminate(msg["player"], broadcast=False)
        elif mtype == "chat":
            self.log(f"[{from_name}] {msg.get('text','')}")

    # ---------------- 명령 처리 ----------------
    def handle_user_input(self, text):
        text = text.strip()
        if not text:
            return
        if text == "/list":
            with self.lock:
                names = list(self.discovered.keys())
            self.log(f"[발견된 참가자] {', '.join(names) if names else '(아직 없음)'}")
            return
        if text == "/start":
            self.cmd_start()
            return
        if self.mode == "PLAYING" and self._current_player() == self.name:
            self.submit_word(text)
            return
        # 그 외에는 채팅으로 전체 브로드캐스트
        self.broadcast_mesh({"type": "chat", "text": text})
        self.log(f"[{self.name}] {text}")


def main():
    parser = argparse.ArgumentParser(description="완전 P2P 끝말잇기")
    parser.add_argument("--name", help="닉네임")
    parser.add_argument("--tcp-port", type=int, default=0, help="내 TCP 수신 포트 (0=자동)")
    parser.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT_DEFAULT, help="UDP 탐색 포트")
    args = parser.parse_args()

    name = args.name or input("닉네임을 입력하세요: ").strip()
    if not name:
        name = f"익명{uuid.uuid4().hex[:4]}"

    peer = Peer(name, args.tcp_port, args.discovery_port)
    peer.start_tcp_listener()
    peer.start_discovery()

    print(f"[시작됨] '{name}' 로 참가 준비 완료. (내 TCP 포트: {peer.tcp_port})")
    print("같은 LAN의 다른 참가자를 자동으로 찾는 중입니다... /list 로 확인, /start 로 게임 시작.")

    try:
        while True:
            line = input()
            peer.handle_user_input(line)
    except (KeyboardInterrupt, EOFError):
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
