#!/usr/bin/env python3
"""Vattenfall Zählerstand – Home-Assistant-Add-on.

Liest Zählerstände aus frei wählbaren Home-Assistant-Sensoren und trägt sie ins
öffentliche Vattenfall-Formular ein. Einstellungen werden in einer kleinen
Weboberfläche (Home-Assistant-Ingress, Seitenleiste) vorgenommen; die Sensoren
erscheinen dort als Dropdown, direkt aus Home Assistant gelesen.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.request
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from form import SHOT_DIR, mask, submit_reading

APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = Path(os.environ.get("SETTINGS_PATH", "/data/settings.json"))
STATE_PATH = Path(os.environ.get("STATE_PATH", "/data/state.json"))

HA_URL = os.environ.get("HA_URL", "http://supervisor/core/api").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN", "")

PORT = int(os.environ.get("PORT", "8099"))
BIND = os.environ.get("BIND", "0.0.0.0")
# Ingress-Anfragen kommen ausschließlich vom Supervisor-Proxy.
# HA-Container (Ingress) und Supervisor-Gateway (Proxy) – beide nur im Docker-Netz erreichbar.
ALLOWED_IPS = set(filter(None, os.environ.get("ALLOWED_IPS", "172.30.32.2,172.30.32.1").split(",")))

# Zeitplan und Meldungen in deutscher Lokalzeit (Container laufen sonst UTC).
try:
    TZ = ZoneInfo("Europe/Berlin")
except Exception:  # ohne tzdata im Image -> Systemzeit (Dockerfile setzt TZ)
    TZ = None

log = logging.getLogger("vattenfall")
LOG_LINES: collections.deque[str] = collections.deque(maxlen=300)

KINDS = (("electricity", "Strom"), ("gas", "Gas"))

DEFAULTS: dict = {
    "postal_code": "",
    "username": "",                # Portal-Login (seit 2026 Pflicht fuer Zählerstand)
    "password": "",                # nur in /data/settings.json, nie geloggt/geechoert
    "schedule_enabled": False,
    "auto_submit": False,          # automatische Läufe: False = nur Testlauf
    "run_day": 1,
    "run_time": "08:00",
    "months": list(range(1, 13)),
    "notify_service": "",
    "headless": True,
    "electricity": {"enabled": True, "contract_account": "", "sensor": "", "decimals": 0},
    "gas": {"enabled": True, "contract_account": "", "sensor": "", "decimals": 0},
}

SENSOR_RE = re.compile(r"^sensor\.[a-z0-9_]+$")
NOTIFY_RE = re.compile(r"^notify\.[a-z0-9_]+$")
PLZ_RE = re.compile(r"^\d{5}$")
ACCOUNT_RE = re.compile(r"^[0-9A-Za-z\- ]{0,30}$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


# --------------------------------------------------------------------------- #
# Einstellungen
# --------------------------------------------------------------------------- #

def validate_settings(data: dict) -> dict:
    """Prüft Eingaben aus der Oberfläche und liefert ein bereinigtes Dict."""
    if not isinstance(data, dict):
        raise ValueError("Ungültige Einstellungen")
    out = json.loads(json.dumps(DEFAULTS))  # tiefe Kopie

    plz = str(data.get("postal_code", "")).strip()
    if plz and not PLZ_RE.match(plz):
        raise ValueError("Postleitzahl muss aus 5 Ziffern bestehen")
    out["postal_code"] = plz

    out["schedule_enabled"] = bool(data.get("schedule_enabled", False))
    out["auto_submit"] = bool(data.get("auto_submit", False))
    out["headless"] = bool(data.get("headless", True))

    try:
        day = int(data.get("run_day", 1))
    except (TypeError, ValueError):
        raise ValueError("Tag im Monat muss eine Zahl sein") from None
    if not 1 <= day <= 28:
        raise ValueError("Tag im Monat muss zwischen 1 und 28 liegen")
    out["run_day"] = day

    run_time = str(data.get("run_time", "08:00"))
    if not TIME_RE.match(run_time):
        raise ValueError("Uhrzeit muss im Format HH:MM angegeben werden")
    out["run_time"] = run_time

    months = data.get("months", list(range(1, 13)))
    if not isinstance(months, list) or not all(isinstance(m, int) and 1 <= m <= 12 for m in months):
        raise ValueError("Monate ungültig")
    out["months"] = sorted(set(months))

    notify = str(data.get("notify_service", "")).strip()
    if notify and not NOTIFY_RE.match(notify):
        raise ValueError("Notify-Dienst ungültig")
    out["notify_service"] = notify

    username = str(data.get("username", "")).strip()
    if username and (len(username) > 64 or any(ord(c) < 32 for c in username)):
        raise ValueError("Benutzername ungültig")
    out["username"] = username

    password = str(data.get("password", ""))
    if password and (len(password) > 128 or any(ord(c) < 32 for c in password)):
        raise ValueError("Passwort ungültig")
    out["password"] = password

    for kind, label in KINDS:
        cfg = data.get(kind) or {}
        account = str(cfg.get("contract_account", "")).strip()
        if not ACCOUNT_RE.match(account):
            raise ValueError(f"Vertragskontonummer ({label}) enthält ungültige Zeichen")
        sensor = str(cfg.get("sensor", "")).strip()
        if sensor and not SENSOR_RE.match(sensor):
            raise ValueError(f"Sensor ({label}) ungültig")
        try:
            decimals = int(cfg.get("decimals", 0))
        except (TypeError, ValueError):
            raise ValueError(f"Nachkommastellen ({label}) ungültig") from None
        if not 0 <= decimals <= 3:
            raise ValueError(f"Nachkommastellen ({label}) müssen zwischen 0 und 3 liegen")
        out[kind] = {
            "enabled": bool(cfg.get("enabled", False)),
            "contract_account": account,
            "sensor": sensor,
            "decimals": decimals,
        }
    return out


def load_settings() -> dict:
    try:
        return validate_settings(json.loads(SETTINGS_PATH.read_text()))
    except FileNotFoundError:
        return json.loads(json.dumps(DEFAULTS))
    except Exception as exc:
        log.warning("Einstellungen unlesbar, nutze Standardwerte: %s", exc)
        return json.loads(json.dumps(DEFAULTS))


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
    tmp.replace(SETTINGS_PATH)


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        log.warning("State konnte nicht gespeichert werden: %s", exc)


# --------------------------------------------------------------------------- #
# Home Assistant
# --------------------------------------------------------------------------- #

def ha_request(path: str, payload: dict | None = None):
    req = urllib.request.Request(
        f"{HA_URL}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode() or "null"
        return json.loads(body)


def read_sensor(entity_id: str) -> tuple[Decimal, str]:
    """Liefert (Wert, Einheit) eines Sensors."""
    state = ha_request(f"/states/{entity_id}")
    raw = str(state.get("state", "")).strip()
    if raw.lower() in {"unknown", "unavailable", "none", ""}:
        raise ValueError(f"Sensor {entity_id} hat keinen gültigen Wert ({raw!r})")
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Sensor {entity_id}: Wert {raw!r} ist keine Zahl") from exc
    unit = str(state.get("attributes", {}).get("unit_of_measurement", "") or "").strip()
    return value, unit


def to_meter_unit(kind: str, label: str, value: Decimal, unit: str) -> Decimal:
    """Rechnet auf die Einheit um, die Vattenfall erwartet (Strom: kWh, Gas: m³)."""
    u = unit.lower().replace("³", "3")
    if kind == "electricity":
        if u in ("", "kwh"):
            return value
        if u == "wh":
            return value / Decimal(1000)
        if u == "mwh":
            return value * Decimal(1000)
        expected = "kWh, Wh oder MWh"
    else:
        if u in ("", "m3"):
            return value
        expected = "m³"
    raise ValueError(f"Einheit {unit!r} wird für {label} nicht unterstützt (erwartet {expected})")


def format_reading(value: Decimal, decimals: int) -> str:
    """Zählerstände werden abgeschnitten (nicht gerundet), wie auf dem Zählwerk."""
    quant = Decimal(1).scaleb(-decimals)
    text = str(value.quantize(quant, rounding=ROUND_DOWN))
    return text.replace(".", ",") if decimals > 0 else text


def list_sensors() -> list[dict]:
    """Alle Sensoren mit numerischem Zustand – Quelle für das Dropdown."""
    out = []
    for s in ha_request("/states"):
        entity_id = s.get("entity_id", "")
        if not entity_id.startswith("sensor."):
            continue
        try:
            Decimal(str(s.get("state", "")).replace(",", "."))
        except InvalidOperation:
            continue
        attrs = s.get("attributes", {}) or {}
        out.append({
            "entity_id": entity_id,
            "name": attrs.get("friendly_name", entity_id),
            "unit": attrs.get("unit_of_measurement", "") or "",
            "device_class": attrs.get("device_class", "") or "",
            "state_class": attrs.get("state_class", "") or "",
            "state": str(s.get("state", "")),
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


def list_notify_services() -> list[str]:
    names = []
    for domain in ha_request("/services"):
        if domain.get("domain") == "notify":
            names = sorted(f"notify.{n}" for n in (domain.get("services") or {}) if n != "send_message")
    return names


def notify(settings: dict, title: str, message: str) -> None:
    try:
        ha_request(
            "/services/persistent_notification/create",
            {"title": title, "message": message, "notification_id": "vattenfall_zaehlerstand"},
        )
        service = settings.get("notify_service", "")
        if service and "." in service:
            domain, name = service.split(".", 1)
            ha_request(f"/services/{domain}/{name}", {"title": title, "message": message})
    except Exception as exc:  # Benachrichtigung darf nie den Lauf kippen
        log.warning("Benachrichtigung fehlgeschlagen: %s", exc)


# --------------------------------------------------------------------------- #
# Lauf
# --------------------------------------------------------------------------- #

RUN_LOCK = threading.Lock()
STATUS: dict = {"running": False, "last": None}


def run_once(settings: dict, dry_run: bool, only_kind: str | None = None) -> dict:
    state = load_state()
    lines: list[str] = []
    failed = False
    plz = settings.get("postal_code", "")
    login = {"username": settings.get("username", ""), "password": settings.get("password", "")}

    if not login["username"] or not login["password"]:
        failed = True
        lines.append("Portal-Login (Benutzername + Passwort) nicht konfiguriert – Vattenfall verlangt Login")
        log.error("Kein Portal-Login konfiguriert")
    else:
        for kind, label in KINDS:
            if only_kind and kind != only_kind:
                continue
            cfg = settings.get(kind) or {}
            if not cfg.get("enabled"):
                continue
            unit_label = "kWh" if kind == "electricity" else "m³"
            try:
                if not cfg.get("contract_account") or not cfg.get("sensor"):
                    raise ValueError("Vertragskonto oder Sensor nicht konfiguriert")
                raw_value, unit = read_sensor(cfg["sensor"])
                value = to_meter_unit(kind, label, raw_value, unit)
                last = state.get(kind, {}).get("value")
                if last is not None and value < Decimal(str(last)):
                    raise ValueError(
                        f"Sensorwert {value} ist kleiner als der zuletzt gemeldete Stand {last} – "
                        "wird nicht übermittelt (Sensor-Fehler oder Zählerwechsel?)"
                    )
                reading = format_reading(value, int(cfg.get("decimals", 0)))
                result = submit_reading(
                    kind, cfg["contract_account"], plz, reading,
                    dry_run=dry_run, headless=bool(settings.get("headless", True)), login=login,
                )
                if result == "submitted":
                    state[kind] = {"value": str(value), "date": dt.date.today().isoformat()}
                    save_state(state)
                lines.append(f"{label}: {reading} {unit_label} ({'Testlauf, nicht abgesendet' if result == 'dry-run' else 'übermittelt'})")
            except Exception as exc:
                failed = True
                log.exception("[%s] Fehler", kind)
                first_line = (str(exc).splitlines() or [type(exc).__name__])[0]
                lines.append(f"{label}: FEHLER – {first_line}")

    if not lines:
        lines.append("Nichts zu tun (Strom und Gas deaktiviert)")

    title = "Vattenfall Zählerstand" + (" – FEHLER" if failed else (" (Testlauf)" if dry_run else ""))
    notify(settings, title, "\n".join(lines))
    return {"when": dt.datetime.now(TZ).strftime("%d.%m.%Y %H:%M"), "dry_run": dry_run, "failed": failed, "lines": lines}


def run_in_background(settings: dict, dry_run: bool, only_kind: str | None = None) -> bool:
    """Startet einen Lauf; False, wenn bereits einer läuft."""
    if not RUN_LOCK.acquire(blocking=False):
        return False

    def worker() -> None:
        STATUS["running"] = True
        try:
            STATUS["last"] = run_once(settings, dry_run, only_kind)
        except Exception as exc:
            log.exception("Lauf abgebrochen")
            STATUS["last"] = {"when": dt.datetime.now(TZ).strftime("%d.%m.%Y %H:%M"), "dry_run": dry_run,
                              "failed": True, "lines": [f"FEHLER – {exc}"]}
        finally:
            STATUS["running"] = False
            RUN_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    return True


def due(settings: dict, now: dt.datetime, state: dict) -> bool:
    if not settings.get("schedule_enabled"):
        return False
    if now.month not in settings.get("months", []):
        return False
    if now.day != int(settings.get("run_day", 1)):
        return False
    hh, mm = settings.get("run_time", "08:00").split(":")
    if (now.hour, now.minute) < (int(hh), int(mm)):
        return False
    return state.get("last_run") != now.strftime("%Y-%m")


def scheduler() -> None:
    while True:
        try:
            now = dt.datetime.now(TZ)
            settings = load_settings()
            state = load_state()
            month = now.strftime("%Y-%m")
            if state.get("last_run_month") != month:
                state["attempts"] = 0
            if due(settings, now, state) and int(state.get("attempts", 0)) < 3:
                if run_in_background(settings, dry_run=not settings.get("auto_submit", False)):
                    state["attempts"] = int(state.get("attempts", 0)) + 1
                    # last_run erst nach erfolgreichem Lauf setzen; fehlerhafte
                    # Läufe werden bis zu 3 Versuche pro Monat wiederholt.
                    wait = 0
                    while STATUS["running"] and wait < 420:
                        time.sleep(5)
                        wait += 5
                    last = STATUS.get("last") or {}
                    if not last.get("failed"):
                        state["last_run"] = month
                    state["last_run_month"] = month
                    save_state(state)
        except Exception:
            log.exception("Fehler im Zeitplaner")
        time.sleep(60)


# --------------------------------------------------------------------------- #
# Weboberfläche (Ingress)
# --------------------------------------------------------------------------- #

SHOT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]+\.png$")
MAX_BODY = 64 * 1024


def list_shots() -> list[str]:
    try:
        files = sorted(SHOT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.name for p in files[:40]]
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    server_version = "VattenfallZaehlerstand/0.2"

    def log_message(self, fmt, *args):  # kein Zugriffslog im Add-on-Log
        pass

    # -- Hilfen --
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode())

    def _allowed(self) -> bool:
        if self.client_address[0] in ALLOWED_IPS:
            return True
        self._json(403, {"error": "Zugriff nur über Home Assistant"})
        return False

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Anfrage zu groß")
        return json.loads(self.rfile.read(length) or b"{}")

    @staticmethod
    def _path(raw: str) -> str:
        return "/" + urlsplit(raw).path.strip("/")

    # -- Routen --
    def do_GET(self) -> None:
        if not self._allowed():
            return
        path = self._path(self.path)
        try:
            if path == "/":
                self._send(200, (APP_DIR / "ui.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/settings":
                public = dict(load_settings())
                public["password_set"] = bool(public.pop("password", ""))
                self._json(200, public)
            elif path == "/api/sensors":
                try:
                    self._json(200, {"sensors": list_sensors()})
                except Exception as exc:
                    log.warning("Sensorliste nicht ladbar: %s", exc)
                    self._json(200, {"sensors": [], "error": str(exc)})
            elif path == "/api/notify":
                try:
                    self._json(200, {"services": list_notify_services()})
                except Exception:
                    self._json(200, {"services": []})
            elif path == "/api/status":
                self._json(200, {"running": STATUS["running"], "last": STATUS["last"],
                                 "log": list(LOG_LINES)[-80:], "shots": list_shots()})
            elif path.startswith("/api/shot/"):
                name = unquote(path[len("/api/shot/"):])
                if not SHOT_NAME_RE.match(name) or not (SHOT_DIR / name).is_file():
                    self._json(404, {"error": "nicht gefunden"})
                else:
                    self._send(200, (SHOT_DIR / name).read_bytes(), "image/png")
            else:
                self._json(404, {"error": "nicht gefunden"})
        except Exception as exc:
            log.exception("GET %s", path)
            self._json(500, {"error": str(exc)})

    def do_POST(self) -> None:
        if not self._allowed():
            return
        path = self._path(self.path)
        try:
            data = self._body()
            if path == "/api/settings":
                incoming = dict(data)
                if not str(incoming.get("password", "")):
                    incoming["password"] = load_settings().get("password", "")
                settings = validate_settings(incoming)
                save_settings(settings)
                public = dict(settings)
                public["password_set"] = bool(public.pop("password", ""))
                self._json(200, public)
            elif path == "/api/run":
                settings = load_settings()
                kind = data.get("kind")
                if kind not in (None, "", "electricity", "gas"):
                    self._json(400, {"error": "kind muss electricity oder gas sein"})
                    return
                if not run_in_background(settings, dry_run=bool(data.get("dry_run", True)),
                                         only_kind=kind or None):
                    self._json(409, {"error": "Es läuft bereits ein Lauf"})
                else:
                    self._json(200, {"started": True})
            else:
                self._json(404, {"error": "nicht gefunden"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            log.exception("POST %s", path)
            self._json(500, {"error": str(exc)})


class ListHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        LOG_LINES.append(self.format(record))


def main() -> int:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    mem = ListHandler()
    mem.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[stream, mem])

    log.info("Gestartet – Oberfläche auf Port %s", PORT)
    threading.Thread(target=scheduler, daemon=True).start()
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
