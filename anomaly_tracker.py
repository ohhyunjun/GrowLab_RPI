from datetime import datetime

# 센서별 정상 범위 (아두이노 ALERT 임계값과 동일)
NORMAL_RANGE = {
    "TEMP": (15.0, 30.0),
    "HUM":  (30.0, 90.0),
    "PH":   (5.0,  7.5),
    "TDS":  (200.0, 800.0),
}

class AnomalyTracker:
    def __init__(self, on_anomaly_start_cb, on_anomaly_end_cb):
        """
        on_anomaly_start_cb(payload: dict) → {"sensor_type": "PH", "value": 8.2}
        on_anomaly_end_cb(payload: dict)   → {"sensor_type": "PH"}
        """
        self.on_start = on_anomaly_start_cb
        self.on_end   = on_anomaly_end_cb
        self._active  = set()  # 현재 이상 중인 센서

    def on_alert(self, alert: dict):
        """[ALERT] 수신 시 호출. 처음 이상일 때만 시작 이벤트 전송."""
        sensor = alert.get("sensor", "").upper()
        value  = alert.get("value")

        if sensor not in self._active:
            self._active.add(sensor)
            self.on_start({"sensor_type": sensor, "value": value})

    def on_data(self, data: dict):
        """[DATA] 수신 시 호출. 이상 중인 센서가 정상 복귀했는지 체크."""
        sensor_map = {
            "TEMP": data.get("temperature"),
            "HUM":  data.get("humidity"),
            "PH":   data.get("ph"),
            "TDS":  data.get("tds"),
        }

        for sensor, value in sensor_map.items():
            if sensor not in self._active or value is None:
                continue
            low, high = NORMAL_RANGE[sensor]
            if low <= value <= high:
                self._active.discard(sensor)
                self.on_end({"sensor_type": sensor})

    def on_float(self, state: str):
        """[FLOAT] 수신 시 호출. WATER 이상 시작/종료 처리."""
        if "LOW" in state and "WATER" not in self._active:
            self._active.add("WATER")
            self.on_start({"sensor_type": "WATER", "value": 0.0})
        elif "OK" in state and "WATER" in self._active:
            self._active.discard("WATER")
            self.on_end({"sensor_type": "WATER"})
