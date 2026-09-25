import ausbildung_scraper as scraper

BATCH_SIZE = 100


def scrape_and_upload_in_batches(links):
    # The scraper itself owns the 4h30 time budget and stops parsing cleanly
    # when that budget is reached.
    jobs = scraper.scrape_details(links)
    jobs = scraper.prioritize_jobs(jobs)

    if not jobs:
        raise RuntimeError("Aucune offre ciblée après filtrage.")

    # Upload offers as soon as the scrape phase finishes. The email already
    # present on the original offer is kept; no deep-search is done here.
    # Missing-email enrichment is handled by the separate workflow, grouped
    # once per company.
    for start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[start:start + BATCH_SIZE]
        print(f"[*] BATCH {start // BATCH_SIZE + 1}: envoi de {len(batch)} offres vers Google Sheets")
        scraper.send_to_sheet(batch)
        print(f"[OK] BATCH envoyé: {len(batch)} offres")

    email_count = sum(1 for job in jobs if job.get("emails_rh"))
    print(f"[OK] Offres ciblées dans Google Sheets: {len(jobs)}")
    print(f"[OK] Emails déjà présents sur les offres: {email_count}")
    return jobs


def main():
    print("=" * 72)
    print("AUSBILDUNG — 6 TARGET-AUSBILDUNGEN / TIME-BUDGET MULTI-SOURCE SCRAPER")
    print(f"UPLOAD BATCH SIZE: {BATCH_SIZE}")
    print(f"SCRAPE BUDGET: ~{scraper.SCRAPE_TIME_BUDGET_SECONDS / 3600:.1f} h")
    print("=" * 72)

    scraper.start_scrape_clock()
    links = scraper.collect_links()
    jobs = scrape_and_upload_in_batches(links)

    print("[OK] Les offres sans email sont laissées au workflow Email Enrichment.")
    print("[OK] Ce workflow ne recherche jamais deux fois la même entreprise pour chaque offre.")
    print("[OK] Run terminé.")


if __name__ == "__main__":
    main()
