"""Persistent combinations and the five-second capture shared with the camera."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from collections import Counter


class GestureConflict(ValueError):
    pass


class GestureStore:
    def __init__(self, path, effects):
        self.path = Path(path)
        self.effects = effects
        self.lock = threading.RLock()
        self.entries = None

    def snapshot(self):
        with self.lock:
            if self.entries is None:
                try:
                    with self.path.open(encoding="utf-8") as source:
                        entries = json.load(source)
                except FileNotFoundError:
                    entries = {}
                if not isinstance(entries, dict):
                    raise ValueError("Файл комбинаций должен содержать словарь")
                self.entries = entries
            return self.entries

    @staticmethod
    def revision(entry):
        payload = json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(payload).hexdigest()

    def rows(self):
        return [{"name": name, "revision": self.revision(entry),
                 "state": entry["state"], "effect": entry.get("effect"),
                 "color": entry.get("color")}
                for name, entry in self.snapshot().items()]

    def save(self, data):
        name = data.get("name")
        state = data.get("state")
        effect = data.get("effect")
        color = data.get("color")
        original = data.get("original")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
            raise ValueError("Введите название длиной от 1 до 64 символов")
        name = name.strip()
        if (not isinstance(state, list) or len(state) != 2
                or all(hand is None for hand in state)
                or any(hand is not None and (not isinstance(hand, list)
                       or len(hand) != 5 or any(type(value) is not bool for value in hand))
                       for hand in state)):
            raise ValueError("Укажите хотя бы одну ладонь и пять состояний пальцев")
        if state[0] is None:
            state = [state[1], None]
        if effect is not None and (not isinstance(effect, str) or effect not in self.effects):
            raise ValueError("Выберите доступный узор")
        if color is not None and (effect is not None or not isinstance(color, list)
                or len(color) != 3 or any(type(value) is not int or not 0 <= value <= 255
                                         for value in color)):
            raise ValueError("Выберите один узор или корректный RGB-цвет")
        with self.lock:
            entries = self.snapshot()
            if original is not None:
                self._check_revision(entries, original, data.get("revision"))
            for other, entry in entries.items():
                if other == original:
                    continue
                if other.casefold() == name.casefold():
                    raise GestureConflict(f"Название «{other}» уже используется")
                if entry["state"] == state:
                    raise GestureConflict(f"Эта схема уже сохранена как «{other}»")
            updated = {"state": state, "effect": effect}
            if color is not None:
                updated["color"] = color
            if original is None:
                entries = {**entries, name: updated}
            else:
                entries = {(name if key == original else key): (updated if key == original else entry)
                           for key, entry in entries.items()}
            self._write(entries)

    def delete(self, name, revision):
        with self.lock:
            entries = self.snapshot()
            self._check_revision(entries, name, revision)
            self._write({key: entry for key, entry in entries.items() if key != name})

    def _check_revision(self, entries, name, revision):
        if not isinstance(name, str) or name not in entries or revision != self.revision(entries[name]):
            raise GestureConflict("Комбинация уже изменена. Обновите список и повторите действие")

    def _write(self, entries):
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=self.path.parent, delete=False) as target:
                temp_path = Path(target.name)
                json.dump(entries, target, ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            try:
                original = self.path.stat()
                if os.geteuid() == 0:
                    os.chown(temp_path, original.st_uid, original.st_gid)
                temp_path.chmod(original.st_mode & 0o777)
            except FileNotFoundError:
                temp_path.chmod(0o644)
            os.replace(temp_path, self.path)
            self.entries = entries
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


class GestureRecording:
    DURATION = 5
    WARMUP_TIMEOUT = 12

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.owner = None
        self.result = None

    def start(self, owner):
        with self.lock:
            if self.pending:
                raise GestureConflict("Запись комбинации уже идёт")
            self.owner = owner
            self.requested_at = self.clock()
            self.started_at = None
            self.last_frame_at = None
            self.result = None
            self.samples = Counter()

    @property
    def pending(self):
        with self.lock:
            return self.owner is not None and self.result is None

    def observe(self, hands):
        with self.lock:
            if not self.pending:
                return
            now = self.clock()
            self._finish_if_due(now)
            if self.result is not None:
                return
            if self.started_at is None:
                self.started_at = now
            self.last_frame_at = now
            self.samples[tuple(tuple(hand) if hand is not None else None for hand in hands)] += 1

    def cancel(self, message="Запись отменена", owner=None):
        with self.lock:
            if self.pending and (owner is None or owner == self.owner):
                self.result = {"status": "cancelled", "error": message}

    def status(self, owner=None):
        with self.lock:
            if self.owner is None or (owner is not None and owner != self.owner):
                return {"status": "idle"}
            now = self.clock()
            self._finish_if_due(now)
            if self.result is not None:
                return self.result
            if self.started_at is None:
                return {"status": "warming"}
            return {"status": "recording", "remaining": self.DURATION - (now - self.started_at)}

    def _finish_if_due(self, now):
        if self.result is not None:
            return
        if self.started_at is None:
            if now - self.requested_at >= self.WARMUP_TIMEOUT:
                self.result = {"status": "failed", "error": "Камера не передала кадры. Повторите запись"}
            return
        if now - self.started_at < self.DURATION:
            return
        visible = [(state, count) for state, count in self.samples.most_common()
                   if any(hand is not None for hand in state)]
        if visible:
            state, count = visible[0]
            if (count >= 5 and count >= 0.6 * sum(self.samples.values())
                    and self.last_frame_at - self.started_at >= self.DURATION - 1.5):
                self.result = {"status": "done", "state": state}
                return
        self.result = {"status": "failed", "error": "Не удалось записать устойчивый жест. Повторите запись"}
