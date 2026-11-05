"""Playwright-Teil: füllt das Vattenfall-Zählerstandformular aus.

Feld- und Button-Erkennung laufen über Beschriftungen (Labels), nicht über feste
CSS-Selektoren. Bei jedem Schritt wird ein Screenshot nach SHOT_DIR geschrieben.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

FORM_URL = "https://online.vattenfall.de/zaehlerstand"
SHOT_DIR = Path(os.environ.get("SHOT_DIR", "/share/vattenfall_zaehlerstand"))

COOKIE_RE = re.compile(r"nur notwendige|ablehnen|alle ablehnen|alle akzeptieren|alles akzeptieren|akzeptieren", re.I)
ACCOUNT_RE = re.compile(r"vertragskonto", re.I)
PLZ_RE = re.compile(r"postleitzahl|plz", re.I)
DATE_RE = re.compile(r"datum", re.I)
READING_RE = re.compile(r"z(ä|ae)hlerstand", re.I)
NEXT_RE = re.compile(r"^\s*(weiter|fortfahren|prüfen|pruefen|los|zählerstand eingeben|zum zählerstand)\s*$", re.I)
SUBMIT_RE = re.compile(r"absenden|übermitteln|uebermitteln|abschicken|^\s*senden\s*$|zählerstand mitteilen|bestätigen|z(ä|ae)hlerstand erfassen|erfassen", re.I)
SUCCESS_RE = re.compile(r"vielen dank|erfolgreich|wurde übermittelt|erfolgreich übermittelt|gespeichert", re.I)
# Login: Vattenfall verlangt seit 2026 Login (Benutzername+Passwort) fuer die
# Zählerstandserfassung; das offene Formular ohne Login gibt es nicht mehr.
LOGIN_OPTION_RE = re.compile(r"benutzername und passwort", re.I)
LOGIN_DROPDOWN_RE = re.compile(r"login-?optionen", re.I)
ZÄHLER_NAV_RE = re.compile(r"zählerstand", re.I)
USER_RE = re.compile(r"benutzername|benutzer|nutzername|e-?mail", re.I)
PASS_RE = re.compile(r"passwort|password", re.I)
KIND_RE = {"electricity": re.compile(r"strom|electric|elektrizit", re.I),
           "gas": re.compile(r"gas|erdgas", re.I)}

log = logging.getLogger("vattenfall")


def mask(value: str) -> str:
    """Vertragsnummern nie vollständig ins Log schreiben."""
    value = (value or "").strip()
    return value if len(value) <= 4 else "*" * (len(value) - 3) + value[-3:]


# --------------------------------------------------------------------------- #

def first_visible(locator):
    """Gibt das erste sichtbare Element eines Locators zurück oder None."""
    for i in range(locator.count()):
        item = locator.nth(i)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def visible_count(locator) -> int:
    n = 0
    for i in range(locator.count()):
        try:
            if locator.nth(i).is_visible():
                n += 1
        except Exception:
            pass
    return n


def find_input(page, label_re):
    """Eingabefeld über Label, danach über Placeholder/aria-label suchen."""
    for locator in (
        page.get_by_label(label_re),
        page.get_by_placeholder(label_re),
    ):
        try:
            hit = first_visible(locator)
        except Exception:
            hit = None
        if hit is not None:
            return hit
    return None


def find_button(page, text_re):
    for role in ("button", "link"):
        hit = first_visible(page.get_by_role(role, name=text_re))
        if hit is not None:
            return hit
    return None


def find_reading_input(page, kind: str):
    """Zählerstand-Eingabefeld; durchsucht auch Shadow-DOM-Komponenten.

    Liefert None, wenn kein Feld sichtbar ist; RuntimeError bei mehrdeutiger Wahl.
    """
    hits = page.evaluate(
        """() => {
            const out = [];
            let n = 0;
            function collect(root, hostMeta) {
                for (const el of root.querySelectorAll('input')) {
                    if (el.offsetParent === null || el.disabled) continue;
                    const meta = [el.getAttribute('aria-label') || '', el.placeholder || '',
                        (el.labels && el.labels[0] ? el.labels[0].innerText : ''), hostMeta].join(' ');
                    el.setAttribute('data-vf-hit', String(n));
                    out.push({n, meta: meta.slice(0, 250)});
                    n++;
                }
                for (const el of root.querySelectorAll('*')) {
                    if (el.shadowRoot) collect(el.shadowRoot, hostMeta + ' ' + (el.getAttribute('label') || el.name || ''));
                }
            }
            collect(document, '');
            return out;
        }"""
    )
    kind_re = KIND_RE.get(kind)
    found = []
    for h in hits:
        if READING_RE.search(h["meta"]):
            found.append((h, kind_re is not None and kind_re.search(h["meta"])))
    if not found:
        return None
    if len(found) == 1:
        return page.locator(f'input[data-vf-hit="{found[0][0]["n"]}"]')
    exact = [h for h, k in found if k]
    if len(exact) == 1:
        return page.locator(f'input[data-vf-hit="{exact[0]["n"]}"]')
    raise RuntimeError(
        f"{len(found)} Zählerstand-Felder gefunden und nicht eindeutig als "
        f"{'Strom' if kind == 'electricity' else 'Gas'} erkennbar – bitte manuell prüfen"
    )


def dismiss_cookies(page) -> None:
    btn = first_visible(page.get_by_role("button", name=COOKIE_RE))
    if btn is not None:
        log.info("Cookie-Banner wird geschlossen")
        btn.click()
        page.wait_for_timeout(500)


def dump_inputs(page) -> None:
    """Hilft beim Debuggen: welche Eingabefelder sind gerade sichtbar?"""
    try:
        rows = page.evaluate(
            """() => {
                const out = [];
                function collect(root) {
                    for (const el of root.querySelectorAll('input,select,textarea')) {
                        if (el.offsetParent === null || el.disabled) continue;
                        out.push({type: el.type || el.tagName, name: el.name || '', id: el.id || '',
                                  label: (el.labels && el.labels[0] ? el.labels[0].innerText : ''),
                                  aria: el.getAttribute('aria-label') || '',
                                  placeholder: el.placeholder || ''});
                    }
                    for (const el of root.querySelectorAll('*')) {
                        if (el.shadowRoot) collect(el.shadowRoot);
                    }
                }
                collect(document);
                return out;
            }"""
        )
        log.info("Sichtbare Felder: %s", json.dumps(rows, ensure_ascii=False))
    except Exception:
        pass


def contract_click_via_cdp(context, page, kind: str):
    """Klickt die Vertrag-Karte auch hinter closed Shadow-DOM via CDP-Accessibility-Tree."""
    needles = ["sparte: gas"] if kind == "gas" else ["sparte: strom", "sparte: electric"]
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("DOM.enable")
        cdp.send("Accessibility.enable")
        nodes = cdp.send("Accessibility.getFullAXTree").get("nodes", [])
    except Exception as exc:
        log.warning("CDP nicht verfügbar: %s", exc)
        return None
    for needle in needles:
        preferred = None
        fallback = None
        for nd in nodes:
            nm = ((nd.get("name") or {}).get("value") or "").lower()
            if needle in nm and nd.get("backendDOMNodeId"):
                if nd.get("role") in ("listitem", "button", "link"):
                    preferred = nd
                    break
                fallback = fallback or nd
        cand = preferred or fallback
        if cand is None:
            continue
        try:
            model = cdp.send("DOM.getBoxModel", {"backendNodeId": cand["backendDOMNodeId"]})["model"]
            xs, ys = model["content"][0::2], model["content"][1::2]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            page.mouse.click(cx, cy)
            name = ((cand.get("name") or {}).get("value") or "").strip()
            return f"{cand.get('role')} '{name[:60]}' bei ({cx:.0f}, {cy:.0f})"
        except Exception as exc:
            log.warning("BoxModel/Klick fehlgeschlagen: %s", exc)
    return None


def ax_box(cdp, nd):
    try:
        model = cdp.send("DOM.getBoxModel", {"backendNodeId": nd["backendDOMNodeId"]})["model"]
        xs, ys = model["content"][0::2], model["content"][1::2]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    except Exception:
        return None


def ax_find_node(cdp, name_re, roles):
    """AX-Knoten (auch closed Shadow-DOM) nach Rolle + Namensregex; liefert Koordinaten."""
    try:
        nodes = cdp.send("Accessibility.getFullAXTree").get("nodes", [])
    except Exception as exc:
        log.warning("AX-Tree fehlgeschlagen: %s", exc)
        return None, None
    for nd in nodes:
        role = (nd.get("role") or {}).get("value") or ""
        name = ((nd.get("name") or {}).get("value") or "")
        if role in roles and name_re.search(name):
            box = ax_box(cdp, nd)
            if box is not None:
                return nd, box
    return None, None


def submit_reading(kind: str, account: str, plz: str, reading: str, *, dry_run: bool, headless: bool, login: dict | None = None) -> str:
    """Trägt einen Zählerstand ein. Rückgabe: 'dry-run' oder 'submitted'.

    login = {'username', 'password'}: Portal-Login (seit 2026 Pflicht). Ohne Login
    wird das alte Vertragskonto+PLZ-Formular versucht.
    """
    from playwright.sync_api import sync_playwright

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    def shot(page, name: str) -> None:
        try:
            page.screenshot(path=str(SHOT_DIR / f"{stamp}_{kind}_{name}.png"), full_page=True)
        except Exception as exc:
            log.warning("Screenshot fehlgeschlagen: %s", exc)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, args=["--no-sandbox"])
        context = browser.new_context(
            locale="de-DE", timezone_id="Europe/Berlin", viewport={"width": 1280, "height": 1600},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.set_default_timeout(20000)
        cdp = None
        try:
            cdp = context.new_cdp_session(page)
            cdp.send("DOM.enable")
            cdp.send("Accessibility.enable")
        except Exception as exc:
            log.warning("CDP nicht verfügbar: %s", exc)
        try:
            log.info("[%s] Öffne %s", kind, FORM_URL)
            page.goto(FORM_URL, wait_until="domcontentloaded")
            page.wait_for_selector("input", timeout=45000)
            page.wait_for_timeout(1000)
            dismiss_cookies(page)
            shot(page, "01_start")
            dump_inputs(page)

            login_ok = False
            username = (login or {}).get("username") or ""
            password = (login or {}).get("password") or ""
            if username and password:
                page.wait_for_timeout(2000)  # JS-Hydration abwarten
                picked = False
                for _ in range(3):
                    page.evaluate(
                        "() => { const b = document.querySelector('button[aria-label=\"Login-Optionen\"]');"
                        " if (b) b.click(); }")
                    page.wait_for_timeout(800)
                    picked = page.evaluate(
                        "() => { const el = [...document.querySelectorAll('li, button, a')]"
                        ".find(e => e.offsetWidth > 0 && /^benutzername und passwort$/i.test(e.textContent.trim()));"
                        " if (el) { el.click(); return true; } return false; }")
                    if picked:
                        break
                    page.wait_for_timeout(1500)
                if not picked:
                    raise RuntimeError("Login-Option 'Benutzername und Passwort' nicht gefunden")
                try:
                    page.wait_for_selector("input[aria-label='Passwort']", timeout=10000)
                except Exception:
                    pass
                user_field = find_input(page, USER_RE)
                pass_field = find_input(page, PASS_RE)
                if user_field is None or pass_field is None:
                    raise RuntimeError("Login-Felder (Benutzername/Passwort) nicht gefunden")
                user_field.fill(username)
                pass_field.fill(password)  # Passwort wird nie geloggt
                login_btn = find_button(page, NEXT_RE)
                if login_btn is None:
                    raise RuntimeError("Weiter-Button fuer Login nicht gefunden")
                login_btn.click()
                page.wait_for_timeout(4000)  # Server-Antwort + Fehlermeldung abwarten
                shot(page, "02_login")
                dump_inputs(page)
                if find_input(page, PASS_RE) is not None:
                    msg = page.evaluate("() => { const lines = document.body.innerText.split(String.fromCharCode(10)); const hit = lines.find(l => /abgelehnt|fehler|ung\u00f6ltig|falsch|gesperrt|aktivier|abgelaufen/i.test(l)); return hit || lines.join(' | ').slice(0, 600); }")
                    log.info("[%s] Portal-Meldung nach Login-Versuch: %s", kind, msg)
                    raise RuntimeError("Login wurde abgelehnt – Benutzername/Passwort prüfen")
                login_ok = True
                log.info("[%s] Im Portal angemeldet", kind)
                # Nach Login: Dashboard sichtbar machen und Diagnostic loggen
                page.wait_for_timeout(4000)
                dump_inputs(page)
                interactive = page.evaluate(
                    "() => JSON.stringify(Array.from(document.querySelectorAll('button,[role=button],a'))"
                    ".filter(e => !e.disabled && e.offsetParent !== null)"
                    ".map(e => ({tag: e.tagName, aria: e.getAttribute('aria-label') || '', text: (e.textContent||'').trim().slice(0,40)}))"
                    ".slice(0, 40))"
                )
                log.info("[%s] Nach Login: Elemente: %s", kind, interactive)
                snippet = page.evaluate(
                    "() => document.body.innerText.split(String.fromCharCode(10)).join(' | ').slice(0, 600)"
                )
                log.info("[%s] Nach Login: Text: %s", kind, snippet)
                # Login zeigt "Vertrag auswählen" – Karte zur passenden Sparte wählen (JS, da Text über mehrere Elemente verteilt ist).
                sparte = "gas" if kind == "gas" else "strom|electric|elektrizit"
                picked = page.evaluate(
                    "(sparte) => { const re = new RegExp('Sparte:\\s*(" + sparte + ")', 'i');"
                    " const hits = [];"
                    " function collect(root) {"
                    "   for (const el of root.querySelectorAll('*')) {"
                    "     if (el.offsetWidth > 0 && re.test(el.innerText || '')) hits.push(el);"
                    "     if (el.shadowRoot) collect(el.shadowRoot);"
                    "   }"
                    " }"
                    " collect(document);"
                    " if (!hits.length) return null;"
                    " hits.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);"
                    " const el = hits[0]; el.scrollIntoView(); el.click();"
                    " return el.tagName + ' ' + el.innerText.trim().slice(0, 60); }",
                    sparte
                )
                if picked is not None:
                    log.info("[%s] Vertrag-Karte geklickt: %s", kind, picked)
                    page.wait_for_timeout(3000)
                    shot(page, "03_contract")
                    dump_inputs(page)
                else:
                    picked = contract_click_via_cdp(context, page, kind)
                    if picked is not None:
                        log.info("[%s] Vertrag-Karte via CDP geklickt: %s", kind, picked)
                        page.wait_for_timeout(3000)
                        shot(page, "03_contract")
                        dump_inputs(page)
                    else:
                        log.info("[%s] Keine Vertrag-Karte für Sparte gefunden", kind)

            filled_account = False
            filled_reading = False
            filled_date = False
            today = dt.date.today().strftime("%d.%m.%Y")

            for step in range(1, 8):
                if login_ok and not filled_date:
                    date_field = find_input(page, DATE_RE)
                    if date_field is None and cdp is not None:
                        _nd, box = ax_find_node(cdp, DATE_RE, ("textbox",))
                        if box is not None:
                            page.mouse.click(box[0], box[1])
                            page.keyboard.type(today)
                            filled_date = True
                            log.info("[%s] Datum %s per AX-Feld eingetragen", kind, today)
                    if date_field is not None:
                        date_field.fill(today)
                        filled_date = True
                        log.info("[%s] Datum %s eingetragen", kind, today)

                if login_ok and not filled_reading:
                    inp = find_reading_input(page, kind)
                    if inp is None and cdp is not None:
                        _nd, box = ax_find_node(cdp, READING_RE, ("textbox",))
                        if box is not None:
                            page.mouse.click(box[0], box[1])
                            page.keyboard.type(reading)
                            filled_reading = True
                            log.info("[%s] Zählerstand %s per AX-Feld eingetragen", kind, reading)
                    if inp is not None:
                        inp.fill(reading)
                        filled_reading = True
                        log.info("[%s] Zählerstand %s eingetragen", kind, reading)

                if not filled_account:
                    acc_field = find_input(page, ACCOUNT_RE)
                    if acc_field is None and cdp is not None:
                        _nd, box = ax_find_node(cdp, ACCOUNT_RE, ("textbox",))
                        if box is not None:
                            page.mouse.click(box[0], box[1])
                            page.keyboard.type(account)
                            _nd, box2 = ax_find_node(cdp, PLZ_RE, ("textbox",))
                            if box2 is not None and plz:
                                page.mouse.click(box2[0], box2[1])
                                page.keyboard.type(plz)
                            filled_account = True
                            log.info("[%s] Vertragskonto %s per AX-Feld eingetragen", kind, mask(account))
                    if acc_field is not None:
                        acc_field.fill(account)
                        plz_field = find_input(page, PLZ_RE)
                        if plz_field is not None and plz:
                            plz_field.fill(plz)
                        filled_account = True
                        log.info("[%s] Vertragskonto %s eingetragen", kind, mask(account))

                shot(page, f"{step + 1:02d}_schritt")
                dump_inputs(page)

                if filled_reading:
                    submit = find_button(page, SUBMIT_RE)
                    if submit is None and cdp is not None:
                        _nd, box = ax_find_node(cdp, SUBMIT_RE, ("button",))
                        if box is not None:
                            if dry_run:
                                shot(page, "99_dry_run_vor_absenden")
                                log.info("[%s] DRY-RUN: Absenden wird NICHT ausgelöst", kind)
                                return "dry-run"
                            page.mouse.click(box[0], box[1])
                            seen = False
                            for _ in range(10):
                                try:
                                    txt = page.evaluate("() => document.body.innerText")
                                except Exception:
                                    txt = ""
                                if SUCCESS_RE.search(txt):
                                    seen = True
                                    break
                                page.wait_for_timeout(2500)
                            if not seen:
                                shot(page, "98_keine_bestaetigung")
                                raise RuntimeError("Keine Erfolgsmeldung nach dem Absenden erkannt – bitte prüfen")
                            shot(page, "99_abgesendet")
                            log.info("[%s] Zählerstand übermittelt", kind)
                            return "submitted"
                    if submit is not None:
                        if dry_run:
                            shot(page, "99_dry_run_vor_absenden")
                            log.info("[%s] DRY-RUN: Absenden wird NICHT ausgelöst", kind)
                            return "dry-run"
                        submit.click()
                        page.wait_for_timeout(1500)
                        seen = False
                        for _ in range(10):
                            try:
                                txt = page.evaluate("() => document.body.innerText")
                            except Exception:
                                txt = ""
                            if SUCCESS_RE.search(txt):
                                seen = True
                                break
                            page.wait_for_timeout(2500)
                        if not seen:
                            shot(page, "98_keine_bestaetigung")
                            raise RuntimeError("Keine Erfolgsmeldung nach dem Absenden erkannt – bitte prüfen")
                        shot(page, "99_abgesendet")
                        log.info("[%s] Zählerstand übermittelt", kind)
                        return "submitted"

                nxt = find_button(page, NEXT_RE)
                if nxt is None and cdp is not None:
                    _nd, box = ax_find_node(cdp, NEXT_RE, ("button",))
                    if box is not None:
                        log.info("[%s] 'Weiter' per AX geklickt bei (%.0f, %.0f)", kind, box[0], box[1])
                        page.mouse.click(box[0], box[1])
                        page.wait_for_timeout(1500)
                        continue
                if nxt is None and cdp is not None:
                    nodes = cdp.send("Accessibility.getFullAXTree").get("nodes", [])
                    names = []
                    for nd in nodes:
                        role = (nd.get("role") or {}).get("value") or ""
                        nm = ((nd.get("name") or {}).get("value") or "").strip()
                        if role in ("button", "link", "checkbox", "listitem") and nm:
                            names.append(f"{role}: {nm[:45]}")
                    log.info("[%s] AX-Elemente: %s", kind, json.dumps(names[:30], ensure_ascii=False))
                if nxt is None and login_ok and not filled_reading:
                    # Das Zählerformular rendert nach dem Karten-Klick oft erst 1-2 s
                    # später: wenn Felder gerade erst sichtbar werden, kurz warten und
                    # den Schritt wiederholen statt abbrechen.
                    _nd, ax_reading = ax_find_node(cdp, READING_RE, ("textbox",)) if cdp is not None else (None, None)
                    if find_reading_input(page, kind) is not None or find_input(page, DATE_RE) is not None or ax_reading is not None:
                        log.info("[%s] Formularfelder gerade erst sichtbar – Nachladen abwarten, Schritt %d wird wiederholt", kind, step)
                        page.wait_for_timeout(2000)
                        continue
                if nxt is None:
                    raise RuntimeError(f"Kein 'Weiter'/'Absenden'-Button gefunden (Schritt {step})")
                nxt.click()
                page.wait_for_timeout(1500)

            raise RuntimeError("Formular nach 7 Schritten nicht abgeschlossen")
        except Exception:
            shot(page, "ERROR")
            try:
                (SHOT_DIR / f"{stamp}_{kind}_ERROR.html").write_text(page.content())
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()


