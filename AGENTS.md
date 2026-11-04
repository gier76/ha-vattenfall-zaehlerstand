# Hinweise für Hermes (und andere Agenten)

Du bekommst dieses Projekt zum **Prüfen, Veröffentlichen auf GitHub und Testen im echten
Home Assistant**. Lies diese Datei komplett, bevor du etwas änderst. Arbeite in kleinen
Schritten, ändere nur, was nötig ist, und führe nach jeder Änderung `bash dev/smoke_test.sh` aus.

## Was das Projekt ist

Ein Home-Assistant-Add-on (Ordner `vattenfall_zaehlerstand/`). Es liest Strom- und
Gaszählerstände aus Sensoren, die der Nutzer in einer Weboberfläche (Ingress, Seitenleiste)
per Dropdown auswählt, und trägt sie per Playwright/Chromium ins öffentliche Vattenfall-Formular
`https://online.vattenfall.de/zaehlerstand` ein. Vattenfall hat dafür **keine** offizielle API.

| Datei | Inhalt |
|---|---|
| `vattenfall_zaehlerstand/run.py` | Einstellungen, HA-API, Zeitplaner, Weboberfläche (nur Standardbibliothek) |
| `vattenfall_zaehlerstand/form.py` | Playwright: Formular ausfüllen, Screenshots |
| `vattenfall_zaehlerstand/ui.html` | Oberfläche (HTML/JS, lädt nichts von außen) |
| `vattenfall_zaehlerstand/config.yaml`, `Dockerfile` | Add-on-Definition |
| `dev/mock_ha.py`, `dev/smoke_test.sh` | lokaler Test ohne Home Assistant |
| `README.md` | Nutzerdokumentation |

## Was geprüft ist und was nicht (wichtig)

**Geprüft** (Rauchtest, läuft grün): Weboberfläche und API gegen einen simulierten HA, Eingabeprüfung,
Zugriffsschutz, Einheitenumrechnung, Zeitplanlogik, Fehlerpfad bei nicht erreichbarem Formular.

**NICHT geprüft:**
1. **Das echte Vattenfall-Formular.** Die Feld- und Button-Erkennung in `form.py` ist aus
   Beschriftungen geraten („Vertragskonto", „Postleitzahl", „Zählerstand", „Weiter", „Absenden").
   Die Seite ist eine JavaScript-Anwendung; ich konnte sie nur laden, nicht bedienen.
   Mögliche Überraschungen: Captcha, zusätzliche Felder (Zählernummer, Ablesedatum), Auswahl
   des Zählers, andere Button-Texte, Zustimmungs-Checkbox.
2. **Der Build und Start als echtes Add-on** (Supervisor, Ingress, `SUPERVISOR_TOKEN`).
3. **`aarch64`** (Raspberry Pi): ob das Playwright-Basisimage dort läuft, ist ungeprüft.

Prüfe außerdem gegen die aktuelle Home-Assistant-Add-on-Doku (nicht aus dem Gedächtnis), ob diese
Punkte in `config.yaml` noch gültig sind: `map: - share:rw` (ältere Kurzschreibweise), `options: {}` /
`schema: {}`, `ingress`, `ingress_port`, `homeassistant_api`. Melde Abweichungen, rate nicht.

## Regeln – das darfst du NICHT ohne ausdrückliche Zustimmung des Nutzers

- **Nichts echt an Vattenfall absenden.** Jeder Lauf mit `dry_run=false` meldet echte Zählerstände.
  Erst der Nutzer entscheidet, ob und wann. Standard bleibt Testlauf.
- **Keine Sicherheitsnetze entfernen oder abschwächen:** Testmodus als Standard, Abbruch bei
  kleinerem Wert als zuletzt gemeldet, Abbruch bei mehreren Zählerstand-Feldern, Einheitenprüfung,
  Beschränkung auf den Ingress-Proxy (`ALLOWED_IPS`), Eingabevalidierung, Maskierung der Vertragsnummer.
- **Keine persönlichen Daten in Git:** keine Vertragskontonummern, keine PLZ, keine Screenshots,
  keine `settings.json`/`state.json`, keine Tokens. `.gitignore` deckt das ab – trotzdem vor jedem
  Commit `git status` und `git diff --staged` ansehen. Screenshots aus den Tests enthalten
  persönliche Daten und dürfen nicht hochgeladen oder in Issues eingefügt werden, ohne sie zu schwärzen.
- **Keine Umgehung von Captcha oder Bot-Schutz.** Taucht eins auf: stoppen, dem Nutzer berichten.
- **Keine neuen externen Abhängigkeiten oder CDN-Einbindungen** ohne Rücksprache (das Add-on ist
  bewusst reine Standardbibliothek + das vorhandene Playwright-Image).

## Aufgabe 1: Code prüfen

1. `bash dev/smoke_test.sh` – muss „ALLE TESTS OK" melden.
2. Lies `run.py`, `form.py`, `ui.html` und prüfe besonders:
   - Webserver: Pfade, Request-Größe, Zugriffsschutz, keine Pfad-Traversal-Lücke, Fehlermeldungen
     ohne sensible Daten.
   - `ui.html`: kein `innerHTML` mit fremden Daten (Sensornamen kommen aus HA).
   - `form.py`: Endlosschleifen, Timeouts, ob `SUBMIT_RE` versehentlich einen falschen Button trifft
     (enthält u. a. „bestätigen"), ob `NEXT_RE`/`SUBMIT_RE` im Testmodus je zum echten Absenden führen können.
   - `run_once`: Reihenfolge Sensor lesen → Einheit → Plausibilität → Absenden → Zustand speichern.
   - Threads: `RUN_LOCK` verhindert parallele Läufe; der Zeitplaner darf nicht doppelt auslösen.
3. Melde Funde als Liste mit Datei, Zeile, Schwere (kritisch/mittel/gering) und Vorschlag.
   Kleine, eindeutige Fehler darfst du beheben; Verhaltensänderungen vorher mit dem Nutzer abstimmen.

## Aufgabe 2: Auf GitHub hochladen

1. Frage den Nutzer nach GitHub-Benutzername und Repository-Namen (Vorschlag:
   `ha-vattenfall-zaehlerstand`) und ob das Repository öffentlich sein soll.
2. Ersetze **alle** `CHANGE-ME` in `repository.yaml` und `vattenfall_zaehlerstand/config.yaml`
   (`grep -rn CHANGE-ME .` muss danach leer sein).
3. Frage nach der Lizenz (Vorschlag: MIT) und lege `LICENSE` an. Nicht selbst entscheiden.
4. `git init`, `git status` prüfen (keine `.png`, `.zip`, `settings.json`, `state.json`), dann committen.
5. Push erst nach Bestätigung des Nutzers. Der README-Hinweis „noch nicht gegen das echte Formular
   getestet" bleibt, bis Aufgabe 3 erfolgreich war – danach Status in der README aktualisieren.

## Aufgabe 3: Im echten Home Assistant testen

Reihenfolge einhalten, nach jedem Schritt Ergebnis berichten:

1. **Installieren:** Ordner `vattenfall_zaehlerstand/` nach `/addons/vattenfall_zaehlerstand/`
   kopieren (Samba/SSH) oder das GitHub-Repository als Add-on-Repository hinzufügen.
   Add-on-Store neu laden, installieren (erster Build dauert wegen Chromium), starten.
   Lokale Add-ons haben den Slug `local_vattenfall_zaehlerstand`:
   `ha addons logs local_vattenfall_zaehlerstand`, nach Codeänderungen
   `ha addons rebuild local_vattenfall_zaehlerstand`.
2. **Oberfläche:** „Zählerstand" in der Seitenleiste öffnen. Prüfen: Sensor-Dropdown ist befüllt,
   Strom-Sensor erscheint unter „Empfohlen", Vorschau zeigt Wert und gemeldeten Wert, Speichern
   funktioniert, Notify-Dropdown ist befüllt. Funktioniert die Seite nicht im Ingress-Rahmen
   (leer, 404, Anfragen gehen ins Leere): Das sind relative URLs `api/...` – zuerst dort suchen.
3. **Testlauf** (Button „Testlauf"): nichts wird gesendet. Danach die Screenshots in der Oberfläche
   ansehen (Dateien liegen unter `/share/vattenfall_zaehlerstand/`). Prüfen:
   - Cookie-Banner behandelt?
   - Vertragskonto und PLZ im richtigen Feld?
   - Zählerstandfeld gefunden, Wert plausibel (Strom ganze kWh, Gas ganze m³)?
   - Endet der Lauf vor dem Absenden mit „DRY-RUN"?
4. **Bei Fehlern:** Die Dateien `*_ERROR.png` und `*_ERROR.html` zeigen, was das Formular
   zeigte. Das Log schreibt die sichtbaren Felder (`Sichtbare Felder: …`). Daraus die Erkennung in
   `form.py` anpassen (Label-Regexe oben in der Datei). Nicht raten, sondern am echten HTML belegen.
   Danach `ha addons rebuild …` und wieder Testlauf.
5. **Erst wenn der Testlauf für Strom und Gas sauber durchläuft:** den Nutzer fragen, ob ein echter
   Lauf gemacht werden soll. Empfehlung: zuerst nur **eine** Sparte (Strom), Ergebnis in der
   Vattenfall-Oberfläche oder per Bestätigungsmail prüfen, dann die zweite. Eine falsche Meldung
   lässt sich nur über Vattenfall korrigieren.
6. **Zeitplan** erst aktivieren, wenn der echte Lauf geklappt hat. „Automatische Läufe senden
   wirklich ab" ist ein eigener Schalter und bleibt aus, bis der Nutzer ihn will.

## Typische Stolpersteine

- Sensor liefert `unknown`/`unavailable` oder eine falsche Einheit (Gas in kWh statt m³): das Add-on
  bricht absichtlich ab. Kein Fehler im Code.
- Zählerwechsel oder Sensor-Reset: Wert kleiner als zuletzt gemeldet → Abbruch ist gewollt.
  Der gemerkte Stand liegt in `/data/state.json` im Add-on.
- Stromkunden mit PV und Zweirichtungszähler: gemeldet werden darf nur der Bezug (OBIS 1.8.0).
  Der gewählte Sensor muss also den **Bezug** zeigen, nicht die Einspeisung.
- Kunden mit intelligentem Messsystem (iMSys): Vattenfall nimmt keine Kunden-Zählerstände an.
- Das Playwright-Basisimage ist groß; langer Erstbuild ist normal.

## Arbeitsweise

- Kleine Schritte, nach jeder Änderung `bash dev/smoke_test.sh`.
- Keine großen Umbauten und keine Dateien komplett neu schreiben, wenn eine gezielte Änderung reicht.
- Alles Unklare dem Nutzer als konkrete Frage stellen, statt zu raten.
- Berichte am Ende: was geprüft, was geändert, was offen, was getestet wurde und was nicht.

## Was noch offen (Stand: 2026-11-04)

- [x] **Aufgabe 1 – Code Review: fertig.** Smoke-Test bestanden: alle 8 API-Checks + Einheiten/
  Formatierung/Zeitplan-Asserts OK. Hinweis: `dev/smoke_test.sh` ist Linux-only — auf Windows
  equivalent mit native-Pfad-Harness ausgeführt (bash übergibt `/c/...`-Pfade an Windows-Python,
  mock_ha.py findet die Datei so nicht).
- [x] **Aufgabe 2 – GitHub: fertig (2026-11-04).** Privates Repo
  https://github.com/gier76/ha-vattenfall-zaehlerstand (main, Commit ab4f0e9, remote verifiziert
  via `git ls-remote`). CHANGE-ME ersetzt, `grep -rn CHANGE-ME .` leer. Windows-Pitfall:
  `git push` hing im Credential-Prompt → git lokal auf Helper `!gh auth git-credential` gestellt;
  einmalig gepusht mit URL `https://gier76:<gh-token>@github.com/...`.
- [ ] **Aufgabe 3 – echter HA-Test:** braucht jetzt: HA-URL+Token (bekannt: 192.168.1.21:8123,
  ha-mcp), GitHub-Token für das private Repo im Supervisor (`POST /api/addons/repos` mit
  `auth_token`), PLZ + Vertragsnummern + Sensor-Auswahl vom Nutzer. Lizenz bisher offen.
- [ ] Formular-Erkennung durch keinen echten Lauf verifiziert.

### Review-Befunde
- **MITTEL – Zeitzone** (run.py Scheduler `datetime.now()` ohne TZ; Container laufen UTC →
  Zeitplan +1/+2 h Versatz): **FIXED 2026-11-04** — `ZoneInfo("Europe/Berlin")` in run.py
  (Fallback auf Systemzeit ohne tzdata) + `ENV TZ=Europe/Berlin` im Dockerfile. Im echten HA
  bei Aufgabe 3 verifizieren (Log-Zeitstempel vs. Lokalzeit).
- **MITTEL – `SUBMIT_RE` enthält `bestätigen`** (form.py Zeile 24): könnte im echten Formular
  einen Bestätigungsdialog treffen; nur nach ausgefülltem Zählerstand aktiv → bei Aufgabe 3
  zuerst dry-run.
- **NIEDRIG – UI-Inkonsistenz**: ui.html `recommended()` listet `ft3`/`ccf` als „Empfohlen“,
  aber `convert()`/`to_meter_unit()` lehnen sie ab.
- **NIEDRIG – `months: []` bei aktivem Zeitplan** läuft nie (validieren erlaubt leere Liste).
- **NIEDRIG – Scheduler schreibt `last_run` in eigene state-Kopie** (read-modify-write ohne
  Lock); praktisch harmlos, Worker liest state nach dem Scheduler-Speichern.
- **OK**: Sicherheitchecks alle erfüllt (IP-Allowlist, Traversal-Regex geblockt, kein innerHTML
  mit Fremd-Daten, Vertragsnummern gemaskt, dry-run Default true, Reihenfolge Sensor→Einheit→
  Plausibilität→Absenden→State wie gefordert, RUN_LOCK greift).
