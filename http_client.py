import requests
import logging
from config import SERVER_URL, SERIAL_NUMBER

logger = logging.getLogger(__name__)


class HttpClient:

    def send_realtime(self, data: dict):
        """1분마다 실시간 센서값 전송 → POST /api/sensor_logs/realtime (DB 저장 X)"""
        self._post("/api/sensor_logs/realtime", self._build_payload(data))

    def send_hourly(self, avg: dict):
        """1시간 평균 전송 → POST /api/sensor_logs (DB 저장 O)"""
        self._post("/api/sensor_logs", self._build_payload(avg))

    def send_alert(self, alert: dict):
        """이상 발생 → POST /api/notices/alert (notice 생성, 쿨다운 있음)"""
        payload = {
            "serial_number": SERIAL_NUMBER,
            "sensor":        alert.get("sensor_type"),
            "value":         alert.get("value"),
        }
        self._post("/api/notices/alert", payload)

    def send_anomaly_start(self, alert: dict):
        """이상 시작 → POST /api/anomalies (sensor_anomaly insert)"""
        payload = {
            "serial_number": SERIAL_NUMBER,
            "sensor_type":   alert.get("sensor_type"),
            "value":         alert.get("value"),
        }
        self._post("/api/anomalies", payload)

    def send_anomaly_end(self, alert: dict):
        """정상 복귀 → PATCH /api/anomalies (ended_at, duration_min 업데이트)"""
        payload = {
            "serial_number": SERIAL_NUMBER,
            "sensor_type":   alert.get("sensor_type"),
        }
        self._patch("/api/anomalies", payload)


    def send_photo(self, image_bytes: bytes, yolo_result: dict, port_index: int):
        """사진 및 YOLO 분석 결과 전송 → POST /api/photos"""
        if image_bytes is None or len(image_bytes) == 0:
            logger.error(f"[HttpClient] portIndex={port_index}: 이미지 비어있음, 전송 스킵")
            return

        url = SERVER_URL + "/api/photos"
        try:
            files = {"imageFile": ("photo.jpg", image_bytes, "image/jpeg")}
            data  = {
                "serialNumber":      SERIAL_NUMBER,
                "portIndex":         str(port_index),
                "growthResult":      yolo_result.get("growthResult",     "no_detection"),
                "growthConfidence":  str(yolo_result.get("growthConfidence",  0.0)),
                "diseaseResult":     yolo_result.get("diseaseResult",    "no_detection"),
                "diseaseConfidence": str(yolo_result.get("diseaseConfidence", 0.0)),
            }
            res = requests.post(url, data=data, files=files, timeout=30)
            res.raise_for_status()
            logger.info(
                f"[HttpClient] 사진 전송 완료 portIndex={port_index} | "
                f"growth={data['growthResult']} disease={data['diseaseResult']}"
            )
        except Exception as e:
            logger.error(f"[HttpClient] 사진 전송 실패 portIndex={port_index}: {e}")

    # ── 내부 헬퍼 ────────────────────────────────────────────────
    def _build_payload(self, data: dict) -> dict:
        return {
            "serial_number":      SERIAL_NUMBER,
            "temperature":        data.get("temperature"),
            "humidity":           data.get("humidity"),
            "ph":                 data.get("ph"),
            "tds":                data.get("tds"),
            "water_level_status": data.get("water_level_status"),
        }

    def _post(self, path: str, payload: dict):
        url = SERVER_URL + path
        try:
            res = requests.post(url, json=payload, timeout=5)
            res.raise_for_status()
        except Exception as e:
            logger.error(f"[HttpClient] POST {path} 실패: {e}")

    def _patch(self, path: str, payload: dict):
        url = SERVER_URL + path
        try:
            res = requests.patch(url, json=payload, timeout=5)
            res.raise_for_status()
        except Exception as e:
            logger.error(f"[HttpClient] PATCH {path} 실패: {e}")