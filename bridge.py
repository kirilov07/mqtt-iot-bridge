#!/usr/bin/env python3
"""
mqtt-iot-bridge — subscribe to MQTT topics and forward to a REST API or log to file.

Supports:
  - JSON payloads:   {"temp": 24.5, "hum": 61.2}
  - Key:value pairs: temp:24.5,hum:61.2

Usage:
    python bridge.py --broker localhost --api http://localhost:8000 --token YOUR_JWT
"""

import argparse
import json
import logging
import re
import signal
import sys
import time
from datetime import datetime
from typing import Optional

import paho.mqtt.client as mqtt
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bridge")


# ── Payload parsing ───────────────────────────────────────────────────────────

def parse_payload(raw: str) -> Optional[dict]:
    """Parse JSON or key:value payload into a dict of {field: float}."""
    raw = raw.strip()
    # Try JSON first
    try:
        obj = json.loads(raw)
        return {k: float(v) for k, v in obj.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back to key:value
    result = {}
    for part in re.split(r"[,;]", raw):
        m = re.match(r"^\s*([A-Za-z_]\w*)\s*[:=]\s*([+-]?\d+\.?\d*)\s*$", part)
        if m:
            try:
                result[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return result if result else None


# ── REST forwarder ────────────────────────────────────────────────────────────

class APIForwarder:
    def __init__(self, base_url: str, token: str, timeout: int = 5):
        self.url     = base_url.rstrip("/") + "/readings/"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.timeout = timeout
        self.queue   = []
        self.flush_every = 10  # batch size before forced flush

    def add(self, fields: dict):
        for field, value in fields.items():
            self.queue.append({"field": field, "value": value})
        if len(self.queue) >= self.flush_every:
            self.flush()

    def flush(self):
        if not self.queue:
            return
        payload = {"readings": self.queue}
        try:
            r = requests.post(self.url, json=payload, headers=self.headers, timeout=self.timeout)
            if r.status_code == 201:
                log.info("Forwarded %d reading(s) → API", len(self.queue))
            else:
                log.warning("API returned %s: %s", r.status_code, r.text[:120])
        except requests.RequestException as e:
            log.error("API unreachable: %s", e)
        finally:
            self.queue = []


# ── MQTT callbacks ────────────────────────────────────────────────────────────

def make_callbacks(forwarder: Optional[APIForwarder], log_file: Optional[str]):
    fh = open(log_file, "a") if log_file else None

    def on_connect(client, userdata, flags, rc):
        codes = {0: "OK", 1: "Bad protocol", 2: "Bad client ID",
                 3: "Broker unavailable", 4: "Bad credentials", 5: "Not authorised"}
        if rc == 0:
            log.info("Connected to broker")
        else:
            log.error("Connection refused: %s", codes.get(rc, rc))

    def on_message(client, userdata, msg):
        topic   = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace")
        ts      = datetime.utcnow().isoformat()
        log.debug("MQTT  [%s] %s", topic, payload)

        fields = parse_payload(payload)
        if not fields:
            log.warning("Could not parse payload on %s: %s", topic, payload[:80])
            return

        log.info("[%s] %s → %s", ts, topic, fields)

        if fh:
            fh.write(f"{ts},{topic},{json.dumps(fields)}\n")
            fh.flush()

        if forwarder:
            forwarder.add(fields)

    def on_disconnect(client, userdata, rc):
        if rc != 0:
            log.warning("Unexpected disconnect (rc=%s) — will reconnect", rc)

    return on_connect, on_message, on_disconnect


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="MQTT → REST API bridge")
    ap.add_argument("--broker",   default="localhost",        help="MQTT broker host")
    ap.add_argument("--port",     default=1883, type=int,     help="MQTT broker port")
    ap.add_argument("--topic",    default="sensors/#",        help="MQTT topic to subscribe (default: sensors/#)")
    ap.add_argument("--user",     default=None,               help="MQTT username")
    ap.add_argument("--password", default=None,               help="MQTT password")
    ap.add_argument("--api",      default=None,               help="REST API base URL (optional)")
    ap.add_argument("--token",    default=None,               help="JWT token for API auth")
    ap.add_argument("--log-file", default=None,               help="CSV file to log readings")
    ap.add_argument("--debug",    action="store_true",        help="Enable debug logging")
    args = ap.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    forwarder = APIForwarder(args.api, args.token) if args.api and args.token else None

    client = mqtt.Client()
    if args.user:
        client.username_pw_set(args.user, args.password)

    on_connect, on_message, on_disconnect = make_callbacks(forwarder, args.log_file)
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    def shutdown(sig, frame):
        log.info("Shutting down…")
        if forwarder:
            forwarder.flush()
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    client.connect(args.broker, args.port, keepalive=60)
    client.subscribe(args.topic)
    log.info("Subscribed to %s on %s:%s", args.topic, args.broker, args.port)

    # Periodic flush every 30 s
    def flush_loop():
        while True:
            time.sleep(30)
            if forwarder:
                forwarder.flush()

    import threading
    threading.Thread(target=flush_loop, daemon=True).start()

    client.loop_forever()


if __name__ == "__main__":
    main()
