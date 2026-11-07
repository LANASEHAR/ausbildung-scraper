import json
import os
from datetime import datetime, timezone

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
    try:
        result = scraper.send_to_sheet(jobs)
        if not isinstance(result, dict) or result.get("status") != "success":
            raise RuntimeError(
                f"Google Sheets n'a pas confirmé l'enregistrement: {result}"
            )
    except Exception as exc:
        # Never discard offers when the Apps Script deployment/webhook is down.
        # The workflow uploads this JSONL file as a recovery artifact.
        os.makedirs("recovery", exist_ok=True)
        recovery_path = "recovery/unsent_batches.jsonl"
        record = {
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "batch_number": batch_number,
            "count": len(jobs),
            "reason": str(exc),
            "jobs": jobs,
        }
        with open(recovery_path, "a", encoding="utf-8") as recovery_file:
            recovery_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(
            f"[RECOVERY] BATCH {batch_number}: {len(jobs)} offres conservées localement "
            f"dans {recovery_path}; Google Sheets NON confirmé. Cause: {exc}"
        )
        return -1

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

    scraper.start_scrape_clock()

    # Historical enrichment is optional: a broken/stale Apps Script deployment
    # must not prevent discovery of new Ausbildung offers.
    try:
        historical_emails = scraper.enrich_existing_missing_emails(limit=100)
        print(f"[*] Emails historiques récupérés: {historical_emails}")
    except Exception as exc:
        print(
            "[WARN] Enrichissement historique ignoré — le scraping continue. "
            f"Webhook Google Sheets à corriger: {exc}"
        )

    batch = []
    batch_number = 0
    total_saved = 0
    total_discovered = 0
    total_recovery_batches = 0

    try:
        for link in scraper.iter_collected_links():
            if scraper.scrape_time_exhausted():
                break

            batch.append(link)
            total_discovered += 1

            if len(batch) >= DISCOVERY_BATCH_SIZE:
                batch_number += 1
                saved = process_batch(batch, batch_number)
                if saved < 0:
                    total_recovery_batches += 1
                else:
                    total_saved += saved
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
            saved = process_batch(batch, batch_number)
            if saved < 0:
                total_recovery_batches += 1
            else:
                total_saved += saved
            batch = []

    except KeyboardInterrupt:
        print("[!] Interruption reçue — flush du dernier batch avant sortie.")
        if batch:
            batch_number += 1
            saved = process_batch(batch, batch_number)
            if saved < 0:
                total_recovery_batches += 1
            else:
                total_saved += saved

    print("=" * 72)
    print(
        f"[OK] Découvertes uniques traitées: {total_discovered} | "
        f"offres confirmées dans Sheets: {total_saved} | batches: {batch_number} "
        f"| batches en récupération locale: {total_recovery_batches}"
    )
    if scraper.scrape_time_exhausted():
        print("[OK] Budget temps atteint: arrêt propre après conservation des batches déjà envoyés.")
    else:
        print("[OK] Toutes les sources disponibles ont été parcourues.")
    print("[OK] Recherche des emails intégrée au même run du scraper.")
    if total_recovery_batches:
        raise RuntimeError(
            f"{total_recovery_batches} batch(s) non confirmés par Google Sheets. "
            "Les données sont conservées dans l'artifact de récupération; "
            "corriger le déploiement Apps Script avant de les réimporter."
        )
    print("[OK] Run terminé.")


if __name__ == "__main__":
    main()
