import threading
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class LedScheduler:
    """
    LED 제어 스케줄러.

    개선 사항:
      1) 레벨 트리거: "지금 시각이 ON 구간이면 켜져 있어야 한다"를 상태로 판단.
         자동 모드로 전환하는 즉시 현재 시각 기준으로 LED를 맞춘다.
         (기존 엣지 트리거는 정시 진입 순간에만 제어해, 13시에 09~20시 자동을
          걸어도 다음 경계까지 안 켜지는 버그가 있었음)
      2) 자정 넘는 스케줄(예: 22~06시) 지원.
      3) 상태 영속: 모드/스케줄/현재 LED 상태를 StateStore에 저장·복원.
      4) reapply(): 아두이노 재연결 시 현재 있어야 할 LED 상태를 다시 하달.
    """

    def __init__(self, send_cmd_cb, state_store):
        """
        send_cmd_cb(cmd: str) → SerialReader.send_raw() 연결 (O/o 단일 바이트)
        state_store           → StateStore (상태 영속)
        """
        self.send_cmd = send_cmd_cb
        self.state    = state_store
        self._stop    = threading.Event()
        self._lock    = threading.Lock()

        # StateStore에서 복원
        snap = state_store.snapshot()
        self._mode     = snap["led_mode"]
        self._on_hour  = snap["led_on_hour"]
        self._off_hour = snap["led_off_hour"]
        self._led_on   = snap["led_on"]   # 마지막으로 하달한 LED 상태

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info(
            f"[LedScheduler] 시작 (모드:{self._mode}, "
            f"ON:{self._on_hour}시, OFF:{self._off_hour}시, LED:{self._led_on})"
        )
        # 시작 즉시 현재 있어야 할 상태로 한 번 맞춘다 (재시작 복원)
        self._apply_current(force=True)

    def stop(self):
        self._stop.set()

    # ── 현재 시각이 ON 구간인지 (자정 넘는 스케줄 포함) ──────────
    def _should_be_on(self, hour: int) -> bool:
        on, off = self._on_hour, self._off_hour
        if on == off:
            return False
        if on < off:
            return on <= hour < off            # 예: 09~20
        return hour >= on or hour < off        # 예: 22~06 (자정 넘음)

    # ── 현재 있어야 할 상태로 LED를 맞춘다 ──────────────────────
    def _apply_current(self, force=False):
        """
        auto 모드: 현재 시각 기준 desired 계산 → 실제와 다르면 교정.
        manual 모드: 저장된 _led_on 상태 유지(시각 무시).
        force=True면 실제 상태와 무관하게 무조건 재하달(재연결/시작 복원용).
        """
        with self._lock:
            if self._mode == "auto":
                desired = self._should_be_on(datetime.now().hour)
            else:
                desired = self._led_on   # manual은 마지막 명령 유지

            if force or desired != self._led_on:
                self.send_cmd("O" if desired else "o")
                self._led_on = desired
                self.state.update(led_on=desired)
                logger.info(f"[LedScheduler] LED {'ON' if desired else 'OFF'} "
                            f"(mode={self._mode})")

    def reapply(self):
        """아두이노 재연결 시 호출 → 현재 있어야 할 LED 상태를 다시 하달."""
        logger.info("[LedScheduler] 아두이노 재연결 → LED 상태 재적용")
        self._apply_current(force=True)

    # ── 서버(MQTT) 명령 처리 ────────────────────────────────────
    def handle_command(self, cmd: str):
        """
          "O"                 → 수동 LED ON
          "o"                 → 수동 LED OFF
          "SCHED:06:00-22:00" → 자동 모드, 스케줄 설정 (즉시 현재시각 반영)
        """
        if cmd == "O":
            with self._lock:
                self._mode = "manual"
                self._led_on = True
                self.send_cmd("O")
                self.state.update(led_mode="manual", led_on=True)
            logger.info("[LedScheduler] 수동 LED ON")

        elif cmd == "o":
            with self._lock:
                self._mode = "manual"
                self._led_on = False
                self.send_cmd("o")
                self.state.update(led_mode="manual", led_on=False)
            logger.info("[LedScheduler] 수동 LED OFF")

        elif cmd.startswith("SCHED:"):
            try:
                times           = cmd.replace("SCHED:", "")
                on_str, off_str = times.split("-")
                with self._lock:
                    self._on_hour  = int(on_str.split(":")[0])
                    self._off_hour = int(off_str.split(":")[0])
                    self._mode     = "auto"
                    self.state.update(
                        led_mode="auto",
                        led_on_hour=self._on_hour,
                        led_off_hour=self._off_hour,
                    )
                logger.info(f"[LedScheduler] 자동 모드: "
                            f"ON={self._on_hour}시 OFF={self._off_hour}시")
                # 핵심: 자동 전환 즉시 현재 시각 기준으로 LED를 맞춘다.
                # (수동 OFF 상태였어도 지금이 ON 구간이면 바로 켜짐)
                self._apply_current()
            except Exception as e:
                logger.error(f"[LedScheduler] SCHED 파싱 오류: {e} / cmd: {cmd}")
        else:
            logger.warning(f"[LedScheduler] 알 수 없는 명령: {cmd}")

    def _run(self):
        # 30초마다 현재 있어야 할 상태로 LED를 유지(레벨 트리거).
        while not self._stop.is_set():
            if self._mode == "auto":
                self._apply_current()
            self._stop.wait(30)
