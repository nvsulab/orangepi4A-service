#!/usr/bin/env python3
import spidev
import time
import os
import signal
import threading
from datetime import datetime


class LEDController:
    def __init__(self, bus=1, cs=0, num_leds=72):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, cs)
        self.spi.max_speed_hz = 3_200_000
        self.NUM_LEDS = num_leds

        # Stop flag — set by SIGTERM/SIGINT handler
        self._stop = threading.Event()
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        print(f"Received signal {signum}, shutting down...", flush=True)
        self._stop.set()

    def encode_ws281x(self, colors):
        """Convert list of (r,g,b) to SPI data"""
        data = bytearray()
        data.extend([0] * 24)  # Reset pulse

        for r, g, b in colors:
            for byte in (g, r, b):
                encoded = 0
                for bit in range(8):
                    if (byte >> bit) & 1:
                        encoded |= (0b110) << (bit * 3)
                    else:
                        encoded |= (0b100) << (bit * 3)
                data.append((encoded >> 16) & 0xFF)
                data.append((encoded >> 8) & 0xFF)
                data.append(encoded & 0xFF)
        return data

    def set_all(self, color):
        """Set all LEDs to same color"""
        colors = [color] * self.NUM_LEDS
        self.spi.xfer3(self.encode_ws281x(colors))

    def startup_then_bounce(self, start_color, bounce_color,
                            start_sec, delay_ms):
        """
        1. Fill strip with start_color for start_sec seconds.
        2. Then run bouncing single-LED effect with fixed bounce_color.
        Both phases are interruptible by SIGTERM/SIGINT.
        """
        print(f"LED Service Started at {datetime.now()}", flush=True)
        print(f"Bouncing {bounce_color}, delay {delay_ms}ms", flush=True)

        try:
            # Resume directly with the moving pixel; never fill the strip on startup.
            # Phase 2: bouncing light with fixed color
            direction = 1
            position = 0
            delay = delay_ms / 1000.0

            while not self._stop.is_set():
                colors = [(0, 0, 0)] * self.NUM_LEDS
                colors[position] = bounce_color
                self.spi.xfer3(self.encode_ws281x(colors))

                if self._stop.wait(delay):
                    break

                position += direction
                if position >= self.NUM_LEDS - 1:
                    position = self.NUM_LEDS - 1
                    direction = -1
                elif position <= 0:
                    position = 0
                    direction = 1

        finally:
            self.cleanup()

    def cleanup(self):
        """Turn off LEDs and close SPI"""
        print(f"\nLED Service Stopped at {datetime.now()}", flush=True)
        try:
            self.set_all((0, 0, 0))
        except Exception as e:
            print(f"cleanup: failed to blank LEDs: {e}", flush=True)
        finally:
            try:
                self.spi.close()
            except Exception as e:
                print(f"cleanup: failed to close SPI: {e}", flush=True)


def parse_color(value, default):
    """Parse 'R,G,B' string from env into tuple."""
    if not value:
        return default
    try:
        r, g, b = (int(x) for x in value.split(","))
        return (r, g, b)
    except Exception:
        return default


def main():
    num_leds = int(os.environ.get("LED_COUNT", 72))
    start_sec = float(os.environ.get("LED_START_SEC", 2))
    delay_ms = int(os.environ.get("LED_DELAY_MS", 40))
    start_color = parse_color(os.environ.get("LED_START_COLOR"), (255, 0, 0))
    bounce_color = parse_color(os.environ.get("LED_BOUNCE_COLOR"), (255, 128, 0))

    controller = LEDController(num_leds=num_leds)
    controller.startup_then_bounce(
        start_color=start_color,
        bounce_color=bounce_color,
        start_sec=start_sec,
        delay_ms=delay_ms,
    )


if __name__ == "__main__":
    main()
