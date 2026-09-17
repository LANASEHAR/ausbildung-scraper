import hashlib
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ============================================================
# SOURCE: AUSBILDUNG ONLY
# ============================================================
BASE_URL = "https://www.arbeitsagentur.de"
SEARCH_URL = BASE_URL + "/jobsuche/suche"

SEARCH_QUERIES = [
    "Kaufmann/-frau", "Kaufmann", "Kauffrau", "Kaufleute",
    "Kaufmann für Büromanagement", "Kauffrau für Büromanagement",
    "Kaufmann im E-Commerce", "Kauffrau im E-Commerce",
    "Kaufmann für Spedition und Logistikdienstleistung",
    "Kauffrau für Spedition und Logistikdienstleistung",
    "Kaufmann für Groß- und Außenhandelsmanagement",
    "Kauffrau für Groß- und Außenhandelsmanagement",
    "Kaufmann für Tourismus und Freizeit", "Kauffrau für Tourismus und Freizeit",
    "Industriekaufmann", "Industriekauffrau",
]

TARGET_OFFERS = 800
MAX_SEARCH_PAGES_PER_QUERY = 40
MAX_DETAIL_PAGES = 1400
DETAIL_WORKERS = 8
DETAIL_TIMEOUT = 18

# Deep search is intentionally smaller/faster than before.
DEEP_SEARCH = True
DEEP_SEARCH_WORKERS = 8
DEEP_SEARCH_TIMEOUT = 12
DEEP_SEARCH_MAX_COMPANIES = 1400
DEEP_SEARCH_MAX_SITE_PAGES = 6
DEEP_SEARCH_MAX_SEARCH_RESULTS = 8
DEEP_SEARCH_DELAY = (0.15, 0.4)

WEBHOOK_TIMEOUT = 180
WEBHOOK_RETRIES = 3
SEARCH_DELAY = (0.45, 1.0)
DETAIL_DELAY = (0.25, 0.65)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
]

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
JOB_LINK_RE = re.compile(r"/jobsuche/jobdetail/", re.I)
BAD_EMAIL_DOMAINS = {
    "arbeitsagentur.de", "example.com", "example.org", "example.net",
    "sentry.io", "wixpress.com", "google.com", "bing.com", "duckduckgo.com",
    "acronymfinder.com", "thesaurus.com", "applied.com",
}
BAD_SITE_DOMAINS = {
    "arbeitsagentur.de", "indeed.com", "stepstone.de", "linkedin.com", "xing.com",
    "meinestadt.de", "azubiyo.de", "ausbildung.de", "jobware.de", "monster.de",
    "kimeta.de", "stellenanzeigen.de", "jobvector.de", "hokify.de", "jobisjob.de",
    "glassdoor.de", "facebook.com", "instagram.com", "youtube.com", "tiktok.com",
    "kununu.com", "meinpraktikum.de", "bing.com", "duckduckgo.com",
}
CONTACT_WORDS = (
    "kontakt", "contact", "impressum", "ansprechpartner", "karriere",
    "bewerbung", "bewerben", "ausbildung", "jobs", "career", "team",
)


def make_session(referer=None):
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
    })
    if referer:
        s.headers["Referer"] = referer
    return s


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_obfuscated_email(value):
    value = str(value or "")
    for pattern, replacement in [
        (r"\s*\[at\]\s*", "@"), (r"\s*\(at\)\s*", "@"),
        (r"\s+at\s+", "@"), (r"\s*\[dot\]\s*", "."),
        (r"\s*\(dot\)\s*", "."), (r"\s+dot\s+", "."),
    ]:
        value = re.sub(pattern, replacement, value, flags=re.I)
    return value


def valid_email(email):
    email = normalize_obfuscated_email(email).lower().strip(" <>.,;:\"'()[]")
    if not EMAIL_RE.fullmatch(email):
        return ""
    domain = email.split("@", 1)[-1]
    if domain in BAD_EMAIL_DOMAINS:
        return ""
    return email


def first_email(text):
    text = normalize_obfuscated_email(text)
    for candidate in EMAIL_RE.findall(text or ""):
        email = valid_email(candidate)
        if email:
            return email
    return ""


def extract_email(soup):
    for a in soup.select('a[href^="mailto:"]'):
        email = valid_email(a.get("href", "").split(":", 1)[-1].split("?", 1)[0])
        if email:
            return email
    email = first_email(soup.get_text(" ", strip=True))
    if email:
        return email
    for tag in soup.find_all(True):
        for value in tag.attrs.values():
            if isinstance(value, list):
                value = " ".join(map(str, value))
            email = first_email(str(value))
            if email:
                return email
    return first_email(str(soup))


def extract_job_links(html):
    soup = BeautifulSoup(html, "html.parser")
    found, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if JOB_LINK_RE.search(href):
            link = urljoin(BASE_URL, href)
            if link not in seen:
                seen.add(link)
                found.append(link)
    return found


def search_page(session, query, page):
    params = {"suchbereich": "ausbildung", "was": query, "wo": "Deutschland", "page": page}
    r = session.get(SEARCH_URL, params=params, timeout=DETAIL_TIMEOUT)
    r.raise_for_status()
    return extract_job_links(r.text)


def collect_links():
    session = make_session(BASE_URL + "/jobsuche/")
    links, seen = [], set()
    for query in SEARCH_QUERIES:
        if len(links) >= MAX_DETAIL_PAGES:
            break
        print(f"[+] AUSBILDUNG Recherche: {query}")
        empty_pages = 0
        for page in range(MAX_SEARCH_PAGES_PER_QUERY):
            try:
                batch = search_page(session, query, page)
            except requests.RequestException as exc:
                print(f"[!] Ausbildung recherche échouée {query} page {page}: {exc}")
                time.sleep(random.uniform(2.0, 4.0))
                continue
            new_count = 0
            for link in batch:
                if link not in seen:
                    seen.add(link)
                    links.append(link)
                    new_count += 1
                    if len(links) >= MAX_DETAIL_PAGES:
                        break
            print(f"    page {page}: {new_count} nouvelles offres (total {len(links)})")
            empty_pages = empty_pages + 1 if (not batch or new_count == 0) else 0
            if empty_pages >= 2 or len(links) >= MAX_DETAIL_PAGES:
                break
            time.sleep(random.uniform(*SEARCH_DELAY))
    print(f"[*] {len(links)} liens uniques AUSBILDUNG collectés.")
    return links


def parse_detail(link):
    session = make_session(BASE_URL + "/jobsuche/")
    match = re.search(r"/jobsuche/jobdetail/([^/?#]+)", link)
    source_id = match.group(1) if match else hashlib.sha256(link.encode()).hexdigest()[:20]
    base = {
        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
        "statut": "NOUVEAU", "role_cible": "Ausbildung Kaufmann/Kauffrau",
        "intitule": "Ausbildung Kaufmann/Kauffrau", "entreprise": "À vérifier",
        "lieu": "Deutschland", "emails_rh": "", "site_entreprise": "",
        "source": "Agentur für Arbeit - Ausbildung", "lien": link, "id": "aa_" + source_id,
    }
    try:
        r = session.get(link, timeout=DETAIL_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = clean(soup.get_text(" ", strip=True))
        h1 = soup.find("h1")
        title = clean(h1.get_text(" ", strip=True)) if h1 else base["intitule"]
        title = re.sub(r"^Stellenangebot:\s*", "", title, flags=re.I)
        email = extract_email(soup)
        company = ""
        for pattern in [
            r"bei\s+(.+?)(?:\s+Das Wichtigste|\s+Aufgaben|\s+Profil|\s+Arbeitsort|$)",
            r"Arbeitgeber\s*:?[ ]+(.+?)(?:\s+Arbeitsort|\s+Angebotsart|$)",
        ]:
            m = re.search(pattern, text, re.I)
            if m:
                company = clean(m.group(1)); break
        if not company:
            lines = [clean(x) for x in soup.stripped_strings if clean(x)]
            for i, line in enumerate(lines):
                if line == title and i + 1 < len(lines):
                    candidate = lines[i + 1]
                    if 2 <= len(candidate) <= 180:
                        company = candidate; break
        location = "Deutschland"
        for pattern in [
            r"Arbeitsort\s*:?[ ]+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|\s+Beginn|$)",
            r"Ort\s*:?[ ]+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|$)",
        ]:
            m = re.search(pattern, text, re.I)
            if m:
                location = clean(m.group(1)); break
        base.update({"intitule": title, "entreprise": company or "Entreprise non indiquée",
                     "lieu": location, "emails_rh": email})
        return base
    except requests.RequestException as exc:
        print(f"[!] détail inaccessible: {link} -> {exc}")
        return base
    except Exception as exc:
        print(f"[!] parsing erreur: {link} -> {exc}")
        return base
    finally:
        time.sleep(random.uniform(*DETAIL_DELAY))


def scrape_details(links):
    jobs = []
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        futures = {executor.submit(parse_detail, link): link for link in links}
        for n, future in enumerate(as_completed(futures), start=1):
            job = future.result()
            if job:
                jobs.append(job)
                if job.get("emails_rh"):
                    print(f"[+] email BA: {job['emails_rh']} | {job['entreprise']}")
            if n % 50 == 0:
                count = sum(1 for x in jobs if x.get("emails_rh"))
                print(f"[*] détails traités: {n}/{len(links)} | offres: {len(jobs)} | avec email: {count}")
    unique = {job["id"]: job for job in jobs}
    jobs = list(unique.values())
    print(f"[*] Offres finales: {len(jobs)} | avec email avant deep search: {sum(1 for x in jobs if x.get('emails_rh'))}")
    return jobs


# ============================================================
# DEEP SEARCH — UNIQUE COMPANIES + VERIFIED OFFICIAL SITES
# ============================================================

def normalize_company(name):
    name = clean(name).lower()
    name = re.sub(r"\b(gmbh|ag|kg|ohg|e\.k\.|gmbh & co\. kg|ug|se|mbh|ltd\.)\b", " ", name, flags=re.I)
    return clean(name)


def host_of(url):
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def domain_is_bad(url):
    host = host_of(url)
    return not host or any(host == d or host.endswith("." + d) for d in BAD_SITE_DOMAINS)


def company_tokens(company):
    generic = {"gmbh", "ag", "kg", "ohg", "ug", "se", "mbh", "co", "and", "der", "die", "das",
               "unternehmen", "group", "holding", "company", "deutschland", "ltd"}
    return [x for x in re.findall(r"[a-z0-9äöüß]{3,}", normalize_company(company)) if x not in generic]


def decode_search_url(href):
    href = unquote(href or "")
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/url?"):
        m = re.search(r"[?&](?:q|url)=([^&]+)", href)
        if m:
            href = unquote(m.group(1))
    return href


def search_engine_bing(session, query):
    r = session.get("https://www.bing.com/search?q=" + quote_plus(query) + "&count=8&setlang=de-DE", timeout=DEEP_SEARCH_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for item in soup.select("li.b_algo"):
        a = item.select_one("h2 a[href]")
        if a:
            snippet = item.select_one(".b_caption p")
            out.append((decode_search_url(a.get("href")), clean(a.get_text(" ", strip=True)), clean(snippet.get_text(" ", strip=True)) if snippet else ""))
    return out


def search_engine_duckduckgo(session, query):
    r = session.get("https://html.duckduckgo.com/html/?q=" + quote_plus(query), timeout=DEEP_SEARCH_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for item in soup.select(".result"):
        a = item.select_one(".result__a[href]")
        if a:
            snippet = item.select_one(".result__snippet")
            out.append((decode_search_url(a.get("href")), clean(a.get_text(" ", strip=True)), clean(snippet.get_text(" ", strip=True)) if snippet else ""))
    return out


def candidate_score(url, title, snippet, company):
    if not url.startswith(("http://", "https://")) or domain_is_bad(url):
        return -999
    host = host_of(url)
    tokens = company_tokens(company)
    score = 0
    for token in tokens:
        if token in host:
            score += 8
        elif any(part.startswith(token[:5]) for part in host.split(".")):
            score += 3
        if token in (title + " " + snippet).lower():
            score += 2
    if any(word in (title + " " + snippet).lower() for word in ("kontakt", "impressum", "offizielle", "official")):
        score += 2
    return score


def verify_official_site(url, company):
    """Open the candidate site and verify company identity before extracting any email."""
    if domain_is_bad(url):
        return "", ""
    root = f"https://{host_of(url)}/"
    tokens = company_tokens(company)
    try:
        r = requests.get(root, headers={"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "de-DE,de;q=0.9"}, timeout=DEEP_SEARCH_TIMEOUT, allow_redirects=True)
        if not r.ok:
            return "", ""
        final_root = f"https://{host_of(r.url)}/"
        if domain_is_bad(final_root):
            return "", ""
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = clean(soup.get_text(" ", strip=True)).lower()
        title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
        identity_hits = sum(1 for token in tokens if token in page_text or token in title.lower())
        # For short/ambiguous names, require at least one strong identity hit.
        if tokens and identity_hits == 0:
            return "", ""
        return final_root, extract_email(soup)
    except requests.RequestException:
        return "", ""


def search_company_web(company, location=""):
    company_clean = normalize_company(company)
    if not company_clean or company_clean in {"à vérifier", "entreprise non indiquée"}:
        return "", ""
    queries = [
        f'"{company_clean}" official website',
        f'"{company_clean}" Kontakt Impressum',
    ]
    session = make_session()
    candidates = []
    for query in queries:
        for engine in ("bing", "ddg"):
            try:
                results = search_engine_bing(session, query) if engine == "bing" else search_engine_duckduckgo(session, query)
            except requests.RequestException:
                continue
            for href, title, snippet in results[:DEEP_SEARCH_MAX_SEARCH_RESULTS]:
                score = candidate_score(href, title, snippet, company_clean)
                if score > 0:
                    candidates.append((score, href))
            time.sleep(random.uniform(0.1, 0.25))
    # Verify candidates in score order. NEVER trust an email from the search snippet.
    seen_hosts = set()
    for _, href in sorted(candidates, key=lambda x: x[0], reverse=True):
        host = host_of(href)
        if not host or host in seen_hosts:
            continue
        seen_hosts.add(host)
        site, email = verify_official_site(href, company_clean)
        if site:
            if email:
                return email, site
            # Site is verified; caller can still record it even if no email is visible.
            return "", site
    return "", ""


def enrich_missing_emails(jobs):
    if not DEEP_SEARCH:
        return jobs

    # One deep-search job per company, not one per offer.
    groups = {}
    for job in jobs:
        if job.get("emails_rh"):
            continue
        company = clean(job.get("entreprise"))
        if not company or company in {"À vérifier", "Entreprise non indiquée"}:
            continue
        key = normalize_company(company)
        groups.setdefault(key, {"company": company, "location": job.get("lieu", ""), "jobs": []})["jobs"].append(job)

    groups = list(groups.values())[:DEEP_SEARCH_MAX_COMPANIES]
    print(f"[*] Deep search: {len(groups)} entreprises UNIQUES à vérifier (déduplication activée).")
    found = 0
    sites = 0

    with ThreadPoolExecutor(max_workers=DEEP_SEARCH_WORKERS) as executor:
        future_map = {
            executor.submit(search_company_web, g["company"], g["location"]): g for g in groups
        }
        for n, future in enumerate(as_completed(future_map), start=1):
            group = future_map[future]
            try:
                email, site = future.result()
            except Exception as exc:
                print(f"[!] deep search erreur {group['company']}: {exc}")
                email, site = "", ""
            if site:
                sites += 1
            if email:
                found += 1
                for job in group["jobs"]:
                    job["emails_rh"] = email
                    job["site_entreprise"] = site
                print(f"[+] DEEP EMAIL {found}: {email} | {group['company']} | {site} | offres liées: {len(group['jobs'])}")
            elif site:
                for job in group["jobs"]:
                    job["site_entreprise"] = site
            if n % 25 == 0 or n == len(groups):
                print(f"[*] deep search: {n}/{len(groups)} | sites vérifiés: {sites} | nouveaux emails: {found}")

    print(f"[OK] Deep search terminé: +{found} emails publics vérifiés | {sites} sites officiels vérifiés.")
    return jobs


def post_json(payload, label):
    webhook = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL manquant")
    session = make_session()
    last_error = None
    for attempt in range(1, WEBHOOK_RETRIES + 1):
        try:
            size = len(payload) if isinstance(payload, list) else len(payload.get("jobs", []))
            print(f"[*] {label}: {size} éléments | tentative {attempt}/{WEBHOOK_RETRIES}")
            response = session.post(webhook, json=payload, timeout=(20, WEBHOOK_TIMEOUT), allow_redirects=True)
            print(f"[*] Webhook HTTP {response.status_code}")
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError:
                result = {"raw": response.text[:1000]}
            print(f"[*] Réponse Apps Script: {result}")
            if isinstance(result, dict) and result.get("status") == "error":
                raise RuntimeError("Google Apps Script: " + str(result.get("message")))
            return result
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            print(f"[!] {label} échoué: {exc}")
            if attempt < WEBHOOK_RETRIES:
                time.sleep(8 * attempt)
    raise RuntimeError(f"Webhook impossible après {WEBHOOK_RETRIES} tentatives: {last_error}")


def send_to_sheet(jobs):
    return post_json(jobs, "Envoi initial vers Google Sheets")


def update_sheet(jobs):
    updates = [
        {"id": j["id"], "emails_rh": j.get("emails_rh", ""), "site_entreprise": j.get("site_entreprise", "")}
        for j in jobs if j.get("emails_rh") or j.get("site_entreprise")
    ]
    if not updates:
        print("[*] Aucun email/site à mettre à jour dans Google Sheets.")
        return
    return post_json({"action": "update", "jobs": updates}, "Mise à jour emails/sites Google Sheets")


def main():
    print("=" * 72)
    print("AUSBILDUNG KAUFMANN/Kauffrau — DAILY SCRAPER")
    print("SOURCE: Bundesagentur für Arbeit — SUCHBEREICH=AUSBILDUNG")
    print(f"Objectif: {TARGET_OFFERS}+ offres candidates")
    print("=" * 72)

    # 1) Collect and parse offers.
    links = collect_links()
    jobs = scrape_details(links)

    # 2) IMPORTANT: send offers immediately. Do not wait for deep search.
    if jobs:
        send_to_sheet(jobs)
        print("[OK] Offres envoyées au Sheet AVANT le deep search.")

    # 3) Deep-search only UNIQUE companies.
    jobs = enrich_missing_emails(jobs)

    # 4) Update only email/site fields in existing Sheet rows.
    update_sheet(jobs)

    email_count = sum(1 for job in jobs if job.get("emails_rh"))
    site_count = sum(1 for job in jobs if job.get("site_entreprise"))
    if len(jobs) < TARGET_OFFERS:
        print(f"[!] Objectif {TARGET_OFFERS} offres non atteint: {len(jobs)} offres.")
    else:
        print(f"[OK] Objectif offres atteint: {len(jobs)}")
    print(f"[OK] Sites officiels vérifiés: {site_count}")
    print(f"[OK] Emails publics vérifiés: {email_count}")
    print("[OK] Run terminé.")


if __name__ == "__main__":
    main()
