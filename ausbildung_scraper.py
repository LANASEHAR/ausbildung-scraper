"""
================================================================================
 AUSBILDUNG SCRAPER — MULTI-SOURCES ALLEMAGNE (VERSION PRODUCTION)
 Sources: Agentur für Arbeit API (Principal) | DuckDuckGo (Spontanées avec Fallback)
 Logique: Deep Scraping /kontakt /impressum /karriere | Zéro Hallucination
 Output: Google Sheet via Webhook + CSV local de sauvegarde
================================================================================
"""

import csv
import hashlib
import os
import random
import re
import sys
import time
from urllib.parse import urljoin, urlparse, quote_plus

import requests
from bs4 import BeautifulSoup

# Fix encodage console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ==============================================================================
# CONFIGURATION CENTRALE
# ==============================================================================

SPECIALITES = [
    ("Kaufmann/-frau für Büromanagement",                    "buero"),
    ("Kauffrau im E-Commerce",                               "ecommerce"),
    ("Kaufmann/-frau im Groß- und Außenhandelsmanagement",   "handel"),
    ("Kaufmann/-frau für Spedition und Logistikdienstleistung", "spedition"),
    ("Kaufmann/-frau für Tourismus und Freizeit",            "tourismus"),
]

EXCLUDED_EMAIL_DOMAINS = {
    "sentry.io", "wixpress.com", "schema.org", "example.com",
    "google.com", "facebook.com", "linkedin.com", "indeed.com",
    "youtube.com", "wikipedia.org", "twitter.com", "xing.com",
    "stepstone.de", "monster.de", "ausbildung.de", "azubiyo.de",
    "bundesagentur.de", "placeholder.com", "test.com", "example.de",
}

EXCLUDED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".zip"}

EXCLUDED_PREFIXES = {
    "noreply@", "no-reply@", "support@", "privacy@",
    "security@", "billing@", "admin@", "datenschutz@",
    "info-dse@", "newsletter@", "bounce@", "mailer-daemon@",
}

RH_KEYWORDS = [
    "bewerbung", "karriere", "ausbildung", "hr", "recruiting",
    "personal", "kontakt", "jobs", "ausbildungsbuero", "bewerber",
]

DEEP_PAGES = [
    "", "/kontakt", "/karriere", "/ausbildung", "/impressum",
    "/jobs", "/stellenangebote", "/ausbildung-bewerbung",
    "/en/contact", "/de/kontakt",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


# ==============================================================================
# UTILITAIRES HTTP & EMAIL
# ==============================================================================

def get_headers(referer="https://www.google.de/"):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.7,en;q=0.5",
        "Referer": referer,
        "DNT": "1",
        "Connection": "keep-alive",
    }

def fetch(url, timeout=8, retries=2):
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(0.4, 1.2))
            resp = requests.get(url, headers=get_headers(url), timeout=timeout, allow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (403, 429):
                time.sleep(2 ** attempt * 3)
        except Exception:
            pass
    return None

def clean_email(raw):
    if not raw:
        return None
    email = raw.strip().lower()
    email = re.sub(r"^[u003e>\"'\\]+", "", email)
    email = email.strip(".").strip()

    if "@" not in email or "." not in email.split("@")[-1]:
        return None
    if any(email.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
        return None
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
        return None

    domain = email.split("@")[-1]
    if any(excl in domain for excl in EXCLUDED_EMAIL_DOMAINS):
        return None
    if any(email.startswith(pre) for pre in EXCLUDED_PREFIXES):
        return None
    if len(email.split("@")[0]) > 50:
        return None
    return email

def deobfuscate(text):
    patterns = [
        (r"\s*\[at\]\s*",  "@"), (r"\s*\(at\)\s*",  "@"),
        (r"\s*\{at\}\s*",  "@"), (r"\s+at\s+",      "@"),
        (r"\s*\[dot\]\s*", "."), (r"\s*\(dot\)\s*", "."),
        (r"&#64;",         "@"), (r"&#46;",         "."),
    ]
    result = text
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result

def extract_emails(html):
    if not html:
        return set()
    clean_text = deobfuscate(html)
    raw_found = EMAIL_REGEX.findall(clean_text)

    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if href.lower().startswith("mailto:"):
                raw_found.append(href[7:].split("?")[0].strip())
    except Exception:
        pass

    result = set()
    for e in raw_found:
        cleaned = clean_email(e)
        if cleaned:
            result.add(cleaned)
    return result

def best_email(emails):
    if not emails:
        return None
    ranked = sorted(
        emails,
        key=lambda e: (-sum(1 for kw in RH_KEYWORDS if kw in e), len(e))
    )
    return " / ".join(ranked[:2])


# ==============================================================================
# DEEP SCRAPING — SITE DE L'ENTREPRISE
# ==============================================================================

def deep_scrape_company(company_url):
    if not company_url or "http" not in company_url:
        return None
    parsed = urlparse(company_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    all_emails = set()

    for path in DEEP_PAGES:
        target = urljoin(base_url, path) if path else company_url
        html = fetch(target, timeout=6)
        if not html:
            continue

        emails = extract_emails(html)
        rh_emails = {e for e in emails if any(kw in e for kw in RH_KEYWORDS)}
        if rh_emails:
            return best_email(rh_emails)
        all_emails.update(emails)

    return best_email(all_emails) if all_emails else None

def make_job_id(url, source):
    raw = f"{source}::{url}".encode("utf-8")
    return f"{source[:3].lower()}_{hashlib.md5(raw).hexdigest()[:8]}"


# ==============================================================================
# SOURCE 1 : AGENTUR FÜR ARBEIT — API REST OFFICIELLE (ZÉRO BLOCAGE)
# ==============================================================================

def scrape_arbeitsagentur_api():
    jobs = []
    headers = {
        "User-Agent": "Jobsuche/2.9.2 (de.arbeitsagentur.jobsuche; iOS 17.4)",
        "X-API-Key": "jobsuche-api-ro-prod"
    }
    
    print("\n[+] Interrogation de l'API Bundesagentur für Arbeit (Source fiable)...")
    for role_name, role_id in SPECIALITES:
        api_url = f"https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs?was={quote_plus(role_name)}&angebotsart=4&size=25"
        try:
            res = requests.get(api_url, headers=headers, timeout=12)
            if res.status_code == 200:
                items = res.json().get("stellenangebote", [])
                print(f"    -> {role_name} : {len(items)} offres réelles détectées")
                for it in items:
                    title = it.get("beruf", role_name)
                    company = it.get("arbeitgeber", "Unternehmen Deutschland")
                    ref_nr = it.get("refnr", "")
                    job_link = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref_nr}" if ref_nr else "https://www.arbeitsagentur.de"
                    job_id = f"aa_{ref_nr}" if ref_nr else f"aa_{hashlib.md5(title.encode()).hexdigest()[:8]}"
                    
                    jobs.append({
                        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
                        "statut": "NOUVEAU",
                        "role_cible": role_name,
                        "intitule": title,
                        "entreprise": company,
                        "lieu": it.get("arbeitsort", {}).get("ort", "Deutschland"),
                        "emails_rh": "Non détecté (Postuler via lien)", 
                        "source": "Agentur für Arbeit API",
                        "lien": job_link,
                        "id": job_id
                    })
            else:
                print(f"    [!] Erreur API {role_name} : Code {res.status_code}")
        except Exception as e:
            print(f"    [!] Exception API {role_name} : {e}")
            
    return jobs

# ==============================================================================
# SOURCE 2 : DUCKDUCKGO (CANDIDATURES SPONTANÉES)
# ==============================================================================

def scrape_spontaneous_via_duckduckgo():
    print("\n[SOURCE 2] DuckDuckGo HTML -- Candidatures spontanees...")
    jobs = []
    QUERIES_SPONTANEES = [
        ("Ausbildung Buromanagement Bewerbung Email Kontakt Unternehmen Deutschland 2025", "Kaufmann/-frau für Büromanagement"),
        ("Kaufmann E-Commerce Ausbildung Bewerbung Email Deutschland 2025", "Kauffrau im E-Commerce"),
    ]

    for i, (query, specialite_cible) in enumerate(QUERIES_SPONTANEES):
        print(f"  -> Requete : {query[:65]}...")
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl=de-de"
        html = fetch(url, timeout=12)

        if not html:
            print("    [!] Bloqué par DuckDuckGo (Cloudflare). Ignoré.")
            continue

        try:
            soup = BeautifulSoup(html, "lxml")
            for result in soup.find_all("a", class_="result__a")[:5]:
                href = result.get("href", "")
                if not href or "http" not in href:
                    continue

                parsed = urlparse(href)
                domain = parsed.netloc.lower()

                if any(skip in domain for skip in ["linkedin", "indeed", "stepstone", "wikipedia"]):
                    continue

                company_url = f"{parsed.scheme}://{parsed.netloc}"
                email = deep_scrape_company(company_url)

                if not email:
                    continue

                jobs.append({
                    "date_detection": time.strftime("%Y-%m-%d %H:%M"),
                    "statut":         "NOUVEAU",
                    "role_cible":     specialite_cible,
                    "intitule":       f"Ausbildung {specialite_cible} - Candidature Spontanee",
                    "entreprise":     domain.replace("www.", "").split(".")[0].title(),
                    "lieu":           "Deutschland",
                    "emails_rh":      email,
                    "source":         "Candidature Spontanee (DuckDuckGo)",
                    "lien":           company_url,
                    "id":             make_job_id(company_url, "sp"),
                })
        except Exception as e:
            pass
        time.sleep(random.uniform(2.0, 4.0))

    return jobs

# ==============================================================================
# DÉDUPLICATION & WEBHOOK
# ==============================================================================

def deduplicate(jobs, existing_ids=None):
    seen = set(existing_ids) if existing_ids else set()
    unique = []
    for job in jobs:
        jid = job.get("id", "")
        if jid and jid not in seen:
            seen.add(jid)
            unique.append(job)
    return unique

def load_existing_ids(filename):
    ids = set()
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if row.get("id"):
                        ids.add(row["id"].strip())
        except Exception:
            pass
    return ids

def send_to_webhook(jobs):
    webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("\n[i] GOOGLE_SHEET_WEBHOOK_URL non configure -> CSV uniquement")
        return False

    success = True
    for i in range(0, len(jobs), 50):
        batch = jobs[i:i + 50]
        try:
            resp = requests.post(webhook_url, json=batch, timeout=20)
            if resp.status_code == 200:
                print(f"  Webhook OK : {len(batch)} offres ajoutees a Google Sheet")
            else:
                print(f"  [!] Webhook HTTP {resp.status_code}")
                success = False
        except Exception as e:
            print(f"  [!] Erreur webhook: {e}")
            success = False
        time.sleep(1)
    return success

# ==============================================================================
# POINT D'ENTRÉE PRINCIPAL
# ==============================================================================

def main():
    print("=" * 70)
    print(" AUSBILDUNG SCRAPER -- MULTI-SOURCES ALLEMAGNE (VERSION PRODUCTION)")
    print("=" * 70)

    CSV_FILENAME = "ausbildung_applications_export.csv"
    existing_ids = load_existing_ids(CSV_FILENAME)
    
    all_new_jobs = []

    # Source 1 : API Agentur für Arbeit (100% fiable)
    try:
        all_new_jobs.extend(scrape_arbeitsagentur_api())
    except Exception as e:
        print(f"[!] Source BA echouee: {e}")

    # Source 2 : DuckDuckGo (Peut être bloqué par GitHub Actions)
    try:
        all_new_jobs.extend(scrape_spontaneous_via_duckduckgo())
    except Exception as e:
        print(f"[!] Source DuckDuckGo echouee: {e}")

    # Déduplication
    unique_new = deduplicate(all_new_jobs, existing_ids)

    print(f"\n{'=' * 70}")
    print(f" RESUME : {len(all_new_jobs)} offres detectees -> {len(unique_new)} nouvelles offres a envoyer")
    print(f"{'=' * 70}")

    if not unique_new:
        print("\n[!] Aucune nouvelle offre. Fin du scraper.")
        return

    # Sauvegarde CSV
    fieldnames = ["date_detection", "statut", "role_cible", "intitule", "entreprise", "lieu", "emails_rh", "source", "lien", "id"]
    existing_rows = {}
    if os.path.exists(CSV_FILENAME):
        try:
            with open(CSV_FILENAME, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    existing_rows[row["id"]] = row
        except Exception: pass

    for job in unique_new:
        existing_rows[job["id"]] = job

    with open(CSV_FILENAME, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows.values())

    # Envoi Webhook
    send_to_webhook(unique_new)

if __name__ == "__main__":
    main()
