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
    "BATCH_LIMIT": int(os.getenv("BATCH_LIMIT", "80")),  # Nb max d'envois par exécution GitHub
    "DELAI_ENTRE_EMAILS_SEC": 2.0,                       # Pause anti-spam entre 2 envois SMTP
}

TITRES_AUSBILDUNG = {
    "hotelfachfrau": "Hotelfachfrau",
    "systemgastronomie": "Fachfrau für Systemgastronomie",
    "einzelhandel": "Kauffrau im Einzelhandel",
    "spedition": "Kauffrau für Spedition und Logistikdienstleistung",
    "handel": "Kauffrau im Groß- und Außenhandelsmanagement",
    "industrie": "Industriekauffrau",
}

MOTIVATIONS = {
    "hotelfachfrau": (
        "Die Hotellerie ist mir bereits aus meiner beruflichen Erfahrung vertraut. "
        "Bei HBX Group / Hotelbeds betreute ich ein internationales B2B-Kundenportfolio "
        "im Bereich Hotellerie und Travel im Nahen Osten und arbeitete täglich mit "
        "Geschäftspartnern auf Arabisch, Französisch und Englisch. Insgesamt bringe ich "
        "über fünf Jahre Erfahrung in Kundenbetreuung, Vertrieb und kaufmännischen Abläufen mit. "
        "Diese Erfahrung möchte ich nun mit einer Ausbildung in Deutschland und einem "
        "anerkannten IHK-Abschluss weiterentwickeln."
    ),
    "systemgastronomie": (
        "Auch wenn mein bisheriger beruflicher Weg nicht direkt aus der Gastronomie kommt, "
        "bringe ich über fünf Jahre Erfahrung im Kundenservice, Vertrieb und in strukturierten "
        "Arbeitsabläufen mit. Als Top-Verkäuferin konnte ich bereits meine Stärke in "
        "Kundenkommunikation und Beratung unter Beweis stellen. Diese Erfahrung möchte ich nun "
        "in die Systemgastronomie einbringen und die professionellen Abläufe in Deutschland "
        "von Grund auf erlernen."
    ),
    "einzelhandel": (
        "Kundenberatung und Verkauf begleiten mich seit mehreren Jahren. In über fünf Jahren "
        "Berufserfahrung habe ich im B2B- und B2C-Vertrieb sowie im Kundenservice gearbeitet "
        "und wurde bei Umanis Intermediation aufgrund meiner Beratungsqualität und Abschlussstärke "
        "als Top-Verkäuferin ausgezeichnet. Heute gehören außerdem Bestandsüberwachung, "
        "Auftragsabwicklung und kaufmännische Aufgaben zu meinem Arbeitsalltag. Diese Erfahrung "
        "möchte ich nun gezielt mit einer deutschen Ausbildung und einem anerkannten "
        "IHK-Abschluss verbinden."
    ),
    "spedition": (
        "Logistik und Koordination sind mir bereits aus meiner Berufserfahrung vertraut. "
        "Bei Helpdesk ForYou koordinierte ich die Einsatzplanung von über 100 Fahrern und "
        "Mitarbeitenden und verfolgte Touren, Termine und Wartungen. Heute arbeite ich bei "
        "Atmlo Chem / EasyChemicalStock mit Beschaffung, Logistik, Bestandsüberwachung, "
        "Auftragsabwicklung und Lieferanten. Insgesamt bringe ich über fünf Jahre kaufmännische "
        "Berufserfahrung mit, die ich nun gezielt durch eine Ausbildung und einen anerkannten "
        "IHK-Abschluss erweitern möchte."
    ),
    "handel": (
        "Ich bringe über fünf Jahre Berufserfahrung in kaufmännischen Bereichen, Kundenbetreuung "
        "und Vertrieb mit. In meiner aktuellen Tätigkeit arbeite ich unter anderem mit Beschaffung, "
        "Lieferanten, Bestandsüberwachung und Auftragsabwicklung sowie mit Excel und Sage. "
        "Zuvor betreute ich bei HBX Group / Hotelbeds internationale B2B-Geschäftspartner. "
        "Diese Erfahrung möchte ich nun mit einer fundierten Ausbildung und einem anerkannten "
        "IHK-Abschluss in Deutschland verbinden."
    ),
    "industrie": (
        "Durch über fünf Jahre Berufserfahrung bringe ich bereits praktische Kenntnisse in "
        "kaufmännischer Organisation, Beschaffung, Auftragsabwicklung und Kundenbetreuung mit. "
        "Aktuell arbeite ich mit Lieferanten, Beständen, Rechnungen, Excel und dem ERP-System Sage. "
        "Ich möchte diese Praxiserfahrung nun mit den kaufmännischen Prozessen eines deutschen "
        "Unternehmens verbinden und dabei einen anerkannten IHK-Abschluss erwerben."
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
    if any(k in t for k in ["hotelfach", "hotelkauffrau", "hotelkaufmann", "hotelmanagement"]):
        return "hotelfachfrau"
    if "systemgastronomie" in t:
        return "systemgastronomie"
    if "einzelhandel" in t:
        return "einzelhandel"
    if any(k in t for k in ["spedition", "logistikdienstleistung", "speditionskauf"]):
        return "spedition"
    if any(k in t for k in ["gross", "groß", "aussenhandel", "außenhandel", "grosshandel", "großhandel"]):
        return "handel"
    if "industriekauf" in t:
        return "industrie"
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

def recuperer_offres_en_attente():
    resp = requests.get(
        CONFIG["WEBHOOK_URL"],
        params={"action": "get_pending", "limit": CONFIG["BATCH_LIMIT"]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if "stats" in data:
        print(f"📊 Diagnostic Sheet ({data.get('sheet_name')}): {data['stats']}")
    return data.get("items", [])


def recuperer_cv_depuis_drive(specialite: str, cv_cache: dict):
    if specialite in cv_cache:
        return cv_cache[specialite]

    resp = requests.get(
        CONFIG["WEBHOOK_URL"],
        params={"action": "get_cv", "specialite": specialite},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success" or not data.get("base64"):
        cv_cache[specialite] = None
        return None

    cv_info = {
        "filename": data["filename"],
        "bytes": base64.b64decode(data["base64"]),
    }
    cv_cache[specialite] = cv_info
    return cv_info


def mettre_a_jour_sheet(updates: list):
    if not updates:
        return
    payload = {"action": "mark_sent", "updates": updates}
    resp = requests.post(CONFIG["WEBHOOK_URL"], json=payload, timeout=60)
    resp.raise_for_status()
    print(f"📊 Sheet mis à jour : {resp.json()}")


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

    print("🔍 Récupération des candidatures en attente depuis Google Sheet...")
    items = recuperer_offres_en_attente()
    if not items:
        print("✅ Aucune candidature ou relance en attente.")
        return

    print(f"📋 {len(items)} ligne(s) prête(s) à être traitée(s) (limite : {CONFIG['BATCH_LIMIT']}).")

    cv_cache = {}
    updates = []
    emails_envoyes_ce_run = set()
    compteur = 0

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(CONFIG["EMAIL"], CONFIG["APP_PASSWORD"])

        for item in items:
            if compteur >= CONFIG["BATCH_LIMIT"]:
                break

            row_index = item["row_index"]
            type_envoi = item["type"]  # "INITIAL" ou "RELANCE"
            email_cible = item["emails_rh"].strip().lower()
            intitule = item.get("intitule", "")
            role_cible = item.get("role_cible", "")

            if email_cible in emails_envoyes_ce_run:
                continue

            specialite = detecter_specialite(intitule, role_cible)
            if not specialite:
                print(f"⏭️ Ligne {row_index} : spécialité non détectée ({intitule})")
                continue

            cv_info = recuperer_cv_depuis_drive(specialite, cv_cache)
            if not cv_info:
                print(f"⏭️ Ligne {row_index} : CV introuvable sur Drive pour '{specialite}'")
                continue

            if type_envoi == "INITIAL":
                sujet, html_body = generer_email_candidature(specialite)
                nouveau_statut = "CANDIDATURE_ENVOYEE"
            else:
                sujet, html_body = generer_email_relance(specialite)
                nouveau_statut = "RELANCE_EFFECTUEE"

            try:
                envoyer_email_smtp(server, email_cible, sujet, html_body, cv_info)
                compteur += 1
                emails_envoyes_ce_run.add(email_cible)
                updates.append({
                    "row_index": row_index,
                    "statut": nouveau_statut,
                    "cv_utilise": cv_info["filename"],
                })
                print(f"✅ [{type_envoi}] #{compteur}/{CONFIG['BATCH_LIMIT']} → {email_cible}")

                # Marquer immédiatement chaque succès dans le Sheet.
                # Ainsi, si GitHub relance le workflow après une panne réseau,
                # les emails déjà envoyés ne sont pas reproposés comme "NOUVEAU".
                mettre_a_jour_sheet(updates)
                updates.clear()

                time.sleep(CONFIG["DELAI_ENTRE_EMAILS_SEC"])

            except smtplib.SMTPResponseException as e:
                print(f"❌ Erreur SMTP ligne {row_index} ({email_cible}): {e.smtp_code} - {e.smtp_error}")
                # Si Gmail bloque pour quota journalier SMTP atteint (erreur 550 / 421), on s'arrête proprement
                if e.smtp_code in (421, 450, 452, 550, 554):
                    print("⛔ Limite SMTP Gmail atteinte pour le moment. Arrêt propre.")
                    break
            except Exception as e:
                print(f"❌ Erreur ligne {row_index} ({email_cible}): {e}")

    # Sauvegarde finale des statuts dans le Sheet
    if updates:
        mettre_a_jour_sheet(updates)

    print(f"🏁 Terminé — {compteur} e-mail(s) envoyé(s) via SMTP.")


if __name__ == "__main__":
    main()
