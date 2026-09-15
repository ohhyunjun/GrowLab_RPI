import threading
import time
import logging
from config import CAMERA_INTERVAL_HOUR

logger = logging.getLogger(__name__)

TOTAL_PORTS = 8  # portIndex 0~7

# 8포트 × (ACK 10초 + 처리 180초) 최악값을 넘는 여유값.
# 이 시간을 넘겨도 [SEQ] DONE이 안 오면 아두이노가 리셋된 것으로 보고
# _seq_running을 강제로 푼다. 안 그러면 이후 촬영이 영구히 스킵된다.
SEQ_MAX_DURATION_SEC = 40 * 60


class MotorScheduler:
    def __init__(self, send_cmd_cb, send_raw_cb=None, on_seq_photo_cb=None,
                 state_store=None):
        """
        send_cmd_cb(cmd: str) -> bool    → SerialReader.send() (줄바꿈 자동 추가)
        send_raw_cb(char: str) -> bool   → SerialReader.send_raw() (단일 바이트)
        on_seq_photo_cb(port_index: int) → 포트별 촬영 트리거
        state_store                      → StateStore (촬영주기 영속, 선택)
        """
        self.send_cmd        = send_cmd_cb
        self.send_raw        = send_raw_cb
        self.on_seq_photo_cb = on_seq_photo_cb
        self.state           = state_store
        # 저장된 촬영주기가 있으면 복원, 없으면 기본값
        if state_store is not None:
            self._interval_hour = state_store.get("photo_interval_hour") or CAMERA_INTERVAL_HOUR
        else:
            self._interval_hour = CAMERA_INTERVAL_HOUR
        self._stop           = threading.Event()
        self._wake           = threading.Event()
        self._lock           = threading.Lock()
        self._seq_running    = False
        self._seq_started_at = 0.0

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info(f"[MotorScheduler] 시작 (주기: {self._interval_hour}시간)")

    def stop(self):
        self._stop.set()
        self._wake.set()

    def set_interval(self, hours: int):
        self._interval_hour = hours
        if self.state is not None:
            self.state.update(photo_interval_hour=hours)
        self._wake.set()
        logger.info(f"[MotorScheduler] 주기 변경: {hours}시간")

    def on_photo_request(self, port_index: int):
        """
        Arduino [PHOTO] PORT:X 수신 시 호출.
        SerialReader의 읽기 스레드에서 직접 호출되므로, 여기서 촬영/YOLO/업로드를
        동기 실행하면 그동안 시리얼 수신이 멈춰 Arduino 쪽 USB 송신 버퍼가 밀려
        타임아웃이 난다. 별도 워커 스레드로 넘겨 즉시 리턴한다.
        """
        logger.info(f"[MotorScheduler] 촬영 요청 PORT:{port_index}")

        # ACK를 먼저 보낸다. 아두이노는 ACK 전까지 짧은 제한(10초)을 적용하고,
        # ACK 이후에는 긴 처리 제한(180초)으로 전환한다.
        # 이 호출은 시리얼 읽기 스레드에서 즉시 실행되므로 지연이 없다.
        if not self.send_cmd(f"ACK:{port_index}"):
            logger.error(f"[MotorScheduler] ACK:{port_index} 전송 실패 — 촬영 스킵")
            return

        t = threading.Thread(
            target=self._process_photo,
            args=(port_index,),
            daemon=True,
        )
        t.start()

    def _process_photo(self, port_index: int):
        """
        1. ESP32-CAM 촬영 + YOLO 실행 + 서버 전송
        2. Arduino에 NEXT:X 전송 → 다음 포트 이동

        촬영 실패(하드웨어 문제)는 ERROR:X → 아두이노가 안전 복귀한다.
        업로드 실패(서버 문제)는 on_seq_photo_cb 안에서 로깅만 하고 예외를
        올리지 않으므로 시퀀스는 계속 진행된다. 서버 장애 때문에 기구를
        원점으로 되돌릴 이유가 없기 때문이다.
        """
        success = False
        if self.on_seq_photo_cb:
            try:
                self.on_seq_photo_cb(port_index)
                success = True
            except Exception as e:
                logger.error(f"[MotorScheduler] 촬영 실패 PORT:{port_index}: {e}")

        if success:
            self.send_cmd(f"NEXT:{port_index}")
            logger.info(f"[MotorScheduler] NEXT:{port_index} 전송")
        else:
            self.send_cmd(f"ERROR:{port_index}")
            logger.error(f"[MotorScheduler] ERROR:{port_index} 전송")

    def on_seq_done(self):
        """SerialReader [SEQ] DONE 수신 시 호출."""
        with self._lock:
            self._seq_running = False
        logger.info("[MotorScheduler] 시퀀스 완료")

    def on_error(self, port_index: int, msg: str):
        """Arduino [ERROR] 수신 시."""
        # 아두이노는 ERROR 이후 안전 복귀만 하고 [SEQ] DONE을 보내지 않으므로
        # 여기서 시퀀스 상태를 풀어야 다음 주기가 정상 동작한다.
        with self._lock:
            self._seq_running = False
        logger.error(f"[MotorScheduler] 아두이노 에러 PORT:{port_index}: {msg}")

    def trigger_now(self):
        """즉시 시퀀스 실행 (테스트 / 수동 트리거용)"""
        logger.info("[MotorScheduler] 즉시 실행 요청")
        self._trigger_sequence()

    def _trigger_sequence(self):
        with self._lock:
            if self._seq_running:
                elapsed = time.time() - self._seq_started_at
                if elapsed < SEQ_MAX_DURATION_SEC:
                    logger.warning(
                        f"[MotorScheduler] 이전 시퀀스 진행 중({int(elapsed)}초), 새 시퀀스 스킵"
                    )
                    return
                # [SEQ] DONE도 [ERROR]도 못 받은 상태. 아두이노 리셋으로 보고 강제 해제.
                logger.error(
                    f"[MotorScheduler] 이전 시퀀스가 {int(elapsed)}초간 미완료 — 상태 강제 초기화"
                )

            self._seq_running    = True
            self._seq_started_at = time.time()

        logger.info("[MotorScheduler] 시퀀스 시작 ('p' 전송)")

        # 아두이노 readSerialCommands()는 'p'를 줄바꿈 없는 단일 문자로 인식한다.
        sent = self.send_raw("p") if self.send_raw else self.send_cmd("p")

        if not sent:
            # 포트가 닫혀 있으면 아두이노는 시작 명령을 못 받는데 RPi만
            # 진행 중이라고 믿게 된다. 즉시 되돌린다.
            with self._lock:
                self._seq_running = False
            logger.error("[MotorScheduler] 'p' 전송 실패 — 시퀀스 취소, 다음 주기에 재시도")

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            timed_out = not self._wake.wait(self._interval_hour * 3600)

            if timed_out and not self._stop.is_set():
                self._trigger_sequence()