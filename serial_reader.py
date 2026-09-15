import serial
import threading
import logging
from config import SERIAL_PORT, SERIAL_BAUD

logger = logging.getLogger(__name__)

RECONNECT_INTERVAL_SEC = 3


class SerialReader:
    def __init__(self, on_data_cb, on_alert_cb, on_float_cb,
                 on_photo_cb=None, on_seq_done_cb=None,
                 on_error_cb=None, on_ready_cb=None, on_selftest_cb=None):
        """
        on_data_cb(data: dict)             → 센서값 수신
        on_alert_cb(alert: dict)           → ALERT 수신
        on_float_cb(state: str)            → FLOAT 상태 수신
        on_photo_cb(port_index: int)       → [PHOTO] PORT:X 수신
        on_seq_done_cb()                   → [SEQ] DONE 수신
        on_error_cb(port_index: int, msg)  → [ERROR] 수신
        on_ready_cb()                      → 포트 연결 직후 / [SYSTEM] Ready 수신
                                             (아두이노 임계값 CFG 전송용)
        on_selftest_cb(report: dict)       → [SELFTEST] 응답 수신 (자가진단 결과)
        """
        self.on_data_cb      = on_data_cb
        self.on_alert_cb     = on_alert_cb
        self.on_float_cb     = on_float_cb
        self.on_photo_cb     = on_photo_cb
        self.on_seq_done_cb  = on_seq_done_cb
        self.on_error_cb     = on_error_cb
        self.on_ready_cb     = on_ready_cb
        self.on_selftest_cb  = on_selftest_cb
        self.ser             = None
        self._ser_lock       = threading.Lock()
        self._stop           = threading.Event()
        self._last_water_status = None

    def start(self):
        # 포트를 여기서 바로 열지 않고 백그라운드 스레드에서 연결/재연결을 전담한다.
        # 이렇게 해야 아두이노가 아직 안 꽂혀 있어도 main() 전체가 죽지 않는다.
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info("[SerialReader] 시작")

    def stop(self):
        self._stop.set()
        with self._ser_lock:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass

    def send(self, command: str) -> bool:
        """
        Arduino로 명령 전송. 줄바꿈이 없으면 자동 추가.
        전송 성공 여부를 반환한다. 호출부(MotorScheduler)가 실패를 감지해
        시퀀스 상태를 되돌려야 하기 때문이다.
        """
        with self._ser_lock:
            if not (self.ser and self.ser.is_open):
                logger.warning(f"[SerialReader] 포트 닫힘, 전송 실패: {command.strip()}")
                return False

            try:
                if not command.endswith('\n'):
                    command += '\n'
                self.ser.write(command.encode())
                logger.info(f"[SerialReader] 전송: {command.strip()}")
                return True
            except (serial.SerialException, OSError) as e:
                logger.error(f"[SerialReader] 전송 중 연결 끊김: {e}")
                return False

    def send_raw(self, char: str) -> bool:
        """Arduino로 단일 바이트 명령 전송 (p, O, o 등 줄바꿈 없이). 성공 여부 반환."""
        with self._ser_lock:
            if not (self.ser and self.ser.is_open):
                logger.warning(f"[SerialReader] 포트 닫힘, 전송 실패: {char}")
                return False

            try:
                self.ser.write(char.encode())
                logger.info(f"[SerialReader] 전송(raw): {char}")
                return True
            except (serial.SerialException, OSError) as e:
                logger.error(f"[SerialReader] 전송 중 연결 끊김: {e}")
                return False

    def reset_arduino(self) -> bool:
        """
        아두이노 소프트 리셋. 'R' 커맨드를 보내면 아두이노가 NVIC_SystemReset()으로
        스스로 재부팅한다(전원 off가 아니라 재시작).
        아두이노 펌웨어에 R 커맨드 핸들러가 있어야 동작한다.
        DTR 리셋이 되는 보드라면 아래 주석 블록을 대신 쓸 수 있으나,
        UNO R4에서 DTR 반응이 불확실하므로 소프트 커맨드를 기본으로 한다.
        """
        ok = self.send("R")   # 아두이노가 'R\n' 수신 시 자가 리셋
        if ok:
            logger.warning("[SerialReader] 아두이노 소프트 리셋 명령 전송(R)")
        else:
            logger.error("[SerialReader] 리셋 명령 전송 실패")
        return ok

    def _connect(self) -> bool:
        """포트가 열릴 때까지 RECONNECT_INTERVAL_SEC 간격으로 재시도한다.
        stop()이 호출되면 False를 반환하고 즉시 빠져나온다."""
        while not self._stop.is_set():
            try:
                new_ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
                with self._ser_lock:
                    self.ser = new_ser
                logger.info(f"[SerialReader] 연결 성공: {SERIAL_PORT}")

                # RPi만 재시작된 경우 아두이노는 이미 부팅을 마쳐
                # [SYSTEM] Ready가 다시 오지 않는다. 연결 시점에도 한 번 보낸다.
                # CFG는 멱등이라 중복 전송돼도 문제없다.
                if self.on_ready_cb:
                    self.on_ready_cb()
                return True
            except serial.SerialException as e:
                logger.warning(
                    f"[SerialReader] 연결 실패({SERIAL_PORT}), "
                    f"{RECONNECT_INTERVAL_SEC}초 후 재시도: {e}"
                )
                self._stop.wait(RECONNECT_INTERVAL_SEC)

        return False

    def _run(self):
        while not self._stop.is_set():
            if not (self.ser and self.ser.is_open):
                if not self._connect():
                    break  # stop() 호출로 인한 정상 종료
                continue

            try:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
            except (serial.SerialException, OSError) as e:
                logger.error(f"[SerialReader] 연결 끊김, 재연결 시도: {e}")
                with self._ser_lock:
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None
                continue

            if not line:
                continue

            logger.debug(f"[SerialReader] raw: {line}")

            try:
                if line.startswith("[DATA]"):
                    self._parse_data(line)
                elif line.startswith("[ALERT]"):
                    self._parse_alert(line)
                elif line.startswith("[FLOAT]"):
                    self._parse_float(line)
                elif line.startswith("[PHOTO]"):
                    self._parse_photo(line)
                elif line.startswith("[SEQ]"):
                    self._parse_seq(line)
                elif line.startswith("[ERROR]"):
                    self._parse_error(line)
                elif line.startswith("[SYSTEM]"):
                    self._parse_system(line)
                elif line.startswith("[SELFTEST]"):
                    self._parse_selftest(line)
                elif line.startswith("[LED]"):
                    logger.info(f"[SerialReader] {line}")
                elif line.startswith("[RESET]"):
                    logger.info(f"[SerialReader] {line}")
                elif line.startswith("[CFG]"):
                    logger.info(f"[SerialReader] {line}")
                elif line.startswith("[RECOVERY]"):
                    logger.warning(f"[SerialReader] {line}")
                elif line.startswith("[RPi]"):
                    logger.info(f"[SerialReader] {line}")
                elif line.startswith("[WARN]"):
                    logger.warning(f"[SerialReader] {line}")

            except Exception as e:
                logger.error(f"[SerialReader] 처리 오류: {e}")

    def _parse_system(self, line: str):
        """[SYSTEM] Ready 수신 → 아두이노가 명령을 받을 준비 완료."""
        logger.info(f"[SerialReader] {line}")
        if "Ready" in line and self.on_ready_cb:
            self.on_ready_cb()

    def _parse_selftest(self, line: str):
        """
        [SELFTEST] PH:OK,TDS:OK,DHT:FAIL,FLOAT:OK
        각 센서의 자가진단 결과를 dict로 파싱해 콜백.
        """
        try:
            payload = line.replace("[SELFTEST]", "").strip()
            report = {}
            for part in payload.split(","):
                if ":" in part:
                    k, v = part.split(":", 1)
                    report[k.strip().upper()] = v.strip().upper()
            logger.info(f"[SerialReader] 자가진단 결과 수신: {report}")
            if self.on_selftest_cb:
                self.on_selftest_cb(report)
        except Exception as e:
            logger.error(f"[SerialReader] SELFTEST 파싱 오류: {e} / line: {line}")

    def _parse_photo(self, line: str):
        """[PHOTO] PORT:X 파싱 → 포트 번호 추출 후 콜백"""
        try:
            # "[PHOTO] PORT:3" → port_index = 3
            port_str = line.split("PORT:")[1].strip()
            port_index = int(port_str)
            logger.info(f"[SerialReader] 촬영 요청 수신 PORT:{port_index}")
            if self.on_photo_cb:
                self.on_photo_cb(port_index)
        except Exception as e:
            logger.error(f"[SerialReader] PHOTO 파싱 오류: {e} / line: {line}")

    def _parse_seq(self, line: str):
        if "START" in line:
            logger.info("[SerialReader] [SEQ] START")
        elif "DONE" in line:
            logger.info("[SerialReader] [SEQ] DONE")
            if self.on_seq_done_cb:
                self.on_seq_done_cb()

    def _parse_error(self, line: str):
        """[ERROR] RPI_TIMEOUT PORT:0 등 파싱"""
        try:
            port_str = line.split("PORT:")[1].strip()
            port_index = int(port_str)
            logger.error(f"[SerialReader] 아두이노 에러 PORT:{port_index} / {line}")
            if self.on_error_cb:
                self.on_error_cb(port_index, line)
        except Exception as e:
            logger.error(f"[SerialReader] ERROR 파싱 오류: {e} / line: {line}")

    def _parse_data(self, line: str):
        try:
            payload = line.replace("[DATA]", "").strip()
            parts   = dict(p.split(":") for p in payload.split(","))

            if "WATER" in parts:
                self._last_water_status = (parts["WATER"].strip() == "1")

            data = {
                "temperature":        float(parts["T"]),
                "humidity":           float(parts["H"]),
                "ph":                 float(parts["PH"]),
                "tds":                float(parts["TDS"]),
                "led":                int(parts.get("LED", 0)),
                "water_level_status": self._last_water_status,
            }
            self.on_data_cb(data)
        except Exception as e:
            logger.error(f"[SerialReader] DATA 파싱 오류: {e} / line: {line}")

    def _parse_float(self, line: str):
        # 아두이노 라벨 규약:
        #   "[FLOAT] OK -> PUMP ON"   물 충분 (WATER:1과 일치)
        #   "[FLOAT] LOW -> PUMP OFF" 물 부족 (WATER:0과 일치)
        # "LOW"를 먼저 검사한다. 라벨이 바뀌어도 이상 상태를 놓치지 않는 쪽이 안전하다.
        if "LOW" in line:
            self._last_water_status = False
            logger.info("[SerialReader] 물 상태: 부족 (LOW)")
        elif "OK" in line:
            self._last_water_status = True
            logger.info("[SerialReader] 물 상태: 충분 (OK)")
        self.on_float_cb(line)

    def _parse_alert(self, line: str):
        try:
            payload     = line.replace("[ALERT]", "").strip()
            sensor, val = payload.split(":")
            self.on_alert_cb({"sensor": sensor.strip(), "value": float(val.strip())})
        except Exception as e:
            logger.error(f"[SerialReader] ALERT 파싱 오류: {e} / line: {line}")