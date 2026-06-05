import logging
import time
from serial_reader   import SerialReader
from sensor_buffer   import SensorBuffer
from http_client     import HttpClient
from mqtt_client     import MqttClient
from led_scheduler   import LedScheduler
from motor_scheduler import MotorScheduler
from anomaly_tracker import AnomalyTracker
from camera_client   import capture
from yolo_runner     import run_yolo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 아두이노 시퀀스 유효 포트 범위 (0~7)
VALID_PORT_RANGE = range(0, 8)


def main():
    http = HttpClient()

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

    def on_mqtt_command(cmd: str):
        led_scheduler.handle_command(cmd)

    def on_photo_interval(hours: int):
        motor_scheduler.set_interval(hours)

    def on_seq_photo(port_index: int):
        if port_index not in VALID_PORT_RANGE:
            return

        image_bytes = capture()
        if image_bytes is None:
            return

        yolo_result = run_yolo(image_bytes)  # plant_stage 인자 제거
        http.send_photo(image_bytes, yolo_result, port_index)

    sensor_buffer = SensorBuffer(on_hourly_cb=on_hourly_avg)

    motor_scheduler = MotorScheduler(
        send_cmd_cb=None,
        on_seq_photo_cb=on_seq_photo,
    )

    serial = SerialReader(
        on_data_cb=on_sensor_data,
        on_alert_cb=on_alert,
        on_float_cb=on_float,
        on_seq_photo_cb=motor_scheduler.on_seq_photo,
        on_seq_done_cb=motor_scheduler.on_seq_done,   # DONE 콜백 연결
    )
    motor_scheduler.send_cmd = serial.send

    led_scheduler = LedScheduler(send_cmd_cb=serial.send)
    mqtt = MqttClient(
        on_command_cb=on_mqtt_command,
        on_photo_interval_cb=on_photo_interval,
    )

    serial.start()
    led_scheduler.start()
    motor_scheduler.start()
    mqtt.start()

    logger.info("=== GrowLab RPi 시작 (PLANTI-G111) ===")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("=== GrowLab RPi 종료 ===")
        serial.stop()
        led_scheduler.stop()
        motor_scheduler.stop()


if __name__ == "__main__":
    main()