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
READING_RE = re.compile(r"z(ä|ae)hlerstand", re.I)
NEXT_RE = re.compile(r"^\s*(weiter|fortfahren|prüfen|pruefen|los|zählerstand eingeben|zum zählerstand)\s*$", re.I)
SUBMIT_RE = re.compile(r"absenden|übermitteln|uebermitteln|abschicken|^\s*senden\s*$|zählerstand mitteilen|bestätigen", re.I)
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
    """Zählerstand-Eingabefeld; bei mehreren Feldern nach Strom/Gas unterscheiden.

    Liefert None, wenn kein Feld sichtbar ist; RuntimeError bei mehrdeutiger Wahl.
    """
    rows = page.evaluate(
        """() => [...document.querySelectorAll('input')].map((e, i) => ({
            i, visible: e.offsetParent !== null && !e.disabled, type: e.type || '',
            aria: e.getAttribute('aria-label') || '',
            placeholder: e.placeholder || '',
            label: (e.labels && e.labels[0] ? e.labels[0].innerText : ''),
            nearby: (e.closest('form,fieldset,.form-group,li,tr') || e.parentElement || document.body).innerText.slice(0, 200)
        }))"""
    )
    kind_re = KIND_RE.get(kind)
    hits = []
    for r in rows:
        if not r["visible"] or r["type"] in ("hidden", "checkbox", "radio", "submit"):
            continue
        meta = " ".join((r["aria"], r["placeholder"], r["label"]))
        if READING_RE.search(meta):
            hits.append((r, kind_re is not None and (kind_re.search(meta) or kind_re.search(r["nearby"]))))
    if not hits:
        return None
    if len(hits) == 1:
        return page.locator("input").nth(hits[0][0]["i"])
    exact = [r for r, k in hits if k]
    if len(exact) == 1:
        return page.locator("input").nth(exact[0]["i"])
    raise RuntimeError(
        f"{len(hits)} Zählerstand-Felder gefunden und nicht eindeutig als "
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
            """() => [...document.querySelectorAll('input,select,textarea')]
                .filter(e => e.offsetParent !== null)
                .map(e => ({type: e.type, name: e.name, id: e.id,
                            label: (e.labels && e.labels[0] ? e.labels[0].innerText : ''),
                            aria: e.getAttribute('aria-label') || '',
                            placeholder: e.placeholder || ''}))"""
        )
        log.info("Sichtbare Felder: %s", json.dumps(rows, ensure_ascii=False))
    except Exception:
        pass


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
                # SPA lädt nach Login verzögert; Reload darf die Session nicht verlieren.
                for _ in range(4):
                    page.wait_for_timeout(2500)
                    try:
                        if find_reading_input(page, kind) is not None:
                            break
                    except RuntimeError:
                        break
                    nav = first_visible(page.get_by_role("link", name=ZÄHLER_NAV_RE))
                    if nav is not None:
                        log.info("[%s] Navigation 'Zählerstand' geklickt", kind)
                        nav.click()
                        page.wait_for_timeout(2500)
                dump_inputs(page)

            filled_account = False
            filled_reading = False

            for step in range(1, 8):
                if login_ok and not filled_reading:
                    inp = find_reading_input(page, kind)
                    if inp is not None:
                        inp.fill(reading)
                        filled_reading = True
                        log.info("[%s] Zählerstand %s eingetragen", kind, reading)

                if not filled_account:
                    acc_field = find_input(page, ACCOUNT_RE)
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
                    if submit is not None:
                        if dry_run:
                            shot(page, "99_dry_run_vor_absenden")
                            log.info("[%s] DRY-RUN: Absenden wird NICHT ausgelöst", kind)
                            return "dry-run"
                        submit.click()
                        page.wait_for_timeout(1500)
                        try:
                            page.get_by_text(SUCCESS_RE).first.wait_for(timeout=25000)
                        except Exception:
                            shot(page, "98_keine_bestaetigung")
                            raise RuntimeError("Keine Erfolgsmeldung nach dem Absenden erkannt – bitte prüfen")
                        shot(page, "99_abgesendet")
                        log.info("[%s] Zählerstand übermittelt", kind)
                        return "submitted"

                nxt = find_button(page, NEXT_RE)
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


