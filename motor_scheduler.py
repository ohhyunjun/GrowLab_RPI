import threading
import logging
from config import CAMERA_INTERVAL_HOUR

logger = logging.getLogger(__name__)

TOTAL_PORTS = 8  # portIndex 0~7


class MotorScheduler:
    def __init__(self, send_cmd_cb, on_seq_photo_cb=None):
        """
        send_cmd_cb(cmd: str)            → SerialReader.send() 연결
        on_seq_photo_cb(port_index: int) → 포트별 촬영 트리거
        """
        self.send_cmd        = send_cmd_cb
        self.on_seq_photo_cb = on_seq_photo_cb
        self._interval_hour  = CAMERA_INTERVAL_HOUR
        self._stop           = threading.Event()
        self._wake           = threading.Event()   # 주기 변경 시 대기 중단용
        self._photo_index    = 0
        self._lock           = threading.Lock()
        self._seq_running    = False

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info(f"[MotorScheduler] 시작 (주기: {self._interval_hour}시간)")

    def stop(self):
        self._stop.set()
        self._wake.set()   # 대기 중이면 즉시 깨워서 종료

    def set_interval(self, hours: int):
        self._interval_hour = hours
        self._wake.set()   # 현재 wait 즉시 중단 → 루프 재시작
        logger.info(f"[MotorScheduler] 주기 변경: {hours}시간")

    def on_seq_photo(self):
        """
        SerialReader [SEQ] PHOTO 수신 시 호출.
        현재 port_index로 촬영 콜백 실행 후 인덱스 증가.
        """
        with self._lock:
            port_index        = self._photo_index
            self._photo_index += 1

        logger.info(f"[MotorScheduler] 촬영 트리거 portIndex={port_index}")
        if self.on_seq_photo_cb:
            self.on_seq_photo_cb(port_index)

    def on_seq_done(self):
        """SerialReader [SEQ] DONE 수신 시 호출. 시퀀스 완료 플래그 해제."""
        with self._lock:
            self._seq_running = False
        logger.info("[MotorScheduler] 시퀀스 완료")

    def trigger_now(self):
        """즉시 시퀀스 실행 (테스트 / 수동 트리거용)"""
        logger.info("[MotorScheduler] 즉시 실행 요청")
        self._trigger_sequence()

    def _trigger_sequence(self):
        """
        시퀀스 시작.
        이전 시퀀스가 진행 중이면 스킵 (중복 방지).
        """
        with self._lock:
            if self._seq_running:
                logger.warning("[MotorScheduler] 이전 시퀀스 진행 중, 새 시퀀스 스킵")
                return
            self._seq_running = True
            self._photo_index = 0

        logger.info("[MotorScheduler] 시퀀스 시작 ('p' 전송)")
        self.send_cmd("p")

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            # _wake.wait(): set되면 True(주기변경), 타임아웃이면 False(실제 트리거)
            timed_out = not self._wake.wait(self._interval_hour * 3600)

            if timed_out and not self._stop.is_set():
                self._trigger_sequence()
            # set된 경우(주기 변경 or stop)는 트리거 없이 루프 재시작