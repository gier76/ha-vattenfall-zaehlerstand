#!/usr/bin/env python3
"""Minimaler Home-Assistant-Ersatz für lokale Tests (nur Standardbibliothek).

Env: MOCK_PORT (Standard 18123), MOCK_POST_LOG (Datei für empfangene POSTs).
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

STATES = [
    {"entity_id": "sensor.stromzaehler", "state": "12345.678",
     "attributes": {"friendly_name": "Stromzähler", "unit_of_measurement": "kWh", "device_class": "energy"}},
    {"entity_id": "sensor.strom_wh", "state": "9876543",
     "attributes": {"friendly_name": "Strom Wh", "unit_of_measurement": "Wh"}},
    {"entity_id": "sensor.gaszahler_m3", "state": "4321.9",
     "attributes": {"friendly_name": "Gaszähler", "unit_of_measurement": "m³", "device_class": "gas"}},
    {"entity_id": "sensor.gas_kwh", "state": "500",
     "attributes": {"friendly_name": "Gas kWh", "unit_of_measurement": "kWh"}},
    {"entity_id": "sensor.temperatur", "state": "21.5",
     "attributes": {"friendly_name": "Temperatur", "unit_of_measurement": "°C"}},
    {"entity_id": "sensor.text", "state": "hallo", "attributes": {"friendly_name": "Text"}},
    {"entity_id": "light.x", "state": "on", "attributes": {}},
]
POST_LOG = os.environ.get("MOCK_POST_LOG", "mock_posts.log")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/states":
            return self._json(STATES)
        if self.path.startswith("/states/"):
            for s in STATES:
                if s["entity_id"] == self.path[len("/states/"):]:
                    return self._json(s)
        if self.path == "/services":
            return self._json([
                {"domain": "notify", "services": {"mobile_app_handy": {}, "send_message": {}}},
                {"domain": "light", "services": {}},
            ])
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        with open(POST_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{self.path} {body}\n")
        self._json({})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(os.environ.get("MOCK_PORT", "18123"))), Handler).serve_forever()
