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
SEARCH_QUERIES = [
    "Kaufmann/-frau",
    "Kaufmann",
    "Kauffrau",
]
MAX_CANDIDATES = 1200
MAX_DETAIL_REQUESTS = 1000
DETAIL_DELAY_RANGE = (0.25, 0.65)
REQUEST_TIMEOUT = 20
WEBHOOK_BATCH_SIZE = 20

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
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
            "Connection": "keep-alive",
        }
    )
    return session


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def first_email(text):
    for email in EMAIL_RE.findall(text or ""):
        email = email.lower().rstrip(".,;:")
        if email.endswith(("@arbeitsagentur.de", "@arbeitsagentur.com")):
            continue
        return email
    return ""


def extract_first_email(soup):
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
        if not title:
            title = "Ausbildung Kaufmann/-frau"
        jobs.append({"title": title, "link": link})
    return jobs


def build_search_urls():
    # The BA search supports broad Kaufmann/Kauffrau searches. We intentionally
    # combine variants and sort by newest so the daily run sees fresh postings.
    urls = []
    for query in SEARCH_QUERIES:
        encoded = requests.utils.quote(query, safe="")
        urls.append(
            f"{BASE_URL}/jobsuche/suche?suchbereich=ausbildung&was={encoded}&sort=veroeffentlichungsdatum"
        )
    return urls


def collect_candidates(session):
    all_results = []
    seen = set()
    for url in build_search_urls():
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            results = parse_search_results(response.text)
            print(f"[+] Recherche: {len(results)} offres sur {url.split('was=')[-1][:30]}")
            for result in results:
                if result["link"] not in seen:
                    seen.add(result["link"])
                    all_results.append(result)
        except requests.RequestException as exc:
            print(f"[!] Recherche inaccessible: {exc}")
    return all_results[:MAX_CANDIDATES]


def parse_detail(session, result):
    response = session.get(result["link"], timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))

    heading = soup.find("h1")
    title = clean_text(heading.get_text(" ", strip=True)) if heading else result["title"]
    title = re.sub(r"^Stellenangebot:\s*", "", title, flags=re.I)

    company = ""
    for pattern in (
        r"bei\s+(.+?)(?:\s+Das Wichtigste|\s+Arbeitsort|\s+Anstellungsart|$)",
        r"Arbeitgeber\s*[:\-]?\s*(.+?)(?:\s+Arbeitsort|\s+Anstellungsart|$)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            company = clean_text(match.group(1))
            break

    if not company:
        lines = [clean_text(x) for x in soup.stripped_strings if clean_text(x)]
        for index, line in enumerate(lines):
            if line == title and index + 1 < len(lines):
                company = lines[index + 1]
                break

    location = "Deutschland"
    for pattern in (
        r"Arbeitsort\s*[:\-]?\s*(.+?)(?:\s+Anstellungsart|\s+Ausbildungsberuf|\s+Veröffentlichungsdatum|$)",
        r"Ort\s*[:\-]?\s*(.+?)(?:\s+Anstellungsart|$)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            location = clean_text(match.group(1))
            break

    email = extract_first_email(soup)
    if not email:
        return None

    job_id_match = re.search(r"/jobsuche/jobdetail/([^/?#]+)", result["link"])
    source_id = job_id_match.group(1) if job_id_match else hashlib.sha256(result["link"].encode()).hexdigest()[:16]

    return {
        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
        "statut": "NOUVEAU",
        "role_cible": title,
        "intitule": title,
        "entreprise": company or "Entreprise non indiquée",
        "lieu": location,
        "emails_rh": email,
        "source": "Agentur für Arbeit",
        "lien": result["link"],
        "id": f"aa_{source_id}",
    }


def scrape_arbeitsagentur():
    print("[+] Recherche massive des Ausbildungsplätze Kaufmann/Kauffrau...")
    session = get_session()
    results = collect_candidates(session)
    print(f"[+] {len(results)} offres candidates uniques trouvées.")

    jobs = []
    checked = min(len(results), MAX_DETAIL_REQUESTS)
    for index, result in enumerate(results[:checked], start=1):
        try:
            job = parse_detail(session, result)
            if job:
                jobs.append(job)
                print(f"[+] {index}/{checked} email: {job['emails_rh']} | {job['entreprise']}")
            else:
                print(f"[-] {index}/{checked} aucune adresse email publique.")
        except requests.RequestException as exc:
            print(f"[!] {index}/{checked} erreur HTTP: {exc}")
        except Exception as exc:
            print(f"[!] {index}/{checked} erreur parsing: {exc}")
        if index < checked:
            time.sleep(random.uniform(*DETAIL_DELAY_RANGE))

    unique = {}
    for job in jobs:
        unique[job["id"]] = job
    jobs = list(unique.values())
    print(f"[*] Offres avec email valides: {len(jobs)}")
    return jobs


def send_to_google_sheet_webhook(jobs):
    webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("Secret GOOGLE_SHEET_WEBHOOK_URL introuvable.")
    if not jobs:
        print("[i] Aucune offre avec email valide à envoyer.")
        return

    total = (len(jobs) + WEBHOOK_BATCH_SIZE - 1) // WEBHOOK_BATCH_SIZE
    for batch_number in range(total):
        batch = jobs[batch_number * WEBHOOK_BATCH_SIZE : (batch_number + 1) * WEBHOOK_BATCH_SIZE]
        response = requests.post(
            webhook_url,
            json=batch,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        print(f"[+] Webhook batch {batch_number + 1}/{total}: HTTP {response.status_code} — {len(batch)} offres")
        print(f"[+] Réponse: {response.text[:300]}")
        time.sleep(0.5)


def main():
    jobs = scrape_arbeitsagentur()
    print(f"[*] Offres prêtes à envoyer: {len(jobs)}")
    send_to_google_sheet_webhook(jobs)


if __name__ == "__main__":
    main()
