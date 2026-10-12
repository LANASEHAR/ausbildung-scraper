import csv
import hashlib
import os
import random
import re
import sys
import time
from urllib.parse import quote_plus, urljoin
from bs4 import BeautifulSoup
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SPECIALITES_KAUFMANN = [
    "Kaufmann/-frau für Büromanagement",
    "Kauffrau im E-Commerce",
    "Kaufmann/-frau im Groß- und Außenhandelsmanagement",
    "Kaufmann/-frau für Spedition und Logistikdienstleistung",
    "Kaufmann/-frau für Tourismus und Freizeit",
    "Kauffrau im Einzelhandel",
    "Industriekauffrau"
]

EXCLUDED_DOMAINS = [
    "sentry.io", "wixpress.com", "schema.org", "example.com",
    "google.com", "facebook.com", "linkedin.com", "ausbildung.de", "azubiyo.de"
]
EXCLUDED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"]
EXCLUDED_PREFIXES = ["noreply@", "no-reply@", "support@", "privacy@", "security@", "billing@", "admin@"]
RH_KEYWORDS = ["bewerbung", "karriere", "ausbildung", "hr", "recruiting", "personal", "kontakt", "jobs"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
]

def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.de/"
    })
    return s

def clean_email(email):
    if not email:
        return None
    email = email.strip().strip(".").lower()
    if any(email.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
        return None
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return None
    domain = email.split("@")[-1]
    if any(ex in domain for ex in EXCLUDED_DOMAINS) or any(email.startswith(pre) for pre in EXCLUDED_PREFIXES):
        return None
    return email

def extract_real_emails(html_content):
    if not html_content:
        return set()
    clean_text = (
        html_content.replace(" [at] ", "@")
        .replace(" (at) ", "@")
        .replace("[at]", "@")
        .replace("(at)", "@")
        .replace(" [dot] ", ".")
    )
    raw = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", clean_text)
    soup = BeautifulSoup(html_content, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            mail = href.replace("mailto:", "").split("?")[0].strip()
            raw.append(mail)
    return {clean_email(e) for e in raw if clean_email(e)}

def deep_scrape_company_site(session, company_url):
    if not company_url or "http" not in company_url:
        return ""
    found_emails = set()
    sub_paths = ["", "/kontakt", "/karriere", "/ausbildung", "/impressum", "/jobs"]
    for path in sub_paths:
        try:
            target_url = urljoin(company_url, path)
            time.sleep(random.uniform(0.3, 0.7))
            resp = session.get(target_url, timeout=6)
            if resp.status_code == 200:
                found_emails.update(extract_real_emails(resp.text))
                if found_emails:
                    break
        except Exception:
            continue
    if found_emails:
        sorted_emails = sorted(
            list(found_emails),
            key=lambda x: any(k in x for k in RH_KEYWORDS),
            reverse=True
        )
        return sorted_emails[0]
    return ""

def scrape_german_portals():
    session = get_session()
    all_jobs = []
    print("\n[+] Lancement du scraping multi-spécialités...")

    for role in SPECIALITES_KAUFMANN:
        print(f"\n[+] Recherche active pour : {role}")
        page = 1
        empty_pages = 0

        while page <= 5:
            search_url = f"https://www.ausbildung.de/suche/?q={quote_plus(role)}&page={page}"
            try:
                time.sleep(random.uniform(1.2, 2.5))
                res = session.get(search_url, timeout=10)
                if res.status_code in [403, 429]:
                    print(f" [!] Accès restreint par le portail ({res.status_code}) sur {role}. Passage au rôle suivant.")
                    break
                if res.status_code != 200:
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                offer_links = soup.find_all("a", href=re.compile(r"/stellen/"))
                if not offer_links:
                    empty_pages += 1
                    if empty_pages >= 2:
                        break
                    page += 1
                    continue

                empty_pages = 0
                for a in offer_links:
                    href = a["href"]
                    if "faq" in href or "ratgeber" in href:
                        continue
                    link = urljoin("https://www.ausbildung.de", href)
                    title = a.text.strip() if a.text.strip() else role
                    company = "Unternehmen Deutschland"
                    match_comp = re.search(r"bei-([a-z0-9-]+)-in-", href)
                    if match_comp:
                        company = match_comp.group(1).replace("-", " ").title()

                    email_rh = deep_scrape_company_site(session, link)
                    job_id = f"aus_{hashlib.md5(link.encode()).hexdigest()[:8]}"

                    if email_rh and "@" in email_rh:
                        all_jobs.append({
                            "date_detection": time.strftime("%Y-%m-%d %H:%M"),
                            "statut": "NOUVEAU",
                            "role_cible": role,
                            "intitule": title,
                            "entreprise": company,
                            "lieu": "Deutschland",
                            "emails_rh": email_rh,
                            "source": "Ausbildung.de",
                            "lien": link,
                            "id": job_id
                        })
                page += 1
            except Exception as e:
                print(f" [!] Erreur sur la page {page} : {e}")
                break

    return all_jobs

def send_to_google_sheet_webhook(jobs):
    webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("[i] Aucun GOOGLE_SHEET_WEBHOOK_URL configuré. Données non transmises.")
        return

    if not jobs:
        print("[i] Aucune offre avec e-mail direct à transmettre.")
        return

    try:
        # Augmentation du timeout à 30 secondes pour le LockService de Google Apps Script
        res = requests.post(webhook_url, json=jobs, timeout=30, allow_redirects=True)
        if res.status_code == 200:
            print("🚀 SUCCÈS WEBHOOK : Offres insérées dans le Google Sheet.")
        else:
            print(f"[!] Erreur code retour Webhook : {res.status_code} | Contenu : {res.text[:200]}")
    except Exception as e:
        print(f"[!] Erreur critique lors de l'envoi webhook : {e}")

def run_scraper_job():
    jobs = scrape_german_portals()
    filename = "ausbildung_applications_export.csv"
    if jobs:
        fieldnames = ["date_detection", "statut", "role_cible", "intitule", "entreprise", "lieu", "emails_rh", "source", "lien", "id"]
        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(jobs)
        print(f"\n✅ {len(jobs)} offre(s) valide(s) avec e-mail RH enregistrée(s).")
        send_to_google_sheet_webhook(jobs)
    else:
        print("\n[!] Aucune offre avec e-mail direct n'a été extraite lors de ce run.")

if __name__ == "__main__":
    run_scraper_job()
