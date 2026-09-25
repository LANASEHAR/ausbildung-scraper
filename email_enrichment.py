import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import ausbildung_scraper as scraper

WEBHOOK_TIMEOUT = 180
WEBHOOK_RETRIES = 4
WEBHOOK_RETRY_DELAYS = (20, 40, 80)
EXPORT_PAGE_SIZE = 500
ENRICH_WORKERS = 10
SEARCH_ROUNDS = 3
SEARCH_RETRY_DELAY = (1.0, 2.5)

# The goal is contact enrichment, not re-collecting jobs.
# Every run reads rows already in Sheets that have no usable email,
# deduplicates them by company, searches the official company site,
# and writes verified public emails back to all matching rows.
MAX_COMPANIES_PER_RUN = 1000


def webhook_url():
    url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not url:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL manquant")
    return url


def post_json(payload):
    session = scraper.make_session()
    last_error = None

    for attempt in range(1, WEBHOOK_RETRIES + 1):
        try:
            r = session.post(
                webhook_url(),
                json=payload,
                timeout=(20, WEBHOOK_TIMEOUT),
                allow_redirects=True,
            )
            r.raise_for_status()
            result = r.json()
            if isinstance(result, dict) and result.get("status") == "error":
                raise RuntimeError(result.get("message", "Apps Script error"))
            return result
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            print(f"[!] Webhook tentative {attempt}/{WEBHOOK_RETRIES}: {exc}")
            if attempt < WEBHOOK_RETRIES:
                time.sleep(WEBHOOK_RETRY_DELAYS[min(attempt - 1, len(WEBHOOK_RETRY_DELAYS) - 1)])

    raise RuntimeError(f"Webhook impossible après {WEBHOOK_RETRIES} tentatives: {last_error}")


def export_missing_rows():
    rows = []
    offset = 0

    while True:
        result = post_json({
            "action": "export_missing_emails",
            "offset": offset,
            "limit": EXPORT_PAGE_SIZE,
        })
        batch = result.get("rows", []) if isinstance(result, dict) else []
        print(
            f"[*] Sheet: page missing-email offset={offset} "
            f"→ {len(batch)} lignes"
        )
        rows.extend(batch)

        if len(batch) < EXPORT_PAGE_SIZE:
            break
        offset += len(batch)

    print(f"[OK] {len(rows)} lignes du Sheet sans email récupérées.")
    return rows


def normalize_company(company):
    return scraper.normalize_company(company)


def exhaustive_company_search(company, location=""):
    """
    Search public web results only to discover the official company domain.
    The email is accepted only after opening and verifying the official site.
    Several query rounds and both search engines are used before giving up.
    """
    company = scraper.clean(company)
    if not company or company.lower() in {"à vérifier", "entreprise non indiquée"}:
        return "", ""

    queries = [
        f'"{company}" official website',
        f'"{company}" Kontakt Impressum',
        f'"{company}" Karriere Ausbildung Kontakt',
        f'"{company}" Bewerbung E-Mail',
        f'"{company}" Ansprechpartner E-Mail',
    ]

    candidates = []
    seen = set()
    session = scraper.make_session()

    for round_no in range(1, SEARCH_ROUNDS + 1):
        for query in queries:
            for engine in ("bing", "ddg"):
                try:
                    results = (
                        scraper.search_engine_bing(session, query)
                        if engine == "bing"
                        else scraper.search_engine_duckduckgo(session, query)
                    )
                except requests.RequestException as exc:
                    print(f"[!] {engine} search failed for {company}: {exc}")
                    continue

                for href, title, snippet in results[:15]:
                    score = scraper.candidate_score(href, title, snippet, company)
                    if score <= 0:
                        continue
                    key = (scraper.host_of(href), href)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append((score, href))

                time.sleep(random.uniform(0.15, 0.35))

        # Open every strong candidate, not just the first result.
        for _, href in sorted(candidates, key=lambda x: x[0], reverse=True):
            host = scraper.host_of(href)
            if not host:
                continue

            try:
                site, homepage_email = scraper.verify_official_site(href, company)
            except Exception:
                site, homepage_email = "", ""

            if not site:
                continue

            email = homepage_email or scraper.crawl_verified_site(site)
            if email:
                return email, site

        if round_no < SEARCH_ROUNDS:
            time.sleep(random.uniform(*SEARCH_RETRY_DELAY))

    return "", ""


def enrich_group(group):
    company = group["company"]
    location = group.get("location", "")
    try:
        email, site = exhaustive_company_search(company, location)
        return group, email, site, ""
    except Exception as exc:
        return group, "", "", str(exc)


def main():
    print("=" * 72)
    print("AUSBILDUNG — SHEET EMAIL ENRICHMENT WORKFLOW")
    print("OBJECTIF: récupérer les contacts publics manquants depuis les sites officiels")
    print("=" * 72)

    rows = export_missing_rows()
    if not rows:
        print("[OK] Aucun contact manquant dans le Sheet.")
        return

    # Group every missing-email row by company so one company is searched once,
    # while every matching Sheet row can receive the verified contact.
    groups = {}
    for row in rows:
        company = scraper.clean(row.get("entreprise", ""))
        if not company:
            continue

        key = normalize_company(company)
        if not key:
            continue

        if key not in groups:
            groups[key] = {
                "company": company,
                "location": scraper.clean(row.get("lieu", "")),
                "rows": [],
            }
        groups[key]["rows"].append(row)

    groups = list(groups.values())[:MAX_COMPANIES_PER_RUN]
    print(f"[*] {len(groups)} entreprises UNIQUES à enrichir.")

    updates = []
    sites_found = 0
    emails_found = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as executor:
        futures = [executor.submit(enrich_group, group) for group in groups]

        for n, future in enumerate(as_completed(futures), start=1):
            group, email, site, error = future.result()

            if error:
                failed += 1
                print(f"[!] {n}/{len(groups)} {group['company']}: {error}")
                continue

            if site:
                sites_found += 1

            if email:
                emails_found += 1
                for row in group["rows"]:
                    updates.append({
                        "id": row["id"],
                        "emails_rh": email,
                        "site_entreprise": site,
                    })
                print(
                    f"[+] EMAIL {emails_found}: {email} | "
                    f"{group['company']} | {site} | lignes: {len(group['rows'])}"
                )
            else:
                print(f"[-] Aucun email vérifié trouvé: {group['company']}")

            if n % 25 == 0 or n == len(groups):
                print(
                    f"[*] Progression {n}/{len(groups)} | "
                    f"sites={sites_found} | emails={emails_found} | "
                    f"échecs={failed}"
                )

    # Apps Script already deduplicates IDs and emails safely.
    # Send in small chunks to avoid long-held spreadsheet locks.
    chunk_size = 100
    updated_total = 0

    for start in range(0, len(updates), chunk_size):
        chunk = updates[start:start + chunk_size]
        result = post_json({"action": "update", "jobs": chunk})
        updated = int(result.get("updated", 0)) if isinstance(result, dict) else 0
        updated_total += updated
        print(
            f"[OK] UPDATE {start + 1}-{start + len(chunk)} / {len(updates)} "
            f"→ {updated} ligne(s) enrichie(s)"
        )

    print("=" * 72)
    print(f"[OK] Entreprises recherchées : {len(groups)}")
    print(f"[OK] Sites officiels vérifiés : {sites_found}")
    print(f"[OK] Entreprises avec email trouvé : {emails_found}")
    print(f"[OK] Lignes réellement enrichies : {updated_total}")
    print(f"[OK] Échecs de recherche : {failed}")
    print("=" * 72)


if __name__ == "__main__":
    main()
