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


def submit_reading(kind: str, account: str, plz: str, reading: str, *, dry_run: bool, headless: bool) -> str:
    """Trägt einen Zählerstand ein. Rückgabe: 'dry-run' oder 'submitted'."""
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
        context = browser.new_context(locale="de-DE", timezone_id="Europe/Berlin", viewport={"width": 1280, "height": 1600})
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

            filled_account = False
            filled_reading = False

            for step in range(1, 8):
                if not filled_account:
                    acc_field = find_input(page, ACCOUNT_RE)
                    if acc_field is not None:
                        acc_field.fill(account)
                        plz_field = find_input(page, PLZ_RE)
                        if plz_field is None:
                            raise RuntimeError("PLZ-Feld nicht gefunden")
                        plz_field.fill(plz)
                        filled_account = True
                        log.info("[%s] Vertragskonto %s und PLZ eingetragen", kind, mask(account))

                elif not filled_reading:
                    fields = page.get_by_label(READING_RE)
                    n = visible_count(fields)
                    if n > 1:
                        raise RuntimeError(
                            f"{n} Zählerstand-Felder gefunden (z. B. HT/NT oder mehrere Zähler) – "
                            "das wird absichtlich nicht automatisch ausgefüllt"
                        )
                    if n == 1:
                        first_visible(fields).fill(reading)
                        filled_reading = True
                        log.info("[%s] Zählerstand %s eingetragen", kind, reading)

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


