import logging
import time
from serial_reader     import SerialReader
from sensor_buffer     import SensorBuffer
from state_store       import StateStore
from http_client       import HttpClient
from mqtt_client       import MqttClient
from led_scheduler     import LedScheduler
from motor_scheduler   import MotorScheduler
from anomaly_tracker   import AnomalyTracker
from selftest_scheduler import SelfTestScheduler
from camera_client     import capture, ping as camera_ping, close as camera_close
from yolo_runner       import run_yolo
from config            import (
    MQTT_BROKER,
    MQTT_PASS,
    PH_RESET_TRIGGER_ABOVE,
    YOLO_CONFIRMATION_ATTEMPTS,
    YOLO_CONFIRMATION_INTERVAL_SECONDS,
    YOLO_CONFIRMATION_MIN_CONFIDENCE,
    build_cfg_command,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

VALID_PORT_RANGE = range(0, 8)

# 생육 단계는 yolo_runner가 숫자 인덱스(문자열)로 보낸다: 씨앗=0 / 정식=1 / 생육=2 / 수확=3 ...
# 검증 로직도 이 "숫자"를 그대로 기준으로 삼는다(단계가 늘어나도 코드 수정 불필요).
def _parse_stage_index(result: dict):
    """result의 growthResult를 숫자 단계로 변환. 숫자가 아니면(no_detection 등) None."""
    raw = str(result.get("growthResult", "no_detection")).strip()
    if not raw.isdigit():
        return None
    return int(raw)


def main():
    http  = HttpClient()
    state = StateStore()   # 하이브리드 상태 저장소 (로컬 state.json)

    def reliable_growth_stage(result: dict):
        """신뢰도 70% 이상이고 숫자 단계인 경우만 그 단계를 반환. 아니면 None."""
        stage = _parse_stage_index(result)
        confidence = float(result.get("growthConfidence", 0.0))
        if stage is None or confidence < YOLO_CONFIRMATION_MIN_CONFIDENCE:
            return None
        return stage

    def needs_growth_verification(port_index: int, result: dict) -> bool:
        stage = _parse_stage_index(result)
        if stage is None:
            return False

        confirmed_by_port = state.get("growth_stage_by_port") or {}
        previous_stage = confirmed_by_port.get(str(port_index))

        # 이미 확정한 단계와 같거나 이전 단계면 재촬영하지 않는다.
        if previous_stage is not None and stage <= int(previous_stage):
            return False

        # 첫 관측 또는 앞선 단계 관측은 신뢰도가 낮더라도 추가 촬영으로 확인한다.
        return True

    def choose_growth_majority(attempts: list[tuple[bytes, dict]]):
        grouped = {}
        for image_bytes, result in attempts:
            stage = reliable_growth_stage(result)
            if stage is not None:
                grouped.setdefault(stage, []).append((image_bytes, result))

        # 같은 단계가 2회 이상(3장 중 2장) 나온 경우만 확정. 신뢰도 최고 결과를 선택.
        for candidates in grouped.values():
            if len(candidates) >= 2:
                return max(
                    candidates,
                    key=lambda item: float(item[1].get("growthConfidence", 0.0)),
                )
        return None

    def remember_growth_stage(port_index: int, result: dict):
        stage = reliable_growth_stage(result)
        if stage is None:
            return

        confirmed_by_port = dict(state.get("growth_stage_by_port") or {})
        previous_stage = confirmed_by_port.get(str(port_index))
        if previous_stage is not None and stage <= int(previous_stage):
            return

        confirmed_by_port[str(port_index)] = stage
        state.update(growth_stage_by_port=confirmed_by_port)

    def no_detection_result() -> dict:
        return {
            "growthResult": "no_detection",
            "growthConfidence": 0.0,
            "diseaseResult": "no_detection",
            "diseaseConfidence": 0.0,
        }

    # [테스트] 아두이노 1회 리셋 플래그
    reset_state = {"done": False}

    def on_anomaly_start(alert: dict):
        http.send_alert(alert)
        http.send_anomaly_start(alert)

    def on_anomaly_end(alert: dict):
        http.send_anomaly_end(alert)

    anomaly_tracker = AnomalyTracker(
        on_anomaly_start_cb=on_anomaly_start,
        on_anomaly_end_cb=on_anomaly_end,
    )

    def maybe_reset_arduino(data: dict):
        """[테스트] pH가 뻥튀기(>임계)면 아두이노 1회 소프트 리셋."""
        if reset_state["done"]:
            return
        ph = data.get("ph")
        if ph is not None and ph > PH_RESET_TRIGGER_ABOVE:
            logger.warning(f"[Main] pH 뻥튀기 감지: {ph} > {PH_RESET_TRIGGER_ABOVE} "
                           f"→ 아두이노 1회 리셋 시도")
            if serial.reset_arduino():
                reset_state["done"] = True
                logger.warning("[Main] 아두이노 리셋 명령 전송 완료 (이후 재리셋 안 함)")

    def on_sensor_data(data: dict):
        http.send_realtime(data)
        sensor_buffer.push(data)
        anomaly_tracker.on_data(data)
        maybe_reset_arduino(data)

    def on_hourly_avg(avg: dict):
        http.send_hourly(avg)

    def on_alert(alert: dict):
        anomaly_tracker.on_alert(alert)

    def on_float(state_str: str):
        anomaly_tracker.on_float(state_str)

    def on_mqtt_command(cmd: str):
        led_scheduler.handle_command(cmd)

    def on_photo_interval(hours: int):
        motor_scheduler.set_interval(hours)

    def on_arduino_ready():
        """
        아두이노 연결/부팅 완료 시:
          1) 센서 임계값(CFG) 전송 — ALERT 활성화
          2) 현재 LED 상태 재하달 — 아두이노 재부팅으로 꺼진 LED를 복원
        """
        cfg = build_cfg_command()
        if serial.send(cfg):
            logger.info(f"[Main] 센서 임계값 전송: {cfg}")
        else:
            logger.error("[Main] 센서 임계값 전송 실패 — ALERT 비활성 상태")
        # LED 상태 복원 (수동 ON이었다면 재연결 후에도 켜지도록)
        led_scheduler.reapply()

    def on_selftest_report(report: dict):
        """아두이노 [SELFTEST] 응답 → 자가진단 스케줄러로 전달."""
        selftest_scheduler.on_arduino_report(report)

    def on_selftest_final(result: dict):
        """자가진단 종합 결과 → 서버 알림(있으면). 없으면 로그만."""
        logger.info(f"[Main] 자가진단 종합: {result['status']} / {result['detail']}")
        # 서버에 자가진단 결과 알림 엔드포인트가 있으면 여기서 전송.
        # 현재 전용 API가 없으므로, FAIL일 때만 기존 alert 경로로 알린다.
        if result["status"] == "FAIL":
            for sensor in result["failed"]:
                try:
                    http.send_alert({"sensor_type": f"SELFTEST_{sensor}", "value": 0.0})
                except Exception as e:
                    logger.error(f"[Main] 자가진단 알림 실패: {e}")

    def on_seq_photo(port_index: int):
        if port_index not in VALID_PORT_RANGE:
            raise ValueError(f"Invalid port_index: {port_index}")

        first_image = capture()
        if first_image is None:
            raise RuntimeError(f"캡처 실패 PORT:{port_index}")

        first_result = run_yolo(first_image)
        selected_image = first_image
        selected_result = first_result

        if needs_growth_verification(port_index, first_result):
            attempts = [(first_image, first_result)]
            logger.info(f"[Main] PORT:{port_index} 생육 변화 후보 — 추가 2회 촬영")

            for attempt in range(2, YOLO_CONFIRMATION_ATTEMPTS + 1):
                time.sleep(YOLO_CONFIRMATION_INTERVAL_SECONDS)
                image_bytes = capture()
                if image_bytes is None:
                    raise RuntimeError(f"재촬영 실패 PORT:{port_index}, attempt:{attempt}")
                attempts.append((image_bytes, run_yolo(image_bytes)))

            confirmed = choose_growth_majority(attempts)
            if confirmed is None:
                logger.warning(f"[Main] PORT:{port_index} 생육 변화 2/3 검증 실패")
                selected_result = no_detection_result()
            else:
                selected_image, selected_result = confirmed
                remember_growth_stage(port_index, selected_result)
                logger.info(
                    f"[Main] PORT:{port_index} 생육 변화 확정 "
                    f"growth={selected_result.get('growthResult')} "
                    f"confidence={selected_result.get('growthConfidence')}"
                )

        if not http.send_photo(selected_image, selected_result, port_index):
            logger.error(f"[Main] PORT:{port_index} 업로드 실패 — 이미지 유실, 시퀀스는 계속 진행")

    sensor_buffer = SensorBuffer(on_hourly_cb=on_hourly_avg)

    motor_scheduler = MotorScheduler(
        send_cmd_cb=None,
        send_raw_cb=None,
        on_seq_photo_cb=on_seq_photo,
        state_store=state,
    )

    serial = SerialReader(
        on_data_cb=on_sensor_data,
        on_alert_cb=on_alert,
        on_float_cb=on_float,
        on_photo_cb=motor_scheduler.on_photo_request,
        on_seq_done_cb=motor_scheduler.on_seq_done,
        on_error_cb=motor_scheduler.on_error,
        on_ready_cb=on_arduino_ready,
        on_selftest_cb=on_selftest_report,
    )
    motor_scheduler.send_cmd = serial.send
    motor_scheduler.send_raw = serial.send_raw

    led_scheduler = LedScheduler(send_cmd_cb=serial.send_raw, state_store=state)

    selftest_scheduler = SelfTestScheduler(
        send_cmd_cb=serial.send,
        camera_ping_cb=camera_ping,
        on_report_cb=on_selftest_final,
    )

    mqtt = None
    if MQTT_BROKER and MQTT_PASS:
        mqtt = MqttClient(
            on_command_cb=on_mqtt_command,
            on_photo_interval_cb=on_photo_interval,
        )
    elif MQTT_BROKER:
        logger.error("[Main] GROWLAB_MQTT_PASS 미설정 — MQTT 스킵, HTTP 파이프라인만 동작")
    else:
        logger.info("[Main] MQTT_BROKER 미설정 — MQTT 스킵, HTTP 파이프라인만 동작")

    serial.start()
    led_scheduler.start()
    motor_scheduler.start()
    selftest_scheduler.start()
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
        selftest_scheduler.stop()
        camera_close()
        if mqtt:
            mqtt.stop()


if __name__ == "__main__":
    main()
