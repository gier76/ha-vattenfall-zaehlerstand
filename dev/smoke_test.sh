#!/usr/bin/env bash
# Lokaler Rauchtest ohne Home Assistant und ohne Zugriff auf Vattenfall.
# Startet Mock-HA + Add-on-Server, prüft API, Validierung, Einheiten, Zeitplan.
# Aufruf:  bash dev/smoke_test.sh        (aus dem Projektordner)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/vattenfall_zaehlerstand"
TMP="$(mktemp -d)"
B=http://127.0.0.1:18099
fail=0
check() { if [ "$2" = "$3" ]; then echo "OK   $1"; else echo "FAIL $1 (erwartet: $3, erhalten: $2)"; fail=1; fi; }

cleanup() { kill "${MOCK_PID:-}" "${APP_PID:-}" 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

MOCK_PORT=18123 MOCK_POST_LOG="$TMP/posts.log" python3 "$ROOT/dev/mock_ha.py" & MOCK_PID=$!
( cd "$APP" && HA_URL=http://127.0.0.1:18123 HA_TOKEN=x PORT=18099 BIND=127.0.0.1 ALLOWED_IPS=127.0.0.1 \
  SETTINGS_PATH="$TMP/settings.json" STATE_PATH="$TMP/state.json" SHOT_DIR="$TMP/shots" \
  python3 -u run.py >"$TMP/server.log" 2>&1 ) & APP_PID=$!
sleep 2

check "UI wird ausgeliefert" "$(curl -s -o /dev/null -w '%{http_code}' $B/)" "200"
check "Sensorliste (nur numerische Sensoren)" \
  "$(curl -s $B/api/sensors | python3 -c "import sys,json; print(','.join(s['entity_id'] for s in json.load(sys.stdin)['sensors']))")" \
  "sensor.gas_kwh,sensor.gaszahler_m3,sensor.strom_wh,sensor.stromzaehler,sensor.temperatur"
check "Notify-Liste ohne send_message" "$(curl -s $B/api/notify)" '{"services": ["notify.mobile_app_handy"]}'
check "Ungültige PLZ wird abgelehnt" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $B/api/settings -d '{"postal_code":"abc"}')" "400"
check "Nicht-Sensor wird abgelehnt" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $B/api/settings -d '{"postal_code":"12345","gas":{"sensor":"light.x"}}')" "400"
check "Gültige Einstellungen werden gespeichert" \
  "$(curl -s -o /dev/null -w '%{http_code}' -X POST $B/api/settings -d '{"postal_code":"12345","electricity":{"enabled":true,"contract_account":"111","sensor":"sensor.stromzaehler"},"gas":{"enabled":false}}')" "200"
check "Pfad-Traversal bei Screenshots wird abgewiesen" "$(curl -s -o /dev/null -w '%{http_code}' --path-as-is "$B/api/shot/../../etc/passwd")" "404"

# Fremde IP muss blockiert werden
( cd "$APP" && HA_URL=http://127.0.0.1:18123 PORT=18100 BIND=127.0.0.1 ALLOWED_IPS=1.2.3.4 \
  SETTINGS_PATH="$TMP/s2.json" STATE_PATH="$TMP/st2.json" SHOT_DIR="$TMP/shots2" python3 -u run.py >/dev/null 2>&1 ) & OTHER=$!
sleep 1.5
check "Fremde IP wird blockiert (403)" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18100/api/settings)" "403"
kill $OTHER 2>/dev/null

# Einheiten, Formatierung, Zeitplan
( cd "$APP" && python3 -I - <<'PY'
import sys, datetime as dt
from decimal import Decimal as D
sys.path.insert(0, ".")
import run
assert run.to_meter_unit("electricity", "Strom", D("9876543"), "Wh") == D("9876.543")
assert run.to_meter_unit("electricity", "Strom", D("12"), "MWh") == D(12000)
assert run.to_meter_unit("gas", "Gas", D("5"), "m³") == D(5)
for bad in [("gas", "Gas", D(1), "kWh"), ("electricity", "Strom", D(1), "°C")]:
    try:
        run.to_meter_unit(*bad); raise SystemExit("sollte fehlschlagen")
    except ValueError:
        pass
assert run.format_reading(D("9876.543"), 0) == "9876"
assert run.format_reading(D("4321.99"), 1) == "4321,9"
s = {"schedule_enabled": True, "months": [12], "run_day": 4, "run_time": "08:00"}
assert run.due(s, dt.datetime(2026, 12, 4, 8, 0), {})
assert not run.due(s, dt.datetime(2026, 12, 4, 7, 59), {})
assert not run.due(s, dt.datetime(2026, 12, 4, 9, 0), {"last_run": "2026-12"})
assert not run.due({**s, "schedule_enabled": False}, dt.datetime(2026, 12, 4, 8, 0), {})
PY
) && echo "OK   Einheiten, Formatierung, Zeitplan" || { echo "FAIL Einheiten/Zeitplan"; fail=1; }

[ $fail -eq 0 ] && echo "ALLE TESTS OK" || echo "FEHLER GEFUNDEN"
exit $fail
