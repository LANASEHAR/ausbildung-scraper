import json
import os
from datetime import datetime, timezone

import ausbildung_scraper as scraper

# Operational batch size, NOT a result cap. The run keeps discovering offers
# until the shared time budget expires.
DISCOVERY_BATCH_SIZE = 100
RECOVERY_PATH = "recovery/unsent_batches.jsonl"


def _save_recovery_batch(batch_number, jobs, reason):
    os.makedirs(os.path.dirname(RECOVERY_PATH), exist_ok=True)
    record = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "batch_number": batch_number,
        "count": len(jobs),
        "reason": str(reason),
        "jobs": jobs,
    }
    with open(RECOVERY_PATH, "a", encoding="utf-8") as recovery_file:
        recovery_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(
        f"[RECOVERY] BATCH {batch_number}: {len(jobs)} offres conservées localement "
        f"dans {RECOVERY_PATH}; Google Sheets NON confirmé. Cause: {reason}"
    )


def _retry_recovery_batches():
    """Retry any batches saved locally due to transient Apps Script lock timeouts."""
    if not os.path.exists(RECOVERY_PATH):
        return 0, 0

    with open(RECOVERY_PATH, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    if not records:
        return 0, 0

    print(f"[*] Reprise finale de {len(records)} batch(s) en attente dans {RECOVERY_PATH}...")
    remaining = []
    recovered_added = 0

    for record in records:
        batch_no = record.get("batch_number", "?")
        jobs = record.get("jobs") or []
        if not jobs:
            continue
        try:
            result = scraper.send_to_sheet(jobs)
            if not isinstance(result, dict) or result.get("status") != "success":
                raise RuntimeError(f"Réponse inattendue: {result}")
            added = int(result.get("added", 0) or 0)
            recovered_added += added
            print(f"[OK] Reprise BATCH {batch_no}: {added} nouvelles offres confirmées.")
        except Exception as exc:
            record["reason"] = str(exc)
            remaining.append(record)
            print(f"[!] Reprise BATCH {batch_no} toujours en échec: {exc}")

    if remaining:
        with open(RECOVERY_PATH, "w", encoding="utf-8") as f:
            for rec in remaining:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    else:
        os.remove(RECOVERY_PATH)

    return recovered_added, len(remaining)


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
        _save_recovery_batch(batch_number, jobs, exc)
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

    if total_recovery_batches:
        recovered_added, remaining_batches = _retry_recovery_batches()
        total_saved += recovered_added
        total_recovery_batches = remaining_batches

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
