# Vattenfall Zählerstand – Home-Assistant-Add-on

Liest Strom- und Gaszählerstände aus **frei wählbaren Home-Assistant-Sensoren** und trägt sie
automatisch im öffentlichen Vattenfall-Formular (`online.vattenfall.de/zaehlerstand`) ein.

Die Einrichtung läuft über eine eigene Oberfläche in der Home-Assistant-Seitenleiste
(„Zählerstand"). Dort wählst du Sensoren aus einem Dropdown, das live aus deiner
Home-Assistant-Installation befüllt wird – keine YAML-Konfiguration nötig.

Vattenfall bietet dafür keine offizielle Schnittstelle. Das Add-on steuert deshalb das
Webformular per Chromium (Playwright). Wenn Vattenfall das Formular ändert, kann das
Add-on brechen – darum gibt es **Testläufe** und Screenshots bei jedem Schritt.

> **Nicht für Kunden mit intelligentem Messsystem (iMSys).** Dort nimmt Vattenfall
> keine Kunden-Zählerstände an, die Daten kommen automatisch vom Messstellenbetreiber.

## Status

Version 0.2.0 – **noch nicht gegen das echte Formular getestet.** Feld- und Button-Erkennung
laufen über Beschriftungen („Vertragskonto", „Postleitzahl", „Zählerstand", „Weiter",
„Absenden"). Ob das so passt, zeigt der erste Testlauf. Die Oberfläche und die API wurden
gegen einen simulierten Home Assistant getestet.

## Installation

1. Ordner `vattenfall_zaehlerstand/` nach `/addons/` von Home Assistant kopieren (Samba/SSH)
   – oder dieses Repository als Add-on-Repository hinzufügen
   (Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories).
2. Add-on-Store neu laden, „Vattenfall Zählerstand" installieren und starten
   (das Image ist groß, weil Chromium enthalten ist).
3. In der Seitenleiste **Zählerstand** öffnen (ggf. „In Seitenleiste anzeigen" aktivieren).
4. PLZ, Vertragskontonummern und Sensoren eintragen → **Speichern** → **Testlauf**.
5. Screenshots in der Oberfläche prüfen. Erst wenn alles stimmt: „Jetzt wirklich senden"
   oder den Zeitplan aktivieren (und „Automatische Läufe senden wirklich ab" einschalten).

## Einstellungen in der Oberfläche

| Bereich | Bedeutung |
|---|---|
| Lieferstelle | PLZ der Lieferstelle |
| Strom / Gas | je eigene Vertragskontonummer, Sensor-Dropdown (mit Filter), Nachkommastellen |
| Zeitplan | Tag im Monat (1–28), Uhrzeit, Monate (z. B. nur Dezember); „senden wirklich ab" aus = nur Testläufe |
| Benachrichtigung | optional zusätzlicher `notify.*`-Dienst (Dropdown); eine HA-Meldung gibt es immer |

Das Dropdown schlägt passende Sensoren zuerst vor (Strom: Energie/kWh/Wh/MWh, Gas: Gas/m³),
zeigt darunter alle weiteren numerischen Sensoren und rechnet Wh/MWh automatisch in kWh um.
Gas muss in m³ vorliegen (Vattenfall fragt den Zählerstand in m³ ab); Sensoren in kWh
werden abgelehnt, statt falsche Werte zu melden.

## Sicherheitsnetze

- Testläufe füllen das Formular aus, senden aber nichts ab.
- Automatische Läufe senden nur, wenn „senden wirklich ab" aktiv ist.
- Kein Versand, wenn der Sensor `unknown`/`unavailable` ist oder eine nicht passende Einheit hat.
- Ein Wert, der **kleiner** ist als der zuletzt gemeldete, wird nicht gesendet.
- Gibt es mehrere Zählerstand-Felder (z. B. HT/NT), bricht das Add-on ab, statt zu raten.
- Zählerstände werden abgeschnitten, nicht gerundet.
- Vertragsnummern erscheinen im Log nur maskiert.
- Die Oberfläche akzeptiert nur Anfragen vom Home-Assistant-Ingress-Proxy.
- Bei Fehlern liegen Screenshot und HTML-Dump unter `/share/vattenfall_zaehlerstand/`.

## Entwicklung

`run.py` (Einstellungen, HA-API, Zeitplaner, Weboberfläche), `form.py` (Playwright),
`ui.html` (Oberfläche). Das Playwright-Basisimage bringt nur die Chromium-Browser mit –
das Python-Paket `playwright` wird ohne Netzwerk aus den mitgelieferten Wheels
im Ordner `vendor/` ins Image gepackt (stdlib `zipfile`, kein pip/apt — deren
Netzwerkaufrufe hängen im Supervisor-Build auf diesem Host). Das `tzdata`-Wheel
liefert die Zeitzone für den Zeitplan (`ZoneInfo("Europe/Berlin")`).

Lokaler Test ohne Home Assistant und ohne Zugriff auf Vattenfall:

```bash
bash dev/smoke_test.sh
```

Das startet `dev/mock_ha.py` als simulierten Home Assistant. Die Umgebungsvariablen dafür:
`HA_URL`, `HA_TOKEN`, `PORT`, `BIND`, `ALLOWED_IPS`, `SETTINGS_PATH`, `STATE_PATH`, `SHOT_DIR`.
Hinweise für Agenten (Prüfung, GitHub-Upload, Test im echten System) stehen in `AGENTS.md`.

## Hinweis

Inoffizielles Projekt, nicht mit Vattenfall verbunden. Nutzung auf eigene Verantwortung;
bitte prüfe regelmäßig, ob die Meldungen bei Vattenfall ankommen.
