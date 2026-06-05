import threading
import logging
from datetime import datetime
from config import LED_ON_HOUR, LED_OFF_HOUR

logger = logging.getLogger(__name__)

class LedScheduler:
    def __init__(self, send_cmd_cb):
        """send_cmd_cb(cmd: str) → SerialReader.send() 연결"""
        self.send_cmd  = send_cmd_cb
        self._mode     = "auto"
        self._on_hour  = LED_ON_HOUR
        self._off_hour = LED_OFF_HOUR
        self._stop     = threading.Event()

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info(f"[LedScheduler] 시작 (모드: {self._mode}, ON: {self._on_hour}시, OFF: {self._off_hour}시)")

    def stop(self):
        self._stop.set()

    def handle_command(self, cmd: str):
        """
        서버 MqttPublisher command 토픽 페이로드 처리
          "O"                 → 수동 LED ON
          "o"                 → 수동 LED OFF
          "SCHED:06:00-22:00" → 자동 모드, 스케줄 설정
        """
        if cmd == "O":
            self._mode = "manual"
            self.send_cmd("O")
            logger.info("[LedScheduler] 수동 LED ON")

        elif cmd == "o":
            self._mode = "manual"
            self.send_cmd("o")
            logger.info("[LedScheduler] 수동 LED OFF")

        elif cmd.startswith("SCHED:"):
            try:
                times           = cmd.replace("SCHED:", "")
                on_str, off_str = times.split("-")
                self._on_hour   = int(on_str.split(":")[0])
                self._off_hour  = int(off_str.split(":")[0])
                self._mode      = "auto"
                logger.info(f"[LedScheduler] 자동 모드: ON={self._on_hour}시 OFF={self._off_hour}시")
            except Exception as e:
                logger.error(f"[LedScheduler] SCHED 파싱 오류: {e} / cmd: {cmd}")
        else:
            logger.warning(f"[LedScheduler] 알 수 없는 명령: {cmd}")

    def _run(self):
        last_triggered_hour = -1
        while not self._stop.is_set():
            if self._mode == "auto":
                now  = datetime.now()
                hour = now.hour
                if hour != last_triggered_hour:
                    if hour == self._on_hour:
                        last_triggered_hour = hour
                        self.send_cmd("O")
                        logger.info("[LedScheduler] 자동 LED ON")
                    elif hour == self._off_hour:
                        last_triggered_hour = hour
                        self.send_cmd("o")
                        logger.info("[LedScheduler] 자동 LED OFF")
            self._stop.wait(30)
