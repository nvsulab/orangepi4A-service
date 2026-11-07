#!/usr/bin/env python3
"""
Flask-сервер для управления LED лентой WS2812 через SPI.
Порт: 3000.  Адрес: http://192.168.12.1:3000

Возможности:
- Сплошной цвет (RGB)
- Шаблонные узоры: радуга, огонь, вода, молния, волна
- Регулировка высоты пламени и воды
- Трекинг пальцев через камеру
- Комбинации жестов -> узоры
- MJPEG-трансляция камеры в браузер
- Освобождение камеры от camera-stream.service при старте / возврат при остановке
- Запись и редактирование комбинаций жестов
- Возврат управления led-bouncing.service при выходе из режима
"""
import os
import time
import math
import random
import threading
import subprocess
import signal
from led_sessions import LEDSessions
from gestures import GestureConflict, GestureRecording, GestureStore

import numpy as np
import spidev
from flask import Flask, render_template, request, jsonify, Response

# ---------- Опциональные зависимости для камеры ----------
try:
    import cv2
    import mediapipe as mp
    CAMERA_AVAILABLE = True
except ImportError as e:
    CAMERA_AVAILABLE = False
    print(f"[WARN] OpenCV / MediaPipe не установлены — режимы камеры недоступны ({e})")


# ===================== LED КЛАСС =====================
class WS2812SPI:
    def __init__(self, bus=1, device=0, num_leds=72, spi_speed_hz=2400000):
        self.num_leds = num_leds
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = spi_speed_hz
        self.spi.mode = 0
        self.bit0 = np.array([0b100], dtype=np.uint8)
        self.bit1 = np.array([0b110], dtype=np.uint8)
        self._lock = threading.Lock()

    def _encode_byte(self, byte_val):
        bits = []
        for i in range(7, -1, -1):
            bits.append(self.bit1 if (byte_val >> i) & 1 else self.bit0)
        return np.concatenate(bits)

    def show(self, colors):
        data = []
        for r, g, b in colors:
            data.append(self._encode_byte(g))
            data.append(self._encode_byte(r))
            data.append(self._encode_byte(b))
        payload = np.concatenate(data).tolist()
        with self._lock:
            self.spi.xfer2(payload)

    def close(self):
        self.spi.close()


# ===================== КОНСТАНТЫ =====================
NUM_LEDS = 72
COLOR_OPEN    = (0, 255, 0)
COLOR_CLOSED  = (255, 0, 0)
COLOR_NO_HAND = (0, 0, 30)
HAND_ZONES = [
    [(0, 7), (7, 14), (14, 21), (21, 28), (28, 36)],
    [(36, 43), (43, 50), (50, 57), (57, 64), (64, 72)],
]

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
RELEASE_CAMERA_SCRIPT = os.path.join(PROJECT_DIR, "scripts", "release_camera.sh")
CAMERA_STREAM_SERVICE = "camera-stream.service"
LED_BOUNCING_SERVICE = "led-bouncing.service"


# ===================== УПРАВЛЕНИЕ LED-BOUNCING =====================
def stop_led_bouncing():
    subprocess.run(["systemctl", "stop", LED_BOUNCING_SERVICE],
                   capture_output=True, text=True, timeout=20, check=True)


def start_led_bouncing():
    subprocess.run(["systemctl", "start", LED_BOUNCING_SERVICE],
                   capture_output=True, text=True, timeout=20, check=True)

# ===================== СОСТОЯНИЕ ЭФФЕКТОВ =====================
class EffectState:
    def __init__(self):
        self.t = 0.0
        self.lightning = []   # (start, end, r, g, b, expiry)
        self.fire_len = 12
        self.water_len = 18

_effect_state = EffectState()


# ===================== ЭФФЕКТЫ =====================
def effect_static(color):
    return [tuple(color)] * NUM_LEDS


def effect_wave(t):
    colors = []
    for i in range(NUM_LEDS):
        hue = (i * 0.05 + t * 2.0) % (2 * math.pi)
        r = int((math.sin(hue) * 0.5 + 0.5) * 255)
        g = int((math.sin(hue + 2.094) * 0.5 + 0.5) * 255)
        b = int((math.sin(hue + 4.188) * 0.5 + 0.5) * 255)
        colors.append((r, g, b))
    return colors


def effect_fire(t):
    colors = [(0, 0, 0)] * NUM_LEDS
    fire_len = max(1, min(NUM_LEDS, _effect_state.fire_len))
    for i in range(fire_len):
        base = 1.0 - i / fire_len
        flicker = 0.5 + 0.5 * math.sin(t * 15 + i * 3.7) * math.sin(t * 7 + i * 1.3)
        intensity = max(0.2, base * flicker)
        r = int(255 * intensity)
        g = int(120 * intensity * intensity)
        colors[i] = (min(255, r), min(255, g), 0)
    return colors


def effect_water(t):
    colors = [(0, 0, 0)] * NUM_LEDS
    water_len = max(1, min(NUM_LEDS, _effect_state.water_len))
    for i in range(water_len):
        wave = math.sin(t * 4 + i * 0.8) * 0.5 + 0.5
        r = int(20 * wave)
        g = int(80 * wave + 40)
        b = int(180 * wave + 60)
        if random.random() < 0.02 and wave > 0.8:
            r, g, b = 255, 255, 255
        colors[i] = (min(255, r), min(255, g), min(255, b))
    return colors


def effect_lightning(t):
    """Кортеж из 6 элементов: (start, end, r, g, b, expiry)."""
    st = _effect_state
    st.lightning = [l for l in st.lightning if l[5] > t]

    if random.random() < 0.15:
        start = random.randint(0, NUM_LEDS - 1)
        end = random.randint(start + 1, min(NUM_LEDS, start + 20))
        c = random.choice([(255, 255, 255), (200, 200, 255), (255, 240, 180)])
        st.lightning.append((start, end, c[0], c[1], c[2], t + 0.25))

    colors = [(0, 0, 0)] * NUM_LEDS
    for (start, end, r, g, b, _) in st.lightning:
        for i in range(start, end):
            colors[i] = (r, g, b)
    return colors


def effect_rainbow(t):
    colors = []
    for i in range(NUM_LEDS):
        hue = (i / NUM_LEDS + t * 0.3) % 1.0
        h = hue * 6
        c, x = 1.0, 1 - abs(h % 2 - 1)
        if h < 1:    r, g, b = c, x, 0
        elif h < 2:  r, g, b = x, c, 0
        elif h < 3:  r, g, b = 0, c, x
        elif h < 4:  r, g, b = 0, x, c
        elif h < 5:  r, g, b = x, 0, c
        else:        r, g, b = c, 0, x
        colors.append((int(r * 255), int(g * 255), int(b * 255)))
    return colors


TEMPLATE_EFFECTS = {
    "wave":      effect_wave,
    "fire":      effect_fire,
    "water":     effect_water,
    "lightning": effect_lightning,
    "rainbow":   effect_rainbow,
}


# ===================== MEDIAPIPE =====================
if CAMERA_AVAILABLE:
    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils
    TIP_IDS = [4, 8, 12, 16, 20]
    PIP_IDS = [3, 6, 10, 14, 18]


def get_finger_states(lm, label):
    """
    lm — объект NormalizedLandmarkList (results.multi_hand_landmarks[i]).
    Обращение к точкам — через lm.landmark[i].
    """
    pts = lm.landmark
    states = []
    if label == "Right":
        states.append(pts[4].x < pts[3].x)
    else:
        states.append(pts[4].x > pts[3].x)
    for i in range(1, 5):
        states.append(pts[TIP_IDS[i]].y < pts[PIP_IDS[i]].y)
    return states


def render_fingers(hands_states):
    colors = [(0, 0, 0)] * NUM_LEDS
    for hand_idx in range(2):
        states = hands_states[hand_idx]
        for finger_idx, (start, end) in enumerate(HAND_ZONES[hand_idx]):
            if states is None:
                color = COLOR_NO_HAND
            elif states[finger_idx]:
                color = COLOR_OPEN
            else:
                color = COLOR_CLOSED
            for i in range(start, end):
                colors[i] = color
    return colors


def state_to_key(states):
    parts = []
    for hand in states:
        parts.append("NA" if hand is None else "".join("1" if s else "0" for s in hand))
    return "|".join(parts)


def compare_states(a, b):
    total, match = 0, 0
    for ha, hb in zip(a, b):
        if ha is None and hb is None:
            continue
        if ha is None or hb is None:
            total += 5
            continue
        for sa, sb in zip(ha, hb):
            total += 1
            if sa == sb:
                match += 1
    return match / total if total > 0 else 0.0


# ===================== КОНТРОЛЛЕР =====================
class Controller:
    def __init__(self):
        self.led = None
        self.mode = "idle"       # idle | static | template | tracking | combo
        self.color = (0, 0, 0)
        self.template = "rainbow"
        self.fire_len = 12
        self.water_len = 18
        self.lock = threading.Lock()
        self.camera_thread = None
        self.camera_stop = threading.Event()
        self.camera_kind = None
        self.camera_owner = None
        self.camera_error = None
        self.recording = GestureRecording()
        # буфер для MJPEG-стрима
        self.last_frame_jpeg = None
        self.frame_lock = threading.Lock()
        self.output_lock = threading.RLock()
        self.active = False

    def start(self):
        threading.Thread(target=self._render_loop, daemon=True).start()
        threading.Thread(target=self._lease_watchdog, daemon=True).start()

    def _lease_watchdog(self):
        while True:
            time.sleep(1)
            try:
                with sessions.lock:
                    sessions.expire()
                    if self.recording.owner not in sessions.clients:
                        self.recording.cancel()
                    self.cleanup_camera()
            except Exception as error:
                print(f"[LED] Не удалось вернуть фоновый эффект, повторю: {error}", flush=True)

    def activate(self):
        stop_led_bouncing()
        try:
            with self.output_lock:
                self.led = WS2812SPI(bus=1, device=0, num_leds=NUM_LEDS)
                with self.lock:
                    self.mode = "idle"
                    self.color = (0, 0, 0)
                self.active = True
        except Exception:
            start_led_bouncing()
            raise

    def deactivate(self):
        # Block both render and camera writers BEFORE returning SPI to the service.
        with self.output_lock:
            self.active = False
            with self.lock:
                self.mode = "idle"
        self._stop_camera()
        with self.output_lock:
            if self.led is not None:
                try:
                    self.led.show([(0, 0, 0)] * NUM_LEDS)
                finally:
                    self.led.close()
                    self.led = None
        start_led_bouncing()

    def show(self, colors, mode=None, camera=False):
        with self.output_lock:
            if not self.active or self.led is None:
                return
            with self.lock:
                current_mode = self.mode
            if mode is not None and mode != current_mode:
                return
            if camera and (self.camera_stop.is_set() or current_mode not in ("tracking", "combo")):
                return
            self.led.show(colors)

    # ---------- Рендер-цикл ----------
    def _render_loop(self):
        prev = time.time()
        while True:
            now = time.time()
            dt = now - prev
            prev = now
            if not self.active:
                time.sleep(0.05)
                continue

            with self.lock:
                mode = self.mode
                color = self.color
                template = self.template

            if mode == "idle":
                time.sleep(0.05)
                continue
            if mode == "static":
                colors = effect_static(color)
            elif mode == "template" and template in TEMPLATE_EFFECTS:
                _effect_state.t += dt
                colors = TEMPLATE_EFFECTS[template](_effect_state.t)
            elif mode in ("tracking", "combo"):
                time.sleep(0.05)
                continue
            else:
                colors = [(0, 0, 0)] * NUM_LEDS

            self.show(colors, mode=mode)
            time.sleep(1 / 30)

    # ---------- API ----------
    def set_static(self, color):
        with self.lock:
            self.color = tuple(color)
            self.mode = "static"
        self._stop_camera()

    def set_template(self, name):
        with self.lock:
            if name in TEMPLATE_EFFECTS:
                self.template = name
                self.mode = "template"
        self._stop_camera()

    def set_fire_len(self, n):
        with self.lock:
            self.fire_len = max(1, min(NUM_LEDS, int(n)))
            _effect_state.fire_len = self.fire_len

    def set_water_len(self, n):
        with self.lock:
            self.water_len = max(1, min(NUM_LEDS, int(n)))
            _effect_state.water_len = self.water_len

    def cleanup_camera(self):
        if self.camera_thread is None:
            return
        self.recording.status()
        if (self.camera_owner not in sessions.clients
                or not self.camera_thread.is_alive()
                or (self.camera_kind == "preview" and not self.recording.pending)):
            self.end_camera()

    def end_camera(self):
        if self.mode in ("tracking", "combo"):
            sessions.release_control()
        else:
            self._stop_camera()
        return self.camera_thread is None

    def _stop_camera(self):
        self.recording.cancel()
        self.camera_stop.set()
        thread = self.camera_thread
        if thread is not None:
            thread.join(timeout=3)
            if not thread.is_alive():
                self.camera_thread = None
                self.camera_kind = None
                self.camera_owner = None
        with self.frame_lock:
            self.last_frame_jpeg = None

    def start_camera(self, kind, owner):
        if not CAMERA_AVAILABLE:
            raise RuntimeError("OpenCV или MediaPipe недоступны")
        self._stop_camera()
        if self.camera_thread is not None:
            raise RuntimeError("Камера ещё останавливается")
        restore = subprocess.run(
            ["systemctl", "is-active", "--quiet", CAMERA_STREAM_SERVICE],
            timeout=5,
        ).returncode == 0
        try:
            subprocess.run(["bash", RELEASE_CAMERA_SCRIPT],
                           capture_output=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            if restore:
                restore_camera_service()
            raise RuntimeError("Не удалось освободить камеру от сервиса видеопотока") from error

        self.camera_stop = threading.Event()
        self.camera_kind = kind
        self.camera_owner = owner
        self.camera_error = None
        self.camera_thread = threading.Thread(
            target=camera_worker_wrapper,
            args=(self, self.camera_stop, kind, restore),
            daemon=True,
        )
        self.camera_thread.start()


ctrl = Controller()
sessions = LEDSessions(ctrl.activate, ctrl.deactivate)


# ===================== CAMERA WORKER =====================
def restore_camera_service():
    try:
        subprocess.run(["systemctl", "start", CAMERA_STREAM_SERVICE],
                       capture_output=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[CAM] Не удалось вернуть видеопоток: {error}", flush=True)
        return False
    return True


def camera_worker_wrapper(controller, stop_event, kind, restore):
    try:
        camera_worker(controller, stop_event, kind)
    except Exception as error:
        print(f"[CAM] {error}", flush=True)
        controller.camera_error = "Камера недоступна. Проверьте подключение и запустите снова"
        controller.recording.cancel(controller.camera_error)
    finally:
        stop_event.set()
        with controller.frame_lock:
            controller.last_frame_jpeg = None
        if restore and not restore_camera_service():
            controller.camera_error = "Камера освобождена, но сервис видеопотока не запустился"


def camera_worker(controller, stop_event, kind):
    cap = cv2.VideoCapture(1)
    hands = None
    last_gesture = None
    stable_counter = 0
    STABLE_REQUIRED = 5
    THRESHOLD = 0.8
    prev = time.monotonic()
    previous_gestures = None

    try:
        if not cap.isOpened():
            raise RuntimeError("Не удалось открыть /dev/video1")
        hands = mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.7,
            min_tracking_confidence=0.7,
        )
        while not stop_event.is_set():
            now = time.monotonic()
            dt = now - prev
            prev = now

            ret, frame = cap.read()
            if not ret:
                raise RuntimeError("Не удалось прочитать кадр")

            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = hands.process(rgb)

            detected = []
            if results.multi_hand_landmarks:
                for lm, hand in zip(results.multi_hand_landmarks,
                                    results.multi_handedness):
                    label = hand.classification[0].label
                    states = get_finger_states(lm, label)
                    # координата запястья для сортировки рук слева-направо
                    detected.append((lm.landmark[0].x, states, lm))

            detected.sort(key=lambda x: x[0])
            hands_states = [None, None]
            for idx, (_, states, lm) in enumerate(detected[:2]):
                hands_states[idx] = states
                mp_draw.draw_landmarks(frame, lm, mp_hands.HAND_CONNECTIONS)

            controller.recording.observe(hands_states)
            if kind == "tracking":
                controller.show(render_fingers(hands_states), mode=kind, camera=True)
                cv2.putText(frame, "TRACKING", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            elif kind == "combo":
                # Keep each match and its SPI write in the same store revision.
                with gesture_store.lock:
                    gestures = gesture_store.snapshot()
                    if (last_gesture is not None and gestures is not previous_gestures
                            and previous_gestures.get(last_gesture) != gestures.get(last_gesture)):
                        last_gesture = None
                        stable_counter = 0
                        _effect_state.lightning.clear()
                    previous_gestures = gestures
                    current_name, current_match = None, 0.0
                    for name, data in gestures.items():
                        score = compare_states(hands_states, data["state"])
                        if score > current_match:
                            current_match = score
                            current_name = name

                    if current_name and current_match >= THRESHOLD:
                        stable_counter = stable_counter + 1 if current_name == last_gesture else 1
                        last_gesture = current_name
                    else:
                        stable_counter = max(0, stable_counter - 1)
                        if stable_counter == 0:
                            last_gesture = None

                    colors = render_fingers(hands_states)
                    if last_gesture and stable_counter >= STABLE_REQUIRED:
                        entry = gestures[last_gesture]
                        effect = entry.get("effect")
                        if entry.get("color") is not None:
                            colors = effect_static(tuple(entry["color"]))
                        elif effect in TEMPLATE_EFFECTS:
                            _effect_state.t += dt
                            colors = TEMPLATE_EFFECTS[effect](_effect_state.t)
                    controller.show(colors, mode=kind, camera=True)

            # --- сохраняем кадр для MJPEG-стрима ---
            try:
                ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    with controller.frame_lock:
                        controller.last_frame_jpeg = buf.tobytes()
            except Exception:
                pass

            time.sleep(1 / 30)
    finally:
        try:
            if hands is not None:
                hands.close()
        finally:
            cap.release()


gesture_store = GestureStore(os.path.join(PROJECT_DIR, "data", "gestures.json"), TEMPLATE_EFFECTS)


# ===================== FLASK =====================
app = Flask(__name__,
            template_folder=os.path.join(PROJECT_DIR, "dashboard"),
            static_folder=os.path.join(PROJECT_DIR, "dashboard", "assets"),
            static_url_path="/assets")


@app.route("/")
def index():
    return render_template("led_panel.html")


def session_id():
    return request.headers.get("X-LED-Session") or request.args.get("session", "")


@app.before_request
def serialize_led_requests():
    if request.method == "POST" and request.path.startswith("/api/"):
        sessions.lock.acquire()
        request.environ["led_session_locked"] = True
        session = session_id()
        if not session or len(session) > 128:
            return jsonify({"error": "Нужна сессия LED-модуля"}), 400
        if request.path not in ("/api/enter", "/api/heartbeat", "/api/leave"):
            if not sessions.heartbeat(session):
                return jsonify({"error": "Сессия LED-модуля завершена"}), 409


@app.teardown_request
def unlock_led_request(error):
    if request.environ.pop("led_session_locked", False):
        sessions.lock.release()


@app.route("/api/enter", methods=["POST"])
def api_enter():
    try:
        ok = sessions.enter(session_id())
        return jsonify({"ok": ok}), 200 if ok else 409
    except Exception as error:
        app.logger.exception("LED activation failed")
        return jsonify({"error": str(error)}), 503


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    ok = sessions.heartbeat(session_id())
    return jsonify({"ok": ok}), 200 if ok else 409


@app.route("/api/leave", methods=["POST"])
def api_leave():
    owner = session_id()
    ctrl.recording.cancel(owner=owner)
    if ctrl.camera_owner == owner:
        ctrl.end_camera()
    sessions.leave(owner)
    return jsonify({"ok": True})


@app.route("/camera_feed")
def camera_feed():
    """MJPEG-стрим с камеры (multipart/x-mixed-replace)."""
    stop_event = ctrl.camera_stop
    if ctrl.camera_kind is None or stop_event.is_set():
        return jsonify({"error": "Камера не запущена"}), 404

    def generate():
        while not stop_event.is_set():
            with ctrl.frame_lock:
                frame = ctrl.last_frame_jpeg
            if frame is None:
                time.sleep(0.1)
                continue
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            time.sleep(1 / 25)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/api/status")
def api_status():
    try:
        gesture_names = list(gesture_store.snapshot())
    except (OSError, ValueError):
        gesture_names = []
    return jsonify({
        "mode": ctrl.mode,
        "color": list(ctrl.color),
        "template": ctrl.template,
        "camera_available": CAMERA_AVAILABLE,
        "gestures": gesture_names,
        "camera_running": ctrl.camera_kind is not None and not ctrl.camera_stop.is_set(),
        "camera_error": ctrl.camera_error,
        "fire_len": ctrl.fire_len,
        "water_len": ctrl.water_len,
    })


@app.route("/api/color", methods=["POST"])
def api_color():
    data = request.get_json(force=True)
    try:
        r = int(data["r"]); g = int(data["g"]); b = int(data["b"])
    except (KeyError, ValueError):
        return jsonify({"error": "bad color"}), 400
    r, g, b = (max(0, min(255, x)) for x in (r, g, b))
    sessions.claim(session_id())
    ctrl.set_static((r, g, b))
    return jsonify({"ok": True, "color": [r, g, b]})


@app.route("/api/template", methods=["POST"])
def api_template():
    data = request.get_json(force=True)
    name = data.get("name")
    if name not in TEMPLATE_EFFECTS:
        return jsonify({"error": "unknown template"}), 400
    sessions.claim(session_id())
    ctrl.set_template(name)
    return jsonify({"ok": True, "template": name})


@app.route("/api/fire_len", methods=["POST"])
def api_fire_len():
    data = request.get_json(force=True)
    try:
        n = int(data["value"])
    except (KeyError, ValueError):
        return jsonify({"error": "bad value"}), 400
    sessions.claim(session_id())
    ctrl.set_fire_len(n)
    return jsonify({"ok": True, "value": ctrl.fire_len})


@app.route("/api/water_len", methods=["POST"])
def api_water_len():
    data = request.get_json(force=True)
    try:
        n = int(data["value"])
    except (KeyError, ValueError):
        return jsonify({"error": "bad value"}), 400
    sessions.claim(session_id())
    ctrl.set_water_len(n)
    return jsonify({"ok": True, "value": ctrl.water_len})


@app.errorhandler(GestureConflict)
def gesture_conflict(error):
    return jsonify({"error": str(error)}), 409


@app.route("/api/gestures")
def api_gestures():
    try:
        return jsonify({"combinations": gesture_store.rows()})
    except (OSError, ValueError, KeyError):
        return jsonify({"error": "Не удалось прочитать файл комбинаций"}), 503


@app.route("/api/gestures/save", methods=["POST"])
def api_gesture_save():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Некорректные данные комбинации"}), 400
    try:
        gesture_store.save(data)
    except GestureConflict:
        raise
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except OSError:
        return jsonify({"error": "Не удалось сохранить файл комбинаций"}), 503
    return api_gestures()


@app.route("/api/gestures/delete", methods=["POST"])
def api_gesture_delete():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Некорректные данные комбинации"}), 400
    try:
        gesture_store.delete(data.get("name"), data.get("revision"))
    except GestureConflict:
        raise
    except (OSError, ValueError):
        return jsonify({"error": "Не удалось обновить файл комбинаций"}), 503
    return api_gestures()


@app.route("/api/gestures/record/start", methods=["POST"])
def api_record_start():
    if ctrl.recording.pending:
        raise GestureConflict("Запись комбинации уже идёт")
    owner = session_id()
    try:
        if (ctrl.camera_thread is None or not ctrl.camera_thread.is_alive()
                or ctrl.camera_stop.is_set()):
            ctrl.start_camera("preview", owner)
        elif ctrl.camera_kind == "preview":
            ctrl.camera_owner = owner
        if ctrl.camera_stop.is_set() and ctrl.camera_error:
            raise RuntimeError(ctrl.camera_error)
        ctrl.recording.start(owner)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        return jsonify({"error": str(error)}), 503
    return jsonify({"ok": True})


@app.route("/api/gestures/record/status", methods=["POST"])
def api_record_status():
    return jsonify(ctrl.recording.status(session_id()))


@app.route("/api/gestures/record/cancel", methods=["POST"])
def api_record_cancel():
    ctrl.recording.cancel(owner=session_id())
    if ctrl.camera_kind == "preview" and ctrl.camera_owner == session_id():
        ctrl.end_camera()
    return jsonify({"ok": True})


def start_camera_mode(kind):
    try:
        ctrl.start_camera(kind, session_id())
        try:
            sessions.claim(session_id())
        except Exception:
            ctrl.end_camera()
            raise
        with ctrl.lock:
            ctrl.mode = kind
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        if ctrl.camera_thread is None and ctrl.mode in ("tracking", "combo"):
            ctrl.end_camera()
        return jsonify({"error": str(error)}), 503
    return jsonify({"ok": True})


@app.route("/api/tracking/start", methods=["POST"])
def api_tracking_start():
    return start_camera_mode("tracking")


@app.route("/api/combo/start", methods=["POST"])
def api_combo_start():
    try:
        if not gesture_store.snapshot():
            return jsonify({"error": "Сначала добавьте комбинацию"}), 409
    except (OSError, ValueError):
        return jsonify({"error": "Не удалось прочитать файл комбинаций"}), 503
    return start_camera_mode("combo")


@app.route("/api/camera/stop", methods=["POST"])
def api_camera_stop():
    stopped = ctrl.end_camera()
    return jsonify({"ok": True, "stopped": stopped})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    sessions.claim(session_id())
    ctrl.set_static((0, 0, 0))
    return jsonify({"ok": True})


if __name__ == "__main__":
    def terminate(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    # Viewing a page leaves the background effect running; only a command claims SPI.
    start_led_bouncing()
    ctrl.start()
    try:
        app.run(host="0.0.0.0", port=3000, threaded=True, debug=False)
    finally:
        with sessions.lock:
            ctrl.deactivate()
