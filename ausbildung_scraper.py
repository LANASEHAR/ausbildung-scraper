import base64
import hashlib
import json
import os
import random
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.arbeitsagentur.de"
SEARCH_URL = BASE_URL + "/jobsuche/suche"

# Bundesagentur Jobsuche REST API.
# The public client key is documented for the Jobsuche API and avoids
# scraping the HTML frontend, which can return HTTP 403 after many pages.
BA_API_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
BA_API_SEARCH_URL = BA_API_BASE + "/pc/v6/jobs"
BA_API_DETAILS_URL = BA_API_BASE + "/pc/v4/jobdetails"
BA_API_KEY = "jobboerse-jobsuche"
BA_API_SIZE = 100
API_RETRIES = 3
API_BACKOFF = (2.0, 5.0, 10.0)

SEARCH_QUERIES = [
    "Hotelfachfrau Ausbildung", "Hotelfachmann Ausbildung", "Hotelkauffrau Ausbildung", "Hotelkaufmann Ausbildung",
    "Fachfrau für Systemgastronomie Ausbildung", "Fachmann für Systemgastronomie Ausbildung",
    "Kauffrau im Einzelhandel Ausbildung", "Kaufmann im Einzelhandel Ausbildung",
    "Kauffrau für Spedition und Logistikdienstleistung Ausbildung", "Kaufmann für Spedition und Logistikdienstleistung Ausbildung",
    "Kauffrau im Groß- und Außenhandelsmanagement Ausbildung", "Kaufmann im Groß- und Außenhandelsmanagement Ausbildung",
    "Industriekauffrau Ausbildung", "Industriekaufmann Ausbildung",
]

ROLE_PRIORITY = [
    ("Hotelfachfrau / Hotelkauffrau", 6, ("hotelfachfrau","hotelfachmann","hotelkauffrau","hotelkaufmann","hotelmanagement")),
    ("Fachfrau für Systemgastronomie", 5, ("fachfrau für systemgastronomie","fachfrau fur systemgastronomie","fachmann für systemgastronomie","fachmann fur systemgastronomie","systemgastronomie")),
    ("Kauffrau im Einzelhandel", 4, ("kauffrau im einzelhandel","kaufmann im einzelhandel","kauffrau einzelhandel","kaufmann einzelhandel")),
    ("Kauffrau für Spedition und Logistikdienstleistung", 3, ("kauffrau für spedition und logistikdienstleistung","kauffrau fur spedition und logistikdienstleistung","kaufmann für spedition und logistikdienstleistung","kaufmann fur spedition und logistikdienstleistung","spedition und logistikdienstleistung","speditionskauffrau","speditionskaufmann")),
    ("Kauffrau im Groß- und Außenhandelsmanagement", 2, ("kauffrau im groß- und außenhandelsmanagement","kauffrau im gross- und aussenhandelsmanagement","kaufmann im groß- und außenhandelsmanagement","kaufmann im gross- und aussenhandelsmanagement","groß- und außenhandelsmanagement","gross- und aussenhandelsmanagement")),
    ("Industriekauffrau", 1, ("industriekauffrau","industriekaufmann")),
]
REGION_PRIORITY = [
    ("Ostbayern & Bayerische Alpen", 7, ("ostbayern","niederbayern","oberpfalz","passau","regensburg","landshut","deggendorf","straubing","dingolfing","kelheim","amberg","weiden","cham","bayerischer wald","bayerische alpen","garmisch-partenkirchen","garmisch","rosenheim","traunstein","berchtesgadener land","berchtesgaden","miesbach","bad reichenhall","allgäu","kempten","sonthofen","oberstdorf")),
    ("Thüringen & Sachsen", 6, ("thüringen","thueringen","erfurt","jena","weimar","gera","suhl","gotha","eisenach","sachsen","dresden","leipzig","chemnitz","zwickau","görlitz","goerlitz","plauen","bautzen","freiberg")),
    ("Schwarzwald, Bodensee & industrielles Baden-Württemberg", 5, ("baden-württemberg","baden-wuerttemberg","schwarzwald","bodensee","stuttgart","karlsruhe","mannheim","heidelberg","ulm","heilbronn","pforzheim","freiburg","offenburg","villingen-schwenningen","reutlingen","tübingen","tuebingen","konstanz","friedrichshafen","ravensburg","lörrach","loerrach","böblingen","boeblingen","esslingen","aalen","singen","donaueschingen")),
    ("Mecklenburg-Vorpommern", 4, ("mecklenburg-vorpommern","mecklenburg vorpommern","rostock","schwerin","wismar","stralsund","greifswald","neubrandenburg","güstrow","guestrow","waren","usedom")),
    ("NRW & Südwestfalen", 3, ("nordrhein-westfalen","nordrhein westfalen","nrw","südwestfalen","suedwestfalen","düsseldorf","duesseldorf","köln","koeln","bonn","aachen","dortmund","essen","bochum","duisburg","münster","muenster","bielefeld","wuppertal","krefeld","neuss","mönchengladbach","moenchengladbach","hagen","siegen","arnsberg","olpe","meschede","lüdenscheid","luedenscheid","iserlohn","soest","paderborn","gütersloh","guetersloh")),
    ("West-Niedersachsen & französisch-deutscher Grenzraum", 2, ("west-niedersachsen","westniedersachsen","niedersachsen","osnabrück","osnabrueck","emsland","lingen","papenburg","meppen","cloppenburg","vechta","oldenburg","ammerland","grafschaft bentheim","nordhorn","aurich","leer","saarland","saarbrücken","saarbruecken","rheinland-pfalz","trier","kaiserslautern","koblenz","landau","zweibrücken","zweibruecken","kehl","ortenau")),
]
PRIMARY_SOURCE_DOMAINS=["ihk-lehrstellenboerse.de","arbeitsagentur.de/jobsuche","meine-ausbildung-in-niedersachsen.de","ausbildung.nrw","meine-ausbildung.de","ihk-ausbildungsatlas.de","ausbildungsatlas.ihk.de","ausbildungsatlas.unikam.de"]
SECTOR_SOURCE_DOMAINS=["yourfirm.de","logistikmitarbeiter.de","hotelcareer.de","hogapage.de","gastgebervonmorgen.de","dehoga.de/ausbildung","systemgastronomie-ausbildung.de","azubiyo.de"]
ALL_SOURCE_DOMAINS=PRIMARY_SOURCE_DOMAINS+SECTOR_SOURCE_DOMAINS
ROLE_SEARCH_TERMS={
"Hotelfachfrau / Hotelkauffrau":'"Hotelfachfrau" OR "Hotelkauffrau" OR "Hotelfachmann" OR "Hotelkaufmann"',
"Fachfrau für Systemgastronomie":'"Fachfrau für Systemgastronomie" OR "Fachmann für Systemgastronomie"',
"Kauffrau im Einzelhandel":'"Kauffrau im Einzelhandel" OR "Kaufmann im Einzelhandel"',
"Kauffrau für Spedition und Logistikdienstleistung":'"Kauffrau für Spedition und Logistikdienstleistung" OR "Kaufmann für Spedition und Logistikdienstleistung"',
"Kauffrau im Groß- und Außenhandelsmanagement":'"Kauffrau im Groß- und Außenhandelsmanagement" OR "Kaufmann im Groß- und Außenhandelsmanagement"',
"Industriekauffrau":'"Industriekauffrau" OR "Industriekaufmann"',
}
REGION_SEARCH_TERMS={
"Ostbayern & Bayerische Alpen":'"Ostbayern" OR "Niederbayern" OR "Oberpfalz" OR "Bayerische Alpen" OR "Allgäu"',
"Thüringen & Sachsen":'"Thüringen" OR "Sachsen" OR "Erfurt" OR "Dresden" OR "Leipzig" OR "Chemnitz"',
"Schwarzwald, Bodensee & industrielles Baden-Württemberg":'"Baden-Württemberg" OR "Schwarzwald" OR "Bodensee" OR "Stuttgart" OR "Karlsruhe" OR "Freiburg" OR "Ulm"',
"Mecklenburg-Vorpommern":'"Mecklenburg-Vorpommern" OR "Rostock" OR "Schwerin" OR "Stralsund" OR "Greifswald"',
"NRW & Südwestfalen":'"Nordrhein-Westfalen" OR "NRW" OR "Südwestfalen" OR "Dortmund" OR "Düsseldorf" OR "Köln" OR "Siegen"',
"West-Niedersachsen & französisch-deutscher Grenzraum":'"West-Niedersachsen" OR "Osnabrück" OR "Emsland" OR "Oldenburg" OR "Saarland" OR "Rheinland-Pfalz" OR "Saarbrücken" OR "Trier"',
}
# The scraper is time-budgeted instead of offer-count limited.
# One GitHub run is allowed to work for about 4h30, then it stops cleanly,
# keeps everything already collected, and uploads it to Google Sheets.
SCRAPE_TIME_BUDGET_SECONDS = 4.5 * 60 * 60
RUN_DEADLINE = 0

EXTERNAL_SEARCH_RESULTS=20
DETAIL_WORKERS=10
DETAIL_TIMEOUT=18
DEEP_SEARCH=True
DEEP_SEARCH_WORKERS=12
DEEP_SEARCH_TIMEOUT=12
DEEP_SEARCH_MAX_COMPANIES=5000
DEEP_SEARCH_MAX_SITE_PAGES=8
DEEP_SEARCH_MAX_SEARCH_RESULTS=12
DEEP_SEARCH_DELAY=(0.1,0.3)
WEBHOOK_TIMEOUT=180
WEBHOOK_RETRIES=4
WEBHOOK_BATCH_SIZE=250
WEBHOOK_RETRY_DELAYS=(45,90,150)
SEARCH_DELAY=(0.35,0.8)
DETAIL_DELAY=(0.15,0.45)
WEBHOOK_TIMEOUT = 180
WEBHOOK_RETRIES = 4
WEBHOOK_BATCH_SIZE = 250
WEBHOOK_RETRY_DELAYS = (45, 90, 150)
SEARCH_DELAY = (0.45, 1.0)
DETAIL_DELAY = (0.25, 0.65)

def start_scrape_clock():
    """Start the single time budget shared by collection and detail parsing."""
    global RUN_DEADLINE
    RUN_DEADLINE = time.monotonic() + SCRAPE_TIME_BUDGET_SECONDS
    print(f"[*] Temps de scraping autorisé: {SCRAPE_TIME_BUDGET_SECONDS / 3600:.1f} h")


def scrape_time_exhausted():
    return bool(RUN_DEADLINE) and time.monotonic() >= RUN_DEADLINE


def scrape_time_remaining():
    if not RUN_DEADLINE:
        return float("inf")
    return max(0.0, RUN_DEADLINE - time.monotonic())


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
    """
    Legacy HTML search kept as a fallback only.
    The primary collector uses the official Jobsuche REST API below.
    """
    params = {"suchbereich": "ausbildung", "was": query, "wo": "Deutschland", "page": page}
    r = session.get(SEARCH_URL, params=params, timeout=DETAIL_TIMEOUT)
    r.raise_for_status()
    return extract_job_links(r.text)


def api_headers():
    return {
        "X-API-Key": BA_API_KEY,
        "Accept": "application/json",
        "User-Agent": random.choice(USER_AGENTS),
    }


def api_search_page(session, query, page):
    """
    Search Ausbildung through BA's public Jobsuche API.
    Retry without the broad 'wo=Deutschland' filter when it yields no data,
    then fall back to the app endpoint.
    """
    endpoint_variants = [
        (BA_API_SEARCH_URL, {"angebotsart": 4, "was": query, "page": page, "size": BA_API_SIZE}),
        (BA_API_SEARCH_URL, {"angebotsart": 4, "was": query, "wo": "Deutschland", "page": page, "size": BA_API_SIZE}),
        (BA_API_BASE + "/pc/v4/app/jobs", {"angebotsart": 4, "was": query, "page": page, "size": BA_API_SIZE}),
    ]

    last_exc = None
    for endpoint, params in endpoint_variants:
        for attempt in range(API_RETRIES):
            try:
                session.headers.update(api_headers())
                r = session.get(endpoint, params=params, timeout=DETAIL_TIMEOUT)
                if r.status_code in (403, 429):
                    wait = API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)]
                    print(f"[!] BA API {r.status_code} {endpoint} page {page} — retry {attempt + 1}/{API_RETRIES} dans {wait:.0f}s")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                payload = r.json()
                offers = payload.get("stellenangebote") or payload.get("ergebnisliste") or payload.get("jobs") or []
                if not isinstance(offers, list):
                    offers = []

                links = []
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    refnr = clean(
                        offer.get("refnr")
                        or offer.get("referenznummer")
                        or offer.get("referenzNr")
                        or ""
                    )
                    if not refnr:
                        continue
                    published = clean(
                        offer.get("aktuelleVeroeffentlichungsdatum")
                        or offer.get("ersteVeroeffentlichungsdatum")
                        or offer.get("modifikationsTimestamp")
                        or ""
                    )
                    token = json.dumps(
                        {"refnr": refnr, "published": published},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    links.append("aaapi://" + base64.urlsafe_b64encode(token.encode()).decode())

                if links:
                    return links

                print(f"[!] BA API returned 0 usable offers for '{query}' page {page} via {endpoint}")
                break

            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < API_RETRIES - 1:
                    wait = API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)]
                    time.sleep(wait)

    if last_exc:
        raise last_exc
    return []


def _bing_search(session, query, count=EXTERNAL_SEARCH_RESULTS):
    r=session.get("https://www.bing.com/search?q="+quote_plus(query)+f"&count={count}&setlang=de-DE",timeout=DEEP_SEARCH_TIMEOUT)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    out=[]
    for item in soup.select("li.b_algo"):
        link=item.select_one("h2 a[href]")
        if link:
            snippet=item.select_one(".b_caption p")
            out.append((decode_search_url(link.get("href")),clean(link.get_text(" ",strip=True)),clean(snippet.get_text(" ",strip=True)) if snippet else ""))
    return out

def _source_search_queries():
    return [(rn,rol,dom,f"site:{dom} {ROLE_SEARCH_TERMS[rol]} Ausbildung {REGION_SEARCH_TERMS[rn]}")
            for rn in REGION_SEARCH_TERMS for rol in ROLE_SEARCH_TERMS for dom in ALL_SOURCE_DOMAINS]

def _collect_external_links():
    session=make_session(); found=[]; seen=set(); queries=_source_search_queries()
    print(f"[*] Multi-source search: {len(queries)} source/region/role queries.")
    for n,(rn,rol,dom,q) in enumerate(queries,1):
        if scrape_time_exhausted():
            print("[!] Temps de scraping atteint pendant la recherche multi-source.")
            break
        try: results=_bing_search(session,q)
        except requests.RequestException as exc:
            print(f"[!] Source search échouée ({dom} / {rn} / {rol}): {exc}"); continue
        base_domain=dom.split("/")[0]; added=0
        for url,title,snippet in results:
            host=host_of(url)
            if not host or not (host==base_domain or host.endswith("."+base_domain)): continue
            if url in seen: continue
            seen.add(url); found.append({"url":url,"source":dom,"region_query":rn,"role_query":rol,"search_title":title,"search_snippet":snippet}); added+=1
        if added: print(f"[+] SOURCE {n}/{len(queries)} | {dom} | {rn} | {rol} | +{added} (total {len(found)})")
        if n%25==0: print(f"[*] Source search progress {n}/{len(queries)} | {len(found)} unique links")
        time.sleep(random.uniform(*SEARCH_DELAY))
    return found

def collect_links():
    session=make_session(BASE_URL+"/jobsuche/"); links=[]; seen=set()
    for query in SEARCH_QUERIES:
        if scrape_time_exhausted():
            print("[!] Temps de scraping atteint pendant la collecte BA.")
            break
        print(f"[+] BA Ausbildung Recherche: {query}"); empty_pages=0; page=1
        while not scrape_time_exhausted():
            try: batch=api_search_page(session,query,page)
            except requests.RequestException as exc:
                print(f"[!] BA API recherche échouée {query} page {page}: {exc}"); page += 1; continue
            new_count=0
            for link in batch:
                if link not in seen: seen.add(link); links.append(link); new_count+=1
            print(f"    page {page}: {new_count} nouvelles offres (total BA {len(links)})")
            empty_pages=empty_pages+1 if (not batch or new_count==0) else 0
            if empty_pages>=2:
                break
            page += 1
            time.sleep(random.uniform(*SEARCH_DELAY))
    if not scrape_time_exhausted():
        for item in _collect_external_links():
            url=item["url"]
            if url not in seen: seen.add(url); links.append(url)
    if not links: raise RuntimeError("Aucune offre trouvée sur les sources Ausbildung demandées.")
    print(f"[*] Total liens uniques collectés (BA + portails/secteurs): {len(links)}")
    return links

def extract_offer_date(text):
    """Extract the Ausbildungsbeginn / posting date when available.
    Returns ISO YYYY-MM-DD for reliable newest-first sorting, else empty.
    """
    patterns = [
        r"(?:veröffentlicht|online seit|eingestellt am|aktualisiert am|aktualisiert)\s*:?\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})",
        r"(?:beginn|ausbildungsbeginn|start)\s*:?\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.I)
        if not m:
            continue
        raw = m.group(1).replace("/", ".")
        parts = raw.split(".")
        if len(parts) == 3:
            d, mo, y = parts
            if len(y) == 2:
                y = "20" + y
            try:
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            except ValueError:
                pass
    return ""

# PRIORITÉ = BESOIN DU MARCHÉ UNIQUEMENT.
# Les signaux "English / Englisch / international / étrangers / migration"
# ne sont PAS des mots-clés de recherche et ne donnent AUCUN bonus de priorité.
# Ils peuvent être présents dans une annonce, mais ne servent pas à filtrer,
# classer ou limiter la collecte.

MARKET_PROFILES = [
    ("P1", 70, (
        "kaufmann im einzelhandel", "kauffrau im einzelhandel", "einzelhandel",
        "verkäufer", "verkaufer", "verkäuferin", "verkauferin",
        "groß- und außenhandelsmanagement", "gross- und aussenhandelsmanagement",
        "großhandel", "grosshandel", "außenhandel", "aussenhandel",
        "export", "import",
    )),
    ("P2", 52, (
        "spedition", "logistikdienstleistung", "disposition",
        "büromanagement", "bürokaufmann", "bürokauffrau",
        "industriekaufmann", "industriekauffrau",
    )),
    ("P3", 35, (
        "e-commerce", "ecommerce", "tourismus", "tourismus und freizeit",
        "reiseverkehr",
    )),
]



PHYSICAL_EXCLUSION_SIGNALS = (
    # Truck / driving jobs — not the target.
    "berufskraftfahrer", "berufskraftfahrerin", "kraftfahrer", "kraftfahrerin",
    "lkw-fahrer", "lkw fahrer", "lkw-fahrerin", "lkw fahrerin",
    "fahrer ce", "fahrer c", "fahrer klasse c", "fahrer klasse ce",
    "busfahrer", "busfahrerin", "auslieferungsfahrer", "auslieferungsfahrerin",
    "kurierfahrer", "kurierfahrerin",

    # Explicitly physical / heavy-work requirements.
    "schwere lasten", "schwer heben", "schweres heben", "heben und tragen",
    "körperlich belastbar", "körperliche belastbarkeit", "körperliche arbeit",
    "körperlich anspruchsvoll", "körperlich anstrengend",
    "be- und entladen", "be und entladen", "be-/entladen",
    "regelmäßig schwer", "schwere körperliche",
)

PHYSICAL_EXCLUSION_TITLE_SIGNALS = (
    "berufskraftfahrer", "kraftfahrer", "lkw-fahrer", "lkw fahrer",
    "fahrer ce", "fahrer c", "busfahrer", "auslieferungsfahrer",
    "kurierfahrer",
)

def is_physically_unsuitable(job):
    """Exclude listings centered on truck driving or explicit heavy physical work."""
    title = clean(job.get("intitule", "")).lower()
    text = (
        title + " " +
        clean(job.get("role_cible", "")) + " " +
        clean(job.get("description", ""))
    ).lower()

    if any(signal in title for signal in PHYSICAL_EXCLUSION_TITLE_SIGNALS):
        return True

    # A listing mentioning loading/unloading alone is not necessarily enough;
    # exclude when it is paired with a physical-work indicator.
    heavy_hits = sum(1 for signal in PHYSICAL_EXCLUSION_SIGNALS if signal in text)
    if heavy_hits >= 1:
        return True

    # Specific warehouse profiles that are primarily physical are outside the
    # user's target. Kaufmännische logistics roles remain eligible.
    if "fachkraft für lagerlogistik" in text or "fachkraft lagerlogistik" in text:
        return True

    return False

def market_priority(job):
    """Classify by documented market-need category only.

    International/English/foreigner signals are deliberately NOT used as
    search filters or priority boosts. There is no reliable national statistic
    measuring German-vs-foreign applicant competition by Ausbildung.
    """
    text = (
        clean(job.get("intitule", "")) + " " +
        clean(job.get("role_cible", "")) + " " +
        clean(job.get("description", ""))
    ).lower()

    base = 30
    matched = "Autre / vérifier"
    for category, category_base, terms in MARKET_PROFILES:
        hit = next((term for term in terms if term in text), None)
        if hit:
            base = category_base
            matched = hit
            break

    # Market priority is independent of English/international/migration words.
    # Keep a simple stable mapping: P1 > P2 > P3.
    if base >= 70:
        priority = "P1"
    elif base >= 52:
        priority = "P2"
    else:
        priority = "P3"

    signal = "besoin marché documenté"
    return priority, base, signal, matched


def priority_role_label(job):
    priority, _, _, _ = market_priority(job)
    role = clean(job.get("role_cible", "")) or "Ausbildung"
    role = re.sub(r"^\s*P[123]\s*[|—:-]\s*", "", role, flags=re.I)
    return f"{priority} | {role}"


def job_priority_from_role(role):
    m = re.match(r"^\s*(P[123])\s*[|—:-]", clean(role), re.I)
    if m:
        return m.group(1).upper()

    text = clean(role).lower()
    if any(x in text for x in (
        "einzelhandel", "verkäufer", "verkaufer", "lagerlogistik",
        "großhandel", "grosshandel", "außenhandel", "aussenhandel",
    )):
        return "P1"
    if any(x in text for x in (
        "spedition", "logistikdienstleistung", "disposition",
        "büromanagement", "industriekaufmann", "industriekauffrau",
    )):
        return "P2"
    return "P3"


def detect_target_role(job):
    text=_normalized_text(clean(job.get("intitule",""))+" "+clean(job.get("role_cible",""))+" "+clean(job.get("description","")))
    for role_name,rank,terms in ROLE_PRIORITY:
        if any(_normalized_text(term) in text for term in terms): return role_name,rank
    return "",0

def detect_region(job):
    text=_normalized_text(clean(job.get("lieu",""))+" "+clean(job.get("intitule",""))+" "+clean(job.get("description",""))[:2500])
    for region_name,rank,terms in REGION_PRIORITY:
        if any(_normalized_text(term) in text for term in terms): return region_name,rank
    return "Rest Deutschland",1

def extract_posting_sort_date(job):
    raw=clean(job.get("date_offre","")); m=re.search(r"(\d{4}-\d{2}-\d{2})",raw)
    if m: return m.group(1)
    m=re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})",raw)
    if m:
        d,mo,y=m.groups(); y=("20"+y) if len(y)==2 else y
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return "0000-00-00"

def job_sort_key(job):
    rn,rr=detect_region(job); ro,ror=detect_target_role(job)
    return (rr,ror,extract_posting_sort_date(job),clean(job.get("date_detection","")),clean(job.get("id","")))

def prioritize_jobs(jobs):
    filtered=[]; seen=set()
    for job in jobs:
        if not isinstance(job,dict): continue
        role_name,role_rank=detect_target_role(job)
        if not role_name: continue
        if is_physically_unsuitable(job):
            print(f"[SKIP] körperlich ungeeignete Ausbildung: {job.get('intitule','')}"); continue
        region_name,region_rank=detect_region(job)
        job["prioritaet_region"]=region_rank; job["region_cible"]=region_name
        job["prioritaet_ausbildung"]=role_rank; job["role_cible"]=role_name
        if job.get("id") in seen: continue
        seen.add(job.get("id")); filtered.append(job)
    filtered.sort(key=job_sort_key,reverse=True)
    print(f"[*] Filtrage: {len(filtered)} offres conservées sur les six Ausbildung cibles.")
    print("[*] Ordre: régions 1→6 puis reste; Ausbildung 1→6; nouvelles offres puis anciennes.")
    return filtered

def _normalized_text(value):
    return clean(value).lower().replace("ä","a").replace("ö","o").replace("ü","u").replace("ß","ss")

def _detect_role_title(title,text):
    t=_normalized_text(clean(title)+" "+clean(text))
    if any(x in t for x in ("hotelfachfrau","hotelfachmann","hotelkauffrau","hotelkaufmann","hotelmanagement")): return "Hotelfachfrau / Hotelkauffrau"
    if "systemgastronomie" in t: return "Fachfrau für Systemgastronomie"
    if "einzelhandel" in t: return "Kauffrau im Einzelhandel"
    if "spedition und logistikdienstleistung" in t or "speditionskauffrau" in t or "speditionskaufmann" in t: return "Kauffrau für Spedition und Logistikdienstleistung"
    if "gross- und aussenhandelsmanagement" in t: return "Kauffrau im Groß- und Außenhandelsmanagement"
    if "industriekauffrau" in t or "industriekaufmann" in t: return "Industriekauffrau"
    return ""

def parse_external_detail(link):
    session=make_session(link); host=host_of(link)
    base={"date_detection":time.strftime("%Y-%m-%d %H:%M"),"date_offre":"","statut":"NOUVEAU","role_cible":"","intitule":"Ausbildung","entreprise":"Entreprise non indiquée","lieu":"Deutschland","emails_rh":"","site_entreprise":"","source":host,"lien":link,"id":"src_"+hashlib.sha256(link.encode()).hexdigest()[:20],"description":""}
    try:
        r=session.get(link,timeout=DETAIL_TIMEOUT,allow_redirects=True); r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser"); text=clean(soup.get_text(" ",strip=True)); h1=soup.find("h1")
        title=clean(h1.get_text(" ",strip=True) if h1 else (soup.title.get_text(" ",strip=True) if soup.title else ""))
        company=""; location=""; published=""; description=""; email=extract_email(soup)
        for script in soup.find_all("script",type="application/ld+json"):
            try:
                data=json.loads(script.string or script.get_text() or "{}"); items=data if isinstance(data,list) else [data]
                for item in items:
                    if not isinstance(item,dict) or item.get("@type")!="JobPosting": continue
                    title=clean(item.get("title") or title); published=clean(item.get("datePosted") or item.get("dateCreated") or published)
                    description=clean(item.get("description") or description)
                    org=item.get("hiringOrganization") or {}
                    if isinstance(org,dict): company=clean(org.get("name") or company)
                    loc=item.get("jobLocation") or {}
                    if isinstance(loc,list): loc=loc[0] if loc else {}
                    if isinstance(loc,dict):
                        addr=loc.get("address") or {}
                        if isinstance(addr,dict): location=clean(" ".join(str(x) for x in (addr.get("postalCode"),addr.get("addressLocality"),addr.get("addressRegion")) if x))
                    break
            except Exception: pass
        if not company:
            for pat in (r"(?:Arbeitgeber|Unternehmen|Firma)\s*:?\s*(.+?)(?:\s+Arbeitsort|\s+Ausbildungsbeginn|\s+Aufgaben|$)",r"bei\s+(.+?)(?:\s+in\s+|\s+für\s+|\s+zum\s+|\s+ab\s+|$)"):
                m=re.search(pat,text,re.I)
                if m: company=clean(m.group(1)); break
        if not location:
            for pat in (r"(?:Arbeitsort|Ort|Standort)\s*:?\s*(.+?)(?:\s+Anstellungsart|\s+Ausbildungsbeginn|\s+Beginn|$)",r"\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,2})\b"):
                m=re.search(pat,text,re.I)
                if m: location=clean(" ".join(m.groups())); break
        if not published: published=extract_offer_date(text)
        role=_detect_role_title(title,text)
        if not role: return None
        base.update({"intitule":title or role,"entreprise":company or "Entreprise non indiquée","lieu":location or "Deutschland","emails_rh":email,"role_cible":role,"date_offre":published,"description":description or text[:8000]})
        return base
    except requests.RequestException as exc:
        print(f"[!] source detail inaccessible: {link} -> {exc}"); return None
    except Exception as exc:
        print(f"[!] source parsing erreur: {link} -> {exc}"); return None
    finally:
        time.sleep(random.uniform(*DETAIL_DELAY))

def parse_detail(link):
    session = make_session(BASE_URL + "/jobsuche/")

    # Primary path: official Jobsuche API.
    if link.startswith("aaapi://"):
        encoded_ref = link[len("aaapi://"):]
        base = {
            "date_detection": time.strftime("%Y-%m-%d %H:%M"),
            "date_offre": "",
            "statut": "NOUVEAU",
            "role_cible": "Ausbildung - priorisierte Logistik / Handel / Einzelhandel",
            "intitule": "Ausbildung Kaufmann/Kauffrau",
            "entreprise": "À vérifier",
            "lieu": "Deutschland",
            "emails_rh": "",
            "site_entreprise": "",
            "source": "Agentur für Arbeit - Ausbildung",
            "lien": "",
            "id": "aa_" + hashlib.sha256(encoded_ref.encode()).hexdigest()[:20],
        }
        try:
            decoded = base64.urlsafe_b64decode(encoded_ref.encode()).decode()
            try:
                token = json.loads(decoded)
                refnr = clean(token.get("refnr"))
                base["date_offre"] = clean(token.get("published"))
            except (json.JSONDecodeError, AttributeError):
                refnr = decoded
            encrypted = base64.b64encode(refnr.encode()).decode()
            last_exc = None

            for attempt in range(API_RETRIES):
                try:
                    session.headers.update(api_headers())
                    r = session.get(
                        f"{BA_API_DETAILS_URL}/{encrypted}",
                        timeout=DETAIL_TIMEOUT,
                    )
                    if r.status_code in (403, 429):
                        wait = API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)]
                        print(f"[!] BA API détails {r.status_code} — retry {attempt + 1}/{API_RETRIES} dans {wait:.0f}s")
                        time.sleep(wait)
                        continue
                    r.raise_for_status()
                    details = r.json()
                    last_exc = None
                    break
                except (requests.RequestException, ValueError) as exc:
                    last_exc = exc
                    if attempt < API_RETRIES - 1:
                        wait = API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)]
                        time.sleep(wait)

            if last_exc:
                raise last_exc

            title = clean(
                details.get("stellenangebotsTitel")
                or details.get("titel")
                or details.get("beruf")
                or base["intitule"]
            )
            company = clean(details.get("arbeitgeber") or "Entreprise non indiquée")

            locations = details.get("arbeitsorte") or []
            location = "Deutschland"
            if locations:
                loc = locations[0] or {}
                location = clean(" ".join(
                    x for x in [loc.get("ort"), loc.get("region"), loc.get("land")]
                    if x
                )) or "Deutschland"

            # Search the complete JSON for public emails present in the
            # official job details response.
            email = first_email(json.dumps(details, ensure_ascii=False))

            published = (
                details.get("ersteVeroeffentlichungsdatum")
                or details.get("aktuelleVeroeffentlichungsdatum")
                or base.get("date_offre")
                or ""
            )
            description = clean(
                details.get("stellenangebotsBeschreibung")
                or details.get("stellenbeschreibung")
                or ""
            )

            title_low = title.lower()
            combined = (title_low + " " + description.lower())
            if "lagerlogistik" in combined or ("lager" in combined and "logistik" in combined):
                role = "Fachkraft für Lagerlogistik"
            elif "einzelhandel" in combined or "verkäufer" in combined or "verkaufer" in combined:
                role = "Verkäufer/in / Einzelhandel"
            elif "spedition" in combined or "logistikdienstleistung" in combined:
                role = "Kauffrau/Kaufmann Spedition & Logistikdienstleistung"
            elif any(x in combined for x in ("groß", "gross", "außenhandel", "aussenhandel", "großhandel", "grosshandel")):
                role = "Groß- und Außenhandelsmanagement"
            else:
                role = "Autre / vérifier"

            external_url = clean(
                details.get("externeUrl")
                or details.get("allianzpartnerUrl")
                or ""
            )

            base.update({
                "date_offre": published,
                "intitule": title,
                "entreprise": company,
                "lieu": location,
                "emails_rh": email,
                "role_cible": role,
                "lien": external_url,
                "description": description,
                "id": "aa_" + clean(details.get("refnr") or details.get("referenznummer") or refnr),
            })
            return base

        except requests.RequestException as exc:
            print(f"[!] détail API inaccessible: {refnr if 'refnr' in locals() else encoded_ref} -> {exc}")
            return base
        except Exception as exc:
            print(f"[!] parsing API erreur: {exc}")
            return base
        finally:
            time.sleep(random.uniform(*DETAIL_DELAY))

    if not link.startswith("aaapi://") and "arbeitsagentur.de" not in host_of(link):
        return parse_external_detail(link)

    # Legacy HTML fallback for any non-API link.
    match = re.search(r"/jobsuche/jobdetail/([^/?#]+)", link)
    source_id = match.group(1) if match else hashlib.sha256(link.encode()).hexdigest()[:20]
    base = {
        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
        "date_offre": "",
        "statut": "NOUVEAU", "role_cible": "Ausbildung - priorisierte Logistik / Handel / Einzelhandel",
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
        location = "Deutschland"
        for pattern in [
            r"Arbeitsort\s*:?[ ]+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|\s+Beginn|$)",
            r"Ort\s*:?[ ]+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|$)",
        ]:
            m = re.search(pattern, text, re.I)
            if m:
                location = clean(m.group(1)); break
        title_low = title.lower()
        if "groß" in title_low or "außenhandel" in title_low or "großhandel" in title_low:
            role = "Groß- und Außenhandelsmanagement"
        elif "spedition" in title_low or "logistikdienstleistung" in title_low:
            role = "Kauffrau/Kaufmann Spedition & Logistikdienstleistung"
        elif "lagerlogistik" in title_low:
            role = "Fachkraft für Lagerlogistik"
        elif "einzelhandel" in title_low or "verkäufer" in title_low:
            role = "Verkäufer/in / Einzelhandel"
        else:
            role = "Autre / vérifier"
        base.update({
            "intitule": title,
            "entreprise": company or "Entreprise non indiquée",
            "lieu": location,
            "emails_rh": email,
            "role_cible": role,
            "date_offre": extract_offer_date(text),
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
    """Parse as many offer pages as possible until the shared time budget ends."""
    jobs = []
    executor = ThreadPoolExecutor(max_workers=DETAIL_WORKERS)
    futures = {executor.submit(parse_detail, link): link for link in links}
    pending = set(futures)
    processed = 0
    try:
        while pending and not scrape_time_exhausted():
            wait_timeout = min(5.0, scrape_time_remaining())
            done, pending = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                processed += 1
                try:
                    job = future.result()
                except Exception as exc:
                    print(f"[!] détail erreur: {exc}")
                    continue
                if job:
                    jobs.append(job)
                    if job.get("emails_rh"):
                        print(f"[+] email offre: {job['emails_rh']} | {job['entreprise']}")
                if processed % 50 == 0:
                    count = sum(1 for x in jobs if x.get("emails_rh"))
                    print(f"[*] détails traités: {processed}/{len(links)} | offres: {len(jobs)} | avec email offre: {count}")
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    jobs = list({job["id"]: job for job in jobs}.values())
    print(f"[*] Offres finales: {len(jobs)} | avec email présent sur l'offre: {sum(1 for x in jobs if x.get('emails_rh'))}")
    if scrape_time_exhausted():
        print("[OK] Budget temps atteint: conservation de toutes les offres déjà récupérées.")
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
    """Verify company identity on the actual site. Never trust search snippets for emails."""
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
        if tokens and identity_hits == 0:
            return "", ""
        return final_root, extract_email(soup)
    except requests.RequestException:
        return "", ""


def site_pages_to_check(site):
    """Discover all relevant contact/career pages on the verified official domain."""
    root = site.rstrip("/") + "/"
    urls = [root]
    seen = {root}
    paths = (
        "/kontakt", "/contact", "/impressum", "/ansprechpartner",
        "/karriere", "/bewerbung", "/jobs", "/ausbildung",
        "/karriere/jobs", "/karriere/ausbildung", "/stellenangebote",
        "/jobs-karriere", "/bewerber", "/personal", "/hr",
        "/kontakt/ansprechpartner", "/kontakt/bewerbung",
    )
    try:
        r = requests.get(
            root,
            headers={"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "de-DE,de;q=0.9"},
            timeout=DEEP_SEARCH_TIMEOUT,
        )
        if r.ok:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(root, a["href"].split("#", 1)[0])
                if host_of(href) != host_of(root):
                    continue
                label = clean(a.get_text(" ", strip=True)).lower()
                value = (href + " " + label).lower()
                if any(word in value for word in CONTACT_WORDS):
                    if href not in seen:
                        urls.append(href); seen.add(href)

            # Public sitemaps often expose contact, careers, HR and imprint
            # pages that are not linked from the homepage.
            for sitemap_url in (urljoin(root, "sitemap.xml"), urljoin(root, "sitemap_index.xml")):
                try:
                    sr = requests.get(sitemap_url, headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=DEEP_SEARCH_TIMEOUT)
                    if not sr.ok:
                        continue
                    sitemap = BeautifulSoup(sr.text, "xml")
                    for loc in sitemap.find_all("loc"):
                        href = clean(loc.get_text(" ", strip=True))
                        value = href.lower()
                        if host_of(href) == host_of(root) and any(word in value for word in CONTACT_WORDS):
                            if href not in seen:
                                urls.append(href); seen.add(href)
                except requests.RequestException:
                    continue
    except requests.RequestException:
        pass

    for path in paths:
        href = urljoin(root, path.lstrip("/"))
        if href not in seen:
            urls.append(href); seen.add(href)
    return urls


def crawl_verified_site(site):
    """Crawl relevant pages on the verified official site until an email is found."""
    queue = list(site_pages_to_check(site))
    seen = set(queue)
    started = time.monotonic()

    while queue:
        # Keep each company's crawl bounded by time, not by an arbitrary page
        # count. This lets the workflow inspect as many relevant pages as the
        # site exposes without one broken site consuming the whole run.
        if time.monotonic() - started >= 90:
            return ""

        page_url = queue.pop(0)
        try:
            r = requests.get(
                page_url,
                headers={"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "de-DE,de;q=0.9,en;q=0.7"},
                timeout=DEEP_SEARCH_TIMEOUT,
                allow_redirects=True,
            )
            if not r.ok:
                continue
            content_type = r.headers.get("Content-Type", "").lower()
            if content_type and "html" not in content_type and "xml" not in content_type:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            email = extract_email(soup) or first_email(r.text)
            if email:
                return email

            # Continue through every relevant same-domain contact/career link
            # discovered on the pages we visit.
            for a in soup.find_all("a", href=True):
                href = urljoin(r.url, a["href"].split("#", 1)[0])
                if host_of(href) != host_of(site):
                    continue
                label = clean(a.get_text(" ", strip=True)).lower()
                value = (href + " " + label).lower()
                if any(word in value for word in CONTACT_WORDS) and href not in seen:
                    seen.add(href)
                    queue.append(href)
        except requests.RequestException:
            pass
        time.sleep(random.uniform(*DEEP_SEARCH_DELAY))
    return ""


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

    # Search snippets are ONLY used to find a candidate site.
    # Emails are accepted only after the real site is opened and verified.
    seen_hosts = set()
    for _, href in sorted(candidates, key=lambda x: x[0], reverse=True):
        host = host_of(href)
        if not host or host in seen_hosts:
            continue
        seen_hosts.add(host)
        site, homepage_email = verify_official_site(href, company_clean)
        if not site:
            continue
        email = homepage_email or crawl_verified_site(site)
        return email, site
    return "", ""


def enrich_missing_emails(jobs):
    """Legacy in-memory enrichment.

    The production workflow now performs this step in email_enrichment.py after
    the offers are already in Sheets. That workflow groups rows by normalized
    company, so the same company is searched once and the verified result is
    copied to every matching offer.
    """
    return jobs


def post_json(payload, label):
    webhook = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL manquant")

    # Never send thousands of rows in one Apps Script request.
    # A timed-out HTTP request does NOT guarantee that the Apps Script execution
    # stopped; retrying the same huge payload can therefore create a lock storm.
    if isinstance(payload, list) and len(payload) > WEBHOOK_BATCH_SIZE:
        results = []
        total = (len(payload) + WEBHOOK_BATCH_SIZE - 1) // WEBHOOK_BATCH_SIZE
        for index in range(0, len(payload), WEBHOOK_BATCH_SIZE):
            chunk = payload[index:index + WEBHOOK_BATCH_SIZE]
            chunk_no = index // WEBHOOK_BATCH_SIZE + 1
            print(f"[*] {label}: batch {chunk_no}/{total} ({len(chunk)} éléments)")
            results.append(post_json(chunk, f"{label} — batch {chunk_no}/{total}"))
        return results

    if (
        isinstance(payload, dict)
        and isinstance(payload.get("jobs"), list)
        and len(payload["jobs"]) > WEBHOOK_BATCH_SIZE
    ):
        jobs = payload["jobs"]
        results = []
        total = (len(jobs) + WEBHOOK_BATCH_SIZE - 1) // WEBHOOK_BATCH_SIZE
        for index in range(0, len(jobs), WEBHOOK_BATCH_SIZE):
            chunk = jobs[index:index + WEBHOOK_BATCH_SIZE]
            chunk_payload = dict(payload)
            chunk_payload["jobs"] = chunk
            chunk_no = index // WEBHOOK_BATCH_SIZE + 1
            print(f"[*] {label}: batch {chunk_no}/{total} ({len(chunk)} éléments)")
            results.append(post_json(chunk_payload, f"{label} — batch {chunk_no}/{total}"))
        return results

    session = make_session()
    last_error = None

    for attempt in range(1, WEBHOOK_RETRIES + 1):
        try:
            size = len(payload) if isinstance(payload, list) else len(payload.get("jobs", []))
            print(f"[*] {label}: {size} éléments | tentative {attempt}/{WEBHOOK_RETRIES}")

            response = session.post(
                webhook,
                json=payload,
                timeout=(20, WEBHOOK_TIMEOUT),
                allow_redirects=True,
            )
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
                # Wait long enough for a timed-out Apps Script execution to finish
                # and release its script lock before retrying.
                delay = WEBHOOK_RETRY_DELAYS[min(attempt - 1, len(WEBHOOK_RETRY_DELAYS) - 1)]
                print(f"[*] Attente {delay}s avant nouvelle tentative...")
                time.sleep(delay)

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
    print("AUSBILDUNG — 6 TARGET-AUSBILDUNGEN / TIME-BUDGET SCRAPER")
    print("SOURCE: Bundesagentur für Arbeit + portails/secteurs Ausbildung")
    print(f"Objectif: scraper sans plafond d'offres, pendant ~{SCRAPE_TIME_BUDGET_SECONDS / 3600:.1f} h")
    print("=" * 72)

    links = collect_links()
    jobs = scrape_details(links)

    # Classify every retained offer with the same P1/P2/P3 strategy
    # used by daily_runner.py.
    jobs = prioritize_jobs(jobs)
    # 1. Offers go to Sheets immediately after offer scraping.
    if jobs:
        send_to_sheet(jobs)
        print("[OK] Offres envoyées au Sheet AVANT le deep search.")

    # 2. Deep search runs after the first Sheet write, once per unique company.
    jobs = enrich_missing_emails(jobs)

    # 3. Only email/site fields are updated in existing rows.
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
