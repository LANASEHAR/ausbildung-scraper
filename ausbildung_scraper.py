import base64
import hashlib
import html as html_lib
import io
import json
import os
import random
import re
import sys
import threading
import time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from urllib.parse import quote, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

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
API_BACKOFF = (1.5, 4.0, 8.0)

SEARCH_QUERIES = [
    "Hotelfachfrau",
    "Hotelfachmann",
    "Hotelkauffrau",
    "Hotelkaufmann",
    "Hotelmanagement Ausbildung",
    "Fachfrau für Systemgastronomie",
    "Fachmann für Systemgastronomie",
    "Systemgastronomie",
    "Kauffrau im Einzelhandel",
    "Kaufmann im Einzelhandel",
    "Kauffrau für Spedition und Logistikdienstleistung",
    "Kaufmann für Spedition und Logistikdienstleistung",
    "Speditionskaufmann",
    "Kauffrau im Groß- und Außenhandelsmanagement",
    "Kaufmann im Groß- und Außenhandelsmanagement",
    "Industriekauffrau",
    "Industriekaufmann",
]

AUSBILDUNG_DE_PROFESSION_SLUGS = [
    "hotelfachmann",
    "fachmann-fuer-systemgastronomie",
    "kaufmann-im-einzelhandel",
    "kaufmann-fuer-spedition-und-logistikdienstleistung",
    "kaufmann-im-gross-und-aussenhandelsmanagement",
    "industriekaufmann",
]

AZUBIYO_PROFESSION_SLUGS = [
    "hotelfachmann",
    "kaufmann-im-einzelhandel",
    "industriekaufmann",
]

ROLE_PRIORITY = [
    ("Hotelfachfrau / Hotelkauffrau", 6, ("hotelfachfrau", "hotelfachmann", "hotelkauffrau", "hotelkaufmann", "hotelmanagement")),
    ("Fachfrau für Systemgastronomie", 5, ("fachfrau für systemgastronomie", "fachfrau fur systemgastronomie", "fachmann für systemgastronomie", "fachmann fur systemgastronomie", "systemgastronomie")),
    ("Kauffrau im Einzelhandel", 4, ("kauffrau im einzelhandel", "kaufmann im einzelhandel", "kauffrau einzelhandel", "kaufmann einzelhandel")),
    ("Kauffrau für Spedition und Logistikdienstleistung", 3, ("kauffrau für spedition und logistikdienstleistung", "kauffrau fur spedition und logistikdienstleistung", "kaufmann für spedition und logistikdienstleistung", "kaufmann fur spedition und logistikdienstleistung", "spedition und logistikdienstleistung", "speditionskauffrau", "speditionskaufmann")),
    ("Kauffrau im Groß- und Außenhandelsmanagement", 2, ("kauffrau im groß- und außenhandelsmanagement", "kauffrau im gross- und aussenhandelsmanagement", "kaufmann im groß- und außenhandelsmanagement", "kaufmann im gross- und aussenhandelsmanagement", "groß- und außenhandelsmanagement", "gross- und aussenhandelsmanagement")),
    ("Industriekauffrau", 1, ("industriekauffrau", "industriekaufmann")),
]

REGION_PRIORITY = [
    ("Ostbayern & Bayerische Alpen", 7, ("ostbayern", "niederbayern", "oberpfalz", "passau", "regensburg", "landshut", "deggendorf", "straubing", "dingolfing", "kelheim", "amberg", "weiden", "cham", "bayerischer wald", "bayerische alpen", "garmisch-partenkirchen", "garmisch", "rosenheim", "traunstein", "berchtesgadener land", "berchtesgaden", "miesbach", "bad reichenhall", "allgäu", "kempten", "sonthofen", "oberstdorf")),
    ("Thüringen & Sachsen", 6, ("thüringen", "thueringen", "erfurt", "jena", "weimar", "gera", "suhl", "gotha", "eisenach", "sachsen", "dresden", "leipzig", "chemnitz", "zwickau", "görlitz", "goerlitz", "plauen", "bautzen", "freiberg")),
    ("Schwarzwald, Bodensee & industrielles Baden-Württemberg", 5, ("baden-württemberg", "baden-wuerttemberg", "schwarzwald", "bodensee", "stuttgart", "karlsruhe", "mannheim", "heidelberg", "ulm", "heilbronn", "pforzheim", "freiburg", "offenburg", "villingen-schwenningen", "reutlingen", "tübingen", "tuebingen", "konstanz", "friedrichshafen", "ravensburg", "lörrach", "loerrach", "böblingen", "boeblingen", "esslingen", "aalen", "singen", "donaueschingen")),
    ("Mecklenburg-Vorpommern", 4, ("mecklenburg-vorpommern", "mecklenburg vorpommern", "rostock", "schwerin", "wismar", "stralsund", "greifswald", "neubrandenburg", "güstrow", "guestrow", "waren", "usedom")),
    ("NRW & Südwestfalen", 3, ("nordrhein-westfalen", "nordrhein westfalen", "nrw", "südwestfalen", "suedwestfalen", "düsseldorf", "duesseldorf", "köln", "koeln", "bonn", "aachen", "dortmund", "essen", "bochum", "duisburg", "münster", "muenster", "bielefeld", "wuppertal", "krefeld", "neuss", "mönchengladbach", "moenchengladbach", "hagen", "siegen", "arnsberg", "olpe", "meschede", "lüdenscheid", "luedenscheid", "iserlohn", "soest", "paderborn", "gütersloh", "guetersloh")),
    ("West-Niedersachsen & französisch-deutscher Grenzraum", 2, ("west-niedersachsen", "westniedersachsen", "niedersachsen", "osnabrück", "osnabrueck", "emsland", "lingen", "papenburg", "meppen", "cloppenburg", "vechta", "oldenburg", "ammerland", "grafschaft bentheim", "nordhorn", "aurich", "leer", "saarland", "saarbrücken", "saarbruecken", "rheinland-pfalz", "trier", "kaiserslautern", "koblenz", "landau", "zweibrücken", "zweibruecken", "kehl", "ortenau")),
]

BA_REGION_MAP = {
    "BADEN_WUERTTEMBERG": "Baden-Württemberg",
    "BAYERN": "Bayern",
    "BERLIN": "Berlin",
    "BRANDENBURG": "Brandenburg",
    "BREMEN": "Bremen",
    "HAMBURG": "Hamburg",
    "HESSEN": "Hessen",
    "MECKLENBURG_VORPOMMERN": "Mecklenburg-Vorpommern",
    "NIEDERSACHSEN": "Niedersachsen",
    "NORDRHEIN_WESTFALEN": "Nordrhein-Westfalen",
    "RHEINLAND_PFALZ": "Rheinland-Pfalz",
    "SAARLAND": "Saarland",
    "SACHSEN": "Sachsen",
    "SACHSEN_ANHALT": "Sachsen-Anhalt",
    "SCHLESWIG_HOLSTEIN": "Schleswig-Holstein",
    "THUERINGEN": "Thüringen",
}

PRIMARY_SOURCE_DOMAINS = [
    "ihk-lehrstellenboerse.de", "arbeitsagentur.de/jobsuche",
    "meine-ausbildung-in-niedersachsen.de", "ausbildung.nrw",
    "meine-ausbildung.de", "ihk-ausbildungsatlas.de",
    "ausbildungsatlas.ihk.de", "ausbildungsatlas.unikam.de",
]
SECTOR_SOURCE_DOMAINS = [
    "yourfirm.de", "logistikmitarbeiter.de", "hotelcareer.de",
    "hogapage.de", "gastgebervonmorgen.de", "dehoga.de/ausbildung",
    "systemgastronomie-ausbildung.de", "azubiyo.de",
]
ALL_SOURCE_DOMAINS = PRIMARY_SOURCE_DOMAINS + SECTOR_SOURCE_DOMAINS

ROLE_SEARCH_TERMS = {
    "Hotelfachfrau / Hotelkauffrau": '"Hotelfachfrau" OR "Hotelkauffrau" OR "Hotelfachmann" OR "Hotelkaufmann"',
    "Fachfrau für Systemgastronomie": '"Fachfrau für Systemgastronomie" OR "Fachmann für Systemgastronomie"',
    "Kauffrau im Einzelhandel": '"Kauffrau im Einzelhandel" OR "Kaufmann im Einzelhandel"',
    "Kauffrau für Spedition und Logistikdienstleistung": '"Kauffrau für Spedition und Logistikdienstleistung" OR "Kaufmann für Spedition und Logistikdienstleistung"',
    "Kauffrau im Groß- und Außenhandelsmanagement": '"Kauffrau im Groß- und Außenhandelsmanagement" OR "Kaufmann im Groß- und Außenhandelsmanagement"',
    "Industriekauffrau": '"Industriekauffrau" OR "Industriekaufmann"',
}
REGION_SEARCH_TERMS = {
    "Ostbayern & Bayerische Alpen": '"Ostbayern" OR "Niederbayern" OR "Oberpfalz" OR "Bayerische Alpen" OR "Allgäu"',
    "Thüringen & Sachsen": '"Thüringen" OR "Sachsen" OR "Erfurt" OR "Dresden" OR "Leipzig" OR "Chemnitz"',
    "Schwarzwald, Bodensee & industrielles Baden-Württemberg": '"Baden-Württemberg" OR "Schwarzwald" OR "Bodensee" OR "Stuttgart" OR "Karlsruhe" OR "Freiburg" OR "Ulm"',
    "Mecklenburg-Vorpommern": '"Mecklenburg-Vorpommern" OR "Rostock" OR "Schwerin" OR "Stralsund" OR "Greifswald"',
    "NRW & Südwestfalen": '"Nordrhein-Westfalen" OR "NRW" OR "Südwestfalen" OR "Dortmund" OR "Düsseldorf" OR "Köln" OR "Siegen"',
    "West-Niedersachsen & französisch-deutscher Grenzraum": '"West-Niedersachsen" OR "Osnabrück" OR "Emsland" OR "Oldenburg" OR "Saarland" OR "Rheinland-Pfalz" OR "Saarbrücken" OR "Trier"',
}

# The scraper is time-budgeted instead of offer-count limited.
# One GitHub run is allowed to work for about 4h30, then it stops cleanly,
# keeps everything already collected, and uploads it to Google Sheets.
SCRAPE_TIME_BUDGET_SECONDS = 4.5 * 60 * 60
RUN_DEADLINE = 0

EXTERNAL_SEARCH_RESULTS = 20
DETAIL_WORKERS = 16
DETAIL_TIMEOUT = 15
DEEP_SEARCH = True
DEEP_SEARCH_WORKERS = 14
DEEP_SEARCH_TIMEOUT = 8
DEEP_SEARCH_MAX_SEARCH_RESULTS = 12
DEEP_SEARCH_DELAY = (0.05, 0.15)
WEBHOOK_TIMEOUT = 180
WEBHOOK_RETRIES = 4
WEBHOOK_BATCH_SIZE = 250
WEBHOOK_RETRY_DELAYS = (15, 35, 65)
SEARCH_DELAY = (0.15, 0.4)
DETAIL_DELAY = (0.02, 0.08)

_THREAD_LOCAL = threading.local()
_COMPANY_CACHE_LOCK = threading.Lock()
COMPANY_ENRICH_CACHE = {}


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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def make_session(referer=None):
    """Create a reusable HTTP session with connection pooling and standard headers."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    if referer:
        session.headers["Referer"] = referer
    return session


def _get_thread_session(referer=None):
    """Return a thread-local pooled HTTP session to reuse TLS connections."""
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = make_session()
        _THREAD_LOCAL.session = session
    if referer:
        session.headers["Referer"] = referer
    else:
        session.headers.pop("Referer", None)
    return session


def clean(value):
    """Normalize arbitrary scraped values into compact plain text."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_obfuscated_email(value):
    """Normalize common public email obfuscations such as '[at]' and '[dot]'."""
    text = html_lib.unescape(str(value or ""))
    text = re.sub(r"\s*(?:\[|\(|\{)\s*(?:at|ät)\s*(?:\]|\)|\})\s*", "@", text, flags=re.I)
    text = re.sub(r"\s+(?:at|ät)\s+", "@", text, flags=re.I)
    text = re.sub(r"\s*(?:\[|\(|\{)\s*(?:dot|punkt)\s*(?:\]|\)|\})\s*", ".", text, flags=re.I)
    return text


def decode_cf_email(encoded):
    """Decode Cloudflare's hex-encoded email protection string."""
    hex_str = clean(encoded).split("#")[-1].strip()
    if len(hex_str) < 4 or len(hex_str) % 2 != 0:
        return ""
    try:
        key = int(hex_str[:2], 16)
        chars = [chr(int(hex_str[i:i + 2], 16) ^ key) for i in range(2, len(hex_str), 2)]
        return "".join(chars)
    except ValueError:
        return ""


def first_email(value):
    """Return the first syntactically valid email found in arbitrary text."""
    normalized = normalize_obfuscated_email(value)
    for candidate in EMAIL_RE.findall(normalized):
        email = valid_email(candidate)
        if email:
            return email
    return ""


CONTACT_WORDS = (
    "kontakt", "contact", "impressum", "ansprechpartner", "karriere", "career",
    "bewerbung", "bewerben", "jobs", "job", "ausbildung", "personal", "hr",
    "recruit", "recruiting", "human resources", "team",
)

BAD_SITE_DOMAINS = {
    "arbeitsagentur.de", "azubiyo.de", "ausbildung.de", "aubi-plus.de",
    "yourfirm.de", "hotelcareer.de", "hogapage.de", "dehoga.de",
    "systemgastronomie-ausbildung.de", "logistikmitarbeiter.de",
    "gastgebervonmorgen.de", "ihk-lehrstellenboerse.de",
    "ihk-ausbildungsatlas.de", "ausbildungsatlas.ihk.de",
    "ausbildungsatlas.unikam.de", "meine-ausbildung.de",
    "meine-ausbildung-in-niedersachsen.de", "ausbildung.nrw",
    "indeed.com", "indeed.de", "stepstone.de", "linkedin.com", "kununu.com",
    "xing.com", "jobware.de", "meinestadt.de", "heyjobs.co", "azubi.de",
    "jobvector.de", "joblift.de", "stellenanzeigen.de", "kimeta.de",
    "gelbeseiten.de", "dasoertliche.de", "11880.com", "northdata.de",
    "google.com", "google.de", "bing.com", "duckduckgo.com",
    "facebook.com", "instagram.com", "youtube.com", "tiktok.com",
    "twitter.com", "x.com", "wikipedia.org",
}

EMAIL_RE = re.compile(r"(?<![A-Z0-9._%+\-])([A-Z0-9._%+\-]+@[A-Z0-9.-]+\.[A-Z]{2,63})(?![A-Z0-9._%+\-])", re.I)
JOB_LINK_RE = re.compile(r"/jobsuche/jobdetail/", re.I)
URL_IN_TEXT_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'()]+", re.I)

ASSET_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".avif", ".tiff", ".css", ".js", ".mjs", ".map", ".woff", ".woff2",
    ".ttf", ".eot", ".otf", ".pdf", ".zip", ".mp4", ".mp3", ".xml", ".json",
)

BAD_EMAIL_DOMAINS = {
    "arbeitsagentur.de", "ausbildung.de", "azubiyo.de", "aubi-plus.de",
    "hotelcareer.de", "hogapage.de", "yourfirm.de", "ihk-lehrstellenboerse.de",
    "heyjobs.co", "azubi.de", "stepstone.de", "indeed.com", "linkedin.com",
    "xing.com", "gelbeseiten.de", "dasoertliche.de", "11880.com",
    "example.com", "example.org", "example.net", "schema.org", "w3.org",
    "sentry.io", "wixpress.com", "google.com", "googleapis.com", "gstatic.com",
    "bing.com", "duckduckgo.com", "cloudflare.com", "facebook.com",
    "instagram.com", "acronymfinder.com", "thesaurus.com", "applied.com",
}
FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "gmx.de", "gmx.net", "web.de", "t-online.de", "freenet.de",
}
GENERIC_LOCALPARTS = {
    "example", "test", "testing", "noreply", "no-reply", "donotreply",
    "do-not-reply", "mailer-daemon", "postmaster", "hostmaster", "abuse",
    "bounce",
}
HR_LOCALPART_HINTS = (
    "ausbildung", "azubi", "bewerbung", "bewerben", "bewerber",
    "karriere", "career", "personal", "recruiting", "recruit",
    "jobs", "job", "hr", "talent",
)
CONTACT_LOCALPART_HINTS = (
    "info", "kontakt", "contact", "office", "mail", "verwaltung",
    "empfang", "zentrale", "direktion", "hotel", "service",
)
LOW_PRIORITY_EMAIL_HINTS = (
    "datenschutz", "privacy", "dsgvo", "webmaster", "support",
    "newsletter", "marketing", "presse", "press",
)
AGENCY_DOMAIN_HINTS = (
    "agentur", "webdesign", "werbeagentur", "mediadesign", "digitalagentur",
    "internetagentur", "it-service", "hosting", "cms",
)
EMAIL_CONTEXT_WORDS = (
    "kontakt", "contact", "impressum", "ansprechpartner", "bewerbung",
    "bewerben", "karriere", "career", "ausbildung", "personal", "hr",
    "recruit", "recruiting", "human resources", "team",
)

PLACEHOLDER_COMPANIES = {
    "", "unternehmen", "arbeitgeber", "firma", "betrieb",
    "entreprise non indiquée", "entreprise non indiquee", "à vérifier",
    "a verifier", "unbekannt", "nicht angegeben", "nicht bekannt",
    "agentur für arbeit", "bundesagentur für arbeit", "n/a", "na", "-",
}


def normalize_email_domain(domain):
    domain = str(domain or "").strip().lower().rstrip(".")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return domain


def valid_email(email):
    """Validate and normalize a real email address, rejecting image/asset false positives."""
    email = normalize_obfuscated_email(email).strip(" <>.,;:\"'()[]")
    if not email or len(email) > 254:
        return ""
    if not EMAIL_RE.fullmatch(email):
        return ""
    local, domain = email.rsplit("@", 1)
    local_low = local.lower()
    domain_low = domain.lower().rstrip(".")
    if not local_low or not domain_low or "." not in domain_low:
        return ""
    if ".." in local_low or ".." in domain_low:
        return ""
    if any(ch.isspace() for ch in email):
        return ""
    if any(domain_low.endswith(ext) for ext in ASSET_EXTENSIONS):
        return ""
    if re.match(r"^\d+[wx]\.", domain_low):
        return ""
    tld = domain_low.rsplit(".", 1)[-1]
    if len(tld) < 2 or not tld.isalpha():
        return ""
    if local_low in GENERIC_LOCALPARTS:
        return ""
    if any(domain_low == bad or domain_low.endswith("." + bad) for bad in BAD_EMAIL_DOMAINS):
        return ""
    if any(hint in domain_low for hint in AGENCY_DOMAIN_HINTS):
        return ""
    return f"{local_low}@{domain_low}"


def email_from_mailto(href):
    href = unquote(str(href or ""))
    if not href.lower().startswith("mailto:"):
        return ""
    raw = href.split(":", 1)[1].split("?", 1)[0].strip()
    return valid_email(raw)


def email_context_score(soup, element):
    """Prefer emails shown in contact/recruiting contexts over random page text."""
    if element is None:
        return 0
    parent = element.parent
    context = clean(" ".join([
        element.get_text(" ", strip=True) if hasattr(element, "get_text") else "",
        parent.get_text(" ", strip=True) if parent and hasattr(parent, "get_text") else "",
        " ".join(str(element.get(attr, "")) for attr in ("aria-label", "title", "alt") if hasattr(element, "get")),
    ])).lower()
    score = 0
    for word in EMAIL_CONTEXT_WORDS:
        if word in context:
            score += 2
    return score


def _score_email_candidate(email, base_score, company=""):
    """Score email candidates so HR/company addresses beat Impressum web agencies."""
    local, domain = email.rsplit("@", 1)
    score = base_score
    if any(hint in local for hint in HR_LOCALPART_HINTS):
        score += 45
    elif any(hint in local for hint in CONTACT_LOCALPART_HINTS):
        score += 20
    if any(hint in local for hint in LOW_PRIORITY_EMAIL_HINTS):
        score -= 35
    if company and email_matches_company(email, company):
        score += 35
    if any(hint in domain for hint in AGENCY_DOMAIN_HINTS):
        score -= 40
    return score


def extract_email_candidates(soup, company=""):
    """Return ranked, syntactically valid public email candidates from HTML soup."""
    if soup is None:
        return []
    candidates = []

    # 1) mailto links are strongest because the site explicitly publishes them.
    for a in soup.select('a[href^="mailto:"]'):
        email = email_from_mailto(a.get("href", ""))
        if email:
            candidates.append((_score_email_candidate(email, 100 + email_context_score(soup, a), company), email))

    # 2) Cloudflare-obfuscated emails.
    for el in soup.select("[data-cfemail], a[href*='email-protection#']"):
        raw = el.get("data-cfemail") or el.get("href", "")
        decoded = valid_email(decode_cf_email(raw))
        if decoded:
            candidates.append((_score_email_candidate(decoded, 90 + email_context_score(soup, el), company), decoded))

    # 3) Visible text, JSON-LD scripts, and safe contact attributes (never img src/srcset).
    text_sources = [soup.get_text(" ", strip=True)]
    for script in soup.find_all("script", type="application/ld+json"):
        text_sources.append(script.string or script.get_text() or "")
    for tag in soup.find_all(True):
        if tag.name in ("img", "svg", "picture", "source", "link", "style"):
            continue
        for attr in ("data-email", "data-mail", "data-contact", "content"):
            val = tag.get(attr)
            if val:
                text_sources.append(str(val))

    for source in text_sources:
        normalized = normalize_obfuscated_email(source)
        for candidate in EMAIL_RE.findall(normalized):
            email = valid_email(candidate)
            if email:
                candidates.append((_score_email_candidate(email, 15, company), email))

    best = {}
    for score, email in candidates:
        if email not in best or score > best[email]:
            best[email] = score
    return sorted(((score, email) for email, score in best.items()), reverse=True)


def extract_email(soup, company=""):
    candidates = extract_email_candidates(soup, company=company)
    return candidates[0][1] if candidates else ""


def extract_email_from_text(text, company=""):
    """Extract the highest-ranked valid email from plain text or JSON strings."""
    normalized = normalize_obfuscated_email(text)
    candidates = []
    for raw in EMAIL_RE.findall(normalized):
        email = valid_email(raw)
        if email:
            candidates.append((_score_email_candidate(email, 15, company), email))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][1]


def email_matches_company(email, company):
    """Soft company-domain consistency check, not a hard reject for free mail."""
    email = valid_email(email)
    if not email or not company:
        return False

    domain = email.rsplit("@", 1)[1]
    if domain in FREE_MAIL_DOMAINS:
        return True

    tokens = company_tokens(company)
    normalized_domain = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())
    return any(len(token) >= 4 and re.sub(r"[^a-z0-9]", "", token) in normalized_domain for token in tokens)


def plausible_public_email(email, company=""):
    """Compatibility wrapper: validate and normalize any extracted email."""
    return valid_email(email)


def extract_urls_from_text(text):
    """Extract non-job-board HTTP(S) website URLs mentioned inside offer text."""
    found = []
    seen = set()
    for raw in URL_IN_TEXT_RE.findall(str(text or "")):
        url = raw.rstrip(".,;:!?)]}\"'")
        if url.lower().startswith("www."):
            url = "https://" + url
        if not url.lower().startswith(("http://", "https://")):
            continue
        if domain_is_bad(url):
            continue
        host = host_of(url)
        if host and host not in seen:
            seen.add(host)
            found.append(f"https://{host}/")
    return found


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
    """Legacy HTML search kept as a fallback only."""
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


def _extract_ba_published_date(obj):
    if not isinstance(obj, dict):
        return ""
    period = obj.get("veroeffentlichungszeitraum")
    period_von = period.get("von") if isinstance(period, dict) else ""
    raw = clean(
        obj.get("datumErsteVeroeffentlichung")
        or period_von
        or obj.get("ersteVeroeffentlichungsdatum")
        or obj.get("aktuelleVeroeffentlichungsdatum")
        or obj.get("aenderungsdatum")
        or obj.get("modifikationsTimestamp")
        or ""
    )
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw


def _format_ba_location(details):
    if not isinstance(details, dict):
        return "Deutschland"
    locations = details.get("stellenlokationen") or details.get("arbeitsorte") or []
    if not locations or not isinstance(locations, list):
        return "Deutschland"
    first = locations[0] or {}
    addr = first.get("adresse") if isinstance(first.get("adresse"), dict) else first
    if not isinstance(addr, dict):
        return "Deutschland"
    plz = clean(addr.get("plz") or "")
    ort = clean(addr.get("ort") or "")
    raw_region = clean(addr.get("region") or "").upper()
    region = BA_REGION_MAP.get(raw_region, clean(addr.get("region") or "").replace("_", " ").title())
    city_part = clean(f"{plz} {ort}")
    parts = [p for p in (city_part, region) if p]
    return ", ".join(parts) if parts else "Deutschland"


def api_search_page(session, query, page):
    """Search Ausbildung through BA's public Jobsuche API."""
    endpoint_variants = [
        (BA_API_SEARCH_URL, {"angebotsart": 4, "was": query, "page": page, "size": BA_API_SIZE, "sort": "Aktualitaet"}),
        (BA_API_SEARCH_URL, {"angebotsart": 4, "was": query, "page": page, "size": BA_API_SIZE}),
        (BA_API_SEARCH_URL, {"angebotsart": 4, "was": query, "wo": "Deutschland", "page": page, "size": BA_API_SIZE}),
    ]

    last_exc = None
    for idx, (endpoint, params) in enumerate(endpoint_variants):
        for attempt in range(API_RETRIES):
            try:
                session.headers.update(api_headers())
                r = session.get(endpoint, params=params, timeout=DETAIL_TIMEOUT)
                if r.status_code in (403, 429):
                    wait_s = API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)]
                    print(f"[!] BA API {r.status_code} {endpoint} page {page} — retry {attempt + 1}/{API_RETRIES} dans {wait_s:.0f}s")
                    time.sleep(wait_s)
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
                    published = _extract_ba_published_date(offer)
                    token = json.dumps(
                        {"refnr": refnr, "published": published},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    links.append("aaapi://" + base64.urlsafe_b64encode(token.encode()).decode())

                if links:
                    return links

                # On page > 1, an empty HTTP 200 response simply means end of pagination.
                if page > 1 and idx == 0:
                    return []
                break

            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < API_RETRIES - 1:
                    wait_s = API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)]
                    time.sleep(wait_s)

    if last_exc:
        raise last_exc
    return []


def _bing_search(session, query, count=EXTERNAL_SEARCH_RESULTS):
    r = session.get(
        "https://www.bing.com/search?q=" + quote_plus(query) + f"&count={count}&setlang=de-DE",
        timeout=DEEP_SEARCH_TIMEOUT,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for item in soup.select("li.b_algo"):
        link = item.select_one("h2 a[href]")
        if link:
            snippet = item.select_one(".b_caption p")
            out.append((
                decode_search_url(link.get("href")),
                clean(link.get_text(" ", strip=True)),
                clean(snippet.get_text(" ", strip=True)) if snippet else "",
            ))
    return out


def _source_search_queries():
    return [
        (rn, rol, dom, f"site:{dom} {ROLE_SEARCH_TERMS[rol]} Ausbildung {REGION_SEARCH_TERMS[rn]}")
        for rn in REGION_SEARCH_TERMS
        for rol in ROLE_SEARCH_TERMS
        for dom in ALL_SOURCE_DOMAINS
    ]


def _iter_ba_links():
    """Yield BA links incrementally so discovered offers can be parsed/uploaded immediately."""
    session = make_session(BASE_URL + "/jobsuche/")
    seen_refnrs = set()

    for query in SEARCH_QUERIES:
        if scrape_time_exhausted():
            return
        print(f"[+] BA Ausbildung Recherche: {query}")
        empty_pages = 0
        page = 1

        while not scrape_time_exhausted():
            try:
                batch = api_search_page(session, query, page)
            except requests.RequestException as exc:
                print(f"[!] BA API recherche échouée {query} page {page}: {exc}")
                empty_pages += 1
                if empty_pages >= 2:
                    print(f"[!] BA API: abandon de la requête '{query}' après 2 pages en erreur.")
                    break
                page += 1
                continue

            new_count = 0
            for link in batch:
                ref_key = link
                try:
                    raw = base64.urlsafe_b64decode(link[len("aaapi://"):].encode()).decode()
                    ref_key = json.loads(raw).get("refnr") or link
                except Exception:
                    pass
                if ref_key in seen_refnrs:
                    continue
                seen_refnrs.add(ref_key)
                new_count += 1
                yield link

            print(f"    page {page}: {new_count} nouvelles offres")
            if not batch:
                break
            empty_pages = empty_pages + 1 if new_count == 0 else 0
            if empty_pages >= 2:
                break

            page += 1
            time.sleep(random.uniform(*SEARCH_DELAY))


def _iter_ausbildung_de_links(session, seen):
    """Directly scrape offer URLs from ausbildung.de profession listings."""
    for slug in AUSBILDUNG_DE_PROFESSION_SLUGS:
        if scrape_time_exhausted():
            return
        for page in range(1, 9):
            if scrape_time_exhausted():
                return
            url = f"https://www.ausbildung.de/berufe/{slug}/stellen/"
            if page > 1:
                url += f"?page={page}"
            try:
                r = session.get(url, timeout=DETAIL_TIMEOUT)
                if not r.ok:
                    break
                soup = BeautifulSoup(r.text, "html.parser")
                new_on_page = 0
                for a in soup.select("a[href*='/stellen/']"):
                    href = urljoin("https://www.ausbildung.de", a.get("href", "").split("#", 1)[0])
                    if "/stellen/" not in href or href in seen:
                        continue
                    seen.add(href)
                    new_on_page += 1
                    yield href
                print(f"[+] ausbildung.de ({slug} p.{page}): +{new_on_page} offres")
                if new_on_page == 0:
                    break
            except requests.RequestException as exc:
                print(f"[!] ausbildung.de erreur ({slug} p.{page}): {exc}")
                break
            time.sleep(random.uniform(*SEARCH_DELAY))


def _iter_azubiyo_links(session, seen):
    """Directly scrape offer URLs from azubiyo.de profession listings."""
    for slug in AZUBIYO_PROFESSION_SLUGS:
        if scrape_time_exhausted():
            return
        for page in range(1, 7):
            if scrape_time_exhausted():
                return
            url = f"https://www.azubiyo.de/ausbildung/{slug}/"
            if page > 1:
                url += f"?page={page}"
            try:
                r = session.get(url, timeout=DETAIL_TIMEOUT)
                if not r.ok:
                    break
                soup = BeautifulSoup(r.text, "html.parser")
                new_on_page = 0
                for a in soup.select("a[href*='/stellenanzeigen/']"):
                    href = urljoin("https://www.azubiyo.de", a.get("href", "").split("#", 1)[0])
                    if "/stellenanzeigen/" not in href or href in seen:
                        continue
                    seen.add(href)
                    new_on_page += 1
                    yield href
                print(f"[+] azubiyo.de ({slug} p.{page}): +{new_on_page} offres")
                if new_on_page == 0:
                    break
            except requests.RequestException as exc:
                print(f"[!] azubiyo.de erreur ({slug} p.{page}): {exc}")
                break
            time.sleep(random.uniform(*SEARCH_DELAY))


def _iter_external_links():
    """Discover Ausbildung offers directly from accessible German portals (no Bing/DDG CAPTCHA)."""
    session = make_session()
    seen = set()
    print("[*] Collecte directe sur portails Ausbildung (ausbildung.de + azubiyo.de)...")
    yield from _iter_ausbildung_de_links(session, seen)
    yield from _iter_azubiyo_links(session, seen)


def iter_collected_links():
    """Interleave BA and direct portal discovery with one global dedupe set."""
    ba = _iter_ba_links()
    external = _iter_external_links()
    ba_done = external_done = False
    seen = set()

    while not scrape_time_exhausted() and not (ba_done and external_done):
        if not ba_done:
            try:
                link = next(ba)
                if link not in seen:
                    seen.add(link)
                    yield link
            except StopIteration:
                ba_done = True

        if not external_done and not scrape_time_exhausted():
            try:
                link = next(external)
                if link not in seen:
                    seen.add(link)
                    yield link
            except StopIteration:
                external_done = True


def collect_links():
    """Compatibility wrapper returning all discovered links."""
    links = list(iter_collected_links())
    if not links:
        raise RuntimeError("Aucune offre trouvée sur les sources Ausbildung demandées.")
    print(f"[*] Total liens uniques collectés (BA + portails): {len(links)}")
    return links


def extract_offer_date(text):
    """Extract the posting or Ausbildungsbeginn date as ISO YYYY-MM-DD."""
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
    "berufskraftfahrer", "berufskraftfahrerin", "kraftfahrer", "kraftfahrerin",
    "lkw-fahrer", "lkw fahrer", "lkw-fahrerin", "lkw fahrerin",
    "fahrer ce", "fahrer c", "fahrer klasse c", "fahrer klasse ce",
    "busfahrer", "busfahrerin", "auslieferungsfahrer", "auslieferungsfahrerin",
    "kurierfahrer", "kurierfahrerin",
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

    if any(signal in text for signal in PHYSICAL_EXCLUSION_SIGNALS):
        return True

    if "fachkraft für lagerlogistik" in text or "fachkraft lagerlogistik" in text:
        return True

    return False


def market_priority(job):
    """Classify by documented market-need category only."""
    text = (
        clean(job.get("intitule", "")) + " " +
        clean(job.get("role_cible", "")) + " " +
        clean(job.get("description", ""))
    ).lower()

    base = 30
    matched = "Autre / vérifier"
    for _, category_base, terms in MARKET_PROFILES:
        hit = next((term for term in terms if term in text), None)
        if hit:
            base = category_base
            matched = hit
            break

    if base >= 70:
        priority = "P1"
    elif base >= 52:
        priority = "P2"
    else:
        priority = "P3"

    return priority, base, "besoin marché documenté", matched


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


def _normalized_text(value):
    return clean(value).lower().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")


def detect_target_role(job):
    text = _normalized_text(
        clean(job.get("intitule", "")) + " " +
        clean(job.get("role_cible", "")) + " " +
        clean(job.get("description", ""))
    )
    for role_name, rank, terms in ROLE_PRIORITY:
        if any(_normalized_text(term) in text for term in terms):
            return role_name, rank
    return "", 0


def detect_region(job):
    text = _normalized_text(
        clean(job.get("lieu", "")) + " " +
        clean(job.get("intitule", "")) + " " +
        clean(job.get("description", ""))[:2500]
    )
    for region_name, rank, terms in REGION_PRIORITY:
        if any(_normalized_text(term) in text for term in terms):
            return region_name, rank
    return "Rest Deutschland", 1


def extract_posting_sort_date(job):
    raw = clean(job.get("date_offre", ""))
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", raw)
    if m:
        d, mo, y = m.groups()
        y = ("20" + y) if len(y) == 2 else y
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            pass
    return "0000-00-00"


def job_sort_key(job):
    _, rr = detect_region(job)
    _, ror = detect_target_role(job)
    return (extract_posting_sort_date(job), rr, ror, clean(job.get("date_detection", "")), clean(job.get("id", "")))


def prioritize_jobs(jobs):
    filtered = []
    seen = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        role_name, role_rank = detect_target_role(job)
        if not role_name:
            continue
        if is_physically_unsuitable(job):
            print(f"[SKIP] körperlich ungeeignete Ausbildung: {job.get('intitule', '')}")
            continue
        region_name, region_rank = detect_region(job)
        job["prioritaet_region"] = region_rank
        job["region_cible"] = region_name
        job["prioritaet_ausbildung"] = role_rank
        job["role_cible"] = role_name
        if job.get("id") in seen:
            continue
        seen.add(job.get("id"))
        filtered.append(job)
    filtered.sort(key=job_sort_key, reverse=True)
    print(f"[*] Filtrage: {len(filtered)} offres conservées sur les six Ausbildung cibles.")
    print("[*] Ordre: publication la plus récente → la plus ancienne; région/Ausbildung utilisés seulement en cas d'égalité.")
    return filtered


def _detect_role_title(title, text):
    t = _normalized_text(clean(title) + " " + clean(text))
    if any(x in t for x in ("hotelfachfrau", "hotelfachmann", "hotelkauffrau", "hotelkaufmann", "hotelmanagement")):
        return "Hotelfachfrau / Hotelkauffrau"
    if "systemgastronomie" in t:
        return "Fachfrau für Systemgastronomie"
    if "einzelhandel" in t:
        return "Kauffrau im Einzelhandel"
    if "spedition und logistikdienstleistung" in t or "speditionskauffrau" in t or "speditionskaufmann" in t:
        return "Kauffrau für Spedition und Logistikdienstleistung"
    if "gross- und aussenhandelsmanagement" in t:
        return "Kauffrau im Groß- und Außenhandelsmanagement"
    if "industriekauffrau" in t or "industriekaufmann" in t:
        return "Industriekauffrau"
    return ""


def is_placeholder_company(name):
    return clean(name).lower() in PLACEHOLDER_COMPANIES


def employer_name_ok(value):
    value = clean(value)
    low = value.lower()
    if not value or len(value) > 160 or "@" in value or low in PLACEHOLDER_COMPANIES:
        return ""
    if any(x in low for x in ("arbeitsagentur.de", "azubiyo.de", "ausbildung.de", "indeed.com", "stepstone.de", "linkedin.com", "xing.com")):
        return ""
    return re.sub(r"^(arbeitgeber|unternehmen|firma|betrieb)\s*:?\s*", "", value, flags=re.I).strip(" -|:;")


def employer_from_json(data):
    if isinstance(data, dict):
        for key, value in data.items():
            k = str(key).lower().replace("_", "")
            if any(x in k for x in ("hiringorganization", "arbeitgeber", "employer", "company", "unternehmen", "firma")):
                if isinstance(value, dict):
                    value = value.get("name") or value.get("legalName") or value.get("companyName")
                candidate = employer_name_ok(value)
                if candidate:
                    return candidate
            candidate = employer_from_json(value)
            if candidate:
                return candidate
    elif isinstance(data, list):
        for value in data:
            candidate = employer_from_json(value)
            if candidate:
                return candidate
    return ""


def extract_employer_name(soup, text=""):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            candidate = employer_from_json(json.loads(script.string or script.get_text() or "{}"))
            if candidate:
                return candidate
        except Exception:
            pass

    for tag in soup.find_all(True):
        for attr in ("data-company", "data-employer", "data-employer-name", "data-company-name"):
            candidate = employer_name_ok(tag.get(attr))
            if candidate:
                return candidate

    patterns = (
        r"(?:Arbeitgeber|Ausbildungsbetrieb|Ausbildungsunternehmen|Unternehmen|Firma|Betrieb)\s*:?\s*([^|]{2,160}?)(?=\s+(?:Arbeitsort|Ausbildungsbeginn|Beginn|Aufgaben|Profil|Angebotsart)\b|$)",
        r"\bbei\s+([A-ZÄÖÜ0-9][^|]{2,160}?)(?=\s+(?:in|für|zum|zur|ab|als)\b|[,.]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            candidate = employer_name_ok(match.group(1))
            if candidate:
                return candidate
    return ""


def ocr_employer_from_images(soup, page_url):
    """OCR fallback for employer names shown only inside logos or banners."""
    if Image is None or pytesseract is None or soup is None or not page_url:
        return ""
    image_urls = []
    seen = set()
    for img in soup.find_all("img", src=True):
        src = img.get("src", "").strip()
        if not src or src.startswith("data:"):
            continue
        full = urljoin(page_url, src)
        if host_of(full) != host_of(page_url):
            continue
        marker_text = " ".join(str(img.get(x, "")) for x in ("alt", "title", "aria-label", "class", "id")).lower()
        priority = any(x in marker_text for x in (
            "logo", "arbeitgeber", "unternehmen", "firma", "betrieb",
            "employer", "company", "header", "banner",
        ))
        if full not in seen:
            seen.add(full)
            image_urls.append((0 if priority else 1, full))
    image_urls.sort(key=lambda x: x[0])

    sess = _get_thread_session()
    for _, image_url in image_urls[:5]:
        try:
            r = sess.get(image_url, headers={"Accept": "image/*"}, timeout=6)
            if not r.ok or not r.content:
                continue
            content_type = r.headers.get("Content-Type", "").lower()
            if content_type and not content_type.startswith("image/"):
                continue
            image = Image.open(io.BytesIO(r.content)).convert("RGB")
            if image.width < 80 or image.height < 30:
                continue
            image.thumbnail((1600, 1000))
            for psm in (6, 11):
                try:
                    raw = pytesseract.image_to_string(image, lang="deu+eng", config=f"--psm {psm}")
                except Exception:
                    raw = pytesseract.image_to_string(image, config=f"--psm {psm}")
                for line in raw.splitlines():
                    candidate = employer_name_ok(line)
                    if candidate and len(candidate.split()) <= 12:
                        low = candidate.lower()
                        if not any(x in low for x in (
                            "ausbildung", "stellenangebot", "bewerbung", "karriere",
                            "arbeitgeber", "arbeitsagentur", "jobsuche",
                        )):
                            return candidate
        except Exception:
            continue
    return ""


def recover_employer_name(url="", title="", location="", email="", soup=None, text="", details=None):
    candidate = employer_from_json(details) if details else ""
    if not candidate and soup is not None:
        candidate = extract_employer_name(soup, text)
    if not candidate and soup is not None and url and not domain_is_bad(url):
        candidate = ocr_employer_from_images(soup, url)
    if candidate:
        return candidate

    if email:
        domain = email.split("@")[-1].lower()
        if domain not in FREE_MAIL_DOMAINS and not any(domain.endswith("." + b) or domain == b for b in BAD_EMAIL_DOMAINS):
            candidate = employer_name_ok(domain.split(".")[0].replace("-", " ").replace("_", " ").title())
            if candidate:
                return candidate

    if url and not domain_is_bad(url):
        try:
            r = _get_thread_session().get(url, timeout=DEEP_SEARCH_TIMEOUT)
            if r.ok:
                page = BeautifulSoup(r.text, "html.parser")
                candidate = extract_employer_name(page, clean(page.get_text(" ", strip=True)))
                if not candidate:
                    meta = page.select_one('meta[property="og:site_name"]')
                    candidate = employer_name_ok(meta.get("content") if meta else "")
                if candidate:
                    return candidate
        except requests.RequestException:
            pass
    return ""


def ensure_employer_name(job, url="", soup=None, text="", details=None):
    candidate = employer_name_ok(job.get("entreprise", ""))
    if not candidate:
        candidate = recover_employer_name(
            url=url,
            title=job.get("intitule", ""),
            location=job.get("lieu", ""),
            email=job.get("emails_rh", ""),
            soup=soup,
            text=text,
            details=details,
        )
    job["entreprise"] = candidate or ""
    if candidate:
        print(f"[EMPLOYER] {job.get('intitule', '')} -> {candidate}")
    else:
        print(f"[WARN] Employeur non récupéré; champ laissé vide: {job.get('intitule', '')}")
    return job["entreprise"]


def _extract_jsonld_items(soup):
    """Flatten all JSON-LD blocks (including @graph lists) into a list of dicts."""
    items = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "{}")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            items.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    return items


def _normalize_external_url(raw_url):
    u = clean(raw_url)
    if not u:
        return ""
    low = u.lower()
    if low.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return ""
    if u.startswith("//"):
        u = "https:" + u
    elif not low.startswith(("http://", "https://")):
        if ":" in u.split("/", 1)[0]:
            return ""
        if "." in u and " " not in u:
            u = "https://" + u
        else:
            return ""
    return u if "." in host_of(u) else ""


def _inspect_external_offer_url(url, company=""):
    """Fetch an external offer/employer link to harvest direct email or official website."""
    url = _normalize_external_url(url)
    if not url:
        return "", ""
    sess = _get_thread_session(url)
    try:
        r = sess.get(url, timeout=DEEP_SEARCH_TIMEOUT, allow_redirects=True)
        if not r.ok:
            return "", ""
        ct = r.headers.get("Content-Type", "").lower()
        if ct and "html" not in ct and "xml" not in ct:
            return "", ""
        soup = BeautifulSoup(r.text, "html.parser")
        final_url = r.url or url
        site = ""
        if not domain_is_bad(final_url):
            site = f"https://{host_of(final_url)}/"
        else:
            for item in _extract_jsonld_items(soup):
                org = item.get("hiringOrganization") or (item if item.get("@type") in ("Organization", "Corporation", "LocalBusiness") else {})
                if isinstance(org, dict):
                    cand = _normalize_external_url(org.get("url") or org.get("sameAs") or "")
                    if cand and not domain_is_bad(cand):
                        site = f"https://{host_of(cand)}/"
                        break
        email = extract_email(soup, company=company)
        return email, site
    except requests.RequestException:
        return "", ""


def parse_external_detail(link):
    session = _get_thread_session(link)
    host = host_of(link)
    base = {
        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
        "date_offre": "",
        "statut": "NOUVEAU",
        "role_cible": "",
        "intitule": "Ausbildung",
        "entreprise": "",
        "lieu": "Deutschland",
        "emails_rh": "",
        "site_entreprise": "",
        "source": host,
        "lien": link,
        "id": "src_" + hashlib.sha256(link.encode()).hexdigest()[:20],
        "description": "",
    }
    try:
        r = session.get(link, timeout=DETAIL_TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = clean(soup.get_text(" ", strip=True))
        h1 = soup.find("h1")
        title = clean(h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(" ", strip=True) if soup.title else ""))
        company = ""
        location = ""
        published = ""
        description = ""
        employer_site = ""
        external_apply_url = ""

        for item in _extract_jsonld_items(soup):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if "JobPosting" in types:
                title = clean(item.get("title") or title)
                published = clean(item.get("datePosted") or item.get("dateCreated") or published)
                description = clean(item.get("description") or description)
                org = item.get("hiringOrganization") or {}
                if isinstance(org, dict):
                    company = employer_name_ok(org.get("name") or company) or company
                    cand_site = _normalize_external_url(org.get("url") or org.get("sameAs") or "")
                    if cand_site and not domain_is_bad(cand_site):
                        employer_site = f"https://{host_of(cand_site)}/"
                direct_apply = item.get("directApply")
                if isinstance(direct_apply, str):
                    external_apply_url = clean(direct_apply or external_apply_url)
                external_apply_url = clean(external_apply_url or item.get("url") or "")
                loc = item.get("jobLocation") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                if isinstance(loc, dict):
                    addr = loc.get("address") or {}
                    if isinstance(addr, dict):
                        location = clean(" ".join(
                            str(x) for x in (addr.get("postalCode"), addr.get("addressLocality"), addr.get("addressRegion")) if x
                        )) or location
            elif any(t in ("Corporation", "Organization", "LocalBusiness") for t in types):
                if not company:
                    cand = employer_name_ok(item.get("name") or item.get("legalName"))
                    if cand and host_of(item.get("url") or "") != host:
                        company = cand
                if not employer_site:
                    cand_site = _normalize_external_url(item.get("url") or item.get("sameAs") or "")
                    if cand_site and not domain_is_bad(cand_site):
                        employer_site = f"https://{host_of(cand_site)}/"

        if not company:
            company = extract_employer_name(soup, text)
        if not employer_site:
            for a in soup.select("a[href^='http']"):
                href = a.get("href", "")
                if domain_is_bad(href):
                    continue
                label = clean(a.get_text(" ", strip=True)).lower()
                if any(w in label for w in ("website", "webseite", "homepage", "unternehmens", "karriere", "bewerben")):
                    employer_site = f"https://{host_of(href)}/"
                    break
        if not employer_site:
            desc_urls = extract_urls_from_text(description or text[:4000])
            if desc_urls:
                employer_site = desc_urls[0]

        email = extract_email(soup, company=company)
        if not company:
            company = recover_employer_name(url=link, title=title, location=location, email=email, soup=soup, text=text)
        if not location:
            for pat in (
                r"(?:Arbeitsort|Ort|Standort)\s*:?\s*(.+?)(?:\s+Anstellungsart|\s+Ausbildungsbeginn|\s+Beginn|$)",
                r"\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){0,2})\b",
            ):
                m = re.search(pat, text, re.I)
                if m:
                    location = clean(" ".join(m.groups()))
                    break
        if not published:
            published = extract_offer_date(text)
        role = _detect_role_title(title, text)
        if not role:
            return None
        base.update({
            "intitule": title or role,
            "entreprise": company or "",
            "lieu": location or "Deutschland",
            "emails_rh": email,
            "role_cible": role,
            "date_offre": published,
            "description": description or text[:8000],
            "site_entreprise": employer_site,
            "lien": external_apply_url or link,
        })
        ensure_employer_name(base, url=employer_site or link, soup=soup, text=text)
        return base
    except requests.RequestException as exc:
        print(f"[!] source detail inaccessible: {link} -> {exc}")
        return None
    except Exception as exc:
        print(f"[!] source parsing erreur: {link} -> {exc}")
        return None
    finally:
        time.sleep(random.uniform(*DETAIL_DELAY))


def parse_detail(link):
    session = _get_thread_session(BASE_URL + "/jobsuche/")

    # Primary path: official Jobsuche API.
    if link.startswith("aaapi://"):
        encoded_ref = link[len("aaapi://"):]
        base = {
            "date_detection": time.strftime("%Y-%m-%d %H:%M"),
            "date_offre": "",
            "statut": "NOUVEAU",
            "role_cible": "Ausbildung - priorisierte Logistik / Handel / Einzelhandel",
            "intitule": "Ausbildung Kaufmann/Kauffrau",
            "entreprise": "",
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
            details = {}

            for attempt in range(API_RETRIES):
                try:
                    session.headers.update(api_headers())
                    r = session.get(f"{BA_API_DETAILS_URL}/{encrypted}", timeout=DETAIL_TIMEOUT)
                    if r.status_code in (403, 429):
                        wait_s = API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)]
                        print(f"[!] BA API détails {r.status_code} — retry {attempt + 1}/{API_RETRIES} dans {wait_s:.0f}s")
                        time.sleep(wait_s)
                        continue
                    r.raise_for_status()
                    details = r.json()
                    last_exc = None
                    break
                except (requests.RequestException, ValueError) as exc:
                    last_exc = exc
                    if attempt < API_RETRIES - 1:
                        wait_s = API_BACKOFF[min(attempt, len(API_BACKOFF) - 1)]
                        time.sleep(wait_s)

            if last_exc:
                raise last_exc

            title = clean(
                details.get("stellenangebotsTitel")
                or details.get("titel")
                or details.get("beruf")
                or base["intitule"]
            )
            company = employer_name_ok(details.get("firma") or details.get("arbeitgeber") or "") or employer_from_json(details)
            location = _format_ba_location(details)
            published = _extract_ba_published_date(details) or base.get("date_offre") or ""
            description = clean(
                details.get("stellenangebotsBeschreibung")
                or details.get("stellenbeschreibung")
                or ""
            )

            email = extract_email_from_text(json.dumps(details, ensure_ascii=False), company=company)
            external_url = clean(
                details.get("externeUrl")
                or details.get("allianzpartnerUrl")
                or ""
            )
            if not email and external_url.lower().startswith("mailto:"):
                email = email_from_mailto(external_url)

            site_entreprise = ""
            norm_ext = _normalize_external_url(external_url)
            if norm_ext and not domain_is_bad(norm_ext):
                site_entreprise = f"https://{host_of(norm_ext)}/"

            if not site_entreprise:
                desc_urls = extract_urls_from_text(description)
                if desc_urls:
                    site_entreprise = desc_urls[0]

            # If BA JSON didn't include an email, check external_url directly when present.
            if not email and norm_ext and "arbeitsagentur.de" not in host_of(norm_ext):
                ext_email, ext_site = _inspect_external_offer_url(norm_ext, company=company)
                if ext_email:
                    email = ext_email
                if ext_site and not site_entreprise:
                    site_entreprise = ext_site

            role = _detect_role_title(title, description) or "Autre / vérifier"

            base.update({
                "date_offre": published,
                "intitule": title,
                "entreprise": company,
                "lieu": location,
                "emails_rh": email,
                "site_entreprise": site_entreprise,
                "role_cible": role,
                "lien": external_url,
                "description": description,
                "id": "aa_" + clean(details.get("refnr") or details.get("referenznummer") or refnr),
            })
            ensure_employer_name(base, url=site_entreprise or external_url, text=description, details=details)
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
        "statut": "NOUVEAU",
        "role_cible": "Ausbildung - priorisierte Logistik / Handel / Einzelhandel",
        "intitule": "Ausbildung Kaufmann/Kauffrau",
        "entreprise": "",
        "lieu": "Deutschland",
        "emails_rh": "",
        "site_entreprise": "",
        "source": "Agentur für Arbeit - Ausbildung",
        "lien": link,
        "id": "aa_" + source_id,
    }
    try:
        r = session.get(link, timeout=DETAIL_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = clean(soup.get_text(" ", strip=True))
        h1 = soup.find("h1")
        title = clean(h1.get_text(" ", strip=True)) if h1 else base["intitule"]
        title = re.sub(r"^Stellenangebot:\s*", "", title, flags=re.I)
        company = extract_employer_name(soup, text)
        email = extract_email(soup, company=company)
        if not company:
            company = recover_employer_name(url=link, title=title, email=email, soup=soup, text=text)
        location = "Deutschland"
        for pattern in [
            r"Arbeitsort\s*:?[ ]+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|\s+Beginn|$)",
            r"Ort\s*:?[ ]+(.+?)(?:\s+Anstellungsart|\s+Angebotsart|$)",
        ]:
            m = re.search(pattern, text, re.I)
            if m:
                location = clean(m.group(1))
                break
        role = _detect_role_title(title, text) or "Autre / vérifier"
        base.update({
            "intitule": title,
            "entreprise": company or "",
            "lieu": location,
            "emails_rh": email,
            "role_cible": role,
            "date_offre": extract_offer_date(text),
        })
        ensure_employer_name(base, url=link, soup=soup, text=text)
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
    """Parse a bounded batch of offer pages without queueing the entire run in memory."""
    jobs = []
    links = list(dict.fromkeys(links))
    if not links:
        return jobs

    executor = ThreadPoolExecutor(max_workers=DETAIL_WORKERS)
    pending = set()
    iterator = iter(links)
    submitted = 0
    processed = 0

    def refill():
        nonlocal submitted
        while len(pending) < DETAIL_WORKERS * 2 and not scrape_time_exhausted():
            try:
                link = next(iterator)
            except StopIteration:
                return
            pending.add(executor.submit(parse_detail, link))
            submitted += 1

    try:
        refill()
        while pending and not scrape_time_exhausted():
            wait_timeout = min(5.0, scrape_time_remaining())
            done, pending = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
            if not done:
                refill()
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

            refill()

            if processed and processed % 50 == 0:
                count = sum(1 for x in jobs if x.get("emails_rh"))
                print(f"[*] détails traités: {processed}/{len(links)} | offres: {len(jobs)} | avec email offre: {count}")

    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)

    jobs = list({job["id"]: job for job in jobs}.values())
    print(f"[*] Offres finales du batch: {len(jobs)} | avec email: {sum(1 for x in jobs if x.get('emails_rh'))}")
    return jobs


# ============================================================
# DEEP SEARCH — UNIQUE COMPANIES + VERIFIED OFFICIAL SITES
# ============================================================

def normalize_company(name):
    name = clean(name).lower()
    name = re.sub(r"\b(gmbh\s*&\s*co\.?\s*kg|gmbh|ag|kg|ohg|e\.k\.|ug|se|mbh|ltd\.?|ggmbh|stiftung\s*&\s*co\.?\s*kg|b\.v\.?\s*&\s*co\.?\s*kg)\b", " ", name, flags=re.I)
    return clean(name)


def host_of(url):
    try:
        raw = str(url or "").strip()
        low = raw.lower()
        if not raw or low.startswith(("mailto:", "tel:", "javascript:", "data:")):
            return ""
        if not low.startswith(("http://", "https://")) and "." in raw:
            raw = "https://" + raw
        host = urlparse(raw).netloc.lower().split(":")[0]
        host = host[4:] if host.startswith("www.") else host
        return host if "." in host else ""
    except Exception:
        return ""


def domain_is_bad(url):
    host = host_of(url)
    return not host or any(host == d or host.endswith("." + d) for d in BAD_SITE_DOMAINS)


def company_tokens(company):
    generic = {
        "gmbh", "ag", "kg", "ohg", "ug", "se", "mbh", "co", "and", "und",
        "der", "die", "das", "unternehmen", "group", "holding", "company",
        "deutschland", "germany", "ltd", "stiftung", "ggmbh", "verwaltung",
        "zentralverwaltungsgesellschaft", "dienstleistung", "service",
    }
    norm = _normalized_text(normalize_company(company))
    return [x for x in re.findall(r"[a-z0-9]{3,}", norm) if x not in generic]


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
    return _bing_search(session, query, count=8)


def search_engine_duckduckgo(session, query):
    r = session.get("https://html.duckduckgo.com/html/?q=" + quote_plus(query), timeout=DEEP_SEARCH_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for item in soup.select(".result"):
        a = item.select_one(".result__a[href]")
        if a:
            snippet = item.select_one(".result__snippet")
            out.append((
                decode_search_url(a.get("href")),
                clean(a.get_text(" ", strip=True)),
                clean(snippet.get_text(" ", strip=True)) if snippet else "",
            ))
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
        if token in _normalized_text(title + " " + snippet):
            score += 2
    if any(word in (title + " " + snippet).lower() for word in ("kontakt", "impressum", "offizielle", "official")):
        score += 2
    return score


def verify_official_site(url, company, location="", trusted_hint=False):
    """Verify company identity on the actual site and extract any homepage email."""
    if domain_is_bad(url):
        return "", ""
    root = f"https://{host_of(url)}/"
    tokens = company_tokens(company)
    city = _normalized_text(_extract_city_for_directory(location))
    sess = _get_thread_session()
    try:
        r = sess.get(root, timeout=DEEP_SEARCH_TIMEOUT, allow_redirects=True)
        if not r.ok:
            return "", ""
        ct = r.headers.get("Content-Type", "").lower()
        if ct and "html" not in ct:
            return "", ""
        final_root = f"https://{host_of(r.url)}/"
        if domain_is_bad(final_root):
            return "", ""
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = _normalized_text(soup.get_text(" ", strip=True))
        title = _normalized_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        combined = f"{title} {page_text}"
        final_host = host_of(final_root)

        # Non-.de domains must show German business context so foreign companies
        # (e.g. tiendanorma.com, mullerseeds.com) are never matched.
        if not final_host.endswith(".de"):
            german_markers = (
                "impressum", "datenschutz", "deutschland", "gmbh", "karriere",
                "ausbildung", "bewerbung", "kontakt", "+49",
            )
            if not any(m in combined for m in german_markers) and not (city and city in combined):
                return "", ""

        page_hits = sum(1 for token in tokens if token in combined)
        host_hits = sum(1 for token in tokens if len(token) >= 4 and token in final_host)
        city_hit = bool(city and len(city) >= 3 and city in combined)

        if tokens:
            if trusted_hint:
                if page_hits == 0 and host_hits == 0:
                    return "", ""
            elif len(tokens) == 1:
                tok = tokens[0]
                if page_hits == 0:
                    return "", ""
                # Short single-token surname/word requires city proof on the site.
                if len(tok) <= 7 and not city_hit:
                    return "", ""
            else:
                if page_hits < 2 and not (page_hits >= 1 and city_hit) and not (host_hits >= 2):
                    return "", ""

        return final_root, extract_email(soup, company=company)
    except requests.RequestException:
        return "", ""


def site_pages_to_check(site):
    """Discover prioritized contact/career/impressum pages on a verified official domain."""
    root = site.rstrip("/") + "/"
    urls = [root]
    seen = {root}
    priority_paths = (
        "/impressum", "/kontakt", "/karriere", "/ausbildung", "/bewerbung",
        "/jobs", "/contact", "/ansprechpartner", "/personal", "/karriere/ausbildung",
        "/karriere/jobs", "/stellenangebote", "/bewerben", "/hr",
    )
    sess = _get_thread_session()
    discovered_links = []
    try:
        r = sess.get(root, timeout=DEEP_SEARCH_TIMEOUT)
        if r.ok:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(root, a["href"].split("#", 1)[0])
                if host_of(href) != host_of(root):
                    continue
                label = clean(a.get_text(" ", strip=True)).lower()
                value = (href + " " + label).lower()
                if any(word in value for word in CONTACT_WORDS) and href not in seen:
                    seen.add(href)
                    discovered_links.append(href)
    except requests.RequestException:
        pass

    for path in priority_paths:
        href = urljoin(root, path.lstrip("/"))
        if href not in seen:
            seen.add(href)
            urls.append(href)
    urls.extend(discovered_links[:10])
    return urls[:16]


def _is_high_confidence_hr_email(email):
    if not email:
        return False
    local = email.split("@", 1)[0]
    return any(hint in local for hint in HR_LOCALPART_HINTS)


def crawl_verified_site(site, company=""):
    """Crawl key contact/career pages on a verified official site within a 15s budget."""
    queue = list(site_pages_to_check(site))
    seen = set(queue)
    started = time.monotonic()
    sess = _get_thread_session()
    all_candidates = []
    checked = 0

    while queue and checked < 10:
        if time.monotonic() - started >= 15 or scrape_time_exhausted():
            break

        page_url = queue.pop(0)
        checked += 1
        try:
            r = sess.get(page_url, timeout=DEEP_SEARCH_TIMEOUT, allow_redirects=True)
            if not r.ok:
                continue
            content_type = r.headers.get("Content-Type", "").lower()
            if content_type and "html" not in content_type and "xml" not in content_type:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            page_candidates = extract_email_candidates(soup, company=company)
            if page_candidates:
                all_candidates.extend(page_candidates)
                best_now = sorted(all_candidates, reverse=True)[0][1]
                if _is_high_confidence_hr_email(best_now):
                    return best_now

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

    if not all_candidates:
        return ""
    all_candidates.sort(reverse=True)
    return all_candidates[0][1]


def _lookup_clearbit_domains(company_clean):
    """Query Clearbit's free company autocomplete API for candidate domains."""
    tokens = company_tokens(company_clean)
    if not tokens:
        return []
    query = " ".join(tokens[:4])
    sess = _get_thread_session()
    domains = []
    try:
        r = sess.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": query},
            timeout=5,
        )
        if not r.ok:
            return []
        items = r.json()
        if not isinstance(items, list):
            return []
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            dom = clean(item.get("domain") or "").lower()
            name = _normalized_text(item.get("name") or "")
            if not dom or domain_is_bad(dom):
                continue
            if not dom.endswith((".de", ".com", ".eu", ".net", ".org")):
                continue
            hits = sum(1 for t in tokens if t in dom or t in name)
            if hits == 0:
                continue
            if len(tokens) >= 2 and hits < 2 and not dom.endswith(".de"):
                continue
            domains.append((0 if dom.endswith(".de") else 1, f"https://{dom}/"))
    except (requests.RequestException, ValueError):
        return []
    domains.sort(key=lambda x: x[0])
    return [u for _, u in domains]


def _extract_city_for_directory(location):
    loc = clean(location)
    if not loc or loc.lower() == "deutschland":
        return ""
    # Strip leading PLZ and trailing state/comma.
    city = loc.split(",")[0].strip()
    city = re.sub(r"^\d{5}\s+", "", city).strip()
    city = re.sub(r"\s*\([^)]*\)\s*", " ", city).strip()
    return city


def _lookup_gelbeseiten(company_clean, location=""):
    """Look up official website and direct email on GelbeSeiten (Germany yellow pages)."""
    tokens = company_tokens(company_clean)
    if not tokens:
        return [], ""
    what_slug = quote("-".join(tokens[:4]))
    city = _extract_city_for_directory(location)
    where_targets = []
    if city:
        where_targets.append(quote(re.sub(r"[^a-z0-9äöüß-]+", "-", city.lower()).strip("-")))
    where_targets.append("bundesweit")

    sess = _get_thread_session()
    candidate_sites = []
    direct_email = ""
    city_norm = _normalized_text(city)

    for where in where_targets:
        if not where:
            continue
        url = f"https://www.gelbeseiten.de/suche/{what_slug}/{where}"
        try:
            r = sess.get(url, timeout=6)
            if not r.ok:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            articles = soup.select("article.mod-Treffer")
            if not articles:
                continue
            for art in articles[:4]:
                art_text = _normalized_text(art.get_text(" ", strip=True))
                token_hits = sum(1 for t in tokens if t in art_text)
                if token_hits == 0:
                    continue
                if len(tokens) >= 2 and token_hits < 2 and not (city_norm and city_norm in art_text):
                    continue
                biz_link = art.select_one("a[href*='/gsbiz/']")
                if not biz_link:
                    continue
                biz_url = urljoin("https://www.gelbeseiten.de", biz_link.get("href", ""))
                try:
                    rb = sess.get(biz_url, timeout=6)
                    if not rb.ok:
                        continue
                    bsoup = BeautifulSoup(rb.text, "html.parser")
                    for a in bsoup.select("a.contains-icon-big-homepage[href], a[href^='http']"):
                        href = a.get("href", "")
                        if not domain_is_bad(href):
                            candidate_sites.append(f"https://{host_of(href)}/")
                            break
                    if not direct_email:
                        direct_email = extract_email(bsoup, company=company_clean)
                except requests.RequestException:
                    continue
                if candidate_sites or direct_email:
                    return candidate_sites, direct_email
        except requests.RequestException:
            continue
    return candidate_sites, direct_email


def _guess_german_domains(company_clean):
    """Generate a small set of high-probability .de/.com domains for German SMEs."""
    tokens = company_tokens(company_clean)
    if not tokens:
        return []
    slugs = []
    if len(tokens) >= 2:
        slugs.append(f"{tokens[0]}-{tokens[1]}")
        slugs.append(f"{tokens[0]}{tokens[1]}")
    if len(tokens) >= 3:
        slugs.append("-".join(tokens[:3]))
    if len(tokens) == 1 and len(tokens[0]) >= 6:
        slugs.append(tokens[0])

    urls = []
    seen = set()
    for slug in slugs[:4]:
        for tld in (".de", ".com"):
            u = f"https://{slug}{tld}/"
            if u not in seen and not domain_is_bad(u):
                seen.add(u)
                urls.append(u)
    return urls


def _verify_and_crawl_candidate(url, company_clean, location="", trusted=False, fallback_email=""):
    site, homepage_email = verify_official_site(url, company_clean, location=location, trusted_hint=trusted)
    if not site:
        return "", ""
    if _is_high_confidence_hr_email(homepage_email):
        return homepage_email, site
    crawled = crawl_verified_site(site, company=company_clean)
    best = crawled or homepage_email or fallback_email
    if best:
        return best, site
    return "", ""


def search_company_web(company, location="", hint_urls=()):
    """Find and verify the employer site, then return (email, site)."""
    company_clean = normalize_company(company)
    if not company_clean or is_placeholder_company(company) or is_placeholder_company(company_clean):
        return "", ""

    candidate_urls = []
    seen_hosts = set()

    def add_candidate(raw_url, trusted=False):
        norm = _normalize_external_url(raw_url)
        if not norm or domain_is_bad(norm):
            return
        h = host_of(norm)
        if h and h not in seen_hosts:
            seen_hosts.add(h)
            candidate_urls.append((f"https://{h}/", trusted))

    for u in (hint_urls or ()):
        add_candidate(u, trusted=True)

    # Try fast Clearbit company domain lookup before directory/guessing.
    if not scrape_time_exhausted():
        for u in _lookup_clearbit_domains(company_clean):
            add_candidate(u, trusted=False)

    for url, trusted in candidate_urls:
        if scrape_time_exhausted():
            break
        email, site = _verify_and_crawl_candidate(url, company_clean, location=location, trusted=trusted)
        if email:
            return email, site

    # Fall back to GelbeSeiten directory lookup (HTTP 200, no CAPTCHA).
    if not scrape_time_exhausted():
        gs_sites, gs_email = _lookup_gelbeseiten(company_clean, location=location)
        for gs_site in gs_sites:
            email, site = _verify_and_crawl_candidate(
                gs_site, company_clean, location=location, fallback_email=gs_email
            )
            if email:
                return email, site
        if gs_email:
            return gs_email, gs_sites[0] if gs_sites else ""

    # Final fast probe of obvious German domain slugs.
    if not scrape_time_exhausted():
        for guess_url in _guess_german_domains(company_clean):
            if host_of(guess_url) in seen_hosts:
                continue
            email, site = _verify_and_crawl_candidate(guess_url, company_clean, location=location)
            if email:
                return email, site

    return "", ""


def enrich_missing_emails(jobs):
    """Enrich missing employer emails inside the same scraper run with run-level caching."""
    missing = [
        job for job in jobs
        if not valid_email(job.get("emails_rh", ""))
        and clean(job.get("entreprise", ""))
        and not is_placeholder_company(job.get("entreprise", ""))
    ]
    if not missing:
        return jobs

    groups = {}
    for job in missing:
        company = clean(job.get("entreprise", ""))
        key = normalize_company(company)
        if not key or is_placeholder_company(key):
            continue
        group = groups.setdefault(key, {
            "key": key,
            "company": company,
            "location": clean(job.get("lieu", "")),
            "hint_urls": [],
            "jobs": [],
        })
        group["jobs"].append(job)
        for hint in (job.get("site_entreprise", ""), job.get("lien", "")):
            if hint and not domain_is_bad(hint) and hint not in group["hint_urls"]:
                group["hint_urls"].append(hint)
        for u in extract_urls_from_text(job.get("description", "")):
            if u not in group["hint_urls"]:
                group["hint_urls"].append(u)

    if not groups:
        return jobs

    to_lookup = []
    cache_hits = 0
    for key, group in groups.items():
        with _COMPANY_CACHE_LOCK:
            cached = COMPANY_ENRICH_CACHE.get(key)
        if cached is not None:
            cache_hits += 1
            email, site = cached
            if email:
                for job in group["jobs"]:
                    job["emails_rh"] = email
                    if site:
                        job["site_entreprise"] = site
            continue
        to_lookup.append(group)

    print(
        f"[*] Email search intégré: {len(to_lookup)} nouvelles entreprises à vérifier "
        f"({cache_hits} en cache)."
    )
    if not to_lookup:
        return jobs

    def enrich_group(group):
        if scrape_time_exhausted():
            return group, "", ""
        try:
            email, site = search_company_web(
                group["company"],
                group["location"],
                hint_urls=group["hint_urls"],
            )
            return group, email, site
        except Exception as exc:
            print(f"[!] Email search erreur | {group['company']}: {exc}")
            return group, "", ""

    with ThreadPoolExecutor(max_workers=DEEP_SEARCH_WORKERS) as executor:
        futures = [executor.submit(enrich_group, group) for group in to_lookup]
        for future in as_completed(futures):
            group, email, site = future.result()
            with _COMPANY_CACHE_LOCK:
                COMPANY_ENRICH_CACHE[group["key"]] = (email, site)
            if not email:
                continue
            for job in group["jobs"]:
                job["emails_rh"] = email
                if site:
                    job["site_entreprise"] = site
            print(
                f"[OK] Email vérifié: {email} | {group['company']} "
                f"| offres={len(group['jobs'])}"
            )

    return jobs


def post_json(payload, label):
    webhook = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL manquant")

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
            except ValueError as exc:
                preview = response.text[:300].replace("\\n", " ")
                raise RuntimeError(
                    f"{label}: réponse non-JSON malgré HTTP 200; "
                    f"vérifier l'URL/le déploiement Apps Script. Réponse: {preview}"
                ) from exc

            if not isinstance(result, dict) or result.get("status") != "success":
                raise RuntimeError(
                    f"{label}: réponse Apps Script inattendue: {str(result)[:1000]}"
                )

            print(f"[*] Réponse Apps Script confirmée: {result}")
            return result

        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            print(f"[!] {label} échoué: {exc}")

            if isinstance(exc, RuntimeError) and "réponse non-JSON" in str(exc):
                break

            if attempt < WEBHOOK_RETRIES:
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


def _recover_missing_company_from_ba(row):
    """If an older Sheet row has 'Entreprise non indiquée' and an aa_ ID, recover metadata via BA API."""
    job_id = clean(row.get("id", ""))
    if not job_id.startswith("aa_"):
        return row
    refnr = job_id[3:]
    if not refnr or len(refnr) < 5:
        return row
    token = json.dumps({"refnr": refnr, "published": ""}, separators=(",", ":"))
    details_job = parse_detail("aaapi://" + base64.urlsafe_b64encode(token.encode()).decode())
    if details_job and not is_placeholder_company(details_job.get("entreprise", "")):
        row["entreprise"] = details_job["entreprise"]
        if details_job.get("lieu") and details_job["lieu"] != "Deutschland":
            row["lieu"] = details_job["lieu"]
        if details_job.get("emails_rh"):
            row["emails_rh"] = details_job["emails_rh"]
        if details_job.get("site_entreprise"):
            row["site_entreprise"] = details_job["site_entreprise"]
        if details_job.get("description"):
            row["description"] = details_job["description"]
    return row


def enrich_existing_missing_emails(limit=100):
    """Enrich a rotating slice of older Sheet rows with valid company identities."""
    limit = max(1, min(int(limit), 500))
    data = post_json(
        {"action": "export_missing_emails", "offset": 0, "limit": limit},
        "Lecture des offres historiques sans email",
    )
    total_missing = max(0, int(data.get("total_missing", 0) or 0)) if isinstance(data, dict) else 0
    rows = data.get("rows", []) if isinstance(data, dict) else []
    if not rows and total_missing == 0:
        print("[*] Aucune offre historique sans email à enrichir.")
        return 0

    start_offset = 0
    if total_missing > limit:
        start_offset = (int(time.time() // 43200) * limit) % total_missing

    usable_rows = []
    # Scan up to 5 pages from the rotating offset so placeholder-company blocks
    # ("Entreprise non indiquée") do not starve historical email enrichment.
    for page_idx in range(5):
        if scrape_time_exhausted() or len(usable_rows) >= limit:
            break
        offset = (start_offset + page_idx * limit) % max(1, total_missing) if total_missing else 0
        if page_idx > 0 or offset != 0:
            data = post_json(
                {"action": "export_missing_emails", "offset": offset, "limit": limit},
                "Lecture du lot historique tournant",
            )
            rows = data.get("rows", []) if isinstance(data, dict) else []
        if not rows:
            break
        for row in rows:
            if is_placeholder_company(row.get("entreprise", "")):
                row = _recover_missing_company_from_ba(row)
            if not is_placeholder_company(row.get("entreprise", "")):
                usable_rows.append(row)
                if len(usable_rows) >= limit:
                    break
        if total_missing <= limit:
            break

    if not usable_rows:
        print("[*] Aucune entreprise exploitable dans le lot historique.")
        return 0

    print(f"[*] Enrichissement historique: {len(usable_rows)} lignes exploitables sans email.")
    enriched = enrich_missing_emails(usable_rows)
    found = sum(1 for job in enriched if valid_email(job.get("emails_rh", "")))
    update_sheet(enriched)
    print(f"[OK] Enrichissement historique terminé: {found} emails trouvés sur {len(usable_rows)} lignes.")
    return found


def main():
    print("Utiliser daily_runner.py comme point d'entrée du workflow.")
    print("Le runner gère la collecte incrémentale, les uploads par batch et le budget temps.")


if __name__ == "__main__":
    main()
