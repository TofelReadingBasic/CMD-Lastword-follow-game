# -*- coding: utf-8 -*-
"""
터미널(Windows cmd 포함)에서 동작하는 간단한 로딩 스피너 애니메이션.
ANSI 이스케이프 없이 캐리지 리턴(\\r)만 사용하므로 cmd.exe에서도 잘 보인다.
"""

import sys
import threading
import time


class Spinner:
    """
    사용법:
        sp = Spinner("사전 확인 중")
        sp.start()
        무거운_작업()
        sp.stop()

    또는:
        with Spinner("AI가 단어를 고르는 중"):
            무거운_작업()
    """

    FRAMES = ["|", "/", "-", "\\"]

    def __init__(self, message: str = "처리 중", interval: float = 0.12):
        self.message = message
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        i = 0
        while not self._stop_event.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r{self.message} {frame}")
            sys.stdout.flush()
            i += 1
            time.sleep(self.interval)
        # 줄 지우기
        clear_len = len(self.message) + 2
        sys.stdout.write("\r" + " " * clear_len + "\r")
        sys.stdout.flush()

    def stop(self):
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


if __name__ == "__main__":
    with Spinner("테스트 로딩 중"):
        time.sleep(2)
    print("완료!")
