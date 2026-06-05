import threading
import logging
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)

BUFFER_SIZE = 3

class SensorBuffer:
    def __init__(self, on_hourly_cb):
        """
        슬라이딩 윈도우(deque, maxlen=3) 방식으로 아두이노 전송 타이밍 지연으로 인한
        분 단위 경계 불안정 해결
        """
        self.on_hourly_cb     = on_hourly_cb
        self._lock            = threading.Lock()
        # 슬라이딩 윈도우: 항상 최근 BUFFER_SIZE 만큼만 유지
        self._buffer          = deque(maxlen=BUFFER_SIZE)
        self._last_flush_hour = -1

    def push(self, data: dict):
        """
        매분 호출
        - 항상 최근 3개를 슬라이딩 윈도우로 유지
        - 정각(00분) 진입 시 평균 계산 후 서버 전송
        """
        with self._lock:
            self._buffer.append(data)
            logger.debug(f"[SensorBuffer] 버퍼 추가 (현재 {len(self._buffer)}개)")

        now    = datetime.now()
        minute = now.minute
        hour   = now.hour

        # 정각에 flush (중복 실행 방지)
        if minute == 0 and hour != self._last_flush_hour and len(self._buffer) > 0:
            self._last_flush_hour = hour
            self._flush(now)

    def _flush(self, now: datetime):
        with self._lock:
            buf = list(self._buffer)
            # flush 후 버퍼 클리어
            self._buffer.clear()

        if not buf:
            logger.warning("[SensorBuffer] flush 호출됐지만 버퍼가 비어있음")
            return

        # 수치형 센서: None 제외하고 평균
        numeric_keys = ["temperature", "humidity", "ph", "tds"]
        avg = {}
        for k in numeric_keys:
            valid_vals = [d[k] for d in buf if d.get(k) is not None]
            avg[k] = round(sum(valid_vals) / len(valid_vals), 2) if valid_vals else None

        # water_level_status: 버퍼 내 마지막 비-None 값 사용
        # float 스위치는 변경 시에만 전송되므로 마지막 유효값이 현재 상태
        water_vals = [d["water_level_status"] for d in buf if d.get("water_level_status") is not None]
        avg["water_level_status"] = water_vals[-1] if water_vals else None

        avg["timestamp"] = now.replace(minute=0, second=0, microsecond=0).isoformat()

        logger.info(f"[SensorBuffer] 1시간 평균 완성 (샘플 {len(buf)}개): {avg}")
        self.on_hourly_cb(avg)
