from concurrent.futures import ThreadPoolExecutor, as_completed

import ausbildung_scraper as scraper

BATCH_SIZE = 100
DETAIL_WORKERS = scraper.DETAIL_WORKERS


def scrape_and_upload_in_batches(links):
    jobs = []
    batch = []

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        futures = {executor.submit(scraper.parse_detail, link): link for link in links}
        for n, future in enumerate(as_completed(futures), start=1):
            try:
                job = future.result()
            except Exception as exc:
                print(f"[!] détail erreur: {exc}")
                continue

            if not job:
                continue

            jobs.append(job)
            batch.append(job)

            if len(batch) >= BATCH_SIZE:
                print(f"[*] BATCH {BATCH_SIZE}: envoi immédiat vers Google Sheets | total offres: {len(jobs)}")
                scraper.send_to_sheet(batch)
                print(f"[OK] BATCH envoyé: {len(batch)} offres")
                batch = []

            if n % 50 == 0:
                email_count = sum(1 for x in jobs if x.get("emails_rh"))
                print(f"[*] détails traités: {n}/{len(links)} | offres: {len(jobs)} | avec email BA: {email_count}")

    if batch:
        print(f"[*] DERNIER BATCH: envoi immédiat de {len(batch)} offres vers Google Sheets")
        scraper.send_to_sheet(batch)
        print(f"[OK] DERNIER BATCH envoyé: {len(batch)} offres")

    jobs = list({job["id"]: job for job in jobs}.values())
    print(f"[OK] Toutes les offres sont maintenant dans Google Sheets: {len(jobs)} offres")
    return jobs


def main():
    print("=" * 72)
    print("AUSBILDUNG KAUFMANN/Kauffrau — DAILY BATCH SCRAPER")
    print(f"UPLOAD BATCH SIZE: {BATCH_SIZE}")
    print("=" * 72)

    links = scraper.collect_links()
    jobs = scrape_and_upload_in_batches(links)

    # Deep search only after all offers have already been saved.
    jobs = scraper.enrich_missing_emails(jobs)

    # Update emails and official sites in batches as well.
    updates = [
        {
            "id": j["id"],
            "emails_rh": j.get("emails_rh", ""),
            "site_entreprise": j.get("site_entreprise", ""),
        }
        for j in jobs
        if j.get("emails_rh") or j.get("site_entreprise")
    ]

    for start in range(0, len(updates), BATCH_SIZE):
        chunk = updates[start:start + BATCH_SIZE]
        print(f"[*] UPDATE BATCH: {start + 1}-{start + len(chunk)} / {len(updates)}")
        scraper.post_json({"action": "update", "jobs": chunk}, "Mise à jour emails/sites Google Sheets")

    email_count = sum(1 for job in jobs if job.get("emails_rh"))
    site_count = sum(1 for job in jobs if job.get("site_entreprise"))
    if len(jobs) < scraper.TARGET_OFFERS:
        print(f"[!] Objectif {scraper.TARGET_OFFERS} offres non atteint: {len(jobs)} offres.")
    else:
        print(f"[OK] Objectif offres atteint: {len(jobs)}")
    print(f"[OK] Sites officiels vérifiés: {site_count}")
    print(f"[OK] Emails publics vérifiés: {email_count}")
    print("[OK] Run terminé.")


if __name__ == "__main__":
    main()
