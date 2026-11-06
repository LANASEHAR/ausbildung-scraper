import ausbildung_scraper as scraper

# Operational batch size, NOT a result cap. The run keeps discovering offers
# until the shared time budget expires.
DISCOVERY_BATCH_SIZE = 100


def process_batch(links, batch_number):
    if not links:
        return 0

    jobs = scraper.scrape_details(links)
    jobs = scraper.prioritize_jobs(jobs)
    jobs = scraper.enrich_missing_emails(jobs)

    if not jobs:
        print(f"[-] BATCH {batch_number}: aucun poste ciblé après filtrage.")
        return 0

    print(
        f"[*] BATCH {batch_number}: {len(jobs)} offres ciblées "
        f"→ envoi immédiat vers Google Sheets"
    )
    result = scraper.send_to_sheet(jobs)
    if not isinstance(result, dict) or result.get("status") != "success":
        raise RuntimeError(
            f"BATCH {batch_number}: Google Sheets n'a pas confirmé l'enregistrement: {result}"
        )

    added = int(result.get("added", 0) or 0)
    duplicate_ids = int(result.get("duplicate_ids", 0) or 0)
    email_count = sum(1 for job in jobs if job.get("emails_rh"))
    print(
        f"[OK] BATCH {batch_number}: {added} nouvelles offres réellement ajoutées "
        f"| {duplicate_ids} IDs déjà présents ignorés | "
        f"{email_count} offres avec email"
    )
    return added


def main():
    print("=" * 72)
    print("AUSBILDUNG — INCREMENTAL MULTI-SOURCE SCRAPER")
    print("6 Ausbildung cibles | régions prioritaires | newest-first metadata")
    print(
        f"Budget scraping: ~{scraper.SCRAPE_TIME_BUDGET_SECONDS / 3600:.1f} h "
        "(uploads continus)"
    )
    print("=" * 72)

    # Revisit older rows with no contact email before discovering new offers.
    # This is bounded and rotates through the Sheet so older rows are not starved.
    historical_emails = scraper.enrich_existing_missing_emails(limit=100)
    print(f"[*] Emails historiques récupérés: {historical_emails}")

    scraper.start_scrape_clock()

    batch = []
    batch_number = 0
    total_saved = 0
    total_discovered = 0

    try:
        for link in scraper.iter_collected_links():
            if scraper.scrape_time_exhausted():
                break

            batch.append(link)
            total_discovered += 1

            if len(batch) >= DISCOVERY_BATCH_SIZE:
                batch_number += 1
                total_saved += process_batch(batch, batch_number)
                batch = []

        # Always flush the final partial batch, including when the discovery
        # deadline is reached. The deadline stops discovering new links; it must
        # not silently discard links already collected in memory. The 30-minute
        # runner safety margin is reserved for processing and uploading this batch.
        if batch:
            batch_number += 1
            print(
                f"[*] Flush final du batch {batch_number}: {len(batch)} liens "
                f"(temps restant: {scraper.scrape_time_remaining() / 60:.1f} min)"
            )
            total_saved += process_batch(batch, batch_number)
            batch = []

    except KeyboardInterrupt:
        print("[!] Interruption reçue — flush du dernier batch avant sortie.")
        if batch:
            batch_number += 1
            total_saved += process_batch(batch, batch_number)

    print("=" * 72)
    print(
        f"[OK] Découvertes uniques traitées: {total_discovered} | "
        f"offres sauvegardées: {total_saved} | batches: {batch_number}"
    )
    if scraper.scrape_time_exhausted():
        print("[OK] Budget temps atteint: arrêt propre après conservation des batches déjà envoyés.")
    else:
        print("[OK] Toutes les sources disponibles ont été parcourues.")
    print("[OK] Recherche des emails intégrée au même run du scraper.")
    print("[OK] Run terminé.")


if __name__ == "__main__":
    main()
