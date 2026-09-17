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

# These are all Ausbildung searches. The important filter is
# suchbereich=ausbildung. We do NOT search the generic Jobs area.
SEARCH_QUERIES = [
    "Kaufmann/-frau",
    "Kaufmann",
    "Kauffrau",
    "Kaufleute",
    "Kaufmann für Büromanagement",
    "Kauffrau für Büromanagement",
    "Kaufmann im E-Commerce",
    "Kauffrau im E-Commerce",
    "Kaufmann für Spedition und Logistikdienstleistung",
    "Kauffrau für Spedition und Logistikdienstleistung",
    "Kaufmann für Groß- und Außenhandelsmanagement",
    "Kauffrau für Groß- und Außenhandelsmanagement",
    "Kaufmann für Tourismus und Freizeit",
    "Kauffrau für Tourismus und Freizeit",
    "Industriekaufmann",
    "Industriekauffrau",
]

TARGET_OFFERS = 800
MAX_SEARCH_PAGES_PER_QUERY = 40
MAX_DETAIL_PAGES = 1400
DETAIL_WORKERS = 8
DETAIL_TIMEOUT = 18

# Deep company-site discovery
DEEP_SEARCH = True
DEEP_SEARCH_WORKERS = 6
DEEP_SEARCH_TIMEOUT = 15
DEEP_SEARCH_MAX_COMPANIES = 1400
DEEP_SEARCH_MAX_SITE_PAGES = 15
DEEP_SEARCH_MAX_SEARCH_RESULTS = 10
DEEP_SEARCH_DELAY = (0.5, 1.2)

# One complete POST to Apps Script per daily run.
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
    "sentry.io", "wixpress.com", "google.com",
}
BAD_SITE_DOMAINS = {
    "arbeitsagentur.de", "indeed.com", "stepstone.de", "linkedin.com",
    "xing.com", "meinestadt.de", "azubiyo.de", "ausbildung.de",
    "jobware.de", "monster.de", "kimeta.de", "stellenanzeigen.de",
    "jobvector.de", "hokify.de", "jobisjob.de", "glassdoor.de",
    "facebook.com", "instagram.com", "youtube.com", "tiktok.com",
    "kununu.com", "meinpraktikum.de",
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
    replacements = [
        (r"\s*\[at\]\s*", "@"),
        (r"\s*\(at\)\s*", "@"),
        (r"\s+at\s+", "@"),
        (r"\s*\[dot\]\s*", "."),
        (r"\s*\(dot\)\s*", "."),
        (r"\s+dot\s+", "."),
    ]
    for pattern, replacement in replacements:
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
    # 1. mailto links
    for a in soup.select('a[href^="mailto:"]'):
        email = valid_email(a.get("href", "").split(":", 1)[-1].split("?", 1)[0])
        if email:
            return email

    # 2. visible page text
    email = first_email(soup.get_text(" ", strip=True))
    if email:
        return email

    # 3. attributes / JSON-LD / scripts / HTML source
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
    # Explicitly select Ausbildung. Do not use angebotsart=1 / suchbereich=jobs.
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
    session = make_session(BASE_URL + "/jobsuche/")
    links = []
    seen = set()

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

            if not batch or new_count == 0:
                empty_pages += 1
            else:
                empty_pages = 0

            if empty_pages >= 2 or len(links) >= MAX_DETAIL_PAGES:
                break

            time.sleep(random.uniform(*SEARCH_DELAY))

    print(f"[*] {len(links)} liens uniques AUSBILDUNG collectés.")
    return links


def parse_detail(link):
    session = make_session(BASE_URL + "/jobsuche/")
    source_id_match = re.search(r"/jobsuche/jobdetail/([^/?#]+)", link)
    source_id = source_id_match.group(1) if source_id_match else hashlib.sha256(link.encode()).hexdigest()[:20]
    job_id = "aa_" + source_id

    base = {
        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
        "statut": "NOUVEAU",
        "role_cible": "Ausbildung Kaufmann/Kauffrau",
        "intitule": "Ausbildung Kaufmann/Kauffrau",
        "entreprise": "À vérifier",
        "lieu": "Deutschland",
        "emails_rh": "",
        "site_entreprise": "",
        "source": "Agentur für Arbeit - Ausbildung",
        "lien": link,
        "id": job_id,
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

        base.update({
            "intitule": title,
            "entreprise": company or "Entreprise non indiquée",
            "lieu": location,
            "emails_rh": email,
        })
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
                email_count = sum(1 for x in jobs if x.get("emails_rh"))
                print(f"[*] détails traités: {n}/{len(links)} | offres: {len(jobs)} | avec email: {email_count}")

    unique = {}
    for job in jobs:
        unique[job["id"]] = job
    jobs = list(unique.values())
    email_count = sum(1 for x in jobs if x.get("emails_rh"))
    print(f"[*] Offres finales: {len(jobs)} | avec email avant deep search: {email_count}")
    return jobs


# ============================================================
# DEEP SEARCH: OFFICIAL COMPANY WEBSITE
# ============================================================

def normalize_company(name):
    name = clean(name)
    name = re.sub(r"\b(GmbH|AG|KG|OHG|e\.K\.|GmbH & Co\. KG|UG|SE|mbH|Ltd\.)\b", " ", name, flags=re.I)
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
    # Remove legal suffixes and generic words that cause false matches.
    generic = {
        "gmbh", "ag", "kg", "ohg", "ug", "se", "mbh", "co", "and", "der", "die",
        "das", "unternehmen", "group", "holding", "company", "deutschland",
    }
    return [
        x for x in re.findall(r"[a-z0-9äöüß]{3,}", normalize_company(company).lower())
        if x not in generic
    ]


def official_domain_score(url, company):
    if not url.startswith(("http://", "https://")) or domain_is_bad(url):
        return -999
    host = host_of(url)
    tokens = company_tokens(company)
    if not tokens:
        return 1

    score = 0
    for token in tokens:
        if token in host:
            score += 5
        elif any(part.startswith(token[:5]) for part in host.split(".")):
            score += 2

    # Company websites are normally .de/.com/.eu, but don't require this.
    if host.endswith((".de", ".com", ".eu", ".net", ".org")):
        score += 1
    return score


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
    url = "https://www.bing.com/search?q=" + quote_plus(query) + "&count=10&setlang=de-DE"
    r = session.get(url, timeout=DEEP_SEARCH_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for item in soup.select("li.b_algo"):
        a = item.select_one("h2 a[href]")
        if not a:
            continue
        href = decode_search_url(a.get("href"))
        title = clean(a.get_text(" ", strip=True))
        snippet_node = item.select_one(".b_caption p")
        snippet = clean(snippet_node.get_text(" ", strip=True)) if snippet_node else ""
        results.append((href, title, snippet))
    return results


def search_engine_duckduckgo(session, query):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    r = session.get(url, timeout=DEEP_SEARCH_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for item in soup.select(".result"):
        a = item.select_one(".result__a[href]")
        if not a:
            continue
        href = decode_search_url(a.get("href"))
        title = clean(a.get_text(" ", strip=True))
        snippet_node = item.select_one(".result__snippet")
        snippet = clean(snippet_node.get_text(" ", strip=True)) if snippet_node else ""
        results.append((href, title, snippet))
    return results


def search_company_web(company, location=""):
    """Find the most likely official domain using multiple public search engines."""
    company_clean = normalize_company(company)
    if not company_clean or company_clean in {"à vérifier", "entreprise non indiquée"}:
        return "", ""

    queries = [
        f'"{company_clean}" official website',
        f'"{company_clean}" Kontakt Impressum',
        f'"{company_clean}" {location} Kontakt' if location else f'"{company_clean}" Kontakt',
        f'"{company_clean}" E-Mail',
    ]

    session = make_session("https://www.google.com/")
    candidates = []

    for query in queries:
        for engine in ("bing", "ddg"):
            try:
                if engine == "bing":
                    results = search_engine_bing(session, query)
                else:
                    results = search_engine_duckduckgo(session, query)
            except requests.RequestException:
                continue

            for href, title, snippet in results[:DEEP_SEARCH_MAX_SEARCH_RESULTS]:
                if not href.startswith(("http://", "https://")) or domain_is_bad(href):
                    continue

                # Search snippets themselves may already contain a public email.
                email = first_email(title + " " + snippet)
                score = official_domain_score(href, company_clean)
                if email:
                    score += 8
                if any(word in (title + " " + snippet).lower() for word in CONTACT_WORDS):
                    score += 2
                candidates.append((score, href, email))

            time.sleep(random.uniform(0.25, 0.6))

        if candidates and max(x[0] for x in candidates) >= 8:
            break

    if not candidates:
        return "", ""

    # Normalize candidates to their domain root and keep best score.
    best_by_host = {}
    for score, href, email in candidates:
        host = host_of(href)
        if not host:
            continue
        root = f"https://{host}/"
        current = best_by_host.get(host)
        if current is None or score > current[0]:
            best_by_host[host] = (score, root, email)

    best = sorted(best_by_host.values(), key=lambda x: x[0], reverse=True)[0]
    return best[2], best[1]


def same_domain(url_a, url_b):
    return host_of(url_a) == host_of(url_b)


def sitemap_urls(site):
    urls = []
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        url = urljoin(site, path)
        try:
            r = requests.get(
                url,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                timeout=DEEP_SEARCH_TIMEOUT,
            )
            if not r.ok or "xml" not in r.headers.get("Content-Type", "") and "sitemap" not in r.text[:200].lower():
                continue
            soup = BeautifulSoup(r.text, "xml")
            for loc in soup.find_all("loc"):
                href = clean(loc.get_text())
                if href.startswith("http"):
                    urls.append(href)
        except requests.RequestException:
            continue
    return urls[:80]


def contact_score(url, anchor_text=""):
    value = (url + " " + anchor_text).lower()
    score = 0
    for word in CONTACT_WORDS:
        if word in value:
            score += 10
    for word in ("email", "e-mail", "mail", "bewerbung"):
        if word in value:
            score += 5
    return score


def site_pages_to_check(site):
    site = site.rstrip("/") + "/"
    urls = [site]
    seen = {site}

    # Sitemap is often the fastest way to discover Impressum/Kontakt pages.
    for href in sitemap_urls(site):
        if same_domain(href, site) and href not in seen:
            score = contact_score(href)
            if score >= 10:
                urls.append(href)
                seen.add(href)

    # Crawl the homepage for internal contact/recruiting links.
    try:
        r = requests.get(
            site,
            headers={"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "de-DE,de;q=0.9"},
            timeout=DEEP_SEARCH_TIMEOUT,
        )
        if r.ok:
            soup = BeautifulSoup(r.text, "html.parser")
            scored = []
            for a in soup.find_all("a", href=True):
                href = urljoin(site, a["href"])
                if not same_domain(href, site):
                    continue
                label = clean(a.get_text(" ", strip=True))
                score = contact_score(href, label)
                if score:
                    scored.append((score, href))
            for _, href in sorted(scored, reverse=True):
                if href not in seen:
                    urls.append(href)
                    seen.add(href)
    except requests.RequestException:
        pass

    # Common paths as fallback. Many German companies expose only Impressum.
    for suffix in (
        "/kontakt", "/kontakt/", "/contact", "/impressum", "/impressum/",
        "/karriere", "/karriere/", "/bewerbung", "/jobs", "/ausbildung",
        "/unternehmen/kontakt", "/unternehmen/impressum",
    ):
        href = urljoin(site, suffix.lstrip("/"))
        if href not in seen:
            urls.append(href)
            seen.add(href)

    # Highest-value URLs first, then homepage.
    urls = sorted(urls, key=lambda u: (0 if u.rstrip("/") == site.rstrip("/") else 1, -contact_score(u)))
    return urls[:DEEP_SEARCH_MAX_SITE_PAGES]


def deep_find_email(company, location=""):
    # First: search engine snippets can expose the email without crawling the site.
    snippet_email, site = search_company_web(company, location)
    if snippet_email:
        return snippet_email, site

    if not site:
        return "", ""

    # Second: crawl the official site, prioritising Kontakt/Impressum/Bewerbung.
    for page_url in site_pages_to_check(site):
        try:
            r = requests.get(
                page_url,
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                },
                timeout=DEEP_SEARCH_TIMEOUT,
                allow_redirects=True,
            )
            if not r.ok:
                continue

            content_type = r.headers.get("Content-Type", "").lower()
            if content_type and "html" not in content_type and "xml" not in content_type:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            email = extract_email(soup)
            if email:
                return email, site

            # Check raw HTML for obfuscated emails hidden in JS/data attributes.
            email = first_email(r.text)
            if email:
                return email, site

        except requests.RequestException:
            pass

        time.sleep(random.uniform(*DEEP_SEARCH_DELAY))

    return "", site


def enrich_missing_emails(jobs):
    if not DEEP_SEARCH:
        return jobs

    candidates = [
        j for j in jobs
        if not j.get("emails_rh")
        and j.get("entreprise")
        and j.get("entreprise") not in {"À vérifier", "Entreprise non indiquée"}
    ]
    candidates = candidates[:DEEP_SEARCH_MAX_COMPANIES]

    print(f"[*] Deep search: {len(candidates)} entreprises sans email à vérifier sur le web.")
    found = 0
    sites = 0

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

            if site:
                job["site_entreprise"] = site
                sites += 1
            if email:
                job["emails_rh"] = email
                found += 1
                print(f"[+] DEEP EMAIL {found}: {email} | {job.get('entreprise')} | {site}")

            if n % 25 == 0 or n == len(candidates):
                print(f"[*] deep search: {n}/{len(candidates)} | sites trouvés: {sites} | nouveaux emails: {found}")

    print(f"[OK] Deep search terminé: +{found} emails publics trouvés sur les sites/snippets des entreprises.")
    return jobs


def send_to_sheet(jobs):
    webhook = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL manquant")

    session = make_session()
    last_error = None

    for attempt in range(1, WEBHOOK_RETRIES + 1):
        try:
            print(f"[*] Envoi unique vers Google Sheets: {len(jobs)} offres")
            response = session.post(
                webhook,
                json=jobs,
                timeout=(20, WEBHOOK_TIMEOUT),
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

            print("[OK] Toutes les offres du run envoyées en une seule requête.")
            return

        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            print(f"[!] Envoi échoué, tentative {attempt}/{WEBHOOK_RETRIES}: {exc}")
            if attempt < WEBHOOK_RETRIES:
                time.sleep(8 * attempt)

    raise RuntimeError(f"Webhook impossible après {WEBHOOK_RETRIES} tentatives: {last_error}")


def main():
    print("=" * 72)
    print("AUSBILDUNG KAUFMANN/Kauffrau — DAILY DEEP SCRAPER")
    print("SOURCE: Bundesagentur für Arbeit — SUCHBEREICH=AUSBILDUNG")
    print(f"Objectif: {TARGET_OFFERS}+ offres candidates")
    print("=" * 72)

    links = collect_links()
    jobs = scrape_details(links)

    # Deep search company official sites BEFORE Google Sheets.
    jobs = enrich_missing_emails(jobs)

    if jobs:
        send_to_sheet(jobs)

    email_count = sum(1 for job in jobs if job.get("emails_rh"))
    site_count = sum(1 for job in jobs if job.get("site_entreprise"))

    if len(jobs) < TARGET_OFFERS:
        print(f"[!] Objectif {TARGET_OFFERS} offres non atteint: {len(jobs)} offres.")
    else:
        print(f"[OK] Objectif offres atteint: {len(jobs)}")

    print(f"[OK] Sites officiels identifiés: {site_count}")
    print(f"[OK] Emails publics finaux: {email_count}")
    print("[OK] Run terminé.")


if __name__ == "__main__":
    main()
