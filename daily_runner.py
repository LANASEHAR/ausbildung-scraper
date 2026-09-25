import ausbildung_scraper as scraper

# Operational batch size, NOT a result cap. The run keeps discovering offers
# until the shared time budget expires.
DISCOVERY_BATCH_SIZE = 100


def process_batch(links, batch_number):
    if not links:
        return 0

    jobs = scraper.scrape_details(links)
    jobs = scraper.prioritize_jobs(jobs)

    if not jobs:
        print(f"[-] BATCH {batch_number}: aucun poste ciblé après filtrage.")
        return 0

    print(
        f"[*] BATCH {batch_number}: {len(jobs)} offres ciblées "
        f"→ envoi immédiat vers Google Sheets"
    )
    scraper.send_to_sheet(jobs)

    email_count = sum(1 for job in jobs if job.get("emails_rh"))
    print(
        f"[OK] BATCH {batch_number}: {len(jobs)} offres sauvegardées | "
        f"{email_count} avec email déjà présent"
    )
    return len(jobs)


def main():
    print("=" * 72)
    print("AUSBILDUNG — INCREMENTAL MULTI-SOURCE SCRAPER")
    print("6 Ausbildung cibles | régions prioritaires | newest-first metadata")
    print(
        f"Budget scraping: ~{scraper.SCRAPE_TIME_BUDGET_SECONDS / 3600:.1f} h "
        "(uploads continus)"
    )
    print("=" * 72)

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

        # Always flush the final partial batch.
        if batch and not scraper.scrape_time_exhausted():
            batch_number += 1
            total_saved += process_batch(batch, batch_number)

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
    print("[OK] Les offres sans email restent dans le Sheet pour Email Enrichment.")
    print("[OK] Run terminé sans deep-search doublonné.")


if __name__ == "__main__":
    main()
