import hashlib
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.arbeitsagentur.de"
SEARCH_URL = BASE_URL + "/jobsuche/suche"

SEARCH_QUERIES = [
    "Kaufmann", "Kauffrau", "Kaufleute", "Kaufmann/-frau",
    "Kaufmann im E-Commerce", "Kauffrau im E-Commerce",
    "Kaufmann für Büromanagement", "Kauffrau für Büromanagement",
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
WEBHOOK_TIMEOUT = 120
WEBHOOK_RETRIES = 3
DETAIL_DELAY = (0.20, 0.55)

# Deep public-email search for companies where the BA offer does not expose an email.
DEEP_SEARCH = True
DEEP_SEARCH_WORKERS = 4
DEEP_SEARCH_TIMEOUT = 12
DEEP_SEARCH_MAX_COMPANIES = 1400
DEEP_SEARCH_MAX_SITE_PAGES = 5
DEEP_SEARCH_DELAY = (0.35, 0.90)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
]

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
JOB_LINK_RE = re.compile(r"/jobsuche/jobdetail/", re.I)
BAD_EMAIL_DOMAINS = {
    "arbeitsagentur.de", "example.com", "example.org", "example.net",
}
BAD_SITE_DOMAINS = {
    "arbeitsagentur.de", "indeed.com", "stepstone.de", "linkedin.com",
    "xing.com", "meinestadt.de", "azubiyo.de", "ausbildung.de",
    "jobware.de", "monster.de", "kimeta.de", "stellenanzeigen.de",
    "jobvector.de", "hokify.de", "jobisjob.de", "glassdoor.de",
}
CONTACT_WORDS = ("kontakt", "contact", "impressum", "ansprechpartner", "karriere", "bewerbung")


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
        domain = email.split("@", 1)[-1]
        if domain not in BAD_EMAIL_DOMAINS and not email.endswith("@arbeitsagentur.de"):
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


def normalize_company(name):
    name = clean(name)
    name = re.sub(r"\b(GmbH|AG|KG|OHG|e\.K\.|GmbH & Co\. KG|UG|SE|mbH)\b", " ", name, flags=re.I)
    return clean(name)


def domain_is_bad(url):
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        return any(host == d or host.endswith("." + d) for d in BAD_SITE_DOMAINS)
    except Exception:
        return True


def likely_official_site(url, company):
    if not url.startswith(("http://", "https://")) or domain_is_bad(url):
        return False
    host = urlparse(url).netloc.lower()
    company_tokens = re.findall(r"[a-z0-9]{3,}", normalize_company(company).lower())
    host_tokens = re.findall(r"[a-z0-9]{3,}", host)
    if not company_tokens:
        return True
    overlap = sum(1 for token in company_tokens if any(token in h or h in token for h in host_tokens))
    return overlap >= 1


def search_official_site(session, company, location=""):
    company_clean = normalize_company(company)
    if not company_clean or company_clean in {"à vérifier", "entreprise non indiquée"}:
        return ""

    queries = [
        f'"{company_clean}" Kontakt Impressum E-Mail',
        f'"{company_clean}" Kontakt',
    ]
    for query in queries:
        try:
            url = "https://www.google.com/search?q=" + quote_plus(query) + "&num=10&hl=de"
            r = session.get(url, timeout=DEEP_SEARCH_TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            candidates = []
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if href.startswith("/url?q="):
                    href = href.split("/url?q=", 1)[1].split("&", 1)[0]
                if not href.startswith(("http://", "https://")):
                    continue
                if likely_official_site(href, company_clean):
                    candidates.append(href)
            if candidates:
                return candidates[0]
        except requests.RequestException:
            continue
        time.sleep(random.uniform(0.5, 1.2))
    return ""


def site_pages_to_check(home_url):
    urls = [home_url]
    try:
        r = requests.get(home_url, headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=DEEP_SEARCH_TIMEOUT)
        if r.ok:
            soup = BeautifulSoup(r.text, "html.parser")
            base_host = urlparse(home_url).netloc.lower()
            scored = []
            for a in soup.find_all("a", href=True):
                href = urljoin(home_url, a["href"])
                if urlparse(href).netloc.lower() != base_host:
                    continue
                label = clean(a.get_text(" ", strip=True)).lower()
                href_low = href.lower()
                score = sum(1 for word in CONTACT_WORDS if word in label or word in href_low)
                if score:
                    scored.append((score, href))
            for _, href in sorted(scored, reverse=True):
                if href not in urls:
                    urls.append(href)
                if len(urls) >= DEEP_SEARCH_MAX_SITE_PAGES:
                    break
    except requests.RequestException:
        pass

    # Common German contact endpoints as fallback.
    for suffix in ("/kontakt", "/contact", "/impressum", "/karriere"):
        candidate = urljoin(home_url.rstrip("/") + "/", suffix.lstrip("/"))
        if candidate not in urls and len(urls) < DEEP_SEARCH_MAX_SITE_PAGES:
            urls.append(candidate)
    return urls[:DEEP_SEARCH_MAX_SITE_PAGES]


def deep_find_email(company, location=""):
    session = make_session()
    site = search_official_site(session, company, location)
    if not site:
        return "", ""

    for page_url in site_pages_to_check(site):
        try:
            r = session.get(page_url, timeout=DEEP_SEARCH_TIMEOUT, allow_redirects=True)
            if not r.ok or "text/html" not in r.headers.get("Content-Type", "text/html"):
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            email = extract_email(soup)
            if email:
                return email, site
        except requests.RequestException:
            continue
        time.sleep(random.uniform(*DEEP_SEARCH_DELAY))
    return "", site


def enrich_missing_emails(jobs):
    if not DEEP_SEARCH:
        return jobs

    candidates = [j for j in jobs if not j.get("emails_rh") and j.get("entreprise") not in {"À vérifier", "Entreprise non indiquée"}]
    candidates = candidates[:DEEP_SEARCH_MAX_COMPANIES]
    print(f"[*] Deep search: {len(candidates)} entreprises sans email à vérifier sur le web.")

    found = 0
    with ThreadPoolExecutor(max_workers=DEEP_SEARCH_WORKERS) as executor:
        future_map = {
            executor.submit(deep_find_email, j.get("entreprise", ""), j.get("lieu", "")): j
            for j in candidates
        }
        for n, future in enumerate(as_completed(future_map), start=1):
            job = future_map[future]
            try:
                email, site = future.result()
            except Exception as exc:
                print(f"[!] deep search erreur {job.get('entreprise')}: {exc}")
                email, site = "", ""
            if email:
                job["emails_rh"] = email
                found += 1
                print(f"[+] DEEP EMAIL {found}: {email} | {job.get('entreprise')} | {site}")
            if site and not job.get("site_entreprise"):
                job["site_entreprise"] = site
            if n % 50 == 0:
                print(f"[*] deep search: {n}/{len(candidates)} | nouveaux emails: {found}")

    print(f"[OK] Deep search terminé: +{found} emails publics trouvés sur les sites des entreprises.")
    return jobs


def scrape_details(links):
    jobs = []
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        futures = {executor.submit(parse_detail, link): link for link in links}
        for n, future in enumerate(as_completed(futures), start=1):
            job = future.result()
            if job:
                jobs.append(job)
                if job["emails_rh"]:
                    email_count = sum(1 for x in jobs if x["emails_rh"])
                    print(f"[+] email {email_count}: {job['emails_rh']} | {job['entreprise']}")
            if n % 50 == 0:
                email_count = sum(1 for x in jobs if x["emails_rh"])
                print(f"[*] détails traités: {n}/{len(links)} | offres: {len(jobs)} | avec email: {email_count}")

    unique = {}
    for job in jobs:
        unique[job["id"]] = job
    jobs = list(unique.values())
    email_count = sum(1 for x in jobs if x["emails_rh"])
    print(f"[*] Offres finales: {len(jobs)} | avec email avant deep search: {email_count}")

    jobs = enrich_missing_emails(jobs)
    return jobs


def send_to_sheet(jobs):
    webhook = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL manquant")

    session = make_session()
    last_error = None

    # ONE HTTP request per complete daily scrape. No batches.
    for attempt in range(1, WEBHOOK_RETRIES + 1):
        try:
            print(f"[*] Envoi unique vers Google Sheets: {len(jobs)} offres")
            response = session.post(
                webhook,
                json=jobs,
                timeout=(15, WEBHOOK_TIMEOUT),
                allow_redirects=True,
            )
            print(f"[*] Webhook HTTP {response.status_code} | tentative {attempt}/{WEBHOOK_RETRIES}")
            response.raise_for_status()

            try:
                payload = response.json()
            except ValueError:
                payload = {"raw": response.text[:1000]}

            print(f"[*] Réponse Apps Script: {payload}")
            if isinstance(payload, dict) and payload.get("status") == "error":
                raise RuntimeError("Google Apps Script: " + str(payload.get("message")))

            print("[OK] Toutes les offres du run ont été envoyées en une seule requête.")
            return

        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            print(f"[!] Envoi échoué, tentative {attempt}/{WEBHOOK_RETRIES}: {exc}")
            if attempt < WEBHOOK_RETRIES:
                time.sleep(5 * attempt)

    raise RuntimeError(f"Webhook impossible après {WEBHOOK_RETRIES} tentatives: {last_error}")


def main():
    print("=" * 70)
    print("AUSBILDUNG KAUFMANN/Kauffrau — DAILY MASS SCRAPER + DEEP EMAIL SEARCH")
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
    print(f"[OK] Emails publics trouvés au total: {email_count}")
    print("[OK] Run terminé.")


if __name__ == "__main__":
    main()
