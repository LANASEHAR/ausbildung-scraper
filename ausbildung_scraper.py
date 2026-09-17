import hashlib
import json
import os
import random
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.arbeitsagentur.de"
TARGET_URL = (
    "https://www.arbeitsagentur.de/jobsuche/suche"
    "?suchbereich=ausbildung&was=Kaufmann/-frau"
)
MAX_RESULTS = 25
DETAIL_DELAY_RANGE = (0.6, 1.2)
REQUEST_TIMEOUT = 20

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15",
]

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
JOB_LINK_RE = re.compile(r"/jobsuche/jobdetail/", re.I)


def get_session():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": BASE_URL + "/jobsuche/",
        }
    )
    return session


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def first_email(text):
    for email in EMAIL_RE.findall(text or ""):
        email = email.lower().rstrip(".,;:")
        if not email.endswith(("@arbeitsagentur.de", "@arbeitsagentur.com")):
            return email
    return ""


def extract_first_email(soup):
    # Prefer mailto links because they are explicit application/contact addresses.
    for link in soup.select('a[href^="mailto:"]'):
        email = link.get("href", "").split(":", 1)[-1].split("?", 1)[0]
        email = first_email(email)
        if email:
            return email

    return first_email(soup.get_text(" ", strip=True))


def parse_search_results(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not JOB_LINK_RE.search(href):
            continue

        link = urljoin(BASE_URL, href)
        if link in seen:
            continue
        seen.add(link)

        title = clean_text(anchor.get_text(" ", strip=True))
        if not title or re.match(r"^\d+:", title):
            title = clean_text(anchor.find_next(string=True) or "Ausbildung Kaufmann/-frau")

        # The result card contains the company/location in nearby text. The detail
        # page is used below for authoritative values and the application email.
        card = anchor.parent
        card_text = clean_text(card.get_text(" ", strip=True)) if card else ""

        jobs.append(
            {
                "title": title,
                "link": link,
                "card_text": card_text,
            }
        )

        if len(jobs) >= MAX_RESULTS:
            break

    return jobs


def parse_detail(session, result):
    response = session.get(result["link"], timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))

    title = ""
    heading = soup.find("h1")
    if heading:
        title = clean_text(heading.get_text(" ", strip=True))
        title = re.sub(r"^Stellenangebot:\s*", "", title, flags=re.I)

    if not title:
        title = result["title"]

    company = ""
    match = re.search(r"bei\s+(.+?)(?:\s+Das Wichtigste|\s+##|$)", text, re.I)
    if match:
        company = clean_text(match.group(1))

    if not company:
        # Usually the company appears immediately after the job title in the detail page.
        lines = [clean_text(x) for x in soup.stripped_strings]
        for index, line in enumerate(lines):
            if line == title and index + 1 < len(lines):
                company = lines[index + 1]
                break

    location = "Deutschland"
    location_match = re.search(r"(?:Arbeitsort|Ort)\s+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|\s+##|$)", text, re.I)
    if location_match:
        location = clean_text(location_match.group(1))

    email = extract_first_email(soup)
    if not email:
        return None

    job_id_match = re.search(r"/jobsuche/jobdetail/([^/?#]+)", result["link"])
    source_id = job_id_match.group(1) if job_id_match else hashlib.sha256(result["link"].encode()).hexdigest()[:16]
    job_id = f"aa_{source_id}"

    return {
        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
        "statut": "NOUVEAU",
        "role_cible": "Ausbildung Kaufmann/-frau",
        "intitule": title,
        "entreprise": company or "Entreprise non indiquée",
        "lieu": location,
        "emails_rh": email,
        "source": "Agentur für Arbeit",
        "lien": result["link"],
        "id": job_id,
    }


def scrape_arbeitsagentur():
    print("[+] Recherche des Ausbildungsplätze Kaufmann/-frau...")
    session = get_session()

    try:
        response = session.get(TARGET_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[!] Impossible d'accéder à la Jobsuche BA: {exc}")
        return []

    results = parse_search_results(response.text)
    print(f"[+] {len(results)} offres candidates trouvées sur la page de recherche.")

    jobs = []
    for index, result in enumerate(results, start=1):
        try:
            job = parse_detail(session, result)
            if job:
                jobs.append(job)
                print(f"[+] {index}/{len(results)} email trouvé: {job['emails_rh']}")
            else:
                print(f"[-] {index}/{len(results)} ignorée: aucune adresse email publique détectée.")
        except requests.RequestException as exc:
            print(f"[!] {index}/{len(results)} erreur détail: {exc}")
        except Exception as exc:
            print(f"[!] {index}/{len(results)} erreur parsing: {exc}")

        if index < len(results):
            time.sleep(random.uniform(*DETAIL_DELAY_RANGE))

    return jobs


def send_to_google_sheet_webhook(jobs):
    webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()

    if not webhook_url:
        raise RuntimeError("Secret GOOGLE_SHEET_WEBHOOK_URL introuvable.")

    if not jobs:
        print("[i] Aucune offre avec email valide à envoyer.")
        return

    # The Apps Script currently processes a batch; keep the payload bounded.
    jobs = jobs[:20]
    response = requests.post(
        webhook_url,
        json=jobs,
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    print(f"[+] Webhook Google Sheet: HTTP {response.status_code}")
    print(f"[+] Réponse: {response.text[:500]}")


def main():
    jobs = scrape_arbeitsagentur()
    print(f"[*] Offres prêtes à envoyer: {len(jobs)}")
    send_to_google_sheet_webhook(jobs)


if __name__ == "__main__":
    main()
