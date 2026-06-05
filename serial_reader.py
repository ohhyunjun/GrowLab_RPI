import serial
import threading
import logging
from config import SERIAL_PORT, SERIAL_BAUD

logger = logging.getLogger(__name__)


class SerialReader:
    def __init__(self, on_data_cb, on_alert_cb, on_float_cb,
                 on_seq_photo_cb=None, on_seq_done_cb=None):
        """
        on_data_cb(data: dict)      → 센서값 수신
        on_alert_cb(alert: dict)    → ALERT 수신
        on_float_cb(state: str)     → FLOAT 상태 수신
        on_seq_photo_cb()           → [SEQ] PHOTO 수신
        on_seq_done_cb()            → [SEQ] DONE 수신  ← 추가
        """
        self.on_data_cb      = on_data_cb
        self.on_alert_cb     = on_alert_cb
        self.on_float_cb     = on_float_cb
        self.on_seq_photo_cb = on_seq_photo_cb
        self.on_seq_done_cb  = on_seq_done_cb
        self.ser             = None
        self._stop           = threading.Event()
        self._last_water_status = None

    def start(self):
        self.ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info("[SerialReader] 시작")

    def stop(self):
        self._stop.set()
        if self.ser:
            self.ser.close()

    def send(self, command: str):
        """Arduino로 단일 바이트 명령 전송"""
        if self.ser and self.ser.is_open:
            self.ser.write(command.encode())
            logger.info(f"[SerialReader] 전송: {command}")
        else:
            logger.warning(f"[SerialReader] 포트 닫힘, 전송 실패: {command}")

    def _run(self):
        while not self._stop.is_set():
            try:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                logger.debug(f"[SerialReader] raw: {line}")

                if line.startswith("[DATA]"):
                    self._parse_data(line)
                elif line.startswith("[ALERT]"):
                    self._parse_alert(line)
                elif line.startswith("[FLOAT]"):
                    self._parse_float(line)
                elif line.startswith("[SEQ]"):
                    self._parse_seq(line)

            except Exception as e:
                logger.error(f"[SerialReader] 오류: {e}")

    def _parse_seq(self, line: str):
        if "PHOTO" in line:
            logger.info("[SerialReader] [SEQ] PHOTO 수신")
            if self.on_seq_photo_cb:
                self.on_seq_photo_cb()
        elif "START" in line:
            logger.info("[SerialReader] [SEQ] START")
        elif "DONE" in line:
            logger.info("[SerialReader] [SEQ] DONE")
            if self.on_seq_done_cb:   # ← DONE 콜백 호출
                self.on_seq_done_cb()

    def _parse_data(self, line: str):
        try:
            payload = line.replace("[DATA]", "").strip()
            parts   = dict(p.split(":") for p in payload.split(","))

            # WATER 키 있을 때만 갱신 (없으면 이전 값 유지)
            if "WATER" in parts:
                self._last_water_status = (parts["WATER"].strip() == "1")

            data = {
                "temperature":        float(parts["T"]),
                "humidity":           float(parts["H"]),
                "ph":                 float(parts["PH"]),
                "tds":                float(parts["TDS"]),
                "led":                int(parts.get("LED", 0)),
                "water_level_status": self._last_water_status,  # None 허용
            }
            self.on_data_cb(data)
        except Exception as e:
            logger.error(f"[SerialReader] DATA 파싱 오류: {e} / line: {line}")

    def _parse_float(self, line: str):
        if "OK" in line:
            self._last_water_status = True
            logger.info("[SerialReader] 물 상태: 충분 (OK)")
        elif "LOW" in line:
            self._last_water_status = False
            logger.info("[SerialReader] 물 상태: 부족 (LOW)")
        self.on_float_cb(line)

    def _parse_alert(self, line: str):
        try:
            payload     = line.replace("[ALERT]", "").strip()
            sensor, val = payload.split(":")
            self.on_alert_cb({"sensor": sensor.strip(), "value": float(val.strip())})
        except Exception as e:
            logger.error(f"[SerialReader] ALERT 파싱 오류: {e} / line: {line}")