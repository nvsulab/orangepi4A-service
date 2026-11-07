import cv2
import mediapipe as mp
import spidev
import numpy as np
import json
import os
import time
import math
import random
from collections import Counter

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

    def _encode_byte(self, byte_val):
        bits = []
        for i in range(7, -1, -1):
            if (byte_val >> i) & 1:
                bits.append(self.bit1)
            else:
                bits.append(self.bit0)
        return np.concatenate(bits)

    def show(self, colors):
        data = []
        for r, g, b in colors:
            data.append(self._encode_byte(g))
            data.append(self._encode_byte(r))
            data.append(self._encode_byte(b))
        payload = np.concatenate(data).tolist()
        self.spi.xfer2(payload)

    def close(self):
        self.spi.close()

# ===================== MEDIAPIPE =====================
mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils

hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7
)

TIP_IDS = [4, 8, 12, 16, 20]
PIP_IDS = [3, 6, 10, 14, 18]

def get_finger_states(hand_landmarks, handedness_label):
    lm = hand_landmarks.landmark
    states = []
    if handedness_label == "Right":
        states.append(lm[4].x < lm[3].x)
    else:
        states.append(lm[4].x > lm[3].x)
    for i in range(1, 5):
        states.append(lm[TIP_IDS[i]].y < lm[PIP_IDS[i]].y)
    return states

# ===================== ЗОНЫ LED (по умолчанию) =====================
HAND_ZONES = [
    [(0, 7), (7, 14), (14, 21), (21, 28), (28, 36)],
    [(36, 43), (43, 50), (50, 57), (57, 64), (64, 72)],
]

COLOR_OPEN   = (0, 255, 0)
COLOR_CLOSED = (255, 0, 0)
COLOR_NO_HAND = (0, 0, 30)

def render_fingers(hands_states, num_leds=72):
    """Отрисовка состояния пальцев по зонам (базовый режим)."""
    colors = [(0, 0, 0)] * num_leds
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

# ===================== LED ЭФФЕКТЫ =====================
NUM_LEDS = 72

class EffectState:
    """Хранит состояние эффекта между кадрами."""
    def __init__(self):
        self.t = 0.0
        self.lightning = []  # список (start, end, r, g, b, expiry)

_effect_state = EffectState()

def effect_wave(t):
    """Волнующийся цвет: синусоида по всей ленте."""
    colors = []
    for i in range(NUM_LEDS):
        hue = (i * 0.05 + t * 2.0) % (2 * math.pi)
        r = int((math.sin(hue) * 0.5 + 0.5) * 255)
        g = int((math.sin(hue + 2.094) * 0.5 + 0.5) * 255)
        b = int((math.sin(hue + 4.188) * 0.5 + 0.5) * 255)
        colors.append((r, g, b))
    return colors

def effect_fire(t):
    """Огонь в начале ленты: первые 12 диодов горят, остальные потухают."""
    colors = [(0, 0, 0)] * NUM_LEDS
    fire_len = 12
    for i in range(fire_len):
        # Чем ближе к началу — тем ярче и краснее
        base = 1.0 - i / fire_len
        flicker = 0.5 + 0.5 * math.sin(t * 15 + i * 3.7) * math.sin(t * 7 + i * 1.3)
        intensity = max(0.2, base * flicker)
        r = int(255 * intensity)
        g = int(120 * intensity * intensity)
        b = 0
        colors[i] = (min(255, r), min(255, g), b)
    return colors

def effect_water(t):
    """Вода в начале ленты: сине-голубые волны с белым бризом."""
    colors = [(0, 0, 0)] * NUM_LEDS
    water_len = 18
    for i in range(water_len):
        wave = math.sin(t * 4 + i * 0.8) * 0.5 + 0.5
        r = int(20 * wave)
        g = int(80 * wave + 40)
        b = int(180 * wave + 60)
        # Белый бриз: случайные вспышки на гребнях
        if random.random() < 0.02 and wave > 0.8:
            r, g, b = 255, 255, 255
        colors[i] = (min(255, r), min(255, g), min(255, b))
    return colors

def effect_lightning(t):
    st = _effect_state
    # убираем устаревшие вспышки (индекс 5 — expiry)
    st.lightning = [l for l in st.lightning if l[5] > t]

    if random.random() < 0.15:
        start = random.randint(0, NUM_LEDS - 1)
        end = random.randint(start + 1, min(NUM_LEDS, start + 20))
        c = random.choice([(255, 255, 255), (200, 200, 255), (255, 240, 180)])
        # (start, end, r, g, b, expiry)
        st.lightning.append((start, end, c[0], c[1], c[2], t + 0.25))

    colors = [(0, 0, 0)] * NUM_LEDS
    for (start, end, r, g, b, _) in st.lightning:
        for i in range(start, end):
            colors[i] = (r, g, b)
    return colors

def effect_rainbow(t):
    """Переливающаяся радуга по всей ленте."""
    colors = []
    for i in range(NUM_LEDS):
        hue = (i / NUM_LEDS + t * 0.3) % 1.0
        # HSV -> RGB (упрощённо)
        h = hue * 6
        c = 1.0
        x = 1 - abs(h % 2 - 1)
        if h < 1: r, g, b = c, x, 0
        elif h < 2: r, g, b = x, c, 0
        elif h < 3: r, g, b = 0, c, x
        elif h < 4: r, g, b = 0, x, c
        elif h < 5: r, g, b = x, 0, c
        else: r, g, b = c, 0, x
        colors.append((int(r * 255), int(g * 255), int(b * 255)))
    return colors

EFFECTS = {
    "wave":      ("Волна",   effect_wave),
    "fire":      ("Огонь",   effect_fire),
    "water":     ("Вода",    effect_water),
    "lightning": ("Молния",  effect_lightning),
    "rainbow":   ("Радуга",  effect_rainbow),
}

def render_effect(effect_key, dt):
    """Обновляет состояние эффекта и возвращает цвета."""
    if effect_key not in EFFECTS:
        return [(0, 0, 0)] * NUM_LEDS
    _effect_state.t += dt
    return EFFECTS[effect_key][1](_effect_state.t)

# ===================== ЖЕСТЫ =====================
GESTURES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "data", "gestures.json")

def load_gestures():
    if os.path.exists(GESTURES_FILE):
        with open(GESTURES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_gestures(data):
    with open(GESTURES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def state_to_key(states):
    parts = []
    for hand in states:
        if hand is None:
            parts.append("NA")
        else:
            parts.append("".join("1" if s else "0" for s in hand))
    return "|".join(parts)

def compare_states(a, b):
    total = 0
    match = 0
    for hand_a, hand_b in zip(a, b):
        if hand_a is None and hand_b is None:
            continue
        if hand_a is None or hand_b is None:
            total += 5
            continue
        for sa, sb in zip(hand_a, hand_b):
            total += 1
            if sa == sb:
                match += 1
    return match / total if total > 0 else 0.0

def median_states(samples):
    result = []
    for hand_idx in range(2):
        hand_samples = [s[hand_idx] for s in samples if s[hand_idx] is not None]
        if not hand_samples:
            result.append(None)
            continue
        finger_median = []
        for finger_idx in range(5):
            votes = [h[finger_idx] for h in hand_samples]
            finger_median.append(Counter(votes).most_common(1)[0][0])
        result.append(finger_median)
    return result

# ===================== КОНСОЛЬНЫЕ ФУНКЦИИ =====================
def print_gesture_list(gestures):
    if not gestures:
        print("\n📭 Список жестов пуст.\n")
        return
    print("\n=== 📋 Список жестов ===")
    for idx, (name, data) in enumerate(gestures.items(), 1):
        effect = data.get("effect", "—")
        effect_name = EFFECTS.get(effect, ("—",))[0] if effect != "—" else "—"
        print(f"  {idx}. {name}  [эффект: {effect_name}]")
    print()

def delete_gesture_interactive(gestures):
    if not gestures:
        print("\n📭 Список жестов пуст, нечего удалять.\n")
        return
    names = list(gestures.keys())
    print("\n=== 🗑 Удаление жеста ===")
    for idx, name in enumerate(names, 1):
        print(f"  {idx}. {name}")
    choice = input("Введите номер для удаления (или Enter для отмены): ").strip()
    if not choice:
        print("Отменено.\n")
        return
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(names):
            name = names[idx]
            del gestures[name]
            save_gestures(gestures)
            print(f"✔ Жест '{name}' удалён. Осталось: {len(gestures)}\n")
        else:
            print("✖ Неверный номер.\n")
    except ValueError:
        print("✖ Введите число.\n")

# ===================== ОСНОВНОЙ ЦИКЛ =====================
if __name__ == "__main__":
    led = WS2812SPI(bus=1, device=0, num_leds=NUM_LEDS, spi_speed_hz=2400000)

    cap = cv2.VideoCapture(1)
    if not cap.isOpened():
        print("Ошибка: не удалось открыть камеру /dev/video1")
        led.close()
        exit()

    gestures = load_gestures()
    print(f"Загружено жестов: {len(gestures)}")
    print("Клавиши: R — записать жест, L — список, D — удалить, q — выход.")

    recording = False
    record_start = 0
    record_samples = []
    RECORD_SECONDS = 5.0

    last_gesture_name = None
    stable_counter = 0
    STABLE_FRAMES_REQUIRED = 5
    MATCH_THRESHOLD = 0.8

    active_effect = None  # ключ эффекта, который сейчас проигрывается
    prev_time = time.time()

    while True:
        now = time.time()
        dt = now - prev_time
        prev_time = now

        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands.process(rgb_frame)

        detected = []
        if results.multi_hand_landmarks:
            for hand_landmarks, handedness in zip(
                results.multi_hand_landmarks, results.multi_handedness
            ):
                label = handedness.classification[0].label
                states = get_finger_states(hand_landmarks, label)
                cx = hand_landmarks.landmark[0].x
                detected.append((cx, states, hand_landmarks, label))

        detected.sort(key=lambda item: item[0])

        hands_states = [None, None]
        for hand_idx, (cx, states, hand_landmarks, label) in enumerate(detected[:2]):
            hands_states[hand_idx] = states
            mp_draw.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)

        # --- Запись жеста ---
        if recording:
            elapsed = now - record_start
            remaining = max(0, RECORD_SECONDS - elapsed)
            cv2.putText(frame, f"RECORDING: {remaining:.1f}s",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
            if hands_states[0] is not None or hands_states[1] is not None:
                record_samples.append((hands_states[0], hands_states[1]))

            if elapsed >= RECORD_SECONDS:
                recording = False
                if not record_samples:
                    print("⚠ Жест не записан: не было видно рук.")
                else:
                    final_state = median_states(record_samples)
                    print("\n=== Запись жеста завершена ===")
                    print(f"Состояние: {state_to_key(final_state)}")
                    name = input("Введите название жеста (Enter — отмена): ").strip()
                    if name:
                        # Выбор эффекта
                        print("\nДоступные эффекты:")
                        for i, (key, (title, _)) in enumerate(EFFECTS.items(), 1):
                            print(f"  {i}. {title} ({key})")
                        print("  0. Без эффекта (показывать состояние пальцев)")
                        eff_choice = input("Выберите эффект (номер, Enter = без эффекта): ").strip()
                        effect_key = None
                        if eff_choice:
                            try:
                                eff_idx = int(eff_choice)
                                if 1 <= eff_idx <= len(EFFECTS):
                                    effect_key = list(EFFECTS.keys())[eff_idx - 1]
                            except ValueError:
                                pass
                        gestures[name] = {"state": final_state, "effect": effect_key}
                        save_gestures(gestures)
                        eff_display = EFFECTS[effect_key][0] if effect_key else "без эффекта"
                        print(f"✔ Жест '{name}' сохранён (эффект: {eff_display}).")
                    else:
                        print("✖ Отмена.")
                record_samples = []

            cv2.imshow('Hand Gestures', frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            continue

        # --- Распознавание ---
        current_name = None
        current_match = 0.0
        for name, data in gestures.items():
            score = compare_states(hands_states, data["state"])
            if score > current_match:
                current_match = score
                current_name = name

        if current_name is not None and current_match >= MATCH_THRESHOLD:
            if current_name == last_gesture_name:
                stable_counter += 1
            else:
                last_gesture_name = current_name
                stable_counter = 1
        else:
            if stable_counter > 0:
                stable_counter -= 1
            if stable_counter == 0:
                last_gesture_name = None

        if last_gesture_name is not None and stable_counter >= STABLE_FRAMES_REQUIRED:
            cv2.putText(frame, f"GESTURE: {last_gesture_name}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
            active_effect = gestures[last_gesture_name].get("effect")
        else:
            if current_name is not None:
                cv2.putText(frame, f"~{current_name} ({current_match*100:.0f}%)",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
            else:
                cv2.putText(frame, "No gesture",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (100, 100, 100), 2)
            active_effect = None

        # --- Отрисовка LED ---
        if active_effect and active_effect in EFFECTS:
            colors = render_effect(active_effect, dt)
        else:
            colors = render_fingers(hands_states)
        led.show(colors)

        cv2.imshow('Hand Gestures', frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('r') or key == ord('R'):
            recording = True
            record_start = time.time()
            record_samples = []
            print("\n⏺ Запись жеста началась. Покажите жест в течение 5 секунд...")
        elif key == ord('l') or key == ord('L'):
            print_gesture_list(gestures)
        elif key == ord('d') or key == ord('D'):
            delete_gesture_interactive(gestures)

    cap.release()
    cv2.destroyAllWindows()
    led.show([(0, 0, 0)] * NUM_LEDS)
    led.close()
    print("Программа завершена.")
