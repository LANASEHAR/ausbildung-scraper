import base64
import os
import re
import smtplib
import ssl
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

CONFIG = {
    "NOM": "Halima Essaouaf",
    "EMAIL": os.getenv("GMAIL_USER", "essaouafhalima@gmail.com"),
    "APP_PASSWORD": os.getenv("GMAIL_APP_PASSWORD", ""),
    "WEBHOOK_URL": os.getenv("GOOGLE_SHEET_WEBHOOK_URL", ""),
    "TEL": "+212619968131",
    "LINKEDIN": "linkedin.com/in/halima-essaouaf-1b4b81202",
    # Light batch per run. The persistent rolling 24h quota is enforced by Apps Script.
    "BATCH_LIMIT": int(os.getenv("BATCH_LIMIT", "20")),
    "ROLLING_24H_LIMIT": int(os.getenv("ROLLING_24H_LIMIT", "100")),
    "DELAI_ENTRE_EMAILS_SEC": 4.0,
    "FETCH_PAGE_SIZE": 500,
    "MAX_FETCH_PAGES": 10,
    "ALLOW_RETRIES_TO_REACH_TARGET": True,
}

TITRES_AUSBILDUNG = {
    "fachverkaeufer_lebensmittel": "Fachverkäufer/in im Lebensmittelhandwerk",
    "einzelhandel": "Kauffrau im Einzelhandel",
    "koch": "Koch/Köchin",
    "hotelfachfrau": "Hotelfachmann/-frau",
    "hotelmanagement": "Kauffrau für Hotelmanagement",
    "baecker": "Bäcker/in",
    "systemgastronomie": "Fachfrau/Fachmann für Systemgastronomie",
    "spedition": "Kauffrau für Spedition und Logistikdienstleistung",
    "handel": "Kauffrau im Groß- und Außenhandelsmanagement",
    "industrie": "Industriekaufmann/-frau",
    "buero": "Kaufmann/-frau für Büromanagement",
    "tourismus": "Kauffrau für Tourismus und Freizeit",
  }

MOTIVATIONS = {
    "fachverkaeufer_lebensmittel": (
        "Die Beratung und der Verkauf von Lebensmitteln verbinden Kundenkontakt, Service und sorgfältiges Arbeiten. "
        "Ich bringe über fünf Jahre Erfahrung in Kundenbetreuung und Vertrieb mit und möchte diese Stärke nun "
        "gezielt im Lebensmittelhandwerk einsetzen und mit einer anerkannten Ausbildung in Deutschland verbinden."
    ),
    "einzelhandel": (
        "Kundenberatung und Verkauf begleiten mich seit mehreren Jahren. In über fünf Jahren Berufserfahrung "
        "habe ich im B2B- und B2C-Vertrieb sowie im Kundenservice gearbeitet und wurde aufgrund meiner "
        "Beratungsqualität und Abschlussstärke als Top-Verkäuferin ausgezeichnet. Diese Erfahrung möchte ich "
        "nun gezielt mit einer deutschen Ausbildung und einem anerkannten IHK-Abschluss verbinden."
    ),
    "koch": (
        "Die Arbeit mit Menschen, Organisation und Service gehört bereits zu meiner Berufserfahrung. "
        "Ich möchte diese Erfahrung nun in der Küche weiterentwickeln, professionelle Abläufe erlernen "
        "und eine anerkannte Ausbildung als Koch/Köchin in Deutschland absolvieren."
    ),
    "hotelfachfrau": (
        "Die Hotellerie ist mir bereits aus meiner beruflichen Erfahrung vertraut. Bei HBX Group / Hotelbeds "
        "betreute ich ein internationales B2B-Kundenportfolio im Bereich Hotellerie und Travel und arbeitete "
        "täglich mit Geschäftspartnern auf Arabisch, Französisch und Englisch. Diese Erfahrung möchte ich nun "
        "mit einer Ausbildung in Deutschland und einem anerkannten IHK-Abschluss weiterentwickeln."
    ),
    "hotelmanagement": (
        "Die Verbindung von Hotellerie, Kundenorientierung und kaufmännischer Organisation spricht mich besonders an. "
        "Durch meine Erfahrung bei HBX Group / Hotelbeds im internationalen B2B-Umfeld bringe ich bereits einen direkten Bezug zur Hotelbranche mit. "
        "Diese Erfahrung möchte ich nun mit einer fundierten Ausbildung zur Kauffrau für Hotelmanagement und einem anerkannten IHK-Abschluss weiterentwickeln."
    ),
    "baecker": (
        "Sorgfalt, Kundenorientierung und zuverlässiges Arbeiten gehören zu meinen bisherigen beruflichen "
        "Erfahrungen. Die Verbindung von handwerklicher Herstellung und direktem Kundenkontakt im "
        "Bäckerhandwerk spricht mich besonders an. Diese Stärken möchte ich durch eine fundierte Ausbildung "
        "in Deutschland weiterentwickeln."
    ),
    "systemgastronomie": (
        "Auch wenn mein bisheriger beruflicher Weg nicht direkt aus der Gastronomie kommt, bringe ich über "
        "fünf Jahre Erfahrung im Kundenservice, Vertrieb und in strukturierten Arbeitsabläufen mit. "
        "Diese Erfahrung möchte ich nun in die Systemgastronomie einbringen und die professionellen Abläufe "
        "in Deutschland von Grund auf erlernen."
    ),
    "spedition": (
        "Logistik und Koordination sind mir bereits aus meiner Berufserfahrung vertraut. Bei Helpdesk ForYou "
        "koordinierte ich die Einsatzplanung von über 100 Fahrern und Mitarbeitenden. Heute arbeite ich mit "
        "Beschaffung, Logistik, Bestandsüberwachung, Auftragsabwicklung und Lieferanten. Diese Erfahrung "
        "möchte ich nun gezielt durch eine Ausbildung und einen anerkannten IHK-Abschluss erweitern."
    ),
    "handel": (
        "Ich bringe über fünf Jahre Berufserfahrung in kaufmännischen Bereichen, Kundenbetreuung und Vertrieb "
        "mit. In meiner aktuellen Tätigkeit arbeite ich unter anderem mit Beschaffung, Lieferanten, "
        "Bestandsüberwachung und Auftragsabwicklung sowie mit Excel und Sage. Diese Erfahrung möchte ich nun "
        "mit einer fundierten Ausbildung und einem anerkannten IHK-Abschluss in Deutschland verbinden."
    ),
    "industrie": (
        "Durch über fünf Jahre Berufserfahrung bringe ich bereits praktische Kenntnisse in kaufmännischer "
        "Organisation, Beschaffung, Auftragsabwicklung und Kundenbetreuung mit. Aktuell arbeite ich mit "
        "Lieferanten, Beständen, Rechnungen, Excel und dem ERP-System Sage. Diese Praxiserfahrung möchte ich "
        "nun mit den kaufmännischen Prozessen eines deutschen Unternehmens verbinden."
    ),
    "buero": (
        "Ich bringe über fünf Jahre Erfahrung in Kundenbetreuung, Vertrieb und kaufmännischen Abläufen mit. "
        "Organisation, Kommunikation und strukturierte Bearbeitung gehören zu meinem Arbeitsalltag. Diese "
        "Erfahrung möchte ich nun mit einer anerkannten Ausbildung für Büromanagement in Deutschland vertiefen."
    ),
}

DEFAULT_MOTIVATION = (
    "Ich bringe über fünf Jahre Berufserfahrung in kaufmännischen Bereichen, Kundenbetreuung "
    "und strukturierten Arbeitsprozessen mit. Diese Erfahrung möchte ich nun gezielt durch "
    "eine fundierte Ausbildung in Deutschland erweitern."
)


# ─────────────────────────────────────────────────────────────────────────────
# LOGIQUE MÉTIER & TEMPLATES HTML
# ─────────────────────────────────────────────────────────────────────────────

def detecter_specialite(intitule: str, role_cible: str) -> str:
    t = f"{intitule or ''} {role_cible or ''}".lower()
    if any(k in t for k in [
        "fachverkäufer im lebensmittelhandwerk", "fachverkaeufer im lebensmittelhandwerk",
        "fachverkäuferin im lebensmittelhandwerk", "fachverkaeuferin im lebensmittelhandwerk",
        "fachverkäufer bäckerei", "fachverkaeufer baeckerei",
        "fachverkäufer konditorei", "fachverkaeufer konditorei",
        "fachverkäufer fleischerei", "fachverkaeufer fleischerei"
    ]):
        return "fachverkaeufer_lebensmittel"
    if "einzelhandel" in t:
        return "einzelhandel"
    if any(k in t for k in ["koch", "köchin", "koechin", "koch/köchin", "koch/koechin"]):
        return "koch"
    if any(k in t for k in ["kauffrau für hotelmanagement", "kauffrau fuer hotelmanagement", "kaufmann für hotelmanagement", "kaufmann fuer hotelmanagement", "kauffrau/kaufmann für hotelmanagement", "kauffrau/kaufmann fuer hotelmanagement"]):
        return "hotelmanagement"
    if any(k in t for k in ["hotelfach", "hotelkauffrau", "hotelkaufmann"]):
        return "hotelfachfrau"
    if any(k in t for k in ["bäcker", "baecker", "bäckerei", "baeckerei", "konditorei"]):
        return "baecker"
    if "systemgastronomie" in t:
        return "systemgastronomie"
    if any(k in t for k in ["spedition", "logistikdienstleistung", "speditionskauf"]):
        return "spedition"
    if any(k in t for k in ["gross", "groß", "aussenhandel", "außenhandel", "grosshandel", "großhandel"]):
        return "handel"
    if "tourismus und freizeit" in t or ("tourismus" in t and "freizeit" in t):
        return "tourismus"
    if "industriekauf" in t:
        return "industrie"
    if any(k in t for k in ["büromanagement", "bueromanagement", "kaufmann/-frau für büromanagement", "kauffrau/kaufmann für büromanagement"]):
        return "buero"
    return ""


def get_signature_html() -> str:
    return f"""
<br><br>
<table style="font-family: Arial, sans-serif; font-size: 13px; color: #333; border-top: 2px solid #1a73e8; padding-top: 8px;">
  <tr>
    <td>
      <strong style="font-size: 14px; color: #1a1a1a;">{CONFIG['NOM']}</strong><br>
      <span style="color: #666;">Bewerberin – Ausbildung Kauffrau</span><br><br>
      📧 <a href="mailto:{CONFIG['EMAIL']}" style="color: #1a73e8;">{CONFIG['EMAIL']}</a><br>
      📞 {CONFIG['TEL']}<br>
      🔗 <a href="https://{CONFIG['LINKEDIN']}" style="color: #1a73e8;">{CONFIG['LINKEDIN']}</a>
    </td>
  </tr>
</table>""".strip()


def generer_email_depuis_apps_script(item: dict, type_envoi: str):
    """Use Code.gs as the single source of truth for personalized emails."""
    data = _webhook_json(
        "POST",
        json_payload={
            "action": "generate_email",
            "type": type_envoi,
            "entreprise": item.get("entreprise", ""),
            "intitule": item.get("intitule", ""),
            "role_cible": item.get("role_cible", ""),
            "lien": item.get("lien", ""),
        },
        label="generate_email",
    )
    if data.get("status") != "success" or not data.get("html_body"):
        raise RuntimeError(f"generate_email Apps Script invalide: {data}")
    return data["subject"], data["html_body"]


def generer_email_candidature(specialite: str):
    titre_poste = TITRES_AUSBILDUNG.get(specialite, "Ausbildungsplatz")
    motivation = MOTIVATIONS.get(specialite, DEFAULT_MOTIVATION)
    body = f"""
<p>Sehr geehrte Damen und Herren,</p>

<p>mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>{titre_poste}</strong>.</p>

<p>{motivation}</p>

<p>Deutsch B1 habe ich abgeschlossen und bereite mich aktuell auf B2 vor. Meine vollständigen Bewerbungsunterlagen finden Sie im Anhang.</p>

<p>Über die Gelegenheit per Videogespräch vorzustellen, würde ich mich sehr freuen.</p>

<p>Mit freundlichen Grüßen</p>

{get_signature_html()}
""".strip()
    sujet = f"Bewerbung um einen Ausbildungsplatz als {titre_poste} – {CONFIG['NOM']}"
    return sujet, body


def generer_email_relance(specialite: str):
    titre_poste = TITRES_AUSBILDUNG.get(specialite, "Ausbildungsplatz")
    body = f"""
<p>Sehr geehrte Damen und Herren,</p>

<p>vor zwei Tagen habe ich Ihnen meine Bewerbung für einen Ausbildungsplatz als <strong>{titre_poste}</strong> geschickt. Ich wollte mich kurz erkundigen, ob meine Unterlagen gut bei Ihnen angekommen sind.</p>

<p>Ich bin weiterhin sehr an der Ausbildung interessiert und sende Ihnen meinen Lebenslauf vorsichtshalber noch einmal im Anhang.</p>

<p>Falls Sie noch weitere Unterlagen oder Informationen benötigen, lasse ich Ihnen diese gerne zukommen. Für ein kurzes Gespräch stehe ich Ihnen jederzeit gerne zur Verfügung.</p>

<p>Vielen Dank für Ihre Zeit. Ich freue mich auf Ihre Rückmeldung.</p>

<p>Mit freundlichen Grüßen</p>
{get_signature_html()}
""".strip()
    sujet = f"Nachfassaktion – Bewerbung als {titre_poste} – {CONFIG['NOM']}"
    return sujet, body


# ─────────────────────────────────────────────────────────────────────────────
# COMMUNICATION AVEC GOOGLE APPS SCRIPT (SHEET + DRIVE)
# ─────────────────────────────────────────────────────────────────────────────

def _webhook_json(method: str, *, json_payload=None, params=None, label="webhook", attempts=4):
    """
    Appel robuste du Web App Apps Script.
    Les 404/5xx peuvent être transitoires côté Web App; on retente avec backoff.
    """
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            if method == "POST":
                resp = requests.post(
                    CONFIG["WEBHOOK_URL"],
                    json=json_payload,
                    timeout=60,
                )
            else:
                resp = requests.get(
                    CONFIG["WEBHOOK_URL"],
                    params=params,
                    timeout=60,
                )

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (404, 429, 500, 502, 503, 504):
                last_error = RuntimeError(
                    f"{label}: HTTP {resp.status_code}"
                )
                if attempt < attempts:
                    delay = min(5 * (2 ** (attempt - 1)), 30)
                    print(
                        f"⚠️ {label}: HTTP {resp.status_code} "
                        f"— retry {attempt + 1}/{attempts} dans {delay}s"
                    )
                    time.sleep(delay)
                    continue

            resp.raise_for_status()

        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < attempts:
                delay = min(5 * (2 ** (attempt - 1)), 30)
                print(
                    f"⚠️ {label}: {exc} "
                    f"— retry {attempt + 1}/{attempts} dans {delay}s"
                )
                time.sleep(delay)
                continue

    raise RuntimeError(
        f"{label}: échec après {attempts} tentatives: {last_error}"
    )


def recuperer_offres_en_attente(offset=0, allow_retries=True):
    # Le Sheet est la source de vérité. Le sender ne dépend jamais du scraper.
    data = _webhook_json(
        "POST",
        json_payload={
            "action": "get_pending",
            "limit": CONFIG["FETCH_PAGE_SIZE"],
            "offset": offset,
            "allow_retries": bool(allow_retries),
            "retry_min_age_hours": 2,
        },
        label="get_pending",
    )
    if "stats" in data:
        print(f"📊 Diagnostic Sheet ({data.get('sheet_name')}): {data['stats']}")
    print(
        f"📥 Candidatures reçues du Sheet : {data.get('returned', 0)} "
        f"(initiales + relances + retries autorisés)"
    )
    return data.get("items", [])


def recuperer_cv_depuis_drive(specialite: str, cv_cache: dict):
    if specialite in cv_cache:
        return cv_cache[specialite]

    # IMPORTANT: use POST, not GET. This avoids the intermittent Apps Script
    # googleusercontent GET 404 seen when fetching CVs from GitHub Actions.
    data = _webhook_json(
        "POST",
        json_payload={
            "action": "get_cv",
            "specialite": specialite,
        },
        label=f"get_cv/{specialite}",
    )

    if data.get("status") != "success" or not data.get("base64"):
        cv_cache[specialite] = None
        return None

    cv_info = {
        "filename": data["filename"],
        "bytes": base64.b64decode(data["base64"]),
    }
    cv_cache[specialite] = cv_info
    return cv_info


def is_valid_email(email: str) -> bool:
    """Reject malformed addresses before they ever reach Gmail SMTP."""
    value = str(email or "").strip().lower()
    value = value.replace("\\@", "@").replace("mailto:", "")
    if not value or len(value) > 254:
        return False
    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", value):
        return False
    local, domain = value.rsplit("@", 1)
    return ".." not in local and ".." not in domain


def normalize_email(email: str) -> str:
    value = str(email or "").strip().lower()
    return value.replace("\\@", "@").replace("mailto:", "").strip()


def reserver_slot_envoi() -> dict:
    return _webhook_json(
        "POST",
        json_payload={
            "action": "reserve_send",
            "limit": CONFIG["ROLLING_24H_LIMIT"],
        },
        label="reserve_send",
    )


def liberer_slot_envoi(token: str):
    if not token:
        return
    try:
        _webhook_json(
            "POST",
            json_payload={"action": "release_send", "token": token},
            label="release_send",
        )
    except Exception as exc:
        # The reservation has a 30-minute TTL, so a failed release is
        # deliberately conservative rather than risking quota overshoot.
        print(f"⚠️ Impossible de libérer le slot {token}: {exc}")


def mettre_a_jour_sheet(updates: list):
    if not updates:
        return
    payload = {"action": "mark_sent", "updates": updates}
    data = _webhook_json(
        "POST",
        json_payload=payload,
        label="mark_sent",
    )
    print(f"📊 Sheet mis à jour : {data}")


# ─────────────────────────────────────────────────────────────────────────────
# ENVOI SMTP GMAIL
# ─────────────────────────────────────────────────────────────────────────────

def envoyer_email_smtp(smtp_server, destinataire: str, sujet: str, html_body: str, cv_info: dict):
    msg = MIMEMultipart()
    msg["From"] = f"{CONFIG['NOM']} <{CONFIG['EMAIL']}>"
    msg["To"] = destinataire
    msg["Reply-To"] = CONFIG["EMAIL"]
    msg["Subject"] = sujet

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if cv_info:
        part = MIMEApplication(cv_info["bytes"], _subtype="pdf")
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=cv_info["filename"],
        )
        msg.attach(part)

    smtp_server.sendmail(CONFIG["EMAIL"], [destinataire], msg.as_string())


def main():
    if not CONFIG["APP_PASSWORD"] or not CONFIG["WEBHOOK_URL"]:
        raise RuntimeError("❌ GMAIL_APP_PASSWORD ou GOOGLE_SHEET_WEBHOOK_URL manquant dans les Secrets.")

    target = CONFIG["BATCH_LIMIT"]
    print("🔍 Source unique des candidatures : Google Sheet")
    print(f"🎯 Objectif de ce run : {target} e-mails effectivement envoyés.")
    print(f"🛡️ Plafond persistant sur 24 h : {CONFIG['ROLLING_24H_LIMIT']} e-mails maximum via SMTP.")
    print("🔁 Les runs restent légers ; si le quota 24 h est atteint, le run s'arrête proprement.")
    print("🚫 Un run incomplet ne provoque PAS un échec du workflow.")

    cv_cache = {}
    emails_envoyes_ce_run = set()
    compteur = 0

    # Une seule grosse lecture du Sheet suffit : Code.gs renvoie d'abord les
    # nouvelles candidatures, puis les relances, puis les retries de secours.
    items = recuperer_offres_en_attente(
        offset=0,
        allow_retries=CONFIG["ALLOW_RETRIES_TO_REACH_TARGET"],
    )

    if not items:
        print("📭 Aucune candidature exploitable dans le Sheet.")
        print("🏁 Run terminé proprement : 0 email envoyé.")
        return

    quota = reserver_slot_envoi()
    if quota.get("status") != "success" or not quota.get("allowed", False):
        print(
            "🛑 Plafond 24 h atteint : "
            f"{quota.get('sent_24h', '?')}/{CONFIG['ROLLING_24H_LIMIT']} emails déjà comptabilisés. "
            "Aucun email ne sera envoyé dans ce run."
        )
        return

    # Probe only: release immediately. Each actual SMTP send reserves its
    # own slot immediately before sending.
    liberer_slot_envoi(quota.get("token"))

    context = ssl.create_default_context()
    server = None

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context)
        server.login(CONFIG["EMAIL"], CONFIG["APP_PASSWORD"])

        for item in items:
            if compteur >= target:
                break

            row_index = item.get("row_index")
            type_envoi = item.get("type", "INITIAL")
            email_cible = normalize_email(item.get("emails_rh", ""))

            if not is_valid_email(email_cible):
                print(f"⏭️ Ligne {row_index} : email invalide ignoré ({email_cible})")
                continue

            if email_cible in emails_envoyes_ce_run:
                continue

            intitule = item.get("intitule", "")
            role_cible = item.get("role_cible", "")
            specialite = detecter_specialite(intitule, role_cible)

            if not specialite:
                print(f"⏭️ Ligne {row_index} : spécialité non détectée ({intitule})")
                continue

            cv_info = recuperer_cv_depuis_drive(specialite, cv_cache)
            if not cv_info:
                print(
                    f"⏭️ Ligne {row_index} : CV introuvable sur Drive "
                    f"pour '{specialite}' — on passe à la suivante"
                )
                continue

            if type_envoi == "INITIAL":
                sujet, html_body = generer_email_depuis_apps_script(item, "INITIAL")
                nouveau_statut = "CANDIDATURE_ENVOYEE"
            else:
                sujet, html_body = generer_email_depuis_apps_script(item, "RELANCE")
                nouveau_statut = "RELANCE_EFFECTUEE"

            reservation = None
            smtp_accepted = False
            try:
                reservation = reserver_slot_envoi()
                if reservation.get("status") != "success" or not reservation.get("allowed", False):
                    print(
                        "🛑 Plafond SMTP 24 h atteint pendant le run : "
                        f"{reservation.get('sent_24h', '?')}/{CONFIG['ROLLING_24H_LIMIT']}. "
                        "Arrêt propre."
                    )
                    break

                token = reservation.get("token")
                envoyer_email_smtp(
                    server, email_cible, sujet, html_body, cv_info
                )

                smtp_accepted = True
                # SMTP a confirmé l'acceptation du message : c'est un vrai
                # envoi réussi. mark_sent finalise le slot réservé.
                compteur += 1
                emails_envoyes_ce_run.add(email_cible)

                # Le Sheet est mis à jour juste après l'envoi.
                # _webhook_json possède déjà ses propres retries.
                try:
                    mettre_a_jour_sheet([{
                        "row_index": row_index,
                        "statut": nouveau_statut,
                        "cv_utilise": cv_info["filename"],
                        "reservation_token": token,
                    }])
                except Exception as sheet_error:
                    # Ne jamais renvoyer un email déjà accepté par SMTP juste
                    # parce que l'écriture Sheet a temporairement échoué.
                    print(
                        f"⚠️ Email envoyé mais mise à jour Sheet échouée "
                        f"pour ligne {row_index}: {sheet_error}"
                    )

                print(f"✅ [{type_envoi}] #{compteur}/{target} → {email_cible}")
                time.sleep(CONFIG["DELAI_ENTRE_EMAILS_SEC"])

            except smtplib.SMTPResponseException as e:
                if reservation and reservation.get("token") and not smtp_accepted:
                    liberer_slot_envoi(reservation.get("token"))
                print(
                    f"❌ Erreur SMTP ligne {row_index} ({email_cible}): "
                    f"{e.smtp_code} - {e.smtp_error}"
                )

                if e.smtp_code in (421, 450, 452):
                    # Erreur temporaire : ne pas marquer comme envoyé.
                    # Le prochain cycle pourra reprendre la ligne.
                    print(
                        "⏸️ Gmail SMTP temporairement limité. "
                        "Arrêt propre du run après les emails déjà envoyés."
                    )
                    break

                if e.smtp_code in (550, 554):
                    print(
                        "⚠️ Destinataire refusé (550/554) — "
                        "on continue avec le prochain candidat."
                    )
                    continue

            except Exception as e:
                if reservation and reservation.get("token") and not smtp_accepted:
                    liberer_slot_envoi(reservation.get("token"))
                print(f"❌ Erreur ligne {row_index} ({email_cible}): {e}")
                continue

    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass

    if compteur < target:
        print(
            f"⚠️ {compteur}/{target} emails effectivement envoyés dans ce run. "
            "Le workflow reste SUCCESS : le prochain cycle reprendra les "
            "candidatures du Sheet et pourra utiliser les retries."
        )
    else:
        print(f"🏁 Objectif atteint : {compteur}/{target} emails effectivement envoyés.")



if __name__ == "__main__":
    main()
