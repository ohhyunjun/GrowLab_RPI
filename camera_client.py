import threading
import time
import logging
import serial
from config import ESP32CAM_PORT, ESP32CAM_BAUD

logger = logging.getLogger(__name__)

HEADER_TIMEOUT_SEC = 15
READ_TIMEOUT_SEC   = 15

# 포트 오픈 직후 대기. 자동 리셋을 막았으면 실제로는 불필요하지만,
# 리셋이 걸리는 보드에서도 첫 캡처가 성공하도록 여유를 둔다.
BOOT_WAIT_SEC = 2.0

# VGA JPEG는 보통 15~40KB. 이 값을 넘으면 헤더 파싱이 깨진 것으로 본다.
MAX_IMAGE_SIZE = 512 * 1024

CAPTURE_RETRY = 2


class _CameraSerial:
    """
    ESP32-CAM UART 클라이언트.

    포트를 캡처마다 여닫지 않고 계속 열어둔다. USB-TTL 보드에 자동 리셋 회로가
    있으면 포트를 열 때 DTR이 토글되면서 ESP32가 재부팅되기 때문이다.

    MotorScheduler가 포트별 워커 스레드에서 호출하므로 _lock으로 직렬화한다.
    """

    def __init__(self):
        self._ser = None
        self._lock = threading.Lock()

    # ── 연결 관리 ────────────────────────────────────────────────
    def _ensure_open(self):
        if self._ser and self._ser.is_open:
            return

        ser = serial.Serial()
        ser.port     = ESP32CAM_PORT
        ser.baudrate = ESP32CAM_BAUD
        ser.timeout  = READ_TIMEOUT_SEC
        # DTR/RTS 자동 리셋 방지. open() 전에 지정해야 리셋 펄스가 나가지 않는다.
        ser.dsrdtr = False
        ser.rtscts = False
        ser.dtr    = False
        ser.rts    = False
        ser.open()

        time.sleep(BOOT_WAIT_SEC)
        ser.reset_input_buffer()
        self._ser = ser
        logger.info(f"[CameraClient] 시리얼 연결: {ESP32CAM_PORT} @ {ESP32CAM_BAUD}")

    def _close(self):
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    # ── 프로토콜 ─────────────────────────────────────────────────
    def _read_header(self) -> tuple:
        """
        IMG:<length>:<checksum> 헤더를 만날 때까지 라인을 읽는다.
        '#'로 시작하는 펌웨어 디버그 로그와 부팅 잔여 출력은 건너뛴다.
        """
        deadline = time.time() + HEADER_TIMEOUT_SEC

        while time.time() < deadline:
            raw = self._ser.readline()
            if not raw:
                continue

            line = raw.decode("utf-8", errors="ignore").strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("ERR:"):
                raise RuntimeError(f"ESP32 응답: {line}")

            if line.startswith("IMG:"):
                parts = line.split(":")
                if len(parts) != 3:
                    raise ValueError(f"헤더 형식 오류: {line}")
                return int(parts[1]), int(parts[2])

            logger.debug(f"[CameraClient] 무시: {line}")

        raise TimeoutError("IMG 헤더 수신 타임아웃")

    def _capture_once(self) -> bytes:
        self._ser.reset_input_buffer()
        self._ser.write(b"CAPTURE\n")
        self._ser.flush()

        length, checksum = self._read_header()

        if length <= 0 or length > MAX_IMAGE_SIZE:
            raise ValueError(f"비정상 이미지 크기: {length}")

        data = self._ser.read(length)
        if len(data) != length:
            raise IOError(f"수신 부족: {len(data)}/{length}")

        if sum(data) != checksum:
            raise IOError("체크섬 불일치 (전원 부족 또는 보율 문제 의심)")

        # END 문자열까지 확인해야 프레임 경계가 맞는지 보장된다.
        # 여기서 어긋나면 다음 캡처의 헤더를 본문으로 읽게 되므로 예외로 끊는다.
        tail = self._ser.readline().decode("utf-8", errors="ignore").strip()
        if tail != "END":
            raise IOError(f"프레임 종료 문자열 불일치: {tail!r}")

        return data

    # ── 공개 API ─────────────────────────────────────────────────
    def capture(self) -> bytes:
        with self._lock:
            for attempt in range(1, CAPTURE_RETRY + 1):
                try:
                    self._ensure_open()
                    data = self._capture_once()
                    logger.info(f"[CameraClient] 캡처 성공 ({len(data)} bytes)")
                    return data
                except Exception as e:
                    logger.error(f"[CameraClient] 캡처 실패({attempt}/{CAPTURE_RETRY}): {e}")
                    # 포트를 닫아 다음 시도에서 재오픈 → ESP32와 프레임 동기 복구
                    self._close()

            return None

    def ping(self) -> bool:
        """헬스체크. 연결 확인용."""
        with self._lock:
            try:
                self._ensure_open()
                self._ser.reset_input_buffer()
                self._ser.write(b"PING\n")
                self._ser.flush()

                deadline = time.time() + 5
                while time.time() < deadline:
                    line = self._ser.readline().decode("utf-8", errors="ignore").strip()
                    if line.startswith("OK:PONG"):
                        return True
                return False
            except Exception as e:
                logger.error(f"[CameraClient] PING 실패: {e}")
                self._close()
                return False

    def close(self):
        with self._lock:
            self._close()


_camera = _CameraSerial()


def capture() -> bytes:
    """ESP32-CAM에서 이미지 캡처 → JPEG bytes 반환. 실패 시 None 반환."""
    return _camera.capture()


def ping() -> bool:
    """ESP32-CAM 응답 확인. True/False 반환."""
    return _camera.ping()


def close():
    """포트 정리 (종료 시 호출)."""
    _camera.close()