import hashlib
import os
import random
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.arbeitsagentur.de"
SEARCH_URL = BASE_URL + "/jobsuche/suche"

SEARCH_QUERIES = [
    "Kaufmann",
    "Kauffrau",
    "Kaufleute",
    "Kaufmann/-frau",
    "Kaufmann im E-Commerce",
    "Kauffrau im E-Commerce",
    "Kaufmann für Büromanagement",
    "Kauffrau für Büromanagement",
    "Kaufmann für Spedition und Logistikdienstleistung",
    "Kauffrau für Spedition und Logistikdienstleistung",
    "Kaufmann für Groß- und Außenhandelsmanagement",
    "Kauffrau für Groß- und Außenhandelsmanagement",
    "Kaufmann für Tourismus und Freizeit",
    "Kauffrau für Tourismus und Freizeit",
]

TARGET_OFFERS = 800
MAX_SEARCH_PAGES_PER_QUERY = 40
MAX_DETAIL_PAGES = 1400
DETAIL_WORKERS = 8
DETAIL_TIMEOUT = 15
WEBHOOK_TIMEOUT = 60
WEBHOOK_RETRIES = 4
DETAIL_DELAY = (0.20, 0.55)
BATCH_SIZE = 10

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
]

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
JOB_LINK_RE = re.compile(r"/jobsuche/jobdetail/", re.I)


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": BASE_URL + "/jobsuche/",
        "Connection": "keep-alive",
    })
    return s


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def first_email(text):
    for email in EMAIL_RE.findall(text or ""):
        email = email.lower().rstrip(".,;:")
        if email.endswith("@arbeitsagentur.de"):
            continue
        return email
    return ""


def extract_email(soup):
    for a in soup.select('a[href^="mailto:"]'):
        email = first_email(a.get("href", "").split(":", 1)[-1].split("?", 1)[0])
        if email:
            return email
    return first_email(soup.get_text(" ", strip=True))


def extract_job_links(html):
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not JOB_LINK_RE.search(href):
            continue
        link = urljoin(BASE_URL, href)
        if link not in seen:
            seen.add(link)
            found.append(link)
    return found


def search_page(session, query, page):
    params = {
        "suchbereich": "ausbildung",
        "was": query,
        "wo": "Deutschland",
        "page": page,
    }
    r = session.get(SEARCH_URL, params=params, timeout=DETAIL_TIMEOUT)
    r.raise_for_status()
    return extract_job_links(r.text)


def collect_links():
    session = make_session()
    links = []
    seen = set()

    for query in SEARCH_QUERIES:
        print(f"[+] Recherche: {query}")
        empty_pages = 0

        for page in range(MAX_SEARCH_PAGES_PER_QUERY):
            try:
                batch = search_page(session, query, page)
            except requests.RequestException as exc:
                print(f"[!] Recherche échouée {query} page {page}: {exc}")
                break

            new_count = 0
            for link in batch:
                if link not in seen:
                    seen.add(link)
                    links.append(link)
                    new_count += 1
                    if len(links) >= MAX_DETAIL_PAGES:
                        break

            print(f"    page {page}: {new_count} nouvelles offres (total {len(links)})")

            if not batch or new_count == 0:
                empty_pages += 1
            else:
                empty_pages = 0

            if empty_pages >= 2 or len(links) >= MAX_DETAIL_PAGES:
                break

            # Small pause reduces the chance of triggering the source's rate limits.
            time.sleep(random.uniform(0.25, 0.60))

    print(f"[*] {len(links)} liens uniques collectés.")
    return links


def parse_detail(link):
    session = make_session()
    source_id_match = re.search(r"/jobsuche/jobdetail/([^/?#]+)", link)
    source_id = source_id_match.group(1) if source_id_match else hashlib.sha256(link.encode()).hexdigest()[:20]
    job_id = "aa_" + source_id

    try:
        r = session.get(link, timeout=DETAIL_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = clean(soup.get_text(" ", strip=True))

        h1 = soup.find("h1")
        title = clean(h1.get_text(" ", strip=True)) if h1 else "Ausbildung Kaufmann/-frau"
        title = re.sub(r"^Stellenangebot:\s*", "", title, flags=re.I)

        email = extract_email(soup)

        company = ""
        for pattern in [
            r"bei\s+(.+?)(?:\s+Das Wichtigste|\s+Aufgaben|\s+Profil|\s+Arbeitsort|$)",
            r"Arbeitgeber\s*:?[ ]+(.+?)(?:\s+Arbeitsort|\s+Angebotsart|$)",
        ]:
            m = re.search(pattern, text, re.I)
            if m:
                company = clean(m.group(1))
                break

        if not company:
            lines = [clean(x) for x in soup.stripped_strings if clean(x)]
            for i, line in enumerate(lines):
                if line == title and i + 1 < len(lines):
                    candidate = lines[i + 1]
                    if 2 <= len(candidate) <= 180:
                        company = candidate
                        break

        location = "Deutschland"
        for pattern in [
            r"Arbeitsort\s*:?[ ]+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|\s+Beginn|$)",
            r"Ort\s*:?[ ]+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|$)",
        ]:
            m = re.search(pattern, text, re.I)
            if m:
                location = clean(m.group(1))
                break

        return {
            "date_detection": time.strftime("%Y-%m-%d %H:%M"),
            "statut": "NOUVEAU",
            "role_cible": "Ausbildung Kaufmann/Kauffrau",
            "intitule": title,
            "entreprise": company or "Entreprise non indiquée",
            "lieu": location,
            "emails_rh": email,
            "source": "Agentur für Arbeit",
            "lien": link,
            "id": job_id,
        }

    except requests.RequestException as exc:
        # Keep the offer even when the detail page is blocked. This means the
        # daily target is based on offers, not only on pages exposing an email.
        print(f"[!] détail inaccessible: {link} -> {exc}")
        return {
            "date_detection": time.strftime("%Y-%m-%d %H:%M"),
            "statut": "NOUVEAU",
            "role_cible": "Ausbildung Kaufmann/Kauffrau",
            "intitule": "Ausbildung Kaufmann/Kauffrau",
            "entreprise": "À vérifier",
            "lieu": "Deutschland",
            "emails_rh": "",
            "source": "Agentur für Arbeit",
            "lien": link,
            "id": job_id,
        }
    except Exception as exc:
        print(f"[!] parsing erreur: {link} -> {exc}")
        return {
            "date_detection": time.strftime("%Y-%m-%d %H:%M"),
            "statut": "NOUVEAU",
            "role_cible": "Ausbildung Kaufmann/Kauffrau",
            "intitule": "Ausbildung Kaufmann/Kauffrau",
            "entreprise": "À vérifier",
            "lieu": "Deutschland",
            "emails_rh": "",
            "source": "Agentur für Arbeit",
            "lien": link,
            "id": job_id,
        }
    finally:
        time.sleep(random.uniform(*DETAIL_DELAY))


def scrape_details(links):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    jobs = []
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        futures = {executor.submit(parse_detail, link): link for link in links}
        for n, future in enumerate(as_completed(futures), start=1):
            job = future.result()
            if job:
                jobs.append(job)
                if job["emails_rh"]:
                    print(f"[+] email {sum(1 for x in jobs if x['emails_rh'])}: {job['emails_rh']} | {job['entreprise']}")

            if n % 50 == 0:
                email_count = sum(1 for x in jobs if x["emails_rh"])
                print(f"[*] détails traités: {n}/{len(links)} | offres: {len(jobs)} | avec email: {email_count}")

    unique = {}
    for job in jobs:
        unique[job["id"]] = job

    jobs = list(unique.values())
    email_count = sum(1 for x in jobs if x["emails_rh"])
    print(f"[*] Offres finales: {len(jobs)} | avec email public: {email_count}")
    return jobs


def post_batch(session, jobs):
    webhook = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL manquant")

    last_error = None

    for attempt in range(1, WEBHOOK_RETRIES + 1):
        try:
            # Google Apps Script web apps can redirect from script.google.com to
            # script.googleusercontent.com. requests follows this redirect.
            r = session.post(
                webhook,
                json=jobs,
                timeout=(10, WEBHOOK_TIMEOUT),
                allow_redirects=True,
            )
            print(f"    webhook HTTP {r.status_code} | tentative {attempt}/{WEBHOOK_RETRIES}")
            r.raise_for_status()

            try:
                payload = r.json()
            except ValueError:
                payload = {"raw": r.text[:500]}

            if isinstance(payload, dict) and payload.get("status") == "error":
                raise RuntimeError("Google Apps Script: " + str(payload.get("message")))

            return payload

        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            print(f"    [!] webhook tentative {attempt}/{WEBHOOK_RETRIES}: {exc}")
            if attempt < WEBHOOK_RETRIES:
                time.sleep(3 * attempt)

    raise RuntimeError(f"Webhook impossible après {WEBHOOK_RETRIES} tentatives: {last_error}")


def send_to_sheet(jobs):
    session = make_session()
    total_added = 0
    total_sent = 0

    for start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[start:start + BATCH_SIZE]
        result = post_batch(session, batch)
        total_sent += len(batch)
        added = int(result.get("added", 0)) if isinstance(result, dict) else 0
        total_added += added
        print(f"[+] Batch {start + 1}-{start + len(batch)} envoyé: {result}")

        # Avoid hammering Apps Script between batches.
        time.sleep(0.5)

    print(f"[*] Total envoyé: {total_sent} | total ajouté selon Apps Script: {total_added}")


def main():
    print("=" * 70)
    print("AUSBILDUNG KAUFMANN/Kauffrau — DAILY MASS SCRAPER")
    print(f"Objectif: {TARGET_OFFERS}+ offres candidates")
    print("=" * 70)

    links = collect_links()
    jobs = scrape_details(links)

    if jobs:
        send_to_sheet(jobs)

    email_count = sum(1 for job in jobs if job["emails_rh"])
    if len(jobs) < TARGET_OFFERS:
        print(f"[!] Objectif de {TARGET_OFFERS} offres non atteint: {len(jobs)} offres exploitables.")
    else:
        print(f"[OK] Objectif offres atteint: {len(jobs)}")

    print(f"[OK] Emails publics trouvés: {email_count}")
    print("[OK] Run terminé.")


if __name__ == "__main__":
    main()
