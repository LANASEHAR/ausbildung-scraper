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
  BATCH_LIMIT:95, DELAI_ENTRE_EMAILS_MS:20000, DELAI_RELANCE_H:7*24, MAX_EXECUTION_MS:5*60*1000,
  CV_FOLDER_NAME:"New Bewerbung",
  CV_MAPPING:{
    fachverkaeufer_lebensmittel:"Bewerbungsmappe_Fachverkaeufer_Lebensmittelhandwerk_Halima_Essaouaf.pdf",
    einzelhandel:"Bewerbungsmappe_Kauffrau_im_Einzelhandel_Halima_Essaouaf.pdf",
    koch:"Bewerbungsmappe_Hotelfachfrau_Halima_Essaouaf.pdf",
    hotelfachfrau:"Bewerbungsmappe_Hotelfachfrau_Halima_Essaouaf.pdf",
    hotelmanagement:"Bewerbungsmappe_Hotelfachfrau_Halima_Essaouaf.pdf",
    baecker:"Bewerbungsmappe_Hotelfachfrau_Halima_Essaouaf.pdf",
    systemgastronomie:"Bewerbungsmappe_Fachfrau_fuer_Systemgastronomie_Halima_Essaouaf.pdf",
    spedition:"Bewerbungsmappe_Kauffrau_fuer_Spedition_und_Logistikdienstleistung_Halima_Essaouaf.pdf",
    handel:"Bewerbungsmappe_Kauffrau_im_Einzelhandel_Halima_Essaouaf.pdf",
    industrie:"Bewerbungsmappe_Industriekauffrau_Halima_Essaouaf.pdf",
    buero:"Bewerbungsmappe_Kauffrau_fuer_Bueromanagement_Halima_Essaouaf.pdf",
    tourismus:"Bewerbungsmappe_Hotelfachfrau_Halima_Essaouaf.pdf"
  }
};

const REMOTE_CONFIG = {
  SHEET_NAME: "Remote Travel Hospitality",
  NOM: "Halima Essaouaf",
  EMAIL: "essaouafhalima@gmail.com",
  TEL: "+212619968131",
  LINKEDIN: "linkedin.com/in/halima-essaouaf-1b4b81202",
  LOCATION: "Casablanca, Morocco",
  LANGUAGES: "Arabic (Native), French (C1), English (C1), German (B2 in progress), Spanish (A2 in progress)",
  PROFILE: "B2B Customer Success, Account Management, Sales, Travel-Tech, Operations, Customer Support, Commercial Administration"
};

const REMOTE_HEADERS = ["date_detection","statut","poste","entreprise","lieu","type_remote","email","site_entreprise","source","lien_offre","linkedin_url","id","fit_score","fit_reason","type_poste","salaire","langues_requises","acces_depuis_maroc","contact_status","message_envoye"];


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
  if (!str || str.includes("non détecté") || str.includes("postuler via lien")) return false;
  const match = str.match(/^([a-z0-9][a-z0-9._%+\-]{0,62}[a-z0-9])@([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/i);
  if (!match) return false;
  return !match[1].includes("..");
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
  return pad(d.getDate()) + "." + pad(d.getMonth() + 1) + "." + d.getFullYear() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
}

// ─────────────────────────────────────────────────────────────────────────────
// QUOTA SMTP — PLAFOND PERSISTANT SUR 24 H
// ─────────────────────────────────────────────────────────────────────────────
// Le compteur est stocké dans PropertiesService, pas dans le runner GitHub.
// Il survit donc aux runs et applique une vraie fenêtre glissante de 24 h.
// Les réservations temporaires empêchent un crash entre SMTP et mark_sent
// de permettre au run suivant de dépasser le plafond.
// ─────────────────────────────────────────────────────────────────────────────

const SMTP_ROLLING_24H_LIMIT = 100;
const SMTP_RESERVATION_TTL_MS = 30 * 60 * 1000;
const SMTP_QUOTA_PROPERTY = "SMTP_ROLLING_24H_STATE_V1";

function loadSmtpQuotaState_() {
  const props = PropertiesService.getScriptProperties();
  let state;
  try {
    state = JSON.parse(props.getProperty(SMTP_QUOTA_PROPERTY) || "{}");
  } catch (error) {
    state = {};
  }
  if (!Array.isArray(state.sent)) state.sent = [];
  if (!Array.isArray(state.reservations)) state.reservations = [];
  const now = Date.now();
  const cutoff = now - 24 * 60 * 60 * 1000;
  const reservationCutoff = now - SMTP_RESERVATION_TTL_MS;
  state.sent = state.sent.filter(ts => Number(ts) > cutoff);
  state.reservations = state.reservations.filter(r => r && r.token && Number(r.ts) > reservationCutoff);
  return state;
}

function saveSmtpQuotaState_(state) {
  PropertiesService.getScriptProperties().setProperty(SMTP_QUOTA_PROPERTY, JSON.stringify(state));
}

function quotaResponse_(state, allowed, token) {
  return {
    status: "success",
    allowed: allowed,
    token: token || "",
    sent_24h: state.sent.length,
    reserved: state.reservations.length,
    remaining: Math.max(0, SMTP_ROLLING_24H_LIMIT - state.sent.length - state.reservations.length),
    limit_24h: SMTP_ROLLING_24H_LIMIT
  };
}

function reserveSmtpSend_() {
  const state = loadSmtpQuotaState_();
  if (state.sent.length + state.reservations.length >= SMTP_ROLLING_24H_LIMIT) {
    saveSmtpQuotaState_(state);
    return quotaResponse_(state, false, "");
  }
  const token = Utilities.getUuid();
  state.reservations.push({ token: token, ts: Date.now() });
  saveSmtpQuotaState_(state);
  return quotaResponse_(state, true, token);
}

function releaseSmtpSend_(token) {
  const state = loadSmtpQuotaState_();
  if (token) state.reservations = state.reservations.filter(r => r.token !== token);
  saveSmtpQuotaState_(state);
  return quotaResponse_(state, true, "");
}

function finalizeSmtpSend_(token) {
  const state = loadSmtpQuotaState_();
  let finalized = false;
  if (token) {
    const before = state.reservations.length;
    state.reservations = state.reservations.filter(r => r.token !== token);
    finalized = state.reservations.length !== before;
    if (finalized) state.sent.push(Date.now());
  } else {
    state.sent.push(Date.now());
    finalized = true;
  }
  saveSmtpQuotaState_(state);
  return finalized;
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


function getRemoteSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(REMOTE_CONFIG.SHEET_NAME);
  if (!sheet) sheet = ss.insertSheet(REMOTE_CONFIG.SHEET_NAME);
  if (sheet.getMaxColumns() < REMOTE_HEADERS.length) sheet.insertColumnsAfter(sheet.getMaxColumns(), REMOTE_HEADERS.length - sheet.getMaxColumns());
  const range = sheet.getRange(1, 1, 1, REMOTE_HEADERS.length);
  const current = range.getValues()[0];
  let changed = false;
  for (let i = 0; i < REMOTE_HEADERS.length; i++) if (!current[i]) { current[i] = REMOTE_HEADERS[i]; changed = true; }
  if (changed) range.setValues([current]);
  return sheet;
}

function remoteFitScore_(job) {
  const t = String([job.poste,job.role_cible,job.intitule,job.entreprise,job.fit_reason].join(" ")).toLowerCase();
  let score = 0;
  const rules = [
    [/account manager|account management|customer success|customer support|customer experience/,24],
    [/travel|travel-tech|hospitality|hotel|tourism|ota|booking|travel agent|tour operator|dmc/,22],
    [/b2b|business development|sales|commercial|partnership|partner success|supplier/,18],
    [/operations|reservation|reservations|back office|onboarding|activation/,14],
    [/french|français|francais/,8],[/arabic|arabe/,8],[/english|anglais/,6],[/german|allemand|deutsch/,4],
    [/remote|work from anywhere|distributed|home-based|remote first|morocco|maroc|mena|emea/,12]
  ];
  rules.forEach(function(r){ if(r[0].test(t)) score += r[1]; });
  return Math.min(100,score);
}

function generateRemoteEmail_(job) {
  const company=String(job.entreprise||"your company").trim();
  const title=String(job.poste||job.intitule||"a remote position").trim();
  const hasJob=!!String(job.lien_offre||job.lien||"").trim() || !!job.poste;
  const subject=hasJob ? "Application – "+title+" – Halima Essaouaf | Travel & B2B Customer Success" : "Spontaneous application – Travel & Hospitality – Halima Essaouaf";
  const opening=hasJob ? "I am reaching out regarding the "+title+" opportunity at "+company+"." : "I am reaching out to explore remote opportunities at "+company+" in customer success, account management, sales or operations.";
  const body=[
    "Dear Hiring Team,","",opening,"",
    "My background combines international travel-tech account management, B2B customer success, sales and operations. At HBX Group (Hotelbeds / Bedsonline), I managed a portfolio of 600+ B2B travel-agency accounts across the Middle East, handling onboarding, activation, relationship management, retention, upselling and multilingual client communication in Arabic, French and English.",
    "",
    "I also bring experience in B2B FinTech customer support, commercial administration, supplier communication, logistics coordination, e-commerce and CRM-based follow-up. This allows me to contribute across customer-facing, commercial and operational responsibilities.",
    "",
    "I am based in Casablanca, Morocco and am specifically looking for a remote role that can be performed from Morocco. I work professionally in Arabic, French and English, with German at B2 level in progress.",
    "",
    "I would be glad to discuss how my travel-tech experience and multilingual B2B background could support your team.","",
    "Kind regards,","Halima Essaouaf","Casablanca, Morocco","+212 619 968 131","essaouafhalima@gmail.com","linkedin.com/in/halima-essaouaf-1b4b81202"
  ].join("\n");
  return {subject:subject,body:body};
}

function remoteInsert_(jobs) {
  const sheet=getRemoteSheet_();
  const data=sheet.getDataRange().getValues();
  const existingIds=new Set();
  for(let i=1;i<data.length;i++){const id=String(data[i][11]||"").trim();if(id)existingIds.add(id);}
  const rows=[];
  for(const job of (Array.isArray(jobs)?jobs:[])){
    const company=String(job.entreprise||"").trim();
    const email=extractFirstEmail(job.email||job.emails_rh||"");
    const jobUrl=String(job.lien_offre||job.lien||"").trim();
    const linkedin=String(job.linkedin_url||"").trim();
    const key=String(job.id||"").trim() || Utilities.base64EncodeWebSafe(Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, String(company).toLowerCase()+"|"+jobUrl+"|"+linkedin)).replace(/=+$/,"");
    if(!company || (!email && !jobUrl) || existingIds.has(key)) continue;
    rows.push([
      job.date_detection||new Date().toISOString(),job.statut||"NOUVEAU",job.poste||job.intitule||"",company,
      job.lieu||"Remote",job.type_remote||"Remote",email,job.site_entreprise||"",job.source||"LinkedIn / Web",
      jobUrl,linkedin,key,Number(job.fit_score||remoteFitScore_(job)),job.fit_reason||"Travel-tech + B2B + multilingual profile",
      job.type_poste||"",job.salaire||"",job.langues_requises||"",job.acces_depuis_maroc||(jobUrl?"À vérifier dans l'offre":""),
      email?"EMAIL_VERIFIE":"OFFRE_A_POSTULER",""
    ]);
    existingIds.add(key);
  }
  if(rows.length) sheet.getRange(sheet.getLastRow()+1,1,rows.length,REMOTE_HEADERS.length).setValues(rows);
  return {status:"success",action:"remote_insert",added:rows.length};
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

  // Génération du mail : le SMTP sender utilise exactement la même logique
  // que l'envoi natif Apps Script, avec personnalisation depuis l'offre.
  if (rawData && rawData.action === "generate_email") {
    const type = String(rawData.type || "INITIAL").toUpperCase();
    const entreprise = String(rawData.entreprise || "").trim();
    const intitule = String(rawData.intitule || "").trim();
    const roleCible = String(rawData.role_cible || "").trim();
    const lien = String(rawData.lien || "").trim();

    if (type === "RELANCE") {
      const generated = genererEmailRelance(entreprise, intitule, roleCible);
      return ContentService
        .createTextOutput(JSON.stringify({
          status: "success", action: "generate_email", type: type,
          titre_poste: generated.titrePoste, subject: "Nachfassaktion – Bewerbung als " + generated.titrePoste + " – " + CONFIG.NOM,
          html_body: generated.body
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const generated = genererEmailCandidature(entreprise, intitule, roleCible, lien);
    return ContentService
      .createTextOutput(JSON.stringify({
        status: "success", action: "generate_email", type: "INITIAL",
        titre_poste: generated.titrePoste,
        subject: "Bewerbung um einen Ausbildungsplatz als " + generated.titrePoste + " – " + CONFIG.NOM,
        html_body: generated.body
      }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  if (rawData && rawData.action === "remote_generate_email") {
    const generated = generateRemoteEmail_(rawData);
    return ContentService.createTextOutput(JSON.stringify({status:"success",action:"remote_generate_email",subject:generated.subject,body:generated.body})).setMimeType(ContentService.MimeType.JSON);
  }

  // rawData est déjà parsé avant d'entrer dans le lock.
  // Le mode get_cv retourne immédiatement sans toucher au Sheet.
  const lock = LockService.getScriptLock();
  try {
    // Keep the critical section short. The Python scraper sends small batches,
    // and all Sheet writes below are batched instead of appendRow()/setValue()
    // calls inside large loops.
    lock.waitLock(25000);

    // ── MODE RESERVE_SEND / RELEASE_SEND : quota SMTP persistante ─────────
    if (rawData && rawData.action === "reserve_send") {
      const result = reserveSmtpSend_();
      return ContentService
        .createTextOutput(JSON.stringify(result))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (rawData && rawData.action === "release_send") {
      const result = releaseSmtpSend_(String(rawData.token || ""));
      return ContentService
        .createTextOutput(JSON.stringify(result))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (rawData && rawData.action === "remote_insert") {
      return ContentService.createTextOutput(JSON.stringify(remoteInsert_(rawData.jobs||[]))).setMimeType(ContentService.MimeType.JSON);
    }

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
      const allowRetries = rawData.allow_retries !== false;
      const retryMinAgeHours = Math.max(0, Number(rawData.retry_min_age_hours || 2));
      const items = [];
      const cvCache = {};
      const sentEmails = buildSheetIndexes(allData).sentEmails;
      const seenEmails = new Set();
      const retryCandidates = [];

      function pushCandidate(i, type) {
        if (items.length >= limit) return false;

        const row = allData[i];
        const email = extractFirstEmail(row[COL.EMAILS_RH] || "");
        if (!email || !isValidEmail(email) || seenEmails.has(email)) return false;

        const intitule = String(row[COL.INTITULE] || "").trim();
        const roleCible = String(row[COL.ROLE_CIBLE] || "").trim();
        const specialite = detecterSpecialite(intitule, roleCible);
        if (!specialite || !(specialite in CONFIG.CV_MAPPING)) return false;

        if (!(specialite in cvCache)) {
          try {
            cvCache[specialite] = getCV(intitule, roleCible);
          } catch (err) {
            cvCache[specialite] = null;
            Logger.log("[GET_PENDING] CV erreur " + specialite + ": " + err.toString());
          }
        }
        if (!cvCache[specialite]) return false;

        seenEmails.add(email);
        items.push({
          row_index: i + 1,
          type: type,
          emails_rh: email,
          intitule: intitule,
          role_cible: roleCible,
          entreprise: String(row[COL.ENTREPRISE] || "").trim(),
          lieu: String(row[COL.LIEU] || "").trim(),
          lien: String(row[COL.LIEN] || "").trim(),
          source: String(row[COL.SOURCE] || "").trim(),
          specialite: specialite
        });
        return true;
      }

      // 1) Priorité absolue : nouvelles candidatures jamais envoyées.
      for (let i = 1; i < allData.length && items.length < limit; i++) {
        const row = allData[i];
        const statut = String(row[COL.STATUT] || "").trim();
        if (statut !== "NOUVEAU") continue;

        const email = extractFirstEmail(row[COL.EMAILS_RH] || "");
        if (!email || sentEmails.has(email)) continue;

        pushCandidate(i, "INITIAL");
      }

      // 2) Ensuite : relances normales après le délai configuré.
      for (let i = 1; i < allData.length && items.length < limit; i++) {
        const row = allData[i];
        const statut = String(row[COL.STATUT] || "").trim();
        if (statut !== "CANDIDATURE_ENVOYEE") continue;

        const dateEnvoi = row[COL.DATE_CANDIDATURE]
          ? new Date(row[COL.DATE_CANDIDATURE])
          : null;
        if (!dateEnvoi || isNaN(dateEnvoi.getTime())) continue;

        const diffHeures = (new Date() - dateEnvoi) / (1000 * 60 * 60);
        if (diffHeures < CONFIG.DELAI_RELANCE_H) continue;

        pushCandidate(i, "RELANCE");
      }

      // 3) File de secours : si on n'arrive pas à 80 avec les nouvelles
      // candidatures + relances normales, réutiliser des candidatures déjà
      // envoyées. On impose un âge minimal de 2h pour éviter qu'un retry du
      // même workflow renvoie immédiatement les mêmes emails.
      if (allowRetries && items.length < limit) {
        const now = new Date();

        for (let i = 1; i < allData.length && items.length < limit; i++) {
          const row = allData[i];
          const statut = String(row[COL.STATUT] || "").trim();

          if (statut !== "CANDIDATURE_ENVOYEE" && statut !== "RELANCE_EFFECTUEE") {
            continue;
          }

          const email = extractFirstEmail(row[COL.EMAILS_RH] || "");
          if (!email || !sentEmails.has(email) || seenEmails.has(email)) continue;

          const dateDernierEnvoi =
            statut === "RELANCE_EFFECTUEE" && row[COL.DATE_RELANCE]
              ? new Date(row[COL.DATE_RELANCE])
              : row[COL.DATE_CANDIDATURE]
                ? new Date(row[COL.DATE_CANDIDATURE])
                : null;

          if (!dateDernierEnvoi || isNaN(dateDernierEnvoi.getTime())) continue;

          const ageHeures = (now - dateDernierEnvoi) / (1000 * 60 * 60);
          if (ageHeures < retryMinAgeHours) continue;

          retryCandidates.push(i);
        }

        // Les plus anciens envois sont repris en premier.
        retryCandidates.sort((a, b) => {
          const da = new Date(
            allData[a][COL.DATE_RELANCE] ||
            allData[a][COL.DATE_CANDIDATURE] ||
            0
          ).getTime() || 0;
          const db = new Date(
            allData[b][COL.DATE_RELANCE] ||
            allData[b][COL.DATE_CANDIDATURE] ||
            0
          ).getTime() || 0;
          return da - db;
        });

        for (const i of retryCandidates) {
          if (items.length >= limit) break;
          pushCandidate(i, "RETRY");
        }
      }

      return ContentService
        .createTextOutput(JSON.stringify({
          status: "success",
          action: "get_pending",
          returned: items.length,
          requested: limit,
          allow_retries: allowRetries,
          retry_min_age_hours: retryMinAgeHours,
          items: items
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // ── MODE MARK_SENT : enregistre chaque envoi SMTP confirmé ────────────
    if (rawData && rawData.action === "mark_sent") {
      const updates = Array.isArray(rawData.updates) ? rawData.updates : [];
      let updated = 0;
      let missingRows = 0;

      for (const update of updates) {
        const rowNumber = Number(update.row_index || 0);
        if (rowNumber < 2 || rowNumber > allData.length) {
          missingRows++;
          continue;
        }

        const rowIndex = rowNumber - 1;
        const status = String(update.statut || "CANDIDATURE_ENVOYEE").trim();
        const cvUtilise = String(update.cv_utilise || "").trim();
        const reservationToken = String(update.reservation_token || "").trim();
        const now = new Date();

        // Finalize the persistent SMTP quota only after the SMTP server has
        // accepted the message. The token makes this idempotent on webhook retry.
        finalizeSmtpSend_(reservationToken);

        // IMPORTANT: write only the cells changed by the email sender.
        // Never rewrite the whole Sheet here, so a scraper run happening at
        // the same time cannot have its newly enriched rows overwritten.
        sheet.getRange(rowNumber, COL.STATUT + 1).setValue(status);

        if (cvUtilise) {
          sheet.getRange(rowNumber, COL.CV_UTILISE + 1).setValue(cvUtilise);
        }

        if (status === "CANDIDATURE_ENVOYEE") {
          sheet.getRange(rowNumber, COL.DATE_CANDIDATURE + 1).setValue(now);
        } else {
          sheet.getRange(rowNumber, COL.DATE_RELANCE + 1).setValue(now);
          const currentCount = Number(allData[rowIndex][COL.NOMBRE_RELANCES] || 0);
          sheet.getRange(rowNumber, COL.NOMBRE_RELANCES + 1).setValue(currentCount + 1);
        }

        updated++;
      }

      return ContentService
        .createTextOutput(JSON.stringify({
          status: "success",
          action: "mark_sent",
          updated: updated,
          missing_rows: missingRows
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
function detecterSpecialite(intitule, roleCible) {
  const t = ((intitule || "") + " " + (roleCible || ""))
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[|—–_:;,/\\]+/g, " ")
    .replace(/[-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  const has = (...terms) => terms.some(term => t.includes(term));

  if (has("fachverkaufer im lebensmittelhandwerk","fachverkauferin im lebensmittelhandwerk",
    "fachverkaufer lebensmittelhandwerk","fachverkaufer baeckerei","fachverkauferin baeckerei",
    "fachverkaufer fleischerei","fachverkauferin fleischerei","fachverkaufer","fachverkauferin",
    "lebensmittelhandwerk")) return "fachverkaeufer_lebensmittel";

  if (has("kauffrau fur hotelmanagement","kaufmann fur hotelmanagement","hotelmanagement")) return "hotelmanagement";
  if (has("hotelfachfrau","hotelfachmann","hotelfach","hotelkauffrau","hotelkaufmann")) return "hotelfachfrau";
  if (has("systemgastronomie","fachfrau fachmann fur systemgastronomie","fachmann fur systemgastronomie")) return "systemgastronomie";
  if (has("koch","koechin","koch koechin")) return "koch";
  if (has("baecker","baeckerin","baeckerei","konditorei")) return "baecker";
  if (has("kauffrau im einzelhandel","kaufmann im einzelhandel","kauffrau einzelhandel","kaufmann einzelhandel","einzelhandel")) return "einzelhandel";
  if (has("kauffrau fur spedition und logistikdienstleistung","kaufmann fur spedition und logistikdienstleistung",
    "spedition und logistikdienstleistung","speditionskauffrau","speditionskaufmann","spedition")) return "spedition";
  if (has("kauffrau im gross und aussenhandelsmanagement","kaufmann im gross und aussenhandelsmanagement",
    "gross und aussenhandelsmanagement","grossaussenhandelsmanagement","grosshandel","gross und aussenhandel",
    "aussenhandel")) return "handel";
  if (has("industriekauffrau","industriekaufmann","industriekauf")) return "industrie";
  if (has("kauffrau fur bueromanagement","kaufmann fur bueromanagement","kauffrau im bueromanagement",
    "kaufmann im bueromanagement","bueromanagement","burokauffrau","burokaufmann")) return "buero";
  if (has("kauffrau fur tourismus und freizeit","kaufmann fur tourismus und freizeit","tourismus und freizeit") ||
      (has("tourismus") && has("freizeit"))) return "tourismus";

  return "";
}

function getTitreAusbildung(
  specialite
) {

  return ({

    fachverkaeufer_lebensmittel:
      "Fachverkäufer/in im Lebensmittelhandwerk",

    einzelhandel:
      "Kauffrau im Einzelhandel",

    koch:
      "Koch/Köchin",

    hotelfachfrau:
      "Hotelfachmann/-frau",

    hotelmanagement:
      "Kauffrau für Hotelmanagement",

    baecker:
      "Bäcker/in",

    systemgastronomie:
      "Fachfrau/Fachmann für Systemgastronomie",

    einzelhandel:
      "Kauffrau im Einzelhandel",

    buero:
      "Kaufmann/-frau für Büromanagement",

    spedition:
      "Kauffrau für Spedition und Logistikdienstleistung",

    handel:
      "Kauffrau im Groß- und Außenhandelsmanagement",

    industrie:
      "Industriekauffrau",

    tourismus:
      "Kauffrau für Tourismus und Freizeit"

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
    fachverkaeufer_lebensmittel: [
      ["fachverkaeufer im lebensmittelhandwerk", 140], ["fachverkaeuferin im lebensmittelhandwerk", 140],
      ["fachverkaeufer baeckerei", 130], ["fachverkaeuferin baeckerei", 130],
      ["fachverkaeufer konditorei", 130], ["fachverkaeuferin konditorei", 130],
      ["fachverkaeufer fleischerei", 130], ["fachverkaeuferin fleischerei", 130],
      ["lebensmittelhandwerk", 90]
    ],
    einzelhandel: [
      ["einzelhandel", 120], ["kauffrau im einzelhandel", 120],
      ["kaufmann im einzelhandel", 120], ["handel", 35]
    ],
    koch: [
      ["koch koechin", 135], ["koch", 120], ["koechin", 120], ["küche", 45]
    ],
    hotelfachfrau: [
      ["hotelfachfrau", 100], ["hotelfachmann", 100],
      ["hotelkauffrau", 90], ["hotelkaufmann", 90], ["hotel", 45]
    ],
    hotelmanagement: [
      ["hotelmanagement", 130], ["kauffrau fuer hotelmanagement", 130],
      ["kaufmann fuer hotelmanagement", 125], ["hotel", 55]
    ],
    baecker: [
      ["baecker baeckerin", 135], ["baecker", 125], ["baeckerin", 125],
      ["baeckerei", 90], ["konditorei", 80]
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
    ],
    buero: [
      ["bueromanagement", 125], ["kauffrau fuer bueromanagement", 125],
      ["kaufmann fuer bueromanagement", 125], ["buero", 45]
    ],
    tourismus: [
      ["tourismus und freizeit", 130], ["kauffrau fuer tourismus und freizeit", 130],
      ["kaufmann fuer tourismus und freizeit", 125], ["tourismus", 70]
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
      <span style="color: #666;">Bewerberin – Ausbildung in Deutschland</span><br><br>
      📧 <a href="mailto:${Session.getEffectiveUser().getEmail() || CONFIG.EMAIL}" style="color: #1a73e8;">${Session.getEffectiveUser().getEmail() || CONFIG.EMAIL}</a><br>
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
function escapeHtml_(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Récupère quelques éléments réellement présents dans l'annonce.
 * Aucun nouveau champ Sheet n'est nécessaire : le lien de l'offre est lu
 * au moment de la génération du mail.
 */
function fetchOfferContext_(lien) {
  const url = String(lien || "").trim();
  if (!/^https?:\/\//i.test(url)) return "";


  try {
    const response = UrlFetchApp.fetch(url, {
      timeoutSeconds: 10,
      muteHttpExceptions: true,
      followRedirects: true,
      headers: {
        "User-Agent": "Mozilla/5.0 (compatible; Ausbildung-Bewerbung/1.0)"
      }
    });

    const status = response.getResponseCode();
    if (status < 200 || status >= 400) return "";

    let html = response.getContentText();
    if (!html) return "";

    // JSON-LD description is often the cleanest job description.
    const jsonLdMatches = html.match(
      /<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi
    ) || [];

    const jsonTexts = [];
    for (const block of jsonLdMatches) {
      const raw = block
        .replace(/^<script[^>]*>/i, "")
        .replace(/<\/script>$/i, "")
        .trim();

      try {
        const parsed = JSON.parse(raw);
        const items = Array.isArray(parsed) ? parsed : [parsed];
        for (const item of items) {
          if (!item || typeof item !== "object") continue;
          if (item.description) jsonTexts.push(String(item.description));
          if (item.title) jsonTexts.push(String(item.title));
          if (item.name) jsonTexts.push(String(item.name));
        }
      } catch (_) {}
    }

    const meta = [];
    const metaMatches = html.match(
      /<meta[^>]+(?:name|property)=["'](?:description|og:description)["'][^>]+content=["']([^"']+)["'][^>]*>/gi
    ) || [];
    for (const tag of metaMatches) {
      const m = tag.match(/content=["']([^"']+)["']/i);
      if (m && m[1]) meta.push(m[1]);
    }

    const visible = html
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
      .replace(/<svg[\s\S]*?<\/svg>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/gi, " ")
      .replace(/&amp;/gi, "&")
      .replace(/&quot;/gi, '"')
      .replace(/&#39;/gi, "'")
      .replace(/\s+/g, " ")
      .trim();

    // Keep the context small so the generator stays fast and predictable.
    return [jsonTexts.join(" "), meta.join(" "), visible]
      .join(" ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 14000);
  } catch (error) {
    Logger.log("[EMAIL_CONTEXT] Angebot konnte nicht gelesen werden: " + error);
    return "";
  }
}

function extractOfferSignals_(text, specialite) {
  const t = String(text || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");

  const maps = {
    hotelfachfrau: [
      ["Reservierungen", /reservier|buchung|reservierung/],
      ["Gästebetreuung", /gaeste?betreuung|gastgeber|gaestekontakt|kundenbetreuung/],
      ["Check-in und Check-out", /check[\s-]?in|check[\s-]?out/],
      ["Rechnungen und kaufmännische Abläufe", /rechnung|abrechnung|kasse|buchhaltung/],
      ["Veranstaltungen", /veranstaltung|event|bankett/],
      ["Beschwerdemanagement", /beschwerde|reklamation/]
    ],
    hotelmanagement: [
      ["Reservierungen", /reservier|buchung/],
      ["Gästebetreuung", /gaeste?betreuung|gastgeber|kundenbetreuung/],
      ["kaufmännische Abläufe", /rechnung|abrechnung|controlling|buchhaltung|administr/],
      ["Vertrieb und Kundenkontakt", /vertrieb|sales|kundenakquise|kundenbetreuung/],
      ["Veranstaltungen", /veranstaltung|event|bankett/]
    ],
    systemgastronomie: [
      ["Kundenservice", /kundenservice|gaestebetreuung|service/],
      ["Organisation der Abläufe", /organisation|ablauf|koordination/],
      ["Warenbestellung und Warenkontrolle", /warenbestell|bestellung|warenkontroll|lager/],
      ["Kasse und Abrechnung", /kasse|abrechnung|rechnung/],
      ["Teamarbeit", /team|mitarbeiter|zusammenarbeit/]
    ],
    einzelhandel: [
      ["Kundenberatung", /kundenberatung|beratung|kundenbetreuung/],
      ["Verkauf", /verkauf|verkaufen|sales/],
      ["Warenpräsentation", /warenpraesentation|warenpraesentation|sortiment|praesentation/],
      ["Warenbestellung und Bestand", /bestell|warenwirtschaft|bestand|inventur/],
      ["Kasse", /kasse|kassieren/]
    ],
    spedition: [
      ["Auftragsabwicklung", /auftragsabwicklung|auftraege|auftragsbearbeitung/],
      ["Transport- und Tourenkoordination", /tour|transport|disposition|speditions/],
      ["Kunden- und Lieferantenkontakt", /kunden|lieferanten|partner/],
      ["Liefertermine", /liefertermin|lieferung|lieferzeiten/],
      ["Dokumentation", /dokument|zoll|frachtschein/]
    ],
    handel: [
      ["Beschaffung", /beschaffung|einkauf|bestellung/],
      ["Lieferantenkontakt", /lieferanten|supplier/],
      ["Auftragsabwicklung", /auftragsabwicklung|auftragsbearbeitung/],
      ["Kundenbetreuung und Vertrieb", /kundenbetreuung|vertrieb|sales/],
      ["Export und Import", /export|import|aussenhandel|grosshandel/]
    ],
    industrie: [
      ["Beschaffung und Einkauf", /beschaffung|einkauf/],
      ["Auftragsabwicklung", /auftragsabwicklung|auftragsbearbeitung/],
      ["Rechnungen und kaufmännische Prozesse", /rechnung|abrechnung|buchhaltung/],
      ["Kunden- und Lieferantenkontakt", /kunden|lieferanten/],
      ["Vertrieb", /vertrieb|sales/]
    ],
    buero: [
      ["Organisation", /organisation|koordination|administr/],
      ["Kundenkommunikation", /kunden|telefon|kommunikation|empfang/],
      ["Auftragsbearbeitung", /auftragsbearbeitung|auftragsabwicklung/],
      ["Dokumentation", /dokument|schriftverkehr|datenpflege/],
      ["Terminplanung", /termin|kalender/]
    ],
    koch: [
      ["Vorbereitung und Küchenabläufe", /vorbereitung|mise en place|kueche|küche/],
      ["Speisenzubereitung", /zubereitung|kochen|gerichte|speisen/],
      ["Hygiene", /hygiene|lebensmittelhygiene/],
      ["Teamarbeit", /team|küchenteam|kuechenteam/]
    ],
    baecker: [
      ["Herstellung und Vorbereitung", /herstellung|teig|backen|produktion/],
      ["Qualität und Hygiene", /qualitaet|qualität|hygiene/],
      ["Kundenkontakt", /kunden|verkauf|beratung/]
    ],
    fachverkaeufer_lebensmittel: [
      ["Kundenberatung", /kundenberatung|beratung/],
      ["Verkauf", /verkauf|verkaufen/],
      ["Warenpräsentation", /warenpraesentation|sortiment|praesentation/],
      ["Lebensmittel und Qualität", /lebensmittel|frische|qualitaet|qualität/]
    ]
  };

  const rules = maps[specialite] || maps.buero;
  const found = [];
  for (const [label, regex] of rules) {
    if (regex.test(t) && !found.includes(label)) found.push(label);
    if (found.length >= 3) break;
  }
  return found;
}

function normaliserEntrepriseEmail_(value) {
  const original = String(value || "").trim();
  if (!original) return "";

  const normalized = original
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  // Valeurs génériques/placeholder : elles ne doivent JAMAIS apparaître
  // dans le mail et ne doivent jamais être précédées de "bei".
  if (
    normalized === "entreprise non indiquee" ||
    normalized === "entreprise non indique" ||
    normalized === "entreprise non renseignee" ||
    normalized === "unternehmen deutschland" ||
    normalized === "unternehmen nicht angegeben" ||
    normalized === "nicht angegeben" ||
    normalized === "nicht bekannt" ||
    normalized === "unbekannt" ||
    normalized === "non indiquee" ||
    normalized === "non indique" ||
    normalized === "n/a" ||
    normalized === "na" ||
    normalized === "-"
  ) {
    return "";
  }

  // Protection supplémentaire si le placeholder est noyé dans une chaîne.
  if (
    /entreprise\s+non\s+indiquee/.test(normalized) ||
    /unternehmen\s+deutschland/.test(normalized) ||
    /unternehmen\s+(nicht\s+)?angegeben/.test(normalized)
  ) {
    return "";
  }

  return original;
}

function getNaturalFitParagraph_(specialite, signals, entreprise) {
  const signalText = signals.length
    ? signals.slice(0, 3).join(", ")
    : "";

  const company = entreprise && entreprise !== "Unternehmen Deutschland"
    ? " bei " + escapeHtml_(entreprise)
    : "";

  const base = {
    hotelfachfrau:
      "Die Hotellerie kenne ich bereits aus meiner Tätigkeit bei HBX Group / Hotelbeds. Dort betreute ich internationale B2B-Geschäftspartner aus dem Hotel- und Travel-Bereich und arbeitete täglich auf Arabisch, Französisch und Englisch. Dadurch bringe ich bereits Erfahrung mit Kunden, Geschäftspartnern und kaufmännischen Abläufen mit.",
    hotelmanagement:
      "Durch meine Tätigkeit bei HBX Group / Hotelbeds kenne ich die Hotel- und Travel-Branche bereits aus einem internationalen B2B-Umfeld. Kundenbetreuung, Kommunikation mit Geschäftspartnern und kaufmännische Abläufe gehören zu meiner bisherigen Erfahrung.",
    systemgastronomie:
      "Ich bringe über fünf Jahre Erfahrung in Kundenservice, Vertrieb und strukturierten Arbeitsabläufen mit. Besonders wichtig sind mir zuverlässiger Service, gute Kommunikation und ein professioneller Umgang mit Kunden und Kollegen.",
    einzelhandel:
      "Kundenberatung und Verkauf gehören seit mehreren Jahren zu meiner Berufserfahrung. Ich habe sowohl im B2B- als auch im B2C-Umfeld gearbeitet und bringe zusätzlich Erfahrung mit Auftragsabwicklung und kaufmännischen Aufgaben mit.",
    spedition:
      "Logistik und Koordination kenne ich bereits aus meiner Berufserfahrung. Bei Helpdesk ForYou koordinierte ich die Einsatzplanung von über 100 Fahrern und Mitarbeitenden. Heute arbeite ich außerdem mit Beschaffung, Logistik, Bestandsüberwachung und Auftragsabwicklung.",
    handel:
      "Ich bringe über fünf Jahre kaufmännische Erfahrung in Kundenbetreuung, Vertrieb und operativen Abläufen mit. In meiner aktuellen Tätigkeit arbeite ich unter anderem mit Beschaffung, Lieferanten, Bestandsüberwachung und Auftragsabwicklung sowie mit Excel und Sage.",
    industrie:
      "Durch meine bisherige kaufmännische Berufserfahrung kenne ich bereits Beschaffung, Auftragsabwicklung, Kundenbetreuung und strukturierte administrative Prozesse. Aktuell arbeite ich unter anderem mit Lieferanten, Beständen, Rechnungen, Excel und Sage.",
    buero:
      "Organisation, Kommunikation und strukturierte Bearbeitung gehören seit mehreren Jahren zu meinem Arbeitsalltag. Durch meine Erfahrung in Kundenbetreuung, Vertrieb und kaufmännischen Abläufen kann ich mich schnell in neue administrative Prozesse einarbeiten.",
    tourismus:
      "Die Reisebranche kenne ich bereits aus meiner Tätigkeit bei HBX Group / Hotelbeds. Dort arbeitete ich mit internationalen B2B-Geschäftspartnern aus dem Hotel- und Travel-Bereich und konnte meine Erfahrung in Kundenbetreuung, Kommunikation und kaufmännischen Abläufen vertiefen.",
    koch:
      "Ich bringe viel Erfahrung im Umgang mit Menschen, Service und strukturierten Arbeitsabläufen mit. Diese Stärken möchte ich nun in einem praktischen Ausbildungsberuf weiterentwickeln und professionelle Küchenabläufe von Grund auf erlernen.",
    baecker:
      "Sorgfalt, Zuverlässigkeit und Kundenorientierung gehören zu meiner bisherigen Berufserfahrung. Die Verbindung aus handwerklicher Arbeit, Qualität und direktem Kundenkontakt spricht mich besonders an.",
    fachverkaeufer_lebensmittel:
      "Kundenberatung und Verkauf gehören bereits zu meiner Berufserfahrung. Ich bringe einen sicheren Umgang mit Kunden, Kommunikationsstärke und Erfahrung im Vertrieb mit und möchte diese Stärken nun gezielt im Lebensmittelhandwerk einsetzen."
  };

  let paragraph = base[specialite] || base.buero;

  if (signalText) {
    paragraph += " " +
      "Besonders angesprochen haben mich in Ihrer Ausschreibung " +
      escapeHtml_(signalText) +
      ". Genau diese Verbindung aus Kundenkontakt, Organisation und praktischem Arbeiten passt gut zu meiner bisherigen Erfahrung.";
  }

  return paragraph;
}

function genererEmailCandidature(entreprise, intitule, roleCible, lien) {
  entreprise = normaliserEntrepriseEmail_(entreprise);
  const specialite = detecterSpecialite(intitule, roleCible);
  const titrePoste = getTitreAusbildung(specialite);
  const entrepriseConnue =
    entreprise &&
    entreprise.trim() &&
    entreprise.trim() !== "Unternehmen Deutschland";

  const context = fetchOfferContext_(lien);
  const signals = extractOfferSignals_(context, specialite);

  const intro = entrepriseConnue
    ? "Ihre Ausschreibung für einen Ausbildungsplatz als <strong>" +
      escapeHtml_(titrePoste) + "</strong> bei <strong>" +
      escapeHtml_(entreprise.trim()) + "</strong> hat mich besonders angesprochen."
    : "Ihre Ausschreibung für einen Ausbildungsplatz als <strong>" +
      escapeHtml_(titrePoste) + "</strong> hat mich besonders angesprochen.";

  const fit = getNaturalFitParagraph_(specialite, signals, entreprise);

  const relocation =
    "Da ich mich derzeit aus Marokko bewerbe, organisiere ich die notwendigen Schritte für Visum, Einreise und Unterlagen selbstständig.";

  const body = [
    "<p>Sehr geehrte Damen und Herren,</p>",
    "<p>" + intro + "</p>",
    "<p>" + fit + "</p>",
    "<p>" + relocation + "</p>",
    "<p>Deutsch B1 habe ich abgeschlossen und bereite mich aktuell auf B2 vor. Meine vollständigen Bewerbungsunterlagen finden Sie im Anhang.</p>",
    "<p>Gerne stelle ich mich Ihnen auch in einem kurzen Videogespräch persönlich vor.</p>",
    "<p>Mit freundlichen Grüßen</p>",
    getSignatureHTML()
  ].join("\n");

  return { titrePoste, body };
}

function genererEmailRelance(entreprise, intitule, roleCible) {
  entreprise = normaliserEntrepriseEmail_(entreprise);
  const specialite = detecterSpecialite(intitule, roleCible);
  const titrePoste = getTitreAusbildung(specialite);
  const companyText = entreprise && entreprise !== "Unternehmen Deutschland"
    ? " bei <strong>" + escapeHtml_(entreprise) + "</strong>"
    : "";

  const body = [
    "<p>Sehr geehrte Damen und Herren,</p>",
    "<p>ich möchte mich kurz nach meiner Bewerbung für die Ausbildung als <strong>" +
      escapeHtml_(titrePoste) + "</strong>" + companyText + " erkundigen. Mein Interesse an der Ausbildung besteht weiterhin sehr.</p>",
    "<p>Falls Sie noch Unterlagen oder Informationen von mir benötigen, lasse ich Ihnen diese gerne zukommen.</p>",
    "<p>Vielen Dank für Ihre Zeit. Ich freue mich über Ihre Rückmeldung.</p>",
    "<p>Mit freundlichen Grüßen</p>",
    getSignatureHTML()
  ].join("\n");

  return { titrePoste, body };
}

// ─────────────────────────────────────────────────────────────────────────────
// PLAFOND PERSISTANT PAR COMPTE GMAIL — 95 ENVOIS / 24 H
// ─────────────────────────────────────────────────────────────────────────────
const APP_SCRIPT_SEND_LIMIT_24H = 95;
const APP_SCRIPT_SEND_STATE_KEY = "APP_SCRIPT_SEND_STATE_V1";

function getUserSendState_() {
  const props = PropertiesService.getUserProperties();
  let state = {};
  try {
    state = JSON.parse(props.getProperty(APP_SCRIPT_SEND_STATE_KEY) || "{}");
  } catch (_) {
    state = {};
  }
  const now = Date.now();
  if (!state.startedAt || now - Number(state.startedAt) >= 24 * 60 * 60 * 1000) {
    state = { startedAt: now, sent: 0 };
  }
  state.sent = Math.max(0, Number(state.sent || 0));
  return state;
}

function getUserSendRemaining_() {
  const state = getUserSendState_();
  return Math.max(0, APP_SCRIPT_SEND_LIMIT_24H - state.sent);
}

function registerUserSend_() {
  const props = PropertiesService.getUserProperties();
  const state = getUserSendState_();
  state.sent += 1;
  props.setProperty(APP_SCRIPT_SEND_STATE_KEY, JSON.stringify(state));
  return state.sent;
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

/**
 * Dispatch horaire en GMT pour éviter les envois en rafale.
 * 95 maximum / 24 h, avec la majorité des envois le matin.
 * Aucun envoi entre 20:00 et 05:00 GMT.
 */
function getDispatchLimitGMT_() {
  // Apps Script runs hourly. Each trigger may send a real batch instead of
  // shrinking to 1–2 messages late in the day. The persistent 95/24h account
  // limit below remains the hard safety cap.
  const hour = Number(Utilities.formatDate(new Date(), "GMT", "HH"));
  return (hour >= 5 && hour < 20) ? 10 : 0;
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
    const limiteCompte = getUserSendRemaining_();
    if (limiteCompte <= 0) {
      Logger.log("⛔ Plafond Apps Script du compte atteint : 95 envois sur 24 h.");
      return;
    }
    const limiteHoraireGMT = getDispatchLimitGMT_();
    if (limiteHoraireGMT <= 0) {
      const heureGMT = Utilities.formatDate(new Date(), "GMT", "HH:mm");
      Logger.log("⏸️ Aucun envoi autorisé à " + heureGMT + " GMT. Fenêtre d'envoi : 05:00–20:00 GMT.");
      return;
    }
    const limite = Math.min(CONFIG.BATCH_LIMIT, limiteHoraireGMT, quotaRestant, limiteCompte);
    Logger.log("📨 Quota restant : " + quotaRestant + " | plafond 24h : " + limiteCompte + " | limite horaire GMT : " + limiteHoraireGMT + " | limite de ce run : " + limite);
    const debutExecution = Date.now();
    let compteur = 0;

    // Historique PERSISTANT : protège contre un second envoi au même email
    // même si la ligne change de statut ou si un nouveau job apparaît.
    const { sentEmails } = buildSheetIndexes(data);

    // Une seule candidature par email pendant cette exécution également.
    const emailsEnvoyesCetteExecution = new Set();

    // Cache des CV pour éviter de relire Drive 30 fois.
    const cvCache = {};
    const missingCvLogged = new Set();
    const pendingStatusUpdates = [];
    const pendingDateUpdates = [];

    const rowIndexes = [];

    // Build the send queue without historical duplicates.
    // A new offer whose contact email has already received an initial
    // application is excluded here, before the main loop. This prevents
    // thousands of useless "already sent" checks/logs on large sheets.
    for (let i = 1; i < data.length; i++) {
      const status = String(data[i][COL.STATUT] || "").trim();
      if (status !== "NOUVEAU") continue;

      const email = extractFirstEmail(data[i][COL.EMAILS_RH] || "");
      if (!email || !isValidEmail(email)) continue;
      if (sentEmails.has(email)) continue;

      rowIndexes.push(i);
    }

    // Relances remain eligible and are handled separately by the date check.
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
      const entreprise = String(row[COL.ENTREPRISE] || "").trim();
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
          continue;
        }

        const specialite = detecterSpecialite(intitule, roleCible);
        if (!specialite) continue;

        if (!(specialite in cvCache)) {
          cvCache[specialite] = getCV(intitule, roleCible);
        }

        const cvFile = cvCache[specialite];

        if (!cvFile) {
          if (!missingCvLogged.has(specialite)) {
            Logger.log("⚠️ CV introuvable pour la spécialité : " + specialite);
            missingCvLogged.add(specialite);
          }
          continue;
        }

        const { titrePoste, body } = genererEmailCandidature(
          entreprise, intitule, roleCible, String(row[COL.LIEN] || "").trim()
        );
        const sujet = "Bewerbung um einen Ausbildungsplatz als " + titrePoste + " – " + CONFIG.NOM;

        try {
          GmailApp.sendEmail(emailCible, sujet, "", {
            htmlBody: body,
            attachments: [cvFile.getAs(MimeType.PDF)],
            name: CONFIG.NOM,
            replyTo: Session.getEffectiveUser().getEmail() || CONFIG.EMAIL,
          });

          registerUserSend_();

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

      // ── RELANCE APRÈS 7 JOURS ─────────────────────────────────────────────
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
            replyTo: Session.getEffectiveUser().getEmail() || CONFIG.EMAIL,
          });

          registerUserSend_();

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
// INSTALLATION DU TRIGGER HORAIRE
// — À APPELER UNE SEULE FOIS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Installe un déclencheur toutes les heures pour traiterAusbildungCandidatures().
 * À appeler UNE SEULE FOIS depuis l'éditeur Apps Script.
 * Supprime les anciens triggers du même nom pour éviter les doublons.
 */
function configurerDeclencheurs() {
  // Each Gmail account must create its own installable trigger.
  // Google runs each trigger under the account that created it.
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === "traiterAusbildungCandidatures") {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  ScriptApp.newTrigger("traiterAusbildungCandidatures")
    .timeBased()
    .everyHours(1)
    .create();

  Logger.log("✅ Trigger Apps Script installé pour le compte : " +
    (Session.getEffectiveUser().getEmail() || "inconnu"));
  Logger.log("   → Plafond : 95 destinataires par jour pour ce compte.");
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