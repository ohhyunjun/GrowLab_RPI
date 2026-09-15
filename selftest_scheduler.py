import threading
import logging
from datetime import datetime

from config import (
    SELFTEST_WEEKDAY, SELFTEST_HOUR,
    SELFTEST_CAMERA_CHECK,
)

logger = logging.getLogger(__name__)


class SelfTestScheduler:
    """
    주 1회 기기 자가진단.

    지정한 요일/시각(기본: 일요일 03시)에 아두이노로 SELFTEST 명령을 보내
    센서(pH/TDS/DHT/FLOAT) 상태를 받고, 카메라(ESP32-CAM)는 RPi가 직접
    PING으로 확인한다. 액추에이터(NEMA17/23)는 리미트 스위치가 없어 물리
    검증이 불가능하므로 자가진단 대상에서 제외한다.

    결과는 on_report_cb로 전달한다(서버 기록/알림은 호출부가 담당).
    """

    def __init__(self, send_cmd_cb, camera_ping_cb=None, on_report_cb=None):
        """
        send_cmd_cb(cmd: str) -> bool  → SerialReader.send() (아두이노로 SELFTEST 전송)
        camera_ping_cb() -> bool       → camera_client.ping (카메라 응답 확인)
        on_report_cb(report: dict)     → 종합 진단 결과 콜백
        """
        self.send_cmd       = send_cmd_cb
        self.camera_ping    = camera_ping_cb
        self.on_report_cb   = on_report_cb
        self._stop          = threading.Event()
        self._last_run_key  = None   # 같은 시각 중복 실행 방지 ("2026-W35" 같은 키)
        # 아두이노가 보낸 최근 SELFTEST 응답을 보관
        self._pending_arduino = None

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info(
            f"[SelfTest] 시작 (매주 {SELFTEST_WEEKDAY}요일 {SELFTEST_HOUR}시, "
            f"카메라체크={SELFTEST_CAMERA_CHECK})"
        )

    def stop(self):
        self._stop.set()

    def trigger_now(self):
        """수동 즉시 실행 (테스트용)."""
        logger.info("[SelfTest] 수동 즉시 실행")
        self._run_selftest()

    def on_arduino_report(self, report: dict):
        """SerialReader가 [SELFTEST] 응답을 파싱해 넘겨줌."""
        self._pending_arduino = report
        # 아두이노 응답이 오면 카메라 체크까지 합쳐 최종 리포트를 만든다.
        self._finalize(report)

    def _run(self):
        # weekday(): 월=0 ... 일=6
        while not self._stop.is_set():
            now = datetime.now()
            # ISO 주 번호로 중복 실행 방지 키 생성
            run_key = f"{now.isocalendar().year}-W{now.isocalendar().week}"

            if (now.weekday() == SELFTEST_WEEKDAY
                    and now.hour == SELFTEST_HOUR
                    and run_key != self._last_run_key):
                self._last_run_key = run_key
                self._run_selftest()

            self._stop.wait(60)   # 1분마다 시각 확인

    def _run_selftest(self):
        logger.info("[SelfTest] 자가진단 시작 → 아두이노 SELFTEST 전송")
        self._pending_arduino = None
        if not self.send_cmd("SELFTEST"):
            logger.error("[SelfTest] SELFTEST 전송 실패 (포트 닫힘?)")
            # 아두이노 응답을 못 받아도 카메라만이라도 확인해 리포트
            self._finalize({"PH": "UNKNOWN", "TDS": "UNKNOWN",
                            "DHT": "UNKNOWN", "FLOAT": "UNKNOWN"})

    def _finalize(self, arduino_report: dict):
        """아두이노 센서 결과 + 카메라 결과를 합쳐 최종 리포트 생성."""
        report = dict(arduino_report) if arduino_report else {}

        if SELFTEST_CAMERA_CHECK and self.camera_ping:
            try:
                cam_ok = self.camera_ping()
                report["CAM"] = "OK" if cam_ok else "FAIL"
            except Exception as e:
                logger.error(f"[SelfTest] 카메라 확인 오류: {e}")
                report["CAM"] = "FAIL"

        # FAIL 항목 요약
        failed = [k for k, v in report.items() if v == "FAIL"]
        status = "FAIL" if failed else "OK"

        logger.info(f"[SelfTest] 종합 진단 결과: {status} / {report}"
                    + (f" (이상: {failed})" if failed else ""))

        if self.on_report_cb:
            try:
                self.on_report_cb({"status": status, "detail": report,
                                   "failed": failed})
            except Exception as e:
                logger.error(f"[SelfTest] 리포트 콜백 오류: {e}")
