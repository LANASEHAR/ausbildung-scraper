/**
 * ==============================================================================
 * CODE.GS — SYSTÈME AUTOMATISÉ AUSBILDUNG KAUFMANN / KAUFFRAU
 * Google Apps Script pour Google Sheet "Ausbildung applications"
 *
 * FONCTIONS PRINCIPALES :
 *   doPost(e)                        → Webhook INSERT + UPDATE avec déduplication par ID/email
 *   traiterAusbildungCandidatures()  → Envoi emails + relances après 7 jours (max 100/run, quota Gmail)
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
  BATCH_LIMIT:100, DELAI_ENTRE_EMAILS_MS:100, DELAI_RELANCE_H:7*24, MAX_EXECUTION_MS:5*60*1000,
  CV_FOLDER_NAME:"New Bewerbung",
  CV_MAPPING:{
    hotelfachfrau:"Bewerbungsmappe_Hotelfachfrau_Halima_Essaouaf.pdf",
    systemgastronomie:"Bewerbungsmappe_Fachfrau_fuer_Systemgastronomie_Halima_Essaouaf.pdf",
    einzelhandel:"Bewerbungsmappe_Kauffrau_im_Einzelhandel_Halima_Essaouaf.pdf",
    spedition:"Bewerbungsmappe_Kauffrau_fuer_Spedition_und_Logistikdienstleistung_Halima_Essaouaf.pdf",
    handel:"Bewerbungsmappe_Kauffrau_im_Einzelhandel_Halima_Essaouaf.pdf",
    industrie:"Bewerbungsmappe_Industriekauffrau_Halima_Essaouaf.pdf"
  }
};

// Colonnes du Google Sheet (0-indexées)

const COL = {
  DATE_DETECTION:      0,  // A
  STATUT:              1,  // B
  ROLE_CIBLE:          2,  // C
  INTITULE:            3,  // D
  ENTREPRISE:          4,  // E
  LIEU:                5,  // F
  EMAILS_RH:           6,  // G
  SITE_ENTREPRISE:     7,  // H
  SOURCE:              8,  // I
  LIEN:                9,  // J
  ID:                 10,  // K
  DATE_CANDIDATURE:   11,  // L
  DATE_RELANCE:       12,  // M
  NOMBRE_RELANCES:    13,  // N
  CV_UTILISE:         14,  // O
  MESSAGE_ENVOYE:     15,  // P
  DATE_OFFRE:         16,  // Q
  PRIORITE_REGION:    17,  // R
  PRIORITE_AUSBILDUNG:18   // S
};

const REQUIRED_COLUMNS = 19;

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
 * Insère uniquement les nouvelles offres (déduplication par ID colonne K).
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

function doGet(e) {
  // ── MODE GET_CV : le sender SMTP demande le PDF depuis Drive ────────────
  // Le sender Python utilise GET?action=get_cv&specialite=...
  // Retourner le PDF en base64 permet à GitHub Actions de l'attacher au mail.
  if (e && e.parameter && e.parameter.action === "get_cv") {
    const specialite = String(e.parameter.specialite || "").trim().toLowerCase();

    if (!specialite || !CONFIG.CV_MAPPING[specialite]) {
      return ContentService
        .createTextOutput(JSON.stringify({
          status: "error",
          message: "Spécialité inconnue: " + specialite
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    try {
      const cvFile = getCV("", specialite);
      if (!cvFile) {
        return ContentService
          .createTextOutput(JSON.stringify({
            status: "error",
            message: "Aucun CV trouvé pour " + specialite
          }))
          .setMimeType(ContentService.MimeType.JSON);
      }

      const blob = cvFile.getBlob();
      return ContentService
        .createTextOutput(JSON.stringify({
          status: "success",
          action: "get_cv",
          specialite: specialite,
          filename: cvFile.getName(),
          mime_type: blob.getContentType(),
          base64: Utilities.base64Encode(blob.getBytes())
        }))
        .setMimeType(ContentService.MimeType.JSON);

    } catch (error) {
      Logger.log("[GET_CV] Erreur " + specialite + ": " + error.toString());
      return ContentService
        .createTextOutput(JSON.stringify({
          status: "error",
          message: error.toString()
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }

  return ContentService
    .createTextOutput(JSON.stringify({
      status: "ok",
      service: "Ausbildung webhook",
      message: "Webhook actif. Utiliser POST pour envoyer les offres."
    }))
    .setMimeType(ContentService.MimeType.JSON);
}

function ensureTrackingColumns(sheet) {

  if (sheet.getMaxColumns() < REQUIRED_COLUMNS) {
    sheet.insertColumnsAfter(
      sheet.getMaxColumns(),
      REQUIRED_COLUMNS - sheet.getMaxColumns()
    );
  }

  const headers = [
    "date_detection",
    "statut",
    "role_cible",
    "intitule",
    "entreprise",
    "lieu",
    "emails_rh",
    "site_entreprise",
    "source",
    "lien",
    "id",
    "date_candidature",
    "date_relance",
    "nombre_relances",
    "cv_utilise",
    "message_envoye",
    "date_offre",
    "priorite_region",
    "priorite_ausbildung"
  ];

  const headerRange = sheet.getRange(
    1,
    1,
    1,
    REQUIRED_COLUMNS
  );

  const currentHeaders = headerRange.getValues()[0];

  let changed = false;

  for (let i = 0; i < REQUIRED_COLUMNS; i++) {
    if (
      currentHeaders[i] === "" ||
      currentHeaders[i] === null ||
      typeof currentHeaders[i] === "undefined"
    ) {
      currentHeaders[i] = headers[i];
      changed = true;
    }
  }

  if (changed) {
    headerRange.setValues([currentHeaders]);
  }
}


function sortOffers(sheet) {

  const lastRow = sheet.getLastRow();

  if (lastRow <= 2) return;

  const numberOfRows = lastRow - 1;

  if (numberOfRows < 1 || REQUIRED_COLUMNS < 1) {
    return;
  }

  sheet
    .getRange(
      2,
      1,
      numberOfRows,
      REQUIRED_COLUMNS
    )
    .sort([
      // Primary order: newest publication first.
      {
        column: COL.DATE_OFFRE + 1,
        ascending: false
      },
      // Detection timestamp is the fallback when publication date is absent/equal.
      {
        column: COL.DATE_DETECTION + 1,
        ascending: false
      },
      // Region and Ausbildung priority are only tie-breakers.
      {
        column: COL.PRIORITE_REGION + 1,
        ascending: false
      },
      {
        column: COL.PRIORITE_AUSBILDUNG + 1,
        ascending: false
      }
    ]);
}

function doPost(e) {
  // ── MODE GET_CV : le sender SMTP demande le PDF depuis Drive ─────────────
  // Le sender utilise POST pour éviter les 404 intermittents observés sur GET.
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

  if (rawData && rawData.action === "get_cv") {
    const specialite = String(rawData.specialite || "").trim().toLowerCase();

    if (!specialite || !CONFIG.CV_MAPPING[specialite]) {
      return ContentService
        .createTextOutput(JSON.stringify({
          status: "error",
          message: "Spécialité inconnue: " + specialite
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    try {
      const cvFile = getCV("", specialite);
      if (!cvFile) {
        return ContentService
          .createTextOutput(JSON.stringify({
            status: "error",
            message: "Aucun CV trouvé pour " + specialite
          }))
          .setMimeType(ContentService.MimeType.JSON);
      }

      const blob = cvFile.getBlob();
      return ContentService
        .createTextOutput(JSON.stringify({
          status: "success",
          action: "get_cv",
          specialite: specialite,
          filename: cvFile.getName(),
          mime_type: blob.getContentType(),
          base64: Utilities.base64Encode(blob.getBytes())
        }))
        .setMimeType(ContentService.MimeType.JSON);

    } catch (error) {
      Logger.log("[GET_CV_POST] Erreur " + specialite + ": " + error.toString());
      return ContentService
        .createTextOutput(JSON.stringify({
          status: "error",
          message: error.toString()
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }

  // Parse the request BEFORE taking the script lock. JSON parsing does not touch
  // the spreadsheet and therefore should never block other webhook executions.
  let rawData2;
  try {
    rawData2 = rawData;
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({
        status: "error",
        message: "JSON invalide: " + error.toString()
      }))
      .setMimeType(ContentService.MimeType.JSON);
  }
  rawData = rawData2;
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


    // ── MODE GET_PENDING : uniquement des candidatures réellement envoyables ───
    // Le sender SMTP demande un nombre d'e-mails, pas un nombre de lignes.
    // On filtre ici les lignes inutilisables avant de les transmettre :
    // email valide, spécialité reconnue, CV présent, et statut/date compatibles.
    if (rawData && rawData.action === "get_pending") {
      const limit = Math.min(500, Math.max(1, Number(rawData.limit || 80)));
      const items = [];
      const cvCache = {};
      const sentEmails = buildSheetIndexes(allData).sentEmails;
      const seenEmails = new Set();

      for (let i = 1; i < allData.length && items.length < limit; i++) {
        const row = allData[i];
        const statut = String(row[COL.STATUT] || "").trim();
        const email = extractFirstEmail(row[COL.EMAILS_RH] || "");

        if (!email || !isValidEmail(email) || email.includes("..")) continue;
        if (seenEmails.has(email)) continue;

        const intitule = String(row[COL.INTITULE] || "").trim();
        const roleCible = String(row[COL.ROLE_CIBLE] || "").trim();
        const specialite = detecterSpecialite(intitule, roleCible);
        if (!specialite || !(specialite in CONFIG.CV_MAPPING)) continue;

        if (!(specialite in cvCache)) {
          try {
            cvCache[specialite] = getCV(intitule, roleCible);
          } catch (err) {
            cvCache[specialite] = null;
            Logger.log("[GET_PENDING] CV erreur " + specialite + ": " + err.toString());
          }
        }
        if (!cvCache[specialite]) continue;

        let type = "";
        if (statut === "NOUVEAU") {
          // Une candidature initiale ne doit jamais repartir vers une adresse
          // déjà contactée historiquement.
          if (sentEmails.has(email)) continue;
          type = "INITIAL";
        } else if (statut === "CANDIDATURE_ENVOYEE") {
          const dateEnvoi = row[COL.DATE_CANDIDATURE]
            ? new Date(row[COL.DATE_CANDIDATURE])
            : null;
          if (!dateEnvoi) continue;
          const diffHeures = (new Date() - dateEnvoi) / (1000 * 60 * 60);
          if (diffHeures < CONFIG.DELAI_RELANCE_H) continue;
          type = "RELANCE";
        } else {
          continue;
        }

        seenEmails.add(email);
        items.push({
          row_index: i + 1,
          type: type,
          emails_rh: email,
          intitule: intitule,
          role_cible: roleCible,
          entreprise: String(row[COL.ENTREPRISE] || "").trim(),
          specialite: specialite
        });
      }

      return ContentService
        .createTextOutput(JSON.stringify({
          status: "success",
          action: "get_pending",
          returned: items.length,
          requested: limit,
          items: items
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }

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
  job.date_detection || new Date().toISOString(), // A
  job.statut || "NOUVEAU",                       // B
  job.role_cible || "",                          // C
  job.intitule || "",                            // D
  job.entreprise || "",                          // E
  job.lieu || "Deutschland (Allemagne)",        // F
  email || "",                                   // G
  job.site_entreprise || "",                     // H
  job.source || "Scraper Cloud Ausbildung",      // I
  job.lien || "",                                // J
  jobId,                                         // K
  "",                                            // L
  "",                                            // M
  0,                                             // N
  "",                                            // O
  "",                                            // P
  job.date_offre || "",                          // Q
  Number(job.prioritaet_region || 0),            // R
  Number(job.prioritaet_ausbildung || 0)         // S
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
 if(
  t.includes("gross") ||
  t.includes("groß") ||
  t.includes("aussenhandel") ||
  t.includes("außenhandel") ||
  t.includes("grosshandel") ||
  t.includes("großhandel")
) return "handel";

  if(t.includes("industriekauf")) return "industrie";
  return "";
}
function getTitreAusbildung(
  specialite
) {

  return ({

    hotelfachfrau:
      "Hotelfachfrau",

    systemgastronomie:
      "Fachfrau für Systemgastronomie",

    einzelhandel:
      "Kauffrau im Einzelhandel",

    spedition:
      "Kauffrau für Spedition und Logistikdienstleistung",

    handel:
      "Kauffrau im Groß- und Außenhandelsmanagement",

    industrie:
      "Industriekauffrau"

  })[specialite] ||
  "Ausbildungsplatz";
}

function normaliserNomCV(value) {
  return String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\\u0300-\\u036f]/g, "")
    .replace(/\\.pdf$/i, "")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\\s+/g, " ")
    .trim();
}

function distanceLevenshteinCV(a, b) {
  a = String(a || "");
  b = String(b || "");
  const prev = Array(b.length + 1);
  const curr = Array(b.length + 1);

  for (let j = 0; j <= b.length; j++) prev[j] = j;

  for (let i = 1; i <= a.length; i++) {
    curr[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(
        curr[j - 1] + 1,
        prev[j] + 1,
        prev[j - 1] + cost
      );
    }
    for (let j = 0; j <= b.length; j++) prev[j] = curr[j];
  }

  return prev[b.length];
}

function scoreCVFilename(filename, specialite) {
  const name = normaliserNomCV(filename);
  const compact = name.replace(/ /g, "");

  const keywords = {
    hotelfachfrau: [
      ["hotelfachfrau", 100], ["hotelfachmann", 100],
      ["hotelkauffrau", 90], ["hotelkaufmann", 90], ["hotel", 45]
    ],
    systemgastronomie: [
      ["systemgastronomie", 110], ["gastronomie", 55], ["gastro", 35]
    ],
    einzelhandel: [
      ["einzelhandel", 120], ["kauffrau im einzelhandel", 120],
      ["kaufmann im einzelhandel", 120], ["handel", 35]
    ],
    spedition: [
      ["spedition", 120], ["logistikdienstleistung", 115],
      ["logistik", 65], ["speditionskauf", 100]
    ],
    // IMPORTANT: "handel" uses the Einzelhandel CV according to the user's mapping.
    handel: [
      ["einzelhandel", 140], ["kauffrau im einzelhandel", 140],
      ["gross und aussenhandelsmanagement", 130],
      ["grossaussenhandel", 130], ["grosshandel", 100],
      ["aussenhandel", 100], ["handel", 45]
    ],
    industrie: [
      ["industriekauffrau", 125], ["industriekaufmann", 125],
      ["industrie", 50]
    ]
  };

  const candidates = keywords[specialite] || [];
  let score = 0;

  for (const [keyword, weight] of candidates) {
    const k = normaliserNomCV(keyword);
    if (name.includes(k)) {
      score = Math.max(score, weight);
      continue;
    }

    // Tolerates small filename variations/typos such as:
    // "Industriekauffrau" vs "Industriekaufrau", etc.
    const words = k.split(" ");
    for (const word of words) {
      if (word.length < 5) continue;
      if (compact.includes(word)) {
        score = Math.max(score, weight - 10);
        continue;
      }

      const nameWords = name.split(" ");
      for (const nw of nameWords) {
        if (nw.length < 5) continue;
        const maxDistance = word.length >= 12 ? 3 : 2;
        const distance = distanceLevenshteinCV(word, nw);
        if (distance <= maxDistance) {
          score = Math.max(score, weight - (distance * 8) - 15);
        }
      }
    }
  }

  // Prefer a Bewerbungsmappe/application PDF when several files are similar.
  if (/bewerbung|bewerbungsmappe|bewerbungsunterlagen/.test(name)) score += 8;
  if (!/\\.pdf$/i.test(filename)) score -= 20;

  return score;
}

function getCV(intitule, roleCible){
  let specialite = detecterSpecialite(intitule, roleCible);

  // Direct specialty lookup is used by the SMTP get_cv endpoint.
  // This avoids depending on detecterSpecialite() recognizing the bare key
  // "handel", "industrie", etc.
  const directSpecialite = String(roleCible || "").trim().toLowerCase();
  if (!specialite && CONFIG.CV_MAPPING[directSpecialite]) {
    specialite = directSpecialite;
  }

  const filename = CONFIG.CV_MAPPING[specialite];
  if (!specialite || !filename) return null;

  // Cache the Drive folder file list once per Apps Script execution.
  // This avoids scanning Drive separately for every Sheet row.
  if (!getCV._files) {
    getCV._files = [];
    const folders = DriveApp.getFoldersByName(CONFIG.CV_FOLDER_NAME);

    if (!folders.hasNext()) {
      Logger.log("[CV] Dossier introuvable: " + CONFIG.CV_FOLDER_NAME);
      return null;
    }

    const folder = folders.next();
    const files = folder.getFiles();

    while (files.hasNext()) {
      const file = files.next();
      getCV._files.push({
        file: file,
        name: file.getName()
      });
    }

    Logger.log("[CV] Index Drive construit : " + getCV._files.length + " fichier(s).");
  }

  // 1) Exact filename match.
  const exactName = normaliserNomCV(filename);
  for (const entry of getCV._files) {
    if (normaliserNomCV(entry.name) === exactName) {
      Logger.log("[CV] Exact → " + entry.name + " pour " + specialite);
      return entry.file;
    }
  }

  // 2) Fuzzy keyword match.
  // Never returns a CV from an unrelated specialty: the score must reach
  // a strong specialty-specific threshold.
  let best = null;
  let bestScore = 0;

  for (const entry of getCV._files) {
    const score = scoreCVFilename(entry.name, specialite);
    if (score > bestScore) {
      bestScore = score;
      best = entry;
    }
  }

  if (best && bestScore >= 55) {
    Logger.log("[CV] Fuzzy → " + best.name + " pour " + specialite + " (score " + bestScore + ")");
    return best.file;
  }

  Logger.log("[CV] Aucun CV suffisamment proche pour " + specialite + " (meilleur score " + bestScore + ")");
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
function genererEmailCandidature(
  entreprise,
  intitule,
  roleCible
) {

  const specialite =
    detecterSpecialite(
      intitule,
      roleCible
    );


  const titrePoste =
    getTitreAusbildung(
      specialite
    );


  /*
   * Si le nom de l'entreprise est connu,
   * on le mentionne naturellement.
   *
   * Si l'entreprise est absente/inconnue,
   * aucune mention artificielle n'est ajoutée.
   */
  const entrepriseConnue =
    entreprise &&
    entreprise.trim() &&
    entreprise.trim() !==
      "Unternehmen Deutschland";


  const introduction =
    entrepriseConnue

      ?

      `mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong>.`

      :

      `mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong>.`;


  /*
   * EMAILS COURTS ET SPÉCIFIQUES À CHAQUE AUSBILDUNG
   *
   * Chaque version utilise les éléments réellement
   * pertinents du CV correspondant.
   */
  const motivation = ({

    /* =====================================================
       HOTELFACHFRAU
       ===================================================== */

    hotelfachfrau:

      `Die Hotellerie ist mir bereits aus meiner beruflichen Erfahrung vertraut. Bei HBX Group / Hotelbeds betreute ich ein internationales B2B-Kundenportfolio im Bereich Hotellerie und Travel im Nahen Osten und arbeitete täglich mit Geschäftspartnern auf Arabisch, Französisch und Englisch. Insgesamt bringe ich über fünf Jahre Erfahrung in Kundenbetreuung, Vertrieb und kaufmännischen Abläufen mit. Diese Erfahrung möchte ich nun mit einer Ausbildung in Deutschland und einem anerkannten IHK-Abschluss weiterentwickeln.`,


    /* =====================================================
       SYSTEMGASTRONOMIE
       ===================================================== */

    systemgastronomie:

      `Auch wenn mein bisheriger beruflicher Weg nicht direkt aus der Gastronomie kommt, bringe ich über fünf Jahre Erfahrung im Kundenservice, Vertrieb und in strukturierten Arbeitsabläufen mit. Als Top-Verkäuferin konnte ich bereits meine Stärke in Kundenkommunikation und Beratung unter Beweis stellen. Diese Erfahrung möchte ich nun in die Systemgastronomie einbringen und die professionellen Abläufe in Deutschland von Grund auf erlernen.`,


    /* =====================================================
       EINZELHANDEL
       ===================================================== */

    einzelhandel:

      `Kundenberatung und Verkauf begleiten mich seit mehreren Jahren. In über fünf Jahren Berufserfahrung habe ich im B2B- und B2C-Vertrieb sowie im Kundenservice gearbeitet und wurde bei Umanis Intermediation aufgrund meiner Beratungsqualität und Abschlussstärke als Top-Verkäuferin ausgezeichnet. Heute gehören außerdem Bestandsüberwachung, Auftragsabwicklung und kaufmännische Aufgaben zu meinem Arbeitsalltag. Diese Erfahrung möchte ich nun gezielt mit einer deutschen Ausbildung und einem anerkannten IHK-Abschluss verbinden.`,


    /* =====================================================
       SPEDITION / LOGISTIK
       ===================================================== */

    spedition:

      `Logistik und Koordination sind mir bereits aus meiner Berufserfahrung vertraut. Bei Helpdesk ForYou koordinierte ich die Einsatzplanung von über 100 Fahrern und Mitarbeitenden und verfolgte Touren, Termine und Wartungen. Heute arbeite ich bei Atmlo Chem / EasyChemicalStock mit Beschaffung, Logistik, Bestandsüberwachung, Auftragsabwicklung und Lieferanten. Insgesamt bringe ich über fünf Jahre kaufmännische Berufserfahrung mit, die ich nun gezielt durch eine Ausbildung und einen anerkannten IHK-Abschluss erweitern möchte.`,


    /* =====================================================
       GROSS- UND AUSSENHANDEL
       ===================================================== */

    handel:

      `Ich bringe über fünf Jahre Berufserfahrung in kaufmännischen Bereichen, Kundenbetreuung und Vertrieb mit. In meiner aktuellen Tätigkeit arbeite ich unter anderem mit Beschaffung, Lieferanten, Bestandsüberwachung und Auftragsabwicklung sowie mit Excel und Sage. Zuvor betreute ich bei HBX Group / Hotelbeds internationale B2B-Geschäftspartner. Diese Erfahrung möchte ich nun mit einer fundierten Ausbildung und einem anerkannten IHK-Abschluss in Deutschland verbinden.`,


    /* =====================================================
       INDUSTRIE
       ===================================================== */

    industrie:

      `Durch über fünf Jahre Berufserfahrung bringe ich bereits praktische Kenntnisse in kaufmännischer Organisation, Beschaffung, Auftragsabwicklung und Kundenbetreuung mit. Aktuell arbeite ich mit Lieferanten, Beständen, Rechnungen, Excel und dem ERP-System Sage. Ich möchte diese Praxiserfahrung nun mit den kaufmännischen Prozessen eines deutschen Unternehmens verbinden und dabei einen anerkannten IHK-Abschluss erwerben.`

  })[specialite] ||

    `Ich bringe über fünf Jahre Berufserfahrung in kaufmännischen Bereichen, Kundenbetreuung und strukturierten Arbeitsprozessen mit. Diese Erfahrung möchte ich nun gezielt durch eine fundierte Ausbildung in Deutschland erweitern.`;


  /* =======================================================
     CORPS DU MAIL
     ======================================================= */

  const body = `

<p>Sehr geehrte Damen und Herren,</p>

<p>
${introduction}
</p>

<p>
${motivation}
</p>

<p>
Deutsch B1 habe ich abgeschlossen und bereite mich aktuell auf B2 vor.
Meine vollständigen Bewerbungsunterlagen finden Sie im Anhang.
</p>

<p>
Über die Gelegenheit per Videogespräch vorzustellen,
würde ich mich sehr freuen.
</p>

<p>
Mit freundlichen Grüßen
</p>

${getSignatureHTML()}

`.trim();


  return {
    titrePoste,
    body
  };
}

function genererEmailRelance(entreprise, intitule, roleCible) {
  const specialite = detecterSpecialite(intitule, roleCible);
  const titrePoste = getTitreAusbildung(specialite);

  const body = `
<p>Sehr geehrte Damen und Herren,</p>

<p>vor zwei Tagen habe ich Ihnen meine Bewerbung für einen Ausbildungsplatz als <strong>${titrePoste}</strong> geschickt. Ich wollte mich kurz erkundigen, ob meine Unterlagen gut bei Ihnen angekommen sind.</p>

<p>Ich bin weiterhin sehr an der Ausbildung interessiert und sende Ihnen meinen Lebenslauf vorsichtshalber noch einmal im Anhang.</p>

<p>Falls Sie noch weitere Unterlagen oder Informationen benötigen, lasse ich Ihnen diese gerne zukommen. Für ein kurzes Gespräch stehe ich Ihnen jederzeit gerne zur Verfügung.</p>

<p>Vielen Dank für Ihre Zeit. Ich freue mich auf Ihre Rückmeldung.</p>

<p>Mit freundlichen Grüßen</p>
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
    // Le quota Apps Script est la vraie limite : on ne tente jamais plus
    // de messages que le nombre de destinataires encore disponible aujourd'hui.
    const quotaRestant = MailApp.getRemainingDailyQuota();
    if (quotaRestant <= 0) {
      Logger.log("⛔ QUOTA GMAIL APPS SCRIPT ÉPUISÉ — aucun envoi tenté. Le prochain trigger réessaiera.");
      return;
    }
    const limite = Math.min(CONFIG.BATCH_LIMIT, quotaRestant);
    Logger.log("📨 Quota restant détecté : " + quotaRestant + " | limite de ce run : " + limite);
    const debutExecution = Date.now();
    let compteur = 0;

    // Historique PERSISTANT : protège contre un second envoi au même email
    // même si la ligne change de statut ou si un nouveau job apparaît.
    const { sentEmails } = buildSheetIndexes(data);

    // Une seule candidature par email pendant cette exécution également.
    const emailsEnvoyesCetteExecution = new Set();

    // Cache des CV pour éviter de relire Drive 30 fois.
    const cvCache = {};
    const pendingStatusUpdates = [];
    const pendingDateUpdates = [];

    const rowIndexes = [];
    for (let i = 1; i < data.length; i++) {
      const status = String(data[i][COL.STATUT] || "").trim();
      if (status === "NOUVEAU") rowIndexes.push(i);
    }
    for (let i = 1; i < data.length; i++) {
      const status = String(data[i][COL.STATUT] || "").trim();
      if (status === "CANDIDATURE_ENVOYEE") rowIndexes.push(i);
    }

    // Nouvelles candidatures d'abord.
    // Pour les nouvelles offres : plus récente → plus ancienne.
    // Les priorités région/Ausbildung servent seulement en cas d'égalité.
    // Les relances viennent ensuite, avec les plus anciennes candidatures à relancer en premier.
    rowIndexes.sort((a, b) => {
      const statusA = String(data[a][COL.STATUT] || "").trim();
      const statusB = String(data[b][COL.STATUT] || "").trim();

      if (statusA !== statusB) {
        return statusA === "NOUVEAU" ? -1 : 1;
      }

      if (statusA === "NOUVEAU") {
        const dateB = new Date(data[b][COL.DATE_OFFRE] || 0).getTime() || 0;
        const dateA = new Date(data[a][COL.DATE_OFFRE] || 0).getTime() || 0;
        if (dateB !== dateA) return dateB - dateA;

        const regionDiff =
          Number(data[b][COL.PRIORITE_REGION] || 0) -
          Number(data[a][COL.PRIORITE_REGION] || 0);
        if (regionDiff !== 0) return regionDiff;

        return Number(data[b][COL.PRIORITE_AUSBILDUNG] || 0) -
               Number(data[a][COL.PRIORITE_AUSBILDUNG] || 0);
      }

      const sentB = new Date(data[b][COL.DATE_CANDIDATURE] || 0).getTime() || 0;
      const sentA = new Date(data[a][COL.DATE_CANDIDATURE] || 0).getTime() || 0;
      return sentA - sentB;
    });

    for (const i of rowIndexes) {
      if (Date.now() - debutExecution >= CONFIG.MAX_EXECUTION_MS) {
        Logger.log("[TIME] Arrêt propre avant la limite Apps Script; le prochain trigger reprendra.");
        break;
      }
      if (compteur >= limite) break;
      const row = data[i];

      const statut = String(row[COL.STATUT] || "").trim();
      const roleCible = String(row[COL.ROLE_CIBLE] || "").trim();
      const intitule = String(row[COL.INTITULE] || "").trim();
      const entreprise = String(row[COL.ENTREPRISE] || "").trim() || "Unternehmen Deutschland";
      const emailCible = extractFirstEmail(row[COL.EMAILS_RH] || "");
      const dateEnvoi = row[COL.DATE_CANDIDATURE] ? new Date(row[COL.DATE_CANDIDATURE]) : null;
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

          const dateEnvoiNow = new Date();
          data[i][COL.STATUT] = "CANDIDATURE_ENVOYEE";
          data[i][COL.DATE_CANDIDATURE] = dateEnvoiNow;
          pendingStatusUpdates.push([rowNum, "CANDIDATURE_ENVOYEE"]);
          pendingDateUpdates.push([rowNum, dateEnvoiNow]);

          compteur++;
          emailsEnvoyesCetteExecution.add(emailCible);
          sentEmails.add(emailCible);

          Logger.log("✅ ENVOI #" + compteur + "/" + limite + " → " + emailCible);

          if (compteur < limite && CONFIG.DELAI_ENTRE_EMAILS_MS > 0) {
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

          const dateRelanceNow = new Date();
          data[i][COL.STATUT] = "RELANCE_EFFECTUEE";
          data[i][COL.DATE_RELANCE] = dateRelanceNow;
          pendingStatusUpdates.push([rowNum, "RELANCE_EFFECTUEE"]);
          pendingDateUpdates.push([rowNum, dateRelanceNow]);

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

    // Écritures Sheet regroupées en fin de run pour réduire fortement les accès API.
    if (pendingStatusUpdates.length) {
      const statusValues = data.slice(1).map(row => [row[COL.STATUT] || ""]);
      const dateCandidatureValues = data.slice(1).map(row => [row[COL.DATE_CANDIDATURE] || ""]);
      const dateRelanceValues = data.slice(1).map(row => [row[COL.DATE_RELANCE] || ""]);

      for (const [rowNum, status] of pendingStatusUpdates) {
        statusValues[rowNum - 2] = [status];
      }
      for (const [rowNum, dateValue] of pendingDateUpdates) {
        const idx = rowNum - 2;
        const status = String(data[rowNum - 1][COL.STATUT] || "").trim();
        if (status === "CANDIDATURE_ENVOYEE") {
          dateCandidatureValues[idx] = [dateValue];
        } else if (status === "RELANCE_EFFECTUEE") {
          dateRelanceValues[idx] = [dateValue];
        }
      }

      sheet.getRange(2, COL.STATUT + 1, statusValues.length, 1).setValues(statusValues);
      sheet.getRange(2, COL.DATE_CANDIDATURE + 1, dateCandidatureValues.length, 1).setValues(dateCandidatureValues);
      sheet.getRange(2, COL.DATE_RELANCE + 1, dateRelanceValues.length, 1).setValues(dateRelanceValues);
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
  Logger.log("   → Jusqu'à 100 envois valides par exécution, plafonnés par le quota Apps Script restant.");
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