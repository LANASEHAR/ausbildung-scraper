import csv
import re
import time
import random
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

SEED_URL = "https://www.linkedin.com/search/results/companies/?origin=FACETED_SEARCH&industryCompanyVertical=%5B%2231%22%2C%2230%22%5D"
INDUSTRIES = {"31": "Hospitality", "30": "Travel Arrangements"}
REGIONS = [
    "Germany",
    "Baden-Württemberg", "Bavaria", "North Rhine-Westphalia", "Lower Saxony",
    "Saxony", "Thuringia", "Saxony-Anhalt", "Mecklenburg-Vorpommern",
    "Rhineland-Palatinate", "Hesse", "Hamburg", "Berlin", "Schleswig-Holstein",
]
MAX_PAGES = 5
MAX_COMPANIES = 1200
DELAY = (0.8, 1.5)
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/154 Safari/537.36"
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def linkedin_company_url(url):
    url = unquote(url or "")
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/url?"):
        m = re.search(r"[?&](?:q|url)=([^&]+)", url)
        if m:
            url = unquote(m.group(1))
    if "linkedin.com/company/" not in url.lower():
        return ""
    p = urlparse(url)
    path = p.path.rstrip("/")
    if not path.lower().startswith("/company/"):
        return ""
    return f"https://www.linkedin.com{path}/"


def search_bing(session, query, page):
    first = (page - 1) * 10 + 1
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count=10&first={first}&setlang=en-US"
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for item in soup.select("li.b_algo"):
        a = item.select_one("h2 a[href]")
        if not a:
            continue
        href = linkedin_company_url(a.get("href"))
        if not href:
            continue
        snippet = item.select_one(".b_caption p")
        rows.append({
            "linkedin_url": href,
            "title": clean(a.get_text(" ", strip=True)),
            "snippet": clean(snippet.get_text(" ", strip=True)) if snippet else "",
        })
    return rows


def search_ddg(session, query):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    r = session.get(url, timeout=10, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for item in soup.select(".result"):
        a = item.select_one(".result__a[href]")
        if not a:
            continue
        href = linkedin_company_url(a.get("href"))
        if not href:
            continue
        snippet = item.select_one(".result__snippet")
        rows.append({
            "linkedin_url": href,
            "title": clean(a.get_text(" ", strip=True)),
            "snippet": clean(snippet.get_text(" ", strip=True)) if snippet else "",
        })
    return rows


def parse_result(row, industry_id, industry, region, query):
    text = clean(f"{row['title']} {row['snippet']}")
    company = row["title"]
    company = re.sub(r"\s*\|\s*LinkedIn.*$", "", company, flags=re.I)
    company = re.sub(r"\s*[-–—]\s*LinkedIn.*$", "", company, flags=re.I)
    company = clean(company)

    size = ""
    m = re.search(r"Company size\s+([0-9,–+ -]+employees?)", text, re.I)
    if m:
        size = clean(m.group(1))

    headquarters = ""
    m = re.search(r"Headquarters\s+(.+?)(?:\s+Type\s+|\s+Founded\s+|\s+Specialties\s+|$)", text, re.I)
    if m:
        headquarters = clean(m.group(1))

    website = ""
    m = re.search(r"Website\s+(https?://\S+|www\.\S+)", text, re.I)
    if m:
        website = m.group(1).rstrip(".,)")

    emails = sorted(set(EMAIL_RE.findall(text)))

    return {
        "industry_id": industry_id,
        "industry": industry,
        "company": company,
        "linkedin_url": row["linkedin_url"],
        "headquarters": headquarters,
        "company_size": size,
        "website": website,
        "public_email_from_index": ";".join(emails),
        "search_region": region,
        "search_query": query,
        "source": SEED_URL,
        "found_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    records = {}

    queries = []
    for industry_id, industry in INDUSTRIES.items():
        for region in REGIONS:
            queries.append((industry_id, industry, region, f'site:linkedin.com/company/ "{industry}" "{region}"'))
            queries.append((industry_id, industry, region, f'site:linkedin.com/company/ "{industry}" "{region}" Germany'))

    print(f"[*] Seed LinkedIn industries: {INDUSTRIES}")
    print(f"[*] Public-index strategy: {len(queries)} queries × up to {MAX_PAGES} pages")
    print("[*] Direct LinkedIn search pages are not assumed to be anonymously accessible; no login/cookie bypass is used.")

    for qi, (industry_id, industry, region, query) in enumerate(queries, 1):
        print(f"[*] Query {qi}/{len(queries)}: {query}")
        got = 0
        for page in range(1, MAX_PAGES + 1):
            try:
                rows = search_bing(session, query, page)
            except requests.RequestException as exc:
                print(f"[!] Bing error page {page}: {exc}")
                rows = []
            if not rows and page == 1:
                try:
                    rows = search_ddg(session, query)
                    print(f"[*] DDG fallback: {len(rows)}")
                except requests.RequestException as exc:
                    print(f"[!] DDG error: {exc}")
            if not rows:
                break
            for row in rows:
                key = row["linkedin_url"].lower()
                if key not in records:
                    records[key] = parse_result(row, industry_id, industry, region, query)
                    got += 1
                if len(records) >= MAX_COMPANIES:
                    break
            if len(records) >= MAX_COMPANIES:
                break
            time.sleep(random.uniform(*DELAY))
        print(f"[+] New companies from query: {got} | total unique: {len(records)}")
        time.sleep(random.uniform(*DELAY))
        if len(records) >= MAX_COMPANIES:
            break

    rows = list(records.values())
    rows.sort(key=lambda x: (x["industry"], x["company"].lower()))
    output = "linkedin_hospitality_travel_companies.csv"
    fields = [
        "industry_id", "industry", "company", "linkedin_url", "headquarters",
        "company_size", "website", "public_email_from_index", "search_region",
        "search_query", "source", "found_at_utc",
    ]
    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"[DONE] {len(rows)} unique LinkedIn company pages written to {output}")
    print(f"[DONE] Hospitality: {sum(r['industry_id']=='31' for r in rows)} | Travel Arrangements: {sum(r['industry_id']=='30' for r in rows)}")


if __name__ == "__main__":
    main()
