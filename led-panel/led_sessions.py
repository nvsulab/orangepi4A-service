"""Exclusive strip ownership shared by live browser pages."""
import threading
import time


class LEDSessions:
    def __init__(self, acquire, release, timeout=30, clock=time.monotonic):
        self.acquire = acquire
        self.release = release
        self.timeout = timeout
        self.clock = clock
        self.lock = threading.RLock()
        self.clients = {}
        self.closed = {}
        self.engaged = False
        self.owners = set()

    def enter(self, session):
        with self.lock:
            if session in self.closed:
                return False
            self.clients[session] = self.clock()
            return True

    def claim(self, session):
        with self.lock:
            if not self.heartbeat(session):
                raise RuntimeError("Сессия LED-модуля завершена")
            if self.engaged and not self.owners:
                self.release()
                self.engaged = False
            if not self.engaged:
                self.acquire()
                self.engaged = True
            self.owners.add(session)

    def heartbeat(self, session):
        with self.lock:
            if session not in self.clients:
                return False
            self.clients[session] = self.clock()
            return True

    def leave(self, session):
        with self.lock:
            # A delayed enter/heartbeat must not resurrect a departed page.
            self.closed[session] = self.clock()
            self.clients.pop(session, None)
            self.owners.discard(session)
            self._release_if_empty()

    def expire(self):
        with self.lock:
            now = self.clock()
            self.clients = {key: seen for key, seen in self.clients.items()
                            if now - seen < self.timeout}
            self.closed = {key: seen for key, seen in self.closed.items()
                           if now - seen < self.timeout * 2}
            self.owners.intersection_update(self.clients)
            self._release_if_empty()

    def release_control(self):
        """Return the strip to the background service when the camera mode stops."""
        with self.lock:
            self.owners.clear()
            self._release_if_empty()

    def _release_if_empty(self):
        if not self.owners and self.engaged:
            self.release()
            self.engaged = False
