import json
import logging
import threading
import time
from pathlib import Path

from anomaly_tracker import AnomalyTracker
from camera_client import capture
from config import (
    MQTT_BROKER,
    VISION_STATE_PATH,
    YOLO_CONFIRMATION_ATTEMPTS,
    YOLO_CONFIRMATION_INTERVAL_SECONDS,
    YOLO_CONFIRMATION_MIN_CONFIDENCE,
)
from http_client import HttpClient
from led_scheduler import LedScheduler
from motor_scheduler import MotorScheduler
from mqtt_client import MqttClient
from sensor_buffer import SensorBuffer
from serial_reader import SerialReader
from yolo_runner import run_yolo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

VALID_PORT_RANGE = range(0, 8)
ALLOWED_GROWTH_RESULTS = {"sprout", "growth"}


class DetectionConsensus:
    """포트별 마지막 확정 YOLO 생육 결과를 로컬에 저장한다."""

    def __init__(self, state_path: str):
        self._path = Path(state_path)
        self._lock = threading.Lock()
        self._labels = self._load()

    def needs_verification(self, port_index: int, result: dict) -> bool:
        label = self._reliable_growth_label(result)
        if label is None:
            return False

        with self._lock:
            return self._labels.get(str(port_index)) != label

    def choose_majority(self, attempts: list[tuple[bytes, dict]]):
        """신뢰도 기준을 통과한 sprout/growth 결과가 2회 이상이면 대표 사진을 반환한다."""
        grouped = {}

        for image_bytes, result in attempts:
            label = self._reliable_growth_label(result)
            if label is not None:
                grouped.setdefault(label, []).append((image_bytes, result))

        winner = None
        for label, candidates in grouped.items():
            if len(candidates) >= 2:
                winner = label
                break

        if winner is None:
            return None

        # 같은 결과를 낸 사진 중 신뢰도가 가장 높은 사진을 서버에 보낸다.
        return max(
            grouped[winner],
            key=lambda item: float(item[1].get("growthConfidence", 0.0)),
        )

    def remember(self, port_index: int, result: dict):
        label = self._reliable_growth_label(result)
        if label is None:
            return

        with self._lock:
            self._labels[str(port_index)] = label
            self._save()

    @staticmethod
    def no_detection_result() -> dict:
        return {
            "growthResult": "no_detection",
            "growthConfidence": 0.0,
            "diseaseResult": "no_detection",
            "diseaseConfidence": 0.0,
        }

    def _reliable_growth_label(self, result: dict):
        label = str(result.get("growthResult", "no_detection")).strip().lower()
        confidence = float(result.get("growthConfidence", 0.0))

        if label not in ALLOWED_GROWTH_RESULTS:
            return None
        if confidence < YOLO_CONFIRMATION_MIN_CONFIDENCE:
            return None
        return label

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("상태 파일은 JSON 객체여야 합니다.")
            return {
                str(port): label
                for port, label in data.items()
                if str(port).isdigit() and label in ALLOWED_GROWTH_RESULTS
            }
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("[DetectionConsensus] 상태 파일을 읽지 못했습니다: %s", exc)
            return {}

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self._path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(self._labels, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary_path.replace(self._path)
        except OSError as exc:
            logger.error("[DetectionConsensus] 상태 파일 저장 실패: %s", exc)


def build_arduino_config_command(config: dict) -> str:
    """MQTT JSON 품종 기준을 Arduino CFG 명령으로 변환한다."""
    fields = (
        "minTemperature", "maxTemperature",
        "minHumidity", "maxHumidity",
        "minPh", "maxPh",
        "minTds", "maxTds",
    )

    def serial_value(value):
        return "NA" if value is None else format(float(value), "g")

    return "CFG:" + ",".join(serial_value(config.get(field)) for field in fields) + "\n"


def main():
    http = HttpClient()
    consensus = DetectionConsensus(VISION_STATE_PATH)

    def on_anomaly_start(alert: dict):
        http.send_alert(alert)
        http.send_anomaly_start(alert)

    def on_anomaly_end(alert: dict):
        http.send_anomaly_end(alert)

    anomaly_tracker = AnomalyTracker(
        on_anomaly_start_cb=on_anomaly_start,
        on_anomaly_end_cb=on_anomaly_end,
    )

    def on_sensor_data(data: dict):
        http.send_realtime(data)
        sensor_buffer.push(data)
        anomaly_tracker.on_data(data)

    def on_hourly_avg(avg: dict):
        http.send_hourly(avg)

    def on_alert(alert: dict):
        anomaly_tracker.on_alert(alert)

    def on_float(state: str):
        anomaly_tracker.on_float(state)

    sensor_buffer = SensorBuffer(on_hourly_cb=on_hourly_avg)

    def process_port_photo(port_index: int):
        """현재 포트에서 ESP32-CAM 촬영, YOLO 검증, 서버 전송, NEXT를 순서대로 처리한다."""
        try:
            first_image = capture()
            if first_image is None:
                serial.send(f"ERROR:{port_index}\n")
                return

            first_result = run_yolo(first_image)
            attempts = [(first_image, first_result)]

            if consensus.needs_verification(port_index, first_result):
                logger.info("[Photo] 변화 후보 port=%s: 추가 2회 촬영 시작", port_index)

                for attempt in range(2, YOLO_CONFIRMATION_ATTEMPTS + 1):
                    time.sleep(YOLO_CONFIRMATION_INTERVAL_SECONDS)
                    image_bytes = capture()
                    if image_bytes is None:
                        logger.error("[Photo] port=%s, %s차 촬영 실패", port_index, attempt)
                        serial.send(f"ERROR:{port_index}\n")
                        return
                    attempts.append((image_bytes, run_yolo(image_bytes)))

                selected = consensus.choose_majority(attempts)
                if selected is None:
                    logger.warning("[Photo] port=%s: 2/3 생육 결과 검증 실패", port_index)
                    selected_image = first_image
                    selected_result = consensus.no_detection_result()
                else:
                    selected_image, selected_result = selected
                    consensus.remember(port_index, selected_result)
            else:
                selected_image = first_image
                selected_result = first_result

            # 사진 바이트는 이미 RPi에 안전하게 수신·검증됐으므로,
            # Arduino는 서버 업로드 성공 여부가 아니라 이 시점부터 다음 포트로 이동할 수 있다.
            serial.send(f"NEXT:{port_index}\n")
            http.send_photo(selected_image, selected_result, port_index)

        except Exception as exc:
            logger.exception("[Photo] port=%s 처리 실패: %s", port_index, exc)
            serial.send(f"ERROR:{port_index}\n")

    def on_seq_photo(port_index: int):
        if port_index not in VALID_PORT_RANGE:
            logger.error("[Photo] 유효하지 않은 portIndex=%s", port_index)
            serial.send(f"ERROR:{port_index}\n")
            return

        threading.Thread(
            target=process_port_photo,
            args=(port_index,),
            daemon=True,
            name=f"photo-port-{port_index}",
        ).start()

    motor_scheduler = MotorScheduler(
        send_cmd_cb=None,
        on_seq_photo_cb=on_seq_photo,
    )

    serial = SerialReader(
        on_data_cb=on_sensor_data,
        on_alert_cb=on_alert,
        on_float_cb=on_float,
        on_seq_photo_cb=motor_scheduler.on_seq_photo,
        on_seq_done_cb=motor_scheduler.on_seq_done,
    )
    motor_scheduler.send_cmd = serial.send

    led_scheduler = LedScheduler(send_cmd_cb=serial.send)

    def on_mqtt_command(cmd: str):
        led_scheduler.handle_command(cmd)

    def on_photo_interval(hours: int):
        motor_scheduler.set_interval(hours)

    def on_cultivation_config(config: dict):
        try:
            command = build_arduino_config_command(config)
            serial.send(command)
            anomaly_tracker.update_thresholds(config)
            logger.info(
                "[CultivationConfig] 적용 species=%s command=%s",
                config.get("speciesName"),
                command.strip(),
            )
        except (TypeError, ValueError) as exc:
            logger.error("[CultivationConfig] 설정 적용 실패: %s", exc)

    mqtt = None
    if MQTT_BROKER:
        mqtt = MqttClient(
            on_command_cb=on_mqtt_command,
            on_photo_interval_cb=on_photo_interval,
            on_cultivation_config_cb=on_cultivation_config,
        )
    else:
        logger.info("[Main] MQTT_BROKER 미설정 — MQTT 스킵, HTTP 파이프라인만 동작")

    serial.start()
    led_scheduler.start()
    motor_scheduler.start()
    if mqtt:
        mqtt.start()

    logger.info("=== GrowLab RPi 시작 (GROWLAB-G111) ===")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("=== GrowLab RPi 종료 ===")
        serial.stop()
        led_scheduler.stop()
        motor_scheduler.stop()
        if mqtt:
            mqtt.stop()


if __name__ == "__main__":
    main()
