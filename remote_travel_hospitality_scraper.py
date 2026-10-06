import csv
import hashlib
import os
import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
SOURCE_LINKEDIN = "https://www.linkedin.com/search/results/companies/?origin=FACETED_SEARCH&industryCompanyVertical=%5B%2231%22%2C%2230%22%5D"
INDUSTRIES = {"31": "Hospitality", "30": "Travel Arrangements"}

REMOTE_QUERIES = [
    '"remote" "Morocco" travel customer success',
    '"remote from Morocco" travel',
    '"work from Morocco" travel',
    '"Morocco" "remote" "customer success" hospitality',
    '"Morocco" "remote" "account manager" travel',
    '"Morocco" "remote" "travel operations"',
    '"Morocco" "remote" "reservations"',
    '"MENA" remote travel "customer success"',
    '"EMEA" remote travel "account manager"',
    '"remote" hospitality "customer support" Morocco',
]

ROLE_TERMS = [
    "customer success", "account manager", "account management", "customer support",
    "customer experience", "business development", "sales", "partnerships",
    "partner success", "supplier relations", "travel operations", "operations",
    "reservations", "booking", "travel consultant", "market manager",
    "onboarding", "implementation", "commercial", "sales executive",
]

JOB_DOMAINS = [
    "linkedin.com/jobs/view", "indeed.com", "greenhouse.io", "lever.co",
    "workable.com", "smartrecruiters.com", "ashbyhq.com", "wellfound.com",
    "teamtailor.com", "personio.com",
]

CONTACT_PATHS = [
    "/contact", "/kontakt", "/impressum", "/careers", "/career", "/jobs",
    "/work-with-us", "/about", "/about-us", "/team", "/human-resources",
]

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
BAD_EMAILS = {"example@example.com", "test@test.com", "info@example.com"}
TIMEOUT = 12
MAX_COMPANIES = 500
MAX_RESULTS_PER_QUERY = 12
WORKERS = 10
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/154 Safari/537.36"


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def host(url):
    try:
        h = urlparse(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def company_key(name):
    x = clean(name).lower()
    x = re.sub(r"\b(gmbh|ag|kg|ltd|llc|inc|group|holding|company)\b", " ", x)
    return clean(x)


def valid_email(email):
    e = email.lower().strip().strip(".,;:()[]<>")
    return bool(EMAIL_RE.fullmatch(e)) and e not in BAD_EMAILS and ".." not in e.split("@")[0]


def extract_email(text):
    for e in EMAIL_RE.findall(text or ""):
        e = e.lower().strip(".,;:()[]<>")
        if valid_email(e):
            return e
    return ""


def search_bing(session, query, count=10):
    url = "https://www.bing.com/search?q=" + quote_plus(query) + f"&count={count}&setlang=en-US"
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for item in soup.select("li.b_algo"):
        a = item.select_one("h2 a[href]")
        if not a:
            continue
        sn = item.select_one(".b_caption p")
        out.append((a.get("href", ""), clean(a.get_text(" ", strip=True)), clean(sn.get_text(" ", strip=True)) if sn else ""))
    return out


def search_ddg(session, query):
    r = session.get("https://html.duckduckgo.com/html/?q=" + quote_plus(query), timeout=8,
                   headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for item in soup.select(".result"):
        a = item.select_one(".result__a[href]")
        if not a:
            continue
        sn = item.select_one(".result__snippet")
        out.append((a.get("href", ""), clean(a.get_text(" ", strip=True)), clean(sn.get_text(" ", strip=True)) if sn else ""))
    return out


def linkedin_company_from_url(url):
    url = unquote(url or "")
    if "linkedin.com/company/" not in url.lower():
        return ""
    p = urlparse(url)
    path = p.path.rstrip("/")
    return "https://www.linkedin.com" + path + "/" if path.lower().startswith("/company/") else ""


def discover_companies():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    companies = {}
    for industry_id, industry in INDUSTRIES.items():
        regions = ["Germany", "Europe", "Morocco", "MENA", "EMEA", "United Kingdom", "Spain", "France", "Portugal", "UAE"]
        for region in regions:
            queries = [
                f'site:linkedin.com/company/ "{industry}" "{region}"',
                f'site:linkedin.com/company/ "{industry}" travel technology "{region}"',
            ]
            for query in queries:
                try:
                    results = search_bing(session, query, 10)
                except requests.RequestException:
                    try:
                        results = search_ddg(session, query)
                    except requests.RequestException:
                        results = []
                for url, title, snippet in results[:MAX_RESULTS_PER_QUERY]:
                    li = linkedin_company_from_url(url)
                    if not li:
                        continue
                    name = re.sub(r"\s*[-|–—]\s*LinkedIn.*$", "", title, flags=re.I)
                    name = clean(name)
                    if not name:
                        continue
                    k = company_key(name)
                    if k not in companies:
                        companies[k] = {
                            "company": name,
                            "linkedin_url": li,
                            "industry_id": industry_id,
                            "industry": industry,
                            "discovery_region": region,
                            "discovery_snippet": snippet,
                        }
                if len(companies) >= MAX_COMPANIES:
                    return list(companies.values())
                time.sleep(random.uniform(.2, .5))
    return list(companies.values())


def candidate_site_urls(session, company):
    candidates = []
    queries = [
        f'"{company}" official website',
        f'"{company}" contact email',
        f'"{company}" careers jobs',
        f'"{company}" travel hospitality',
    ]
    seen = set()
    for q in queries:
        try:
            results = search_bing(session, q, 8)
        except requests.RequestException:
            continue
        for url, title, snippet in results:
            h = host(url)
            if not h or "linkedin.com" in h or any(x in h for x in JOB_DOMAINS):
                continue
            if h not in seen:
                seen.add(h)
                candidates.append(url)
        time.sleep(random.uniform(.15, .4))
    return candidates[:8]


def verify_site_and_email(session, url, company):
    h = host(url)
    if not h:
        return "", ""
    root = "https://" + h + "/"
    try:
        r = session.get(root, timeout=TIMEOUT, allow_redirects=True,
                        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"})
        if not r.ok:
            return "", ""
        final = "https://" + host(r.url) + "/"
        soup = BeautifulSoup(r.text, "html.parser")
        text = clean(soup.get_text(" ", strip=True)).lower()
        tokens = [t for t in re.findall(r"[a-z0-9]{4,}", company_key(company).lower()) if t not in {"travel","group","hotel","company"}]
        if tokens and not any(t in text or t in (soup.title.get_text(" ", strip=True).lower() if soup.title else "") for t in tokens):
            return "", ""
        email = extract_email(r.text)
        if email:
            return final, email
        for path in CONTACT_PATHS:
            u = urljoin(final, path.lstrip("/"))
            try:
                cr = session.get(u, timeout=7, allow_redirects=True, headers={"User-Agent": USER_AGENT})
                if cr.ok:
                    email = extract_email(cr.text)
                    if email:
                        return final, email
            except requests.RequestException:
                pass
    except requests.RequestException:
        return "", ""
    return final, ""


def find_company_email(company):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    for url in candidate_site_urls(session, company):
        site, email = verify_site_and_email(session, url, company)
        if site and email:
            return site, email
    return "", ""


def find_remote_jobs(company):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    results = []
    queries = [
        f'"{company}" remote Morocco jobs',
        f'"{company}" "remote" "Morocco"',
        f'"{company}" "work from Morocco"',
        f'"{company}" remote "customer success"',
        f'"{company}" remote "account manager"',
        f'"{company}" remote travel hospitality jobs',
    ]
    seen = set()
    for q in queries:
        try:
            rows = search_bing(session, q, 10)
        except requests.RequestException:
            continue
        for url, title, snippet in rows:
            low = (title + " " + snippet + " " + url).lower()
            role_hit = any(term in low for term in ROLE_TERMS)
            remote_hit = any(term in low for term in ["remote", "work from home", "work from anywhere", "morocco", "maroc", "mena", "emea"])
            if not role_hit or not remote_hit:
                continue
            if url in seen:
                continue
            seen.add(url)
            results.append({
                "poste": clean(re.sub(r"\s*[-|–—]\s*(LinkedIn|Indeed|Glassdoor).*$", "", title, flags=re.I)),
                "lien_offre": url,
                "job_snippet": snippet,
                "source": "Web / job index",
            })
        if len(results) >= 5:
            break
        time.sleep(random.uniform(.15,.4))
    return results[:5]


def discover_remote_jobs_global():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    found = []
    seen = set()
    queries = list(REMOTE_QUERIES)
    for term in ROLE_TERMS:
        queries.extend([
            f'"{term}" remote Morocco travel',
            f'"{term}" remote Morocco hospitality',
            f'"{term}" "Morocco" "remote" travel',
            f'"{term}" "Morocco" "remote" hospitality',
        ])
    for query in queries:
        for domain in ["linkedin.com/jobs/view", "greenhouse.io", "lever.co", "workable.com", "smartrecruiters.com", "ashbyhq.com", "indeed.com"]:
            q = f'site:{domain} {query}'
            try:
                rows = search_bing(session, q, 10)
            except requests.RequestException:
                rows = []
            for url, title, snippet in rows:
                low = (title + " " + snippet + " " + url).lower()
                if not any(x in low for x in ["remote","morocco","maroc","mena","emea","worldwide","anywhere"]):
                    continue
                if not any(term in low for term in ROLE_TERMS):
                    continue
                key = url.split("#",1)[0]
                if key in seen:
                    continue
                seen.add(key)
                found.append({
                    "poste": clean(re.sub(r"\\s*[-|–—]\\s*(LinkedIn|Indeed|Glassdoor).*$", "", title, flags=re.I)),
                    "lien_offre": key,
                    "job_snippet": snippet,
                    "source": "Remote job index",
                    "remote_evidence": clean(snippet),
                })
                if len(found) >= 300:
                    return found
            time.sleep(random.uniform(.1,.3))
    return found


def infer_company_from_job(job):
    title = job.get("poste","")
    snippet = job.get("job_snippet","")
    for pattern in [
        r"\\bat\\s+([A-Z][A-Za-z0-9&. -]{2,80})$",
        r"\\bchez\\s+([A-Z][A-Za-z0-9&. -]{2,80})$",
        r"^(.+?)\\s+[-|–—]\\s+([A-Z][A-Za-z0-9&. ]{2,80})$",
        r"^(.+?)\\s+at\\s+([A-Z][A-Za-z0-9&. ]{2,80})$",
    ]:
        m = re.search(pattern, title, flags=re.I)
        if m:
            return clean(m.group(1 if len(m.groups()) == 1 else len(m.groups())))
    m = re.search(r"(?:at|chez)\\s+([A-Z][A-Za-z0-9&. -]{2,80})", snippet)
    if m:
        return clean(m.group(1))
    return ""


def enrich_global_job(job):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    company = infer_company_from_job(job)
    url = job.get("lien_offre","")
    site = ""
    email = ""
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.ok:
            soup = BeautifulSoup(r.text, "html.parser")
            text = clean(soup.get_text(" ", strip=True))
            title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
            meta = clean((soup.find("meta", attrs={"property":"og:title"}) or {}).get("content","") if soup.find("meta", attrs={"property":"og:title"}) else "")
            combined = " ".join([title, meta, text[:12000]])
            if not company:
                company = infer_company_from_job({"poste":title, "job_snippet":combined})
            email = extract_email(r.text)
            h = host(url)
            if h:
                site = "https://" + h + "/"
    except requests.RequestException:
        pass
    return company, site, email


def fit_score(company, job=None):
    text = clean(" ".join(str((job or {}).get(k, "")) for k in ["poste","job_snippet"]) + " " + company).lower()
    score = 0
    rules = [
        (r"customer success|account manager|account management|customer support|customer experience",24),
        (r"travel|travel-tech|hospitality|hotel|tourism|ota|booking|dmc|travel agent|tour operator",22),
        (r"b2b|sales|business development|commercial|partnership|supplier",18),
        (r"operations|reservation|booking|onboarding|activation|back office",14),
        (r"french|francais|français",8),(r"arabic|arabe",8),(r"english|anglais",6),
        (r"german|deutsch|allemand",4),(r"remote|morocco|maroc|mena|emea",12)
    ]
    for pattern, weight in rules:
        if re.search(pattern, text):
            score += weight
    return min(100, score)


def make_record(c, site, email, job):
    title = job.get("poste","") if job else ""
    url = job.get("lien_offre","") if job else ""
    identity = company_key(c["company"]) + "|" + url + "|" + c["linkedin_url"]
    rid = hashlib.sha256(identity.encode()).hexdigest()[:24]
    score = fit_score(c["company"], job)
    return {
        "date_detection": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "statut": "NOUVEAU",
        "poste": title,
        "entreprise": c["company"],
        "lieu": "Remote / " + c["discovery_region"],
        "type_remote": "Remote" if url else "Company contact",
        "email": email,
        "site_entreprise": site,
        "source": job.get("source") if job else "LinkedIn industry 31/30 + official site",
        "lien_offre": url,
        "linkedin_url": c["linkedin_url"],
        "id": rid,
        "fit_score": score,
        "fit_reason": "HBX/Hotelbeds 600+ B2B travel accounts; multilingual Arabic/French/English; Customer Success, Sales and Operations",
        "type_poste": "Remote travel/hospitality" if title else "Spontaneous outreach",
        "salaire": "",
        "langues_requises": "Arabic / French / English / German",
        "acces_depuis_maroc": "Remote depuis le Maroc à confirmer" if url else "Contact direct depuis le Maroc",
        "contact_status": "EMAIL_VERIFIE" if email else "OFFRE_A_POSTULER",
    }


def post_batches(records):
    if not WEBHOOK:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL manquant")
    session = requests.Session()
    for i in range(0, len(records), 100):
        chunk = records[i:i+100]
        payload = {"action":"remote_insert","jobs":chunk}
        last = None
        for attempt in range(4):
            try:
                r = session.post(WEBHOOK, json=payload, timeout=180)
                r.raise_for_status()
                print("[SHEET]", r.text[:500])
                last = None
                break
            except requests.RequestException as exc:
                last = exc
                time.sleep(20*(attempt+1))
        if last:
            raise last


def main():
    if not WEBHOOK:
        raise SystemExit("GOOGLE_SHEET_WEBHOOK_URL manquant")
    print("[*] Pipeline séparé: Remote Travel/Hospitality depuis le Maroc")
    print("[*] LinkedIn seed:", SOURCE_LINKEDIN)
    companies = discover_companies()
    print(f"[*] Entreprises uniques découvertes: {len(companies)}")
    global_jobs = discover_remote_jobs_global()
    print(f"[*] Offres remote Maroc/EMEA découvertes directement: {len(global_jobs)}")

    records = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        for c in companies:
            futures[pool.submit(find_company_email, c["company"])] = ("email", c)
            futures[pool.submit(find_remote_jobs, c["company"])] = ("jobs", c)

        completed = 0
        for f in as_completed(futures):
            kind, c = futures[f]
            completed += 1
            try:
                result = f.result()
            except Exception as exc:
                print("[!] enrich error", c["company"], exc)
                continue

            if kind == "email":
                site, email = result
                c["site"] = site
                c["email"] = email
            else:
                c["jobs"] = result

            if completed % 20 == 0:
                print(f"[*] Enrichissement: {completed}/{len(futures)}")

    # Offres trouvées directement, même si l'entreprise n'était pas dans la liste LinkedIn.
    for job in global_jobs:
        company = infer_company_from_job(job)
        if not company:
            continue
        site, email = "", ""
        try:
            company2, site2, email2 = enrich_global_job(job)
            company = company2 or company
            site, email = site2, email2
        except Exception as exc:
            print("[!] job enrichment error", job.get("lien_offre",""), exc)
        if company:
            c0 = {"company": company, "linkedin_url": "", "industry_id": "", "industry": "Hospitality / Travel", "discovery_region": "Remote / Morocco"}
            records.append(make_record(c0, site, email, job))

    for c in companies:
        site = c.get("site","")
        email = c.get("email","")
        jobs = c.get("jobs",[])
        # Règle stricte: on conserve l'entreprise uniquement si email vérifié OU offre remote trouvée.
        if email:
            records.append(make_record(c, site, email, None))
        for job in jobs:
            records.append(make_record(c, site, email, job))

    unique = {}
    for r in records:
        unique[r["id"]] = r
    records = list(unique.values())
    records.sort(key=lambda r: (-int(r["fit_score"]), 0 if r["email"] else 1, r["entreprise"].lower()))

    print(f"[DONE] Leads exploitables: {len(records)} | emails: {sum(bool(r['email']) for r in records)} | offres: {sum(bool(r['lien_offre']) for r in records)}")

    fields = list(records[0].keys()) if records else [
        "date_detection","statut","poste","entreprise","lieu","type_remote","email","site_entreprise",
        "source","lien_offre","linkedin_url","id","fit_score","fit_reason","type_poste","salaire",
        "langues_requises","acces_depuis_maroc","contact_status"
    ]
    with open("remote_travel_hospitality.csv","w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        w.writerows(records)

    if records:
        post_batches(records)

if __name__ == "__main__":
    main()
