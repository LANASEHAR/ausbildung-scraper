/**
 * ==============================================================================
 * CODE.GS — SYSTÈME AUTOMATISÉ AUSBILDUNG KAUFMANN / KAUFFRAU
 * Google Apps Script pour Google Sheet "Ausbildung applications"
 *
 * FONCTIONS PRINCIPALES :
 *   doPost(e)                        → Webhook INSERT + UPDATE avec déduplication par ID/email
 *   traiterAusbildungCandidatures()  → Envoi emails + relances 48h (max 100/run, quota Gmail)
 *   getCV(intitule, roleCible)        → Mapping CV par spécialité Kauffrau
 *   configurerDeclencheurs()          → Installe le trigger horaire automatique
 *
 * MAPPING CV (fichiers confirmés sur Google Drive) :
 *   Büromanagement → "Bewerbung Kauffrau Buromanagemenet Halima Essaouaf.pdf"
 *   E-Commerce     → "Bewerbung Kauffrau ECommerce Halima Essaouaf.pdf"
 *   Groß/Außenhandel → "Bewerbung Kauffrau GrossAussenhandel Halima Essaouaf.pdf"
 *   Spedition      → "Bewerbung Kauffrau Spedition Logistik Halima Essaouaf.pdf"
 *   Tourismus      → "Bewerbung Kauffrau Tourismus Freizeit Halima Essaouaf.pdf"
 * ==============================================================================
 */

// ─────────────────────────────────────────────────────────────────────────────
// CONFIGURATION — À PERSONNALISER
// ─────────────────────────────────────────────────────────────────────────────

const CONFIG={
  NOM:"Halima Essaouaf", EMAIL:"essaouafhalima@gmail.com", TEL:"+212619968131",
  LINKEDIN:"linkedin.com/in/halima-essaouaf-1b4b81202", NOM_ONGLET:"Ausbildung",
  BATCH_LIMIT:100, DELAI_ENTRE_EMAILS_MS:1000, DELAI_RELANCE_H:48,
  CV_FOLDER_NAME:"New Bewerbung",
  CV_MAPPING:{
    hotelfachfrau:"Bewerbungsmappe_Hotelfachfrau_Halima_Essaouaf.pdf",
    systemgastronomie:"Bewerbungsmappe_Fachfrau_fuer_Systemgastronomie_Halima_Essaouaf.pdf",
    einzelhandel:"Bewerbungsmappe_Kauffrau_im_Einzelhandel_Halima_Essaouaf.pdf",
    spedition:"Bewerbungsmappe_Kauffrau_fuer_Spedition_und_Logistikdienstleistung_Halima_Essaouaf.pdf",
    handel:"Bewerbungsmappe_Kauffrau_im_Gross-_und_Aussenhandelsmanagement_Halima_Essaouaf.pdf",
    industrie:"Bewerbungsmappe_Industriekauffrau_Halima_Essaouaf.pdf"
  }
};

// Colonnes du Google Sheet (0-indexées)
const COL = {
  DATE_DETECTION: 0,  // A
  STATUT:         1,  // B
  ROLE_CIBLE:     2,  // C
  INTITULE:       3,  // D
  ENTREPRISE:     4,  // E
  LIEU:            5,  // F
  EMAILS_RH:      6,  // G
  SOURCE:         7,  // H
  LIEN:            8,  // I
  ID:              9,  // J
  DATE_ENVOI:    10,  // K
  DATE_RELANCE:  11,  // L
}


// ─────────────────────────────────────────────────────────────────────────────
// UTILITAIRES
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Retourne l'onglet Ausbildung, ou l'onglet actif si introuvable.
 */
function getSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(CONFIG.NOM_ONGLET) || ss.getActiveSheet();
}

/**
 * Valide qu'un email est utilisable (contient "@" et n'est pas un placeholder).
 */
function normalizeEmail(email) {
  if (!email) return "";
  return String(email)
    .trim()
    .toLowerCase()
    .replace(/^mailto:/i, "")
    .replace(/[<>"']/g, "")
    .trim();
}

/**
 * Extrait le premier email valide d'une cellule.
 * Une seule adresse est conservée dans le Sheet afin de garantir
 * l'unicité des contacts.
 */
function extractFirstEmail(emailsRh) {
  if (!emailsRh) return "";
  const matches = String(emailsRh).match(/[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/ig) || [];
  for (const candidate of matches) {
    const email = normalizeEmail(candidate);
    if (isValidEmail(email)) return email;
  }
  return "";
}

function isValidEmail(email) {
  const str = normalizeEmail(email);
  return /^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$/i.test(str) &&
    !str.includes("non détecté") &&
    !str.includes("postuler via lien");
}

/**
 * Retourne les IDs et emails déjà utilisés dans le Sheet.
 * L'email est normalisé pour empêcher les doublons du type
 * Contact@Entreprise.de / contact@entreprise.de.
 */
function buildSheetIndexes(data) {
  const existingIds = new Set();
  const existingEmails = new Set();
  const sentEmails = new Set();

  for (let i = 1; i < data.length; i++) {
    const id = String(data[i][COL.ID] || "").trim();
    const email = extractFirstEmail(data[i][COL.EMAILS_RH] || "");
    const status = String(data[i][COL.STATUT] || "").trim();

    if (id) existingIds.add(id);
    if (email) existingEmails.add(email);

    if (email && (status === "CANDIDATURE_ENVOYEE" || status === "RELANCE_EFFECTUEE")) {
      sentEmails.add(email);
    }
  }

  return { existingIds, existingEmails, sentEmails };
}


/**
 * Formate une date en "DD.MM.YYYY HH:MM" (format allemand).
 */
function formatDateDE(date) {
  const d = new Date(date);
  const pad = n => String(n).padStart(2, "0");
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}


// ─────────────────────────────────────────────────────────────────────────────
// WEBHOOK doPost — RÉCEPTION DES OFFRES DEPUIS LE SCRAPER PYTHON
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Point d'entrée du webhook HTTP POST.
 * Appelé automatiquement par ausbildung_scraper.py via GOOGLE_SHEET_WEBHOOK_URL.
 * Insère uniquement les nouvelles offres (déduplication par ID colonne J).
 */
function testerDoPost() {
  const testPayload = [{
    id: "TEST_MANUEL_" + new Date().getTime(),
    date_detection: new Date().toISOString(),
    statut: "NOUVEAU",
    role_cible: "Groß- und Außenhandelsmanagement",
    intitule: "Ausbildung Kauffrau im Groß- und Außenhandelsmanagement",
    entreprise: "TEST – NE PAS CONTACTER",
    lieu: "Deutschland",
    emails_rh: "test@example.org",
    source: "TEST MANUEL",
    lien: ""
  }];
  const fakeEvent = {
    postData: {
      contents: JSON.stringify(testPayload),
      type: "application/json"
    }
  };
  const result = doPost(fakeEvent);
  Logger.log(result.getContent());
  return result.getContent();
}

function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({
      status: "ok",
      service: "Ausbildung webhook",
      message: "Webhook actif. Utiliser POST pour envoyer les offres."
    }))
    .setMimeType(ContentService.MimeType.JSON);
}

function ensureTrackingColumns(sheet) {
  const headers = [
    "Date offre", "Priorité région", "Priorité Ausbildung"
  ];

  // Add metadata headers only when they are not already present.
  const lastColumn = Math.max(sheet.getLastColumn(), COL.DATE_RELANCE + 1);
  if (sheet.getMaxColumns() < COL.PRIORITE_AUSBILDUNG + 1) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), COL.PRIORITE_AUSBILDUNG + 1 - sheet.getMaxColumns());
  }

  const headerValues = sheet.getRange(1, 1, 1, COL.PRIORITE_AUSBILDUNG + 1).getValues()[0];
  if (!headerValues[COL.DATE_OFFRE]) headerValues[COL.DATE_OFFRE] = headers[0];
  if (!headerValues[COL.PRIORITE_REGION]) headerValues[COL.PRIORITE_REGION] = headers[1];
  if (!headerValues[COL.PRIORITE_AUSBILDUNG]) headerValues[COL.PRIORITE_AUSBILDUNG] = headers[2];

  sheet.getRange(1, 1, 1, COL.PRIORITE_AUSBILDUNG + 1).setValues([headerValues]);
}

function sortOffers(sheet) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= 2) return;

  // Region priority → Ausbildung priority → newest offer date → detection date.
  sheet.getRange(2, 1, lastRow - 1, COL.PRIORITE_AUSBILDUNG + 1).sort([
    {column: COL.PRIORITE_REGION + 1, ascending: false},
    {column: COL.PRIORITE_AUSBILDUNG + 1, ascending: false},
    {column: COL.DATE_OFFRE + 1, ascending: false},
    {column: COL.DATE_DETECTION + 1, ascending: false}
  ]);
}

function doPost(e) {
  // Parse the request BEFORE taking the script lock. JSON parsing does not touch
  // the spreadsheet and therefore should never block other webhook executions.
  let rawData;
  try {
    rawData = JSON.parse(e.postData.contents);
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({
        status: "error",
        message: "JSON invalide: " + error.toString()
      }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  const lock = LockService.getScriptLock();
  try {
    // Keep the critical section short. The Python scraper sends small batches,
    // and all Sheet writes below are batched instead of appendRow()/setValue()
    // calls inside large loops.
    lock.waitLock(25000);

    const sheet = getSheet();
    ensureTrackingColumns(sheet);
    const allData = sheet.getDataRange().getValues();
    const { existingIds, existingEmails } = buildSheetIndexes(allData);

    // ── MODE EXPORT_MISSING_EMAILS : lecture des lignes sans email ─────────
    // Utilisé par le workflow GitHub d'enrichissement. La lecture est paginée
    // pour éviter une réponse énorme et pour laisser le workflow reprendre
    // proprement sur plusieurs appels.
    if (rawData && rawData.action === "export_missing_emails") {
      const offset = Math.max(0, Number(rawData.offset || 0));
      const limit = Math.min(500, Math.max(1, Number(rawData.limit || 500)));
      const rows = [];
      let skipped = 0;

      for (let i = 1; i < allData.length && rows.length < limit; i++) {
        const row = allData[i];
        const email = extractFirstEmail(row[COL.EMAILS_RH] || "");
        if (email) continue;
        if (skipped < offset) {
          skipped++;
          continue;
        }

        rows.push({
          id: String(row[COL.ID] || "").trim(),
          entreprise: String(row[COL.ENTREPRISE] || "").trim(),
          intitule: String(row[COL.INTITULE] || "").trim(),
          role_cible: String(row[COL.ROLE_CIBLE] || "").trim(),
          lieu: String(row[COL.LIEU] || "").trim(),
          source: String(row[COL.SOURCE] || "").trim(),
          lien: String(row[COL.LIEN] || "").trim(),
          row_number: i + 1
        });
      }

      return ContentService
        .createTextOutput(JSON.stringify({
          status: "success",
          action: "export_missing_emails",
          offset: offset,
          returned: rows.length,
          total_rows: Math.max(0, allData.length - 1),
          rows: rows
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // ── MODE UPDATE : enrichissement email/site depuis le scraper ─────────
    if (rawData && rawData.action === "update") {
      const jobs = Array.isArray(rawData.jobs) ? rawData.jobs : [];
      const idToRow = new Map();

      for (let i = 1; i < allData.length; i++) {
        const id = String(allData[i][COL.ID] || "").trim();
        if (id) idToRow.set(id, i); // 0-based index in allData
      }

      let updated = 0;
      let duplicateEmails = 0;
      let missingIds = 0;
      let changed = false;

      for (const job of jobs) {
        const jobId = String(job.id || "").trim();
        if (!jobId || !idToRow.has(jobId)) {
          missingIds++;
          continue;
        }

        const rowIndex = idToRow.get(jobId);
        const currentEmail = extractFirstEmail(allData[rowIndex][COL.EMAILS_RH] || "");
        const newEmail = extractFirstEmail(job.emails_rh || "");

        if (!newEmail || currentEmail === newEmail) continue;

        // The same verified company email may legitimately belong to
        // several duplicate/related offers. Keep it on each matching row.
        // buildSheetIndexes/sender logic prevents sending the same address twice.
        allData[rowIndex][COL.EMAILS_RH] = newEmail;
        if (currentEmail) existingEmails.delete(currentEmail);
        existingEmails.add(newEmail);

        updated++;
        changed = true;
      }

      // One batched write for the whole existing data range. This is vastly
      // faster than thousands of individual getRange().setValue() calls.
      if (changed && allData.length > 1) {
        sheet.getRange(1, 1, allData.length, allData[0].length).setValues(allData);
      }

      Logger.log("[UPDATE] " + updated + " email(s) mis à jour | doublons email ignorés: " +
        duplicateEmails + " | IDs inconnus: " + missingIds);

      return ContentService
        .createTextOutput(JSON.stringify({
          status: "success",
          action: "update",
          updated: updated,
          duplicate_emails: duplicateEmails,
          missing_ids: missingIds
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // ── MODE INSERT : une offre = un ID unique ET un email unique ─────────
    const jobs = Array.isArray(rawData) ? rawData : [rawData];
    const rowsToAppend = [];

    let duplicateIds = 0;
    let duplicateEmails = 0;
    let invalidEmails = 0;

    for (const job of jobs) {
      const jobId = String(job.id || "").trim();
      const email = extractFirstEmail(job.emails_rh || "");

      if (!jobId || existingIds.has(jobId)) {
        duplicateIds++;
        continue;
      }

      if (!email) {
        invalidEmails++;
        // Keep the offer anyway: the enrichment workflow needs this row
        // to search the official company website later.
      }

      // Do not reject a second offer merely because the company/contact email
      // is already present on another offer. IDs are the offer-level identity.
      // The sender protects the contact from duplicate applications.
      if (email && existingEmails.has(email)) {
        duplicateEmails++;
      }

      rowsToAppend.push([
        job.date_detection || new Date().toISOString(),
        job.statut || "NOUVEAU",
        job.role_cible || "",
        job.intitule || "",
        job.entreprise || "",
        job.lieu || "Deutschland (Allemagne)",
        email || "",
        job.source || "Scraper Cloud Ausbildung",
        job.lien || "",
        jobId,
        "",
        "",
        job.date_offre || "",
        Number(job.prioritaet_region || 0),
        Number(job.prioritaet_ausbildung || 0),
      ]);

      existingIds.add(jobId);
      if (email) existingEmails.add(email);
    }

    // One setValues() call instead of appendRow() for every job.
    if (rowsToAppend.length) {
      const firstRow = sheet.getLastRow() + 1;
      sheet.getRange(firstRow, 1, rowsToAppend.length, rowsToAppend[0].length)
        .setValues(rowsToAppend);
      sortOffers(sheet);
    }

    Logger.log("[INSERT] " + rowsToAppend.length + " ajoutées | IDs doublons ignorés: " + duplicateIds +
      " | offres partageant un email existant: " + duplicateEmails + " | offres sans email conservées pour enrichissement: " + invalidEmails);

    return ContentService
      .createTextOutput(JSON.stringify({
        status: "success",
        action: "insert",
        added: rowsToAppend.length,
        duplicate_ids: duplicateIds,
        duplicate_emails: duplicateEmails,
        invalid_emails: invalidEmails
      }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    Logger.log("[WEBHOOK] Erreur: " + error.toString());
    return ContentService
      .createTextOutput(JSON.stringify({
        status: "error",
        message: error.toString()
      }))
      .setMimeType(ContentService.MimeType.JSON);
  } finally {
    try { lock.releaseLock(); } catch (_) {}
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// MAPPING CV — SÉLECTION PAR SPÉCIALITÉ KAUFFRAU
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Détermine la clé de spécialité à partir du contenu de l'intitulé et du rôle cible.
 * Retourne l'une des clés : "buero" | "ecommerce" | "handel" | "spedition" | "tourismus"
 */
function detecterSpecialite(intitule,roleCible){
  const t=((intitule||"")+" "+(roleCible||"")).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"");
  if(t.includes("hotelfach")||t.includes("hotelkauffrau")||t.includes("hotelkaufmann")||t.includes("hotelmanagement")) return "hotelfachfrau";
  if(t.includes("systemgastronomie")) return "systemgastronomie";
  if(t.includes("einzelhandel")) return "einzelhandel";
  if(t.includes("spedition")||t.includes("logistikdienstleistung")||t.includes("speditionskauf")) return "spedition";
  if(t.includes("gross")||t.includes("aussenhandel")||t.includes("grosshandel")) return "handel";
  if(t.includes("industriekauf")) return "industrie";
  return "";
}
function getTitreAusbildung(specialite){
  return ({hotelfachfrau:"Hotelfachfrau / Hotelkauffrau",systemgastronomie:"Fachfrau für Systemgastronomie",einzelhandel:"Kauffrau im Einzelhandel",spedition:"Kauffrau für Spedition und Logistikdienstleistung",handel:"Kauffrau im Groß- und Außenhandelsmanagement",industrie:"Industriekauffrau"})[specialite]||"Ausbildungsplatz";
}

function getCV(intitule,roleCible){
  const specialite=detecterSpecialite(intitule,roleCible), filename=CONFIG.CV_MAPPING[specialite];
  if(!specialite||!filename) return null;

  const folders=DriveApp.getFoldersByName(CONFIG.CV_FOLDER_NAME);
  if(!folders.hasNext()){Logger.log("[CV] Dossier introuvable: "+CONFIG.CV_FOLDER_NAME);return null;}
  const folder=folders.next();

  // 1) Exact filename match.
  const exact=folder.getFilesByName(filename);
  if(exact.hasNext()) return exact.next();

  // 2) Strict role-keyword fallback inside the same folder, so renamed PDFs
  // still work without ever falling back to another Ausbildung.
  const keywords={
    hotelfachfrau:["hotelfachfrau","hotelfachmann","hotelkkauffrau","hotelkauffrau","hotelkaufmann"],
    systemgastronomie:["systemgastronomie"],
    einzelhandel:["einzelhandel"],
    spedition:["spedition","logistikdienstleistung"],
    handel:["gross","groß","aussenhandel","außenhandel"],
    industrie:["industriekauffrau","industriekaufmann"]
  }[specialite]||[];

  const files=folder.getFiles();
  while(files.hasNext()){
    const file=files.next();
    const name=file.getName().toLowerCase();
    if(keywords.some(k=>name.includes(k))) return file;
  }

  Logger.log("[CV] Kein passender CV in "+CONFIG.CV_FOLDER_NAME+" für "+specialite);
  return null;
}




// ─────────────────────────────────────────────────────────────────────────────
// GÉNÉRATION DES EMAILS ALLEMANDS PAR SPÉCIALITÉ
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Génère la signature HTML complète (utilisée dans les emails HTML).
 */
function getSignatureHTML() {
  return `
<br><br>
<table style="font-family: Arial, sans-serif; font-size: 13px; color: #333; border-top: 2px solid #1a73e8; padding-top: 8px;">
  <tr>
    <td>
      <strong style="font-size: 14px; color: #1a1a1a;">${CONFIG.NOM}</strong><br>
      <span style="color: #666;">Bewerberin – Ausbildung Kauffrau</span><br><br>
      📧 <a href="mailto:${CONFIG.EMAIL}" style="color: #1a73e8;">${CONFIG.EMAIL}</a><br>
      📞 ${CONFIG.TEL}<br>
      🔗 <a href="https://${CONFIG.LINKEDIN}" style="color: #1a73e8;">${CONFIG.LINKEDIN}</a>
    </td>
  </tr>
</table>`.trim();
}

/**
 * Génère le corps HTML de l'email de candidature initiale.
 * Adapté par spécialité avec des formulations professionnelles en allemand.
 */
function genererEmailCandidature(entreprise,intitule,roleCible){
  const specialite=detecterSpecialite(intitule,roleCible), titrePoste=getTitreAusbildung(specialite);
  const entrepriseDisplay=(entreprise&&entreprise!=="Unternehmen Deutschland")?entreprise:"Ihr Unternehmen";
  const motivation={
    hotelfachfrau:"Bei HBX Group / Hotelbeds betreute ich ein internationales B2B-Kundenportfolio im Bereich Hotellerie und Travel im Nahen Osten. Kundenbindung, Konditionsabstimmung und schnelle Problemlösung gehörten zu meinem Alltag.",
    systemgastronomie:"Bei Umanis Intermediation (CGI) wurde ich als Top-Verkäuferin ausgezeichnet. Bei Total Call habe ich Kunden technisch und kaufmännisch beraten. Diese Erfahrung in Verkauf und Service möchte ich in die Systemgastronomie einbringen.",
    einzelhandel:"Ich bringe mehr als fünf Jahre Erfahrung in Vertrieb und Kundenberatung mit. Bei Umanis Intermediation (CGI) wurde ich als Top-Verkäuferin ausgezeichnet; aktuell gehören auch Bestandsüberwachung und Auftragsabwicklung zu meinen Aufgaben.",
    spedition:"Bei Helpdesk ForYou koordinierte ich die Einsatzplanung von über 100 Fahrern und Mitarbeitenden sowie Touren, Termine und Wartungen. Seit April 2026 arbeite ich mit Beschaffung, Logistik, Bestandsüberwachung, Auftragsabwicklung und Lieferantenabstimmung.",
    handel:"Seit April 2026 übernehme ich bei Atmlo Chem / EasyChemicalStock Beschaffung, Lieferantenabstimmung, Bestandsüberwachung und Auftragsabwicklung und arbeite mit Excel und ERP Sage. Zuvor betreute ich internationale B2B-Geschäftspartner bei HBX Group / Hotelbeds.",
    industrie:"Meine aktuelle Tätigkeit bei Atmlo Chem / EasyChemicalStock umfasst Beschaffung, Bestandsüberwachung, Auftragsabwicklung, Rechnungen, Excel-Reporting und ERP Sage. Dazu kommen mehrjährige Erfahrungen im internationalen B2B-Kundenmanagement und Vertrieb."
  }[specialite]||"Meine bisherige Berufserfahrung verbindet Kundenkontakt, kaufmännische Organisation und strukturierte Arbeitsprozesse.";
  const body=`Sehr geehrte Damen und Herren,

mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> bei ${entrepriseDisplay}.

${motivation}

Ich bringe <strong>mehr als fünf Jahre praktische Berufserfahrung</strong> mit. Trotz meines DEUG in Wirtschaft und Management möchte ich bewusst eine Ausbildung in Deutschland absolvieren, die deutschen Standards systematisch von Grund auf lernen und einen anerkannten IHK-Abschluss erwerben. Gleichzeitig bringe ich bereits Berufserfahrung, Eigenständigkeit, Disziplin und internationale Kommunikationsstärke mit.

Meine Sprachkenntnisse: Arabisch Muttersprache, Französisch C1, Englisch C1, Deutsch B1 abgeschlossen und <strong>B2 aktuell in aktiver Vorbereitung</strong>, Spanisch A2.

Meine Bewerbungsunterlagen finden Sie im Anhang. Über die Gelegenheit zu einem persönlichen oder digitalen Gespräch freue ich mich sehr.

Mit freundlichen Grüßen
${getSignatureHTML()}`.trim();
  return {titrePoste,body};
}

function genererEmailRelance(entreprise, intitule, roleCible) {
  const specialite = detecterSpecialite(intitule, roleCible);
  const titrePoste = getTitreAusbildung(specialite);
  const entrepriseDisplay = (entreprise && entreprise !== "Unternehmen Deutschland") ? entreprise : null;

  const salutation = "Sehr geehrte Damen und Herren,";

  const body = `
${salutation}

vor zwei Tagen habe ich Ihnen meine Bewerbungsunterlagen für den Ausbildungsplatz als <strong>${titrePoste}</strong> zugesendet und möchte mich kurz erkundigen, ob diese gut bei Ihnen eingegangen sind.

Da ich nach wie vor großes Interesse an einer Ausbildung in Ihrem Unternehmen habe, erlauben Sie mir, meinen Lebenslauf zur Sicherheit erneut beizufügen.

Ich stehe Ihnen jederzeit für ein erstes Gespräch zur Verfügung und freue mich sehr auf Ihre Rückmeldung.

Mit freundlichen Grüßen
${getSignatureHTML()}
`.trim();

  return { titrePoste, body };
}


// ─────────────────────────────────────────────────────────────────────────────
// FONCTION PRINCIPALE — ENVOIS & RELANCES
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Traite toutes les lignes du Google Sheet Ausbildung :
 *   - Statut "NOUVEAU"             → Envoie candidature initiale + CV
 *   - Statut "CANDIDATURE_ENVOYEE" → Envoie relance 48h si délai atteint
 * Maximum CONFIG.BATCH_LIMIT envois par exécution.
 */
function extrairePriorite(roleCible) {
  const m = String(roleCible || "").match(/^\s*(P1|P2|P3)\s*[|—:-]/i);
  if (m) return m[1].toUpperCase();

  const text = String(roleCible || "").toLowerCase();
  if (/einzelhandel|verkäufer|verkaufer|lagerlogistik|großhandel|grosshandel|außenhandel|aussenhandel/.test(text)) return "P1";
  if (/spedition|logistikdienstleistung|disposition|büromanagement|industriekaufmann|industriekauffrau/.test(text)) return "P2";
  return "P3";
}

function traiterAusbildungCandidatures() {
  const lock = LockService.getScriptLock();

  try {
    lock.waitLock(30000);

    const sheet = getSheet();
    const data = sheet.getDataRange().getValues();
    const now = new Date();

    if (data.length <= 1) {
      Logger.log("⚠️ Sheet vide.");
      return;
    }

    // Le workflow tente jusqu'à CONFIG.BATCH_LIMIT messages par passage.
    // Les limites réelles du compte Google restent appliquées côté Gmail/Apps Script.
    // Une erreur d'envoi est journalisée sans bloquer les autres lignes.
    const limite = CONFIG.BATCH_LIMIT;
    let compteur = 0;

    // Historique PERSISTANT : protège contre un second envoi au même email
    // même si la ligne change de statut ou si un nouveau job apparaît.
    const { sentEmails } = buildSheetIndexes(data);

    // Une seule candidature par email pendant cette exécution également.
    const emailsEnvoyesCetteExecution = new Set();

    // Cache des CV pour éviter de relire Drive 30 fois.
    const cvCache = {};

    const rowIndexes = [];
    for (let i = 1; i < data.length; i++) {
      const status = String(data[i][COL.STATUT] || "").trim();
      if (status === "NOUVEAU") rowIndexes.push(i);
    }
    for (let i = 1; i < data.length; i++) {
      const status = String(data[i][COL.STATUT] || "").trim();
      if (status === "CANDIDATURE_ENVOYEE") rowIndexes.push(i);
    }

    // P1/P2/P3 is stored inside the original ROLE_CIBLE column.
    // No new Sheet columns are required.
    rowIndexes.sort((a, b) => {
      const pa = extrairePriorite(data[a][COL.ROLE_CIBLE]);
      const pb = extrairePriorite(data[b][COL.ROLE_CIBLE]);
      const order = {P1: 3, P2: 2, P3: 1};
      const priorityDiff = (order[pb] || 0) - (order[pa] || 0);
      if (priorityDiff !== 0) return priorityDiff;
      return a - b;
    });

    for (const i of rowIndexes) {
      if (compteur >= limite) break;
      const row = data[i];

      const statut = String(row[COL.STATUT] || "").trim();
      const roleCible = String(row[COL.ROLE_CIBLE] || "").trim();
      const intitule = String(row[COL.INTITULE] || "").trim();
      const entreprise = String(row[COL.ENTREPRISE] || "").trim() || "Unternehmen Deutschland";
      const emailCible = extractFirstEmail(row[COL.EMAILS_RH] || "");
      const dateEnvoi = row[COL.DATE_ENVOI] ? new Date(row[COL.DATE_ENVOI]) : null;
      const rowNum = i + 1;

      if (!emailCible || !isValidEmail(emailCible)) {
        continue;
      }

      // Protection permanente contre une DEUXIÈME CANDIDATURE INITIALE
      // à la même adresse. Une relance 48h reste autorisée pour la ligne
      // déjà envoyée.
      if (emailsEnvoyesCetteExecution.has(emailCible)) {
        continue;
      }

      // ── CANDIDATURE INITIALE ────────────────────────────────────────────
      if (statut === "NOUVEAU") {
        if (sentEmails.has(emailCible)) {
          Logger.log("⏭️ Candidature initiale déjà envoyée historiquement : " + emailCible);
          continue;
        }

        const specialite = detecterSpecialite(intitule, roleCible);

        if (!(specialite in cvCache)) {
          cvCache[specialite] = getCV(intitule, roleCible);
        }

        const cvFile = cvCache[specialite];

        if (!cvFile) {
          Logger.log("⏭️ Ligne " + rowNum + " : CV manquant pour " + specialite);
          continue;
        }

        const { titrePoste, body } = genererEmailCandidature(
          entreprise, intitule, roleCible
        );
        const sujet = "Bewerbung um einen Ausbildungsplatz als " + titrePoste + " – " + CONFIG.NOM;

        try {
          GmailApp.sendEmail(emailCible, sujet, "", {
            htmlBody: body,
            attachments: [cvFile.getAs(MimeType.PDF)],
            name: CONFIG.NOM,
            replyTo: CONFIG.EMAIL,
          });

          sheet.getRange(rowNum, COL.STATUT + 1).setValue("CANDIDATURE_ENVOYEE");
          sheet.getRange(rowNum, COL.DATE_ENVOI + 1).setValue(new Date());

          compteur++;
          emailsEnvoyesCetteExecution.add(emailCible);
          sentEmails.add(emailCible);

          Logger.log("✅ ENVOI #" + compteur + "/" + limite + " → " + emailCible);

          if (compteur < limite) {
            Utilities.sleep(CONFIG.DELAI_ENTRE_EMAILS_MS);
          }

        } catch (err) {
          Logger.log("❌ ERREUR ENVOI ligne " + rowNum + " / " + emailCible + ": " + err.toString());
        }

        continue;
      }

      // ── RELANCE 48H ─────────────────────────────────────────────────────
      if (statut === "CANDIDATURE_ENVOYEE" && dateEnvoi) {
        const diffHeures = (now - dateEnvoi) / (1000 * 60 * 60);

        if (diffHeures < CONFIG.DELAI_RELANCE_H) {
          continue;
        }

        // IMPORTANT : si on relance, on autorise explicitement cette adresse
        // une seconde fois. C'est la seule exception à la règle "pas de
        // candidature initiale deux fois".
        const specialite = detecterSpecialite(intitule, roleCible);

        if (!(specialite in cvCache)) {
          cvCache[specialite] = getCV(intitule, roleCible);
        }

        const cvFile = cvCache[specialite];
        const { titrePoste, body } = genererEmailRelance(
          entreprise, intitule, roleCible
        );
        const sujet = "Nachfassaktion – Bewerbung als " + titrePoste + " – " + CONFIG.NOM;

        try {
          GmailApp.sendEmail(emailCible, sujet, "", {
            htmlBody: body,
            attachments: cvFile ? [cvFile.getAs(MimeType.PDF)] : [],
            name: CONFIG.NOM,
            replyTo: CONFIG.EMAIL,
          });

          sheet.getRange(rowNum, COL.STATUT + 1).setValue("RELANCE_EFFECTUEE");
          sheet.getRange(rowNum, COL.DATE_RELANCE + 1).setValue(new Date());

          compteur++;
          emailsEnvoyesCetteExecution.add(emailCible);

          Logger.log("🔄 RELANCE #" + compteur + "/" + limite + " → " + emailCible);

          if (compteur < limite) {
            Utilities.sleep(CONFIG.DELAI_ENTRE_EMAILS_MS);
          }

        } catch (err) {
          Logger.log("❌ ERREUR RELANCE ligne " + rowNum + " / " + emailCible + ": " + err.toString());
        }
      }
    }

    Logger.log("FIN — " + compteur + " email(s) envoyé(s) / tentatives autorisées: " + limite);

  } catch (error) {
    Logger.log("[TRAITEMENT] Erreur: " + error.toString());
  } finally {
    try { lock.releaseLock(); } catch (_) {}
  }
}



// ─────────────────────────────────────────────────────────────────────────────
// INSTALLATION DU TRIGGER HORAIRE — À APPELER UNE SEULE FOIS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Installe un déclencheur toutes les heures pour traiterAusbildungCandidatures().
 * À appeler UNE SEULE FOIS depuis l'éditeur Apps Script.
 * Supprime les anciens triggers du même nom pour éviter les doublons.
 */
function configurerDeclencheurs() {
  // Supprimer tous les triggers existants sur cette fonction
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === "traiterAusbildungCandidatures") {
      ScriptApp.deleteTrigger(trigger);
      Logger.log("[TRIGGER] Ancien trigger supprimé.");
    }
  });

  // Créer un nouveau trigger horaire
  ScriptApp.newTrigger("traiterAusbildungCandidatures")
    .timeBased()
    .everyHours(1)
    .create();

  Logger.log("✅ Trigger horaire installé : 'traiterAusbildungCandidatures' sera exécuté toutes les heures.");
  Logger.log("   → Jusqu'à 100 envois valides par exécution horaire, dans la limite du quota Gmail.");
}

/**
 * Affiche la liste des triggers actifs (diagnostic).
 */
function diagnosticTriggers() {
  const triggers = ScriptApp.getProjectTriggers();
  Logger.log(`\n[DIAGNOSTIC] ${triggers.length} trigger(s) actif(s) :`);
  triggers.forEach((t, i) => {
    Logger.log(`  ${i + 1}. ${t.getHandlerFunction()} — ${t.getTriggerSource()} — ID: ${t.getUniqueId()}`);
  });
}

/**
 * Test rapide : affiche le CV détecté pour un intitulé donné.
 * Utile pour vérifier le mapping sans envoyer d'email.
 */
function testerMappingCV() {
  const tests = [
    ["Ausbildung Kauffrau Büromanagement bei Siemens", "Kaufmann/-frau für Büromanagement"],
    ["Ausbildungsplatz E-Commerce 2026", "Kauffrau im E-Commerce"],
    ["Kaufmann im Groß- und Außenhandel", "Kaufmann/-frau im Groß- und Außenhandelsmanagement"],
    ["Ausbildung Speditionskaufmann Hamburg", "Kaufmann/-frau für Spedition und Logistikdienstleistung"],
    ["Kauffrau Tourismus und Freizeit München", "Kaufmann/-frau für Tourismus und Freizeit"],
  ];

  Logger.log("\n[TEST MAPPING CV]");
  tests.forEach(([intitule, role]) => {
    const specialite = detecterSpecialite(intitule, role);
    const filename   = CONFIG.CV_MAPPING[specialite];
    const titre      = getTitreAusbildung(specialite);
    Logger.log(`  "${intitule.substring(0, 50)}"  →  [${specialite}] → "${filename}"`);
    Logger.log(`    Titre email: "${titre}"`);
  });
}
