/**
 * ==============================================================================
 * CODE.GS — SYSTÈME AUTOMATISÉ AUSBILDUNG KAUFMANN / KAUFFRAU
 * Google Apps Script pour Google Sheet "Ausbildung applications"
 *
 * FONCTIONS PRINCIPALES :
 *   doPost(e)                        → Webhook INSERT + UPDATE avec déduplication ID/email
 *   traiterAusbildungCandidatures()  → Envoi emails + relances 48h (max 30/run)
 *   getCV(intitule, roleCible)        → Mapping 5 CVs par spécialité Kauffrau
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

const CONFIG = {
  // Identité complète pour les emails
  NOM:         "Halima Essaouaf",
  EMAIL:       "essaouafhalima@gmail.com",
  TEL:         "+33 X XX XX XX XX",     // ← À compléter
  LINKEDIN:    "linkedin.com/in/halima-essaouaf",  // ← À compléter

  // Nom de l'onglet dans le Google Sheet (fallback sur onglet actif)
  NOM_ONGLET:  "Ausbildung",

  // Maximum de candidatures réussies par exécution.
  // Le trigger est horaire : objectif = jusqu'à 30 emails valides / heure.
  BATCH_LIMIT: 30,

  // Pause entre deux envois pour éviter un burst trop agressif.
  DELAI_ENTRE_EMAILS_MS: 1000,

  // Délai de relance en heures
  DELAI_RELANCE_H: 48,

  // Mapping des CV sur Google Drive.
  // Les 2 fichiers fournis existent et correspondent exactement aux noms ci-dessous.
  // Pour Lagerlogistik et Einzelhandel, le script REFUSE d'envoyer avec un mauvais CV :
  // il attend le CV spécifique correspondant.
  CV_MAPPING: {
    "buero":          "Bewerbung Kauffrau Buromanagemenet Halima Essaouaf.pdf",
    "ecommerce":      "Bewerbung Kauffrau ECommerce Halima Essaouaf.pdf",
    "handel":         "Bewerbung Kauffrau GrossAussenhandel Halima Essaouaf.pdf",
    "spedition":      "Bewerbung Kauffrau Spedition Logistik Halima Essaouaf.pdf",
    "lagerlogistik":  "Bewerbung Fachkraft Lagerlogistik Halima Essaouaf.pdf",
    "einzelhandel":   "Bewerbung Verkäuferin Einzelhandel Halima Essaouaf.pdf",
    "tourismus":      "Bewerbung Kauffrau Tourismus Freizeit Halima Essaouaf.pdf",
  },
};

// Colonnes du Google Sheet (0-indexées)
const COL = {
  DATE_DETECTION: 0,  // A
  STATUT:         1,  // B
  ROLE_CIBLE:     2,  // C
  INTITULE:       3,  // D
  ENTREPRISE:     4,  // E
  LIEU:           5,  // F
  EMAILS_RH:      6,  // G
  SOURCE:         7,  // H
  LIEN:           8,  // I
  ID:             9,  // J
  DATE_ENVOI:    10,  // K
  DATE_RELANCE:  11,  // L
};


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

        if (existingEmails.has(newEmail) && newEmail !== currentEmail) {
          duplicateEmails++;
          continue;
        }

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
        continue;
      }

      if (existingEmails.has(email)) {
        duplicateEmails++;
        continue;
      }

      rowsToAppend.push([
        job.date_detection || new Date().toISOString(),
        job.statut || "NOUVEAU",
        job.role_cible || "",
        job.intitule || "",
        job.entreprise || "",
        job.lieu || "Deutschland (Allemagne)",
        email,
        job.source || "Scraper Cloud Ausbildung",
        job.lien || "",
        jobId,
        "",
        "",
      ]);

      // Prevent duplicates inside the SAME request.
      existingIds.add(jobId);
      existingEmails.add(email);
    }

    // One setValues() call instead of appendRow() for every job.
    if (rowsToAppend.length) {
      const firstRow = sheet.getLastRow() + 1;
      sheet.getRange(firstRow, 1, rowsToAppend.length, rowsToAppend[0].length)
        .setValues(rowsToAppend);
    }

    Logger.log("[INSERT] " + rowsToAppend.length + " ajoutées | IDs doublons: " + duplicateIds +
      " | emails doublons: " + duplicateEmails + " | emails invalides/absents: " + invalidEmails);

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
function detecterSpecialite(intitule, roleCible) {
  const text = ((intitule || "") + " " + (roleCible || ""))
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");

  // Priorité aux catégories les plus spécifiques.
  if (
    text.includes("lagerlogistik") ||
    text.includes("fachkraft fur lagerlogistik") ||
    (text.includes("lager") && text.includes("logistik"))
  ) {
    return "lagerlogistik";
  }

  if (
    text.includes("einzelhandel") ||
    text.includes("verkaufer") ||
    text.includes("verkaeufer") ||
    text.includes("verkaeuferin")
  ) {
    return "einzelhandel";
  }

  if (
    text.includes("spedition") ||
    text.includes("logistikdienstleistung") ||
    text.includes("speditionskaufmann") ||
    text.includes("speditionskauffrau")
  ) {
    return "spedition";
  }

  if (
    text.includes("gross") ||
    text.includes("aussenhandel") ||
    text.includes("außenhandel") ||
    text.includes("grosshandel") ||
    text.includes("großhandel") ||
    text.includes("gross- und aussenhandelsmanagement") ||
    text.includes("groß- und außenhandelsmanagement")
  ) {
    return "handel";
  }

  if (text.includes("e-commerce") || text.includes("ecommerce") || text.includes("e commerce")) {
    return "ecommerce";
  }

  if (
    text.includes("tourismus") ||
    text.includes("freizeit") ||
    text.includes("reisebuero") ||
    text.includes("reiseverkehr")
  ) {
    return "tourismus";
  }

  return "buero";
}


/**
 * Titre formel de l'Ausbildung selon la spécialité détectée.
 */
function getTitreAusbildung(specialite, intitule) {
  const text = String(intitule || "").toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");

  if (specialite === "einzelhandel" && text.includes("verkaufer")) {
    return "Verkäuferin";
  }

  const titres = {
    "buero":         "Kauffrau für Büromanagement",
    "ecommerce":     "Kauffrau im E-Commerce",
    "handel":        "Kauffrau im Groß- und Außenhandelsmanagement",
    "spedition":     "Kauffrau für Spedition und Logistikdienstleistung",
    "lagerlogistik": "Fachkraft für Lagerlogistik",
    "einzelhandel":  "Kauffrau im Einzelhandel",
    "tourismus":     "Kauffrau für Tourismus und Freizeit",
  };
  return titres[specialite] || "Kauffrau für Büromanagement";
}


/**
 * Recherche et retourne le fichier CV PDF correct depuis Google Drive.
 * Utilise le mapping CONFIG.CV_MAPPING avec les noms de fichiers exacts confirmés.
 * Fallback sur le CV Büromanagement si le CV spécifique est introuvable.
 */
function getCV(intitule, roleCible) {
  const specialite = detecterSpecialite(intitule, roleCible);
  const filename = CONFIG.CV_MAPPING[specialite];

  if (!filename) {
    Logger.log("[CV] Aucun mapping pour la spécialité: " + specialite);
    return null;
  }

  Logger.log("[CV] Spécialité: " + specialite + " → " + filename);

  const files = DriveApp.getFilesByName(filename);
  if (files.hasNext()) {
    return files.next();
  }

  // IMPORTANT : aucun fallback vers un autre métier.
  // On préfère ne pas envoyer plutôt que d'envoyer le mauvais CV.
  Logger.log("[CV] MANQUANT : " + filename + " → candidature non envoyée.");
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
function genererEmailCandidature(entreprise, intitule, roleCible) {
  const specialite = detecterSpecialite(intitule, roleCible);
  const titrePoste = getTitreAusbildung(specialite, intitule);
  const entrepriseDisplay = (entreprise && entreprise !== "Unternehmen Deutschland") ? entreprise : null;

  const salutation = entrepriseDisplay
    ? `Sehr geehrte Damen und Herren des Unternehmens ${entrepriseDisplay},`
    : "Sehr geehrte Damen und Herren,";

  // Corps de motivation adapté par spécialité
  const motivationParSpecialite = {
    "buero": `
mit großem Interesse und Begeisterung bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Ich bin eine organisierte, engagierte und kommunikationsstarke Persönlichkeit, die Freude daran hat, Abläufe zu optimieren, Aufgaben strukturiert zu erledigen und ein verlässlicher Teil eines Teams zu sein. Die Ausbildung zum Kaufmann/-frau für Büromanagement entspricht genau meinen Stärken und Interessen: der Umgang mit Kunden, das Koordinieren von Aufgaben und die Arbeit in einem modernen Büroumfeld.`,

    "ecommerce": `
mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Der digitale Handel fasziniert mich sehr: von der Produktpräsentation über den Onlineshop bis hin zur Kundenkommunikation. Ich bin technikaffin, lernbereit und bringe bereits erste Kenntnisse im Online-Marketing und in der Arbeit mit digitalen Tools mit. Ich möchte mein Wissen in Ihrem Unternehmen vertiefen und aktiv zu Ihrem Wachstum im E-Commerce beitragen.`,

    "handel": `
mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Die Welt des nationalen und internationalen Handels fasziniert mich sehr – insbesondere die Arbeit mit Lieferanten, die Koordination von Warenflüssen und die Kommunikation auf internationaler Ebene. Ich bin kommunikativ, zahlenaffin und spreche mehrere Sprachen, was mir im Groß- und Außenhandel einen echten Vorteil verschafft.`,

    "spedition": `
mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Logistik und die Organisation von Transporten begeistern mich: Güter effizient von A nach B zu bringen, Lieferketten zu koordinieren und mit nationalen wie internationalen Partnern zusammenzuarbeiten – das ist genau das Berufsfeld, in dem ich mich langfristig entwickeln möchte. Ich bin belastbar, strukturiert und teamorientiert.`,

    "tourismus": `
mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Die Tourismusbranche begeistert mich durch ihre Vielseitigkeit und den täglichen Kontakt mit Menschen aus aller Welt. Ich bin serviceorientiert, kommunikativ, spreche mehrere Sprachen und bringe echte Freude daran mit, unvergessliche Reiseerlebnisse für Kunden zu gestalten.`,
  };

  const motivation = motivationParSpecialite[specialite] || motivationParSpecialite["buero"];

  const body = `
${salutation}
${motivation}

Anbei finden Sie meine vollständigen Bewerbungsunterlagen (Lebenslauf) als PDF-Datei. Ich freue mich sehr auf die Möglichkeit, mich in einem persönlichen Gespräch – telefonisch oder per Video-Call – vorzustellen und mehr über Ihre Ausbildung zu erfahren.

Über eine positive Rückmeldung würde ich mich sehr freuen.

Mit freundlichen Grüßen
${getSignatureHTML()}
`.trim();

  return { titrePoste, body };
}

/**
 * Génère l'email de relance 48h en allemand professionnel.
 */
function genererEmailRelance(entreprise, intitule, roleCible) {
  const specialite = detecterSpecialite(intitule, roleCible);
  const titrePoste = getTitreAusbildung(specialite);
  const entrepriseDisplay = (entreprise && entreprise !== "Unternehmen Deutschland") ? entreprise : null;

  const salutation = entrepriseDisplay
    ? `Sehr geehrte Damen und Herren des Unternehmens ${entrepriseDisplay},`
    : "Sehr geehrte Damen und Herren,";

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

    // Quota réel restant. Google impose des quotas quotidiens variables
    // selon le type de compte : on ne tente jamais de dépasser le quota.
    const quotaRestant = MailApp.getRemainingDailyQuota();
    if (quotaRestant <= 0) {
      Logger.log("🛑 Plus aucun quota email aujourd'hui.");
      return;
    }

    const limite = Math.min(CONFIG.BATCH_LIMIT, quotaRestant);
    let compteur = 0;

    // Historique PERSISTANT : protège contre un second envoi au même email
    // même si la ligne change de statut ou si un nouveau job apparaît.
    const { sentEmails } = buildSheetIndexes(data);

    // Une seule candidature par email pendant cette exécution également.
    const emailsEnvoyesCetteExecution = new Set();

    // Cache des CV pour éviter de relire Drive 30 fois.
    const cvCache = {};

    for (let i = 1; i < data.length && compteur < limite; i++) {
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

    Logger.log("FIN — " + compteur + " email(s) envoyé(s), limite de cette heure: " + limite);

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
  Logger.log("   → Jusqu'à 30 envois valides par exécution horaire, dans la limite du quota Gmail.");
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
