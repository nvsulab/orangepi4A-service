import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "led-panel"))
from gestures import GestureConflict, GestureRecording, GestureStore

OPEN = [True] * 5
CLOSED = [False] * 5


class GestureStoreTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "gestures.json"
        self.legacy = {"Открытая ладонь": {"state": [OPEN, None], "effect": "wave"}}
        self.path.write_text(json.dumps(self.legacy), encoding="utf-8")
        self.store = GestureStore(self.path, {"wave", "fire"})

    def test_create_edit_delete_and_reload_legacy(self):
        before = self.store.snapshot()
        self.store.save({"name": "Кулаки", "state": [CLOSED, CLOSED], "color": [9, 8, 7]})
        self.assertEqual(before, self.legacy)
        row = self.store.rows()[1]
        self.store.save(dict(row, original=row["name"], name="Один кулак",
                             state=[None, CLOSED], color=None, effect="fire"))
        self.assertEqual(self.store.snapshot()["Один кулак"], {"state": [CLOSED, None], "effect": "fire"})
        renamed = self.store.rows()[1]
        self.store.delete(renamed["name"], renamed["revision"])
        self.assertEqual(GestureStore(self.path, {"wave", "fire"}).snapshot(), self.legacy)

    def test_duplicate_and_stale_revision(self):
        with self.assertRaisesRegex(GestureConflict, "уже используется"):
            self.store.save({"name": "открытая ладонь", "state": [CLOSED, None], "effect": "fire"})
        with self.assertRaisesRegex(GestureConflict, "уже сохранена"):
            self.store.save({"name": "Дубликат", "state": [None, OPEN], "effect": "fire"})
        row = self.store.rows()[0]
        self.store.save(dict(row, original=row["name"], effect="fire"))
        with self.assertRaisesRegex(GestureConflict, "уже изменена"):
            self.store.delete(row["name"], row["revision"])

    def test_invalid_state_and_color_leave_file_intact(self):
        before = self.path.read_bytes()
        for extra in [{"state": [None, None]}, {"state": [[1] * 5, None]},
                      {"color": [256, 0, 0]}, {"effect": "wave", "color": [1, 2, 3]}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.store.save(dict({"name": "Новый", "state": [CLOSED, None]}, **extra))
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_atomic_replace_preserves_snapshot_and_file(self):
        before = self.path.read_bytes()
        with patch("gestures.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.save({"name": "Новый", "state": [CLOSED, None], "effect": "wave"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.snapshot(), self.legacy)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class GestureRecordingTest(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.recording = GestureRecording(clock=lambda: self.now)
        self.recording.start("page")

    def test_timer_starts_with_first_frame_and_keeps_most_stable_pose(self):
        self.now = 2
        self.assertEqual(self.recording.status("page")["status"], "warming")
        for index in range(10):
            self.now = 2 + index / 2
            self.recording.observe([OPEN, CLOSED] if index != 3 else [None, None])
        self.assertEqual(self.recording.status("page")["remaining"], .5)
        self.now = 7
        self.assertEqual(self.recording.status("page"), {"status": "done", "state": (tuple(OPEN), tuple(CLOSED))})
        self.assertEqual(self.recording.status("other"), {"status": "idle"})

    def test_unstable_or_stalled_capture_is_rejected(self):
        for stalled in [False, True]:
            self.now = 0
            self.recording = GestureRecording(clock=lambda: self.now)
            self.recording.start("page")
            for index in range(10):
                self.now = index / (20 if stalled else 2)
                self.recording.observe([OPEN if stalled or index % 2 else CLOSED, None])
            self.now = 5
            self.assertEqual(self.recording.status("page")["status"], "failed")

    def test_timeout_and_owner_cancellation(self):
        self.recording.cancel(owner="other")
        self.assertTrue(self.recording.pending)
        self.recording.cancel(owner="page")
        self.assertEqual(self.recording.status("page")["status"], "cancelled")
        self.recording.start("new")
        self.now = 12
        self.assertEqual(self.recording.status("new")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
