#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import html
import os
import re
import sys
import time
import random
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

import requests

GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SENDER_NAME = "Halima Essaouaf"
SIGNATURE_EMAIL = "essaouafhalima@gmail.com"
SIGNATURE_CITY = "Casablanca, Marokko"
SIGNATURE_PHONE = "+212 619 968 131"
SIGNATURE_LINKEDIN = "linkedin.com/in/halima-essaouaf-1b4b81202"

MAX_EMAILS_PER_RUN = max(1, min(int(os.environ.get("MAX_EMAILS_PER_RUN", "25")), 25))
GMAIL_DAILY_SAFETY_CAP = max(1, min(int(os.environ.get("GMAIL_DAILY_SAFETY_CAP", "500")), 500))
MIN_DELAY_SECONDS = 40
MAX_DELAY_SECONDS = 60
HTTP_TIMEOUT = 90

INVALID_COMPANY_MARKERS = {
    "", "entreprise non indiquée", "entreprise non indiquee",
    "unternehmen nicht angegeben", "unbekannt", "unknown", "n/a", "na",
    "none", "null", "unknown company", "unternehmen deutschland",
    "employeur à identifier", "employeur a identifier",
}

ROLE_CV_MAP = {
    "hotelfachfrau": "Bewerbungsmappe_Hotelfachfrau_Halima_Essaouaf.pdf",
    "systemgastronomie": "Bewerbungsmappe_Fachfrau_fuer_Systemgastronomie_Halima_Essaouaf.pdf",
    "einzelhandel": "Bewerbungsmappe_Kauffrau_im_Einzelhandel_Halima_Essaouaf.pdf",
    "spedition": "Bewerbungsmappe_Kauffrau_fuer_Spedition_und_Logistikdienstleistung_Halima_Essaouaf.pdf",
    "handel": "Bewerbungsmappe_Kauffrau_im_Gross-_und_Aussenhandelsmanagement_Halima_Essaouaf.pdf",
    "industrie": "Bewerbungsmappe_Industriekauffrau_Halima_Essaouaf.pdf",
    "buero": "Bewerbungsmappe_Kauffrau_Bueromanagement_Halima_Essaouaf.pdf",
}
ROLE_TITLES = {
    "hotelfachfrau": "Hotelfachfrau",
    "systemgastronomie": "Fachfrau für Systemgastronomie",
    "einzelhandel": "Kauffrau im Einzelhandel",
    "spedition": "Kauffrau für Spedition und Logistikdienstleistung",
    "handel": "Kauffrau im Groß- und Außenhandelsmanagement",
    "industrie": "Industriekauffrau",
    "buero": "Kauffrau für Büromanagement",
}
ROLE_MOTIVATIONS = {
    "hotelfachfrau": "Die Hotellerie ist mir bereits aus meiner beruflichen Erfahrung vertraut. Bei HBX Group / Hotelbeds betreute ich ein internationales B2B-Kundenportfolio im Bereich Hotellerie und Travel im Nahen Osten und arbeitete täglich mit Geschäftspartnern auf Arabisch, Französisch und Englisch. Insgesamt bringe ich über fünf Jahre Erfahrung in Kundenbetreuung, Vertrieb und kaufmännischen Abläufen mit. Diese Erfahrung möchte ich nun mit einer Ausbildung in Deutschland und einem anerkannten IHK-Abschluss weiterentwickeln.",
    "systemgastronomie": "Auch wenn mein bisheriger beruflicher Weg nicht direkt aus der Gastronomie kommt, bringe ich über fünf Jahre Erfahrung im Kundenservice, Vertrieb und in strukturierten Arbeitsabläufen mit. Als Top-Verkäuferin konnte ich bereits meine Stärke in Kundenkommunikation und Beratung unter Beweis stellen. Diese Erfahrung möchte ich nun in die Systemgastronomie einbringen und die professionellen Abläufe in Deutschland von Grund auf erlernen.",
    "einzelhandel": "Kundenberatung und Verkauf begleiten mich seit mehreren Jahren. In über fünf Jahren Berufserfahrung habe ich im B2B- und B2C-Vertrieb sowie im Kundenservice gearbeitet und wurde bei Umanis Intermediation aufgrund meiner Beratungsqualität und Abschlussstärke als Top-Verkäuferin ausgezeichnet. Heute gehören außerdem Bestandsüberwachung, Auftragsabwicklung und kaufmännische Aufgaben zu meinem Arbeitsalltag. Diese Erfahrung möchte ich nun gezielt mit einer deutschen Ausbildung und einem anerkannten IHK-Abschluss verbinden.",
    "spedition": "Logistik und Koordination sind mir bereits aus meiner Berufserfahrung vertraut. Bei Helpdesk ForYou koordinierte ich die Einsatzplanung von über 100 Fahrern und Mitarbeitenden und verfolgte Touren, Termine und Wartungen. Heute arbeite ich bei Atmlo Chem / EasyChemicalStock mit Beschaffung, Logistik, Bestandsüberwachung, Auftragsabwicklung und Lieferanten. Insgesamt bringe ich über fünf Jahre kaufmännische Berufserfahrung mit, die ich nun gezielt durch eine Ausbildung und einen anerkannten IHK-Abschluss erweitern möchte.",
    "handel": "Ich bringe über fünf Jahre Berufserfahrung in kaufmännischen Bereichen, Kundenbetreuung und Vertrieb mit. In meiner aktuellen Tätigkeit arbeite ich unter anderem mit Beschaffung, Lieferanten, Bestandsüberwachung und Auftragsabwicklung sowie mit Excel und Sage. Zuvor betreute ich bei HBX Group / Hotelbeds internationale B2B-Geschäftspartner. Diese Erfahrung möchte ich nun mit einer fundierten Ausbildung und einem anerkannten IHK-Abschluss in Deutschland verbinden.",
    "industrie": "Durch über fünf Jahre Berufserfahrung bringe ich bereits praktische Kenntnisse in kaufmännischer Organisation, Beschaffung, Auftragsabwicklung und Kundenbetreuung mit. Aktuell arbeite ich mit Lieferanten, Beständen, Rechnungen, Excel und dem ERP-System Sage. Ich möchte diese Praxiserfahrung nun mit den kaufmännischen Prozessen eines deutschen Unternehmens verbinden und dabei einen anerkannten IHK-Abschluss erwerben.",
    "buero": "Durch meine mehrjährige Berufserfahrung in Kundenbetreuung, Vertrieb und kaufmännischen Abläufen bringe ich bereits praktische Erfahrung in Organisation, Auftragsbearbeitung und strukturierter Kommunikation mit. Diese Erfahrung möchte ich nun gezielt mit einer deutschen Ausbildung und einem anerkannten IHK-Abschluss im Büromanagement verbinden.",
}

EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,63}$", re.I)

class SenderError(RuntimeError):
    pass

def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SenderError(f"Missing required environment variable: {name}")
    return value

def normalize_email(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^mailto:", "", text, flags=re.I)
    return text.strip(" <>.,;:\"'()[]").lower()

def valid_email(value: Any) -> str:
    email = normalize_email(value)
    return email if email and EMAIL_RE.fullmatch(email) else ""

def normalize_company(value: Any) -> tuple[str, bool]:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if raw.casefold().rstrip(".") in INVALID_COMPANY_MARKERS:
        return "in Ihrem Unternehmen", False
    return raw, True

def detect_role(intitule: str, role_cible: str) -> str:
    text = f"{intitule} {role_cible}".lower()
    normalized = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    if "hotelfach" in normalized or "hotelkauf" in normalized: return "hotelfachfrau"
    if "systemgastronomie" in normalized: return "systemgastronomie"
    if "einzelhandel" in normalized: return "einzelhandel"
    if "spedition" in normalized or "logistikdienstleistung" in normalized: return "spedition"
    if "gross- und aussenhandelsmanagement" in normalized or "grosshandel" in normalized or "aussenhandel" in normalized: return "handel"
    if "industriekauf" in normalized: return "industrie"
    if "bueromanagement" in normalized or "buero management" in normalized: return "buero"
    return ""

def signature_html() -> str:
    return (
        '<div style="font-family:Arial,sans-serif;font-size:13px;color:#333;margin-top:22px">'
        f"<strong>{html.escape(SENDER_NAME)}</strong><br>"
        f"{html.escape(SIGNATURE_CITY)}<br>"
        f'<a href="mailto:{html.escape(SIGNATURE_EMAIL)}">{html.escape(SIGNATURE_EMAIL)}</a><br>'
        f"{html.escape(SIGNATURE_PHONE)}<br>"
        f'<a href="https://{html.escape(SIGNATURE_LINKEDIN)}">{html.escape(SIGNATURE_LINKEDIN)}</a>'
        "</div>"
    )

def signature_text() -> str:
    return f"{SENDER_NAME}\n{SIGNATURE_CITY}\n{SIGNATURE_EMAIL}\n{SIGNATURE_PHONE}\n{SIGNATURE_LINKEDIN}"

def build_email(row: dict[str, Any]) -> tuple[str, str, str, str]:
    role = detect_role(str(row.get("intitule", "")), str(row.get("role_cible", "")))
    if role not in ROLE_TITLES:
        raise SenderError(f"Unsupported Ausbildung role: {row.get('role_cible')} / {row.get('intitule')}")
    title = ROLE_TITLES[role]
    company, known = normalize_company(row.get("entreprise"))
    if known:
        intro_html = f"mit großem Interesse bewerbe ich mich bei <strong>{html.escape(company)}</strong> um einen Ausbildungsplatz als <strong>{html.escape(title)}</strong>."
        intro_text = f"mit großem Interesse bewerbe ich mich bei {company} um einen Ausbildungsplatz als {title}."
    else:
        intro_html = f"mit großem Interesse bewerbe ich mich in Ihrem Unternehmen um einen Ausbildungsplatz als <strong>{html.escape(title)}</strong>."
        intro_text = f"mit großem Interesse bewerbe ich mich in Ihrem Unternehmen um einen Ausbildungsplatz als {title}."
    motivation = ROLE_MOTIVATIONS[role]
    body_html = (
        "<p>Sehr geehrte Damen und Herren,</p>"
        f"<p>{intro_html}</p>"
        f"<p>{html.escape(motivation)}</p>"
        "<p>Deutsch B1 habe ich abgeschlossen und bereite mich aktuell auf B2 vor. Meine vollständigen Bewerbungsunterlagen finden Sie im Anhang.</p>"
        "<p>Über die Gelegenheit, mich per Videogespräch persönlich vorzustellen, würde ich mich sehr freuen.</p>"
        "<p>Mit freundlichen Grüßen</p>"
        f"{signature_html()}"
    )
    body_text = (
        "Sehr geehrte Damen und Herren,\n\n"
        f"{intro_text}\n\n{motivation}\n\n"
        "Deutsch B1 habe ich abgeschlossen und bereite mich aktuell auf B2 vor. Meine vollständigen Bewerbungsunterlagen finden Sie im Anhang.\n\n"
        "Über die Gelegenheit, mich per Videogespräch persönlich vorzustellen, würde ich mich sehr freuen.\n\n"
        f"Mit freundlichen Grüßen\n{signature_text()}"
    )
    return f"Bewerbung um einen Ausbildungsplatz als {title} – {SENDER_NAME}", body_text, body_html, ROLE_CV_MAP[role]

class SheetGateway:
    def __init__(self, url: str):
        self.url = url

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(self.url, json=payload, timeout=HTTP_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "error":
            raise SenderError(data.get("message", "Apps Script error"))
        return data

    def export_queue(self, limit: int) -> dict[str, Any]:
        return self.post({"action": "export_send_queue", "limit": limit, "smtp_daily_safety_cap": GMAIL_DAILY_SAFETY_CAP})

    def get_cv(self, filename: str) -> tuple[str, bytes]:
        data = self.post({"action": "get_cv", "filename": filename})
        encoded = data.get("base64")
        if not encoded:
            raise SenderError(f"CV not returned: {filename}")
        return data.get("filename", filename), base64.b64decode(encoded)

    def update_status(self, row_number: int, status: str, message: str = "", cv_filename: str = "", sent_at: str = "") -> dict[str, Any]:
        return self.post({
            "action": "update_send_status", "row_number": row_number, "status": status,
            "message_envoye": message, "cv_utilise": cv_filename, "date_candidature": sent_at,
        })

class GmailAPI:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = ""

    def refresh_access_token(self) -> None:
        response = requests.post(
            GMAIL_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=HTTP_TIMEOUT,
        )
        if not response.ok:
            raise SenderError(f"OAuth token refresh failed: HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        self.access_token = data.get("access_token", "")
        if not self.access_token:
            raise SenderError("OAuth token refresh returned no access_token")

    def send(self, recipient: str, subject: str, body_text: str, body_html: str, attachment_name: str, attachment_bytes: bytes) -> None:
        if not self.access_token:
            self.refresh_access_token()
        message = EmailMessage()
        message["From"] = f"{SENDER_NAME} <{require_env('GMAIL_USER')}>"
        message["To"] = recipient
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid()
        message["Reply-To"] = require_env("GMAIL_USER")
        message.set_content(body_text)
        message.add_alternative(body_html, subtype="html")
        message.add_attachment(attachment_bytes, maintype="application", subtype="pdf", filename=attachment_name)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        response = requests.post(
            GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
            json={"raw": raw},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 401:
            self.refresh_access_token()
            response = requests.post(
                GMAIL_SEND_URL,
                headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
                json={"raw": raw},
                timeout=HTTP_TIMEOUT,
            )
        if not response.ok:
            raise SenderError(f"Gmail API send failed: HTTP {response.status_code}: {response.text[:1000]}")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-emails", type=int, default=MAX_EMAILS_PER_RUN)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    max_emails = max(1, min(args.max_emails, 25))
    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").lower() == "true"
    if dry_run:
        max_emails = 1

    gateway = SheetGateway(require_env("GOOGLE_SHEET_WEBHOOK_URL"))
    gmail_user = require_env("GMAIL_USER")
    api = GmailAPI(require_env("GMAIL_CLIENT_ID"), require_env("GMAIL_CLIENT_SECRET"), require_env("GMAIL_REFRESH_TOKEN"))

    queue = gateway.export_queue(max_emails)
    rows = queue.get("rows", [])
    sent_last_24h = int(queue.get("sent_last_24h", 0))
    if sent_last_24h >= GMAIL_DAILY_SAFETY_CAP:
        print(f"[STOP] Gmail API safety cap reached: {sent_last_24h}/{GMAIL_DAILY_SAFETY_CAP} in rolling 24h.")
        return 0
    rows = rows[:min(max_emails, GMAIL_DAILY_SAFETY_CAP - sent_last_24h)]
    if not rows:
        print("[OK] No ready applications.")
        return 0

    print(f"[QUEUE] {len(rows)} row(s) | sent last 24h={sent_last_24h}/{GMAIL_DAILY_SAFETY_CAP} | dry_run={dry_run}")
    cv_cache: dict[str, tuple[str, bytes]] = {}
    successful = 0

    for row in rows:
        row_number = int(row["row_number"])
        recipient = valid_email(row.get("emails_rh"))
        if not recipient:
            print(f"[SKIP] row={row_number}: invalid email")
            continue
        try:
            subject, body_text, body_html, cv_filename = build_email(row)
            if cv_filename not in cv_cache:
                cv_cache[cv_filename] = gateway.get_cv(cv_filename)
            attachment_name, attachment_bytes = cv_cache[cv_filename]
            target = gmail_user if dry_run else recipient
            if dry_run:
                subject = "[TEST GMAIL API] " + subject

            gateway.update_status(row_number, "Envoi Gmail API en cours", message=subject, cv_filename=attachment_name)
            api.send(target, subject, body_text, body_html, attachment_name, attachment_bytes)
            successful += 1
            print(f"[SENT] {successful}/{len(rows)} row={row_number} target={target}")

            if not dry_run:
                gateway.update_status(
                    row_number, "Envoyé", message=subject, cv_filename=attachment_name,
                    sent_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                print("[DRY_RUN] Row unchanged.")
                break

            if successful < len(rows):
                delay = random.randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
                print(f"[WAIT] {delay}s")
                time.sleep(delay)

        except SenderError as exc:
            print(f"[GMAIL API ERROR] row={row_number}: {exc}")
            if not dry_run:
                try:
                    gateway.update_status(row_number, "Erreur Gmail API", message=str(exc)[:1000])
                except Exception as update_exc:
                    print(f"[CRITICAL] status update failed for row {row_number}: {update_exc}")
        except (requests.RequestException, OSError, TimeoutError) as exc:
            print(f"[NETWORK ERROR] row={row_number}: {type(exc).__name__}: {exc}")
        except Exception as exc:
            print(f"[SKIP] row={row_number}: {type(exc).__name__}: {exc}")

    print(f"[DONE] Successful sends: {successful}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
