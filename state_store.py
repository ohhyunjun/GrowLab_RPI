import os
import json
import threading
import logging

logger = logging.getLogger(__name__)

# 상태 파일 경로. systemd 서비스로 돌릴 때도 쓰기 가능한 위치에 둔다.
STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "state.json"
)

# 기본 상태 (state.json이 없을 때 사용)
_DEFAULT_STATE = {
    "led_mode":     "auto",   # "auto" | "manual"
    "led_on":       False,    # 현재 LED가 켜져 있어야 하는지 (수동/자동 결과)
    "led_on_hour":  6,
    "led_off_hour": 22,
    "photo_interval_hour": 12,
    "growth_stage_by_port": {},
}


class StateStore:
    """
    하이브리드 상태 저장소 (선택지 C).

    - 기본: 로컬 state.json을 "진실의 원천"으로 사용한다.
      RPi가 명령을 받을 때마다 여기에 저장하고, 시작 시 읽어 복원한다.
    - 확장: 나중에 백엔드에 기기용 설정 조회 API
      (예: GET /api/devices/{serial}/settings)가 생기면
      load_from_server()를 구현해 서버값을 우선 적용하도록 열어둔다.
      지금은 stub이며 로컬 파일만 쓴다.
    """

    def __init__(self, path=STATE_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._state = dict(_DEFAULT_STATE)
        self._load()

    # ── 로컬 파일 ────────────────────────────────────────────────
    def _load(self):
        if not os.path.exists(self._path):
            logger.info(f"[StateStore] {self._path} 없음 — 기본값 사용")
            self._save_locked()
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 알려진 키만 병합 (미래 키 추가/삭제에 견고)
            for k in _DEFAULT_STATE:
                if k in data:
                    self._state[k] = data[k]
            logger.info(f"[StateStore] 상태 복원: {self._state}")
        except Exception as e:
            logger.error(f"[StateStore] 로드 실패({e}) — 기본값 사용")

    def _save_locked(self):
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)   # 원자적 교체 (쓰기 중 손상 방지)
        except Exception as e:
            logger.error(f"[StateStore] 저장 실패: {e}")

    # ── 서버 우선 (확장 지점, 현재 stub) ─────────────────────────
    def load_from_server(self, fetch_fn=None) -> bool:
        """
        나중에 기기용 설정 API가 생기면 fetch_fn을 넘겨 서버값으로 덮어쓴다.
        fetch_fn() -> dict (led_mode/led_on/... 키) 또는 None.
        현재는 호출부에서 fetch_fn을 주지 않으므로 아무것도 하지 않는다.
        """
        if fetch_fn is None:
            return False
        try:
            remote = fetch_fn()
            if not remote:
                return False
            with self._lock:
                for k in _DEFAULT_STATE:
                    if k in remote:
                        self._state[k] = remote[k]
                self._save_locked()
            logger.info(f"[StateStore] 서버값으로 상태 갱신: {self._state}")
            return True
        except Exception as e:
            logger.error(f"[StateStore] 서버 조회 실패: {e}")
            return False

    # ── 접근자 ───────────────────────────────────────────────────
    def get(self, key):
        with self._lock:
            return self._state.get(key)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def update(self, **kwargs):
        """여러 키를 한 번에 갱신하고 즉시 저장."""
        with self._lock:
            for k, v in kwargs.items():
                if k in _DEFAULT_STATE:
                    self._state[k] = v
                else:
                    logger.warning(f"[StateStore] 알 수 없는 키 무시: {k}")
            self._save_locked()
        logger.debug(f"[StateStore] 갱신: {kwargs}")
