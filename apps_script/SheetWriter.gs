/**
 * Apps Script web app: receives JSON POSTs from the Python listener and
 * appends rows to the master Reddit Triage sheet.
 *
 * Header-aware: doPost writes each field to whichever column currently holds
 * its header, so you can drag columns around without breaking writes.
 * setupSheet() applies dropdowns / widths / wrap by HEADER NAME too, so the
 * column ORDER below can change freely without touching the formatting logic.
 *
 * Setup:
 *   1. Master sheet > Extensions > Apps Script, paste this as Code.gs
 *   2. Run setupSheet() once
 *   3. Deploy > New deployment > Web app (Execute as Me, Anyone access)
 *   4. Put the web app URL + a shared token in the Python listener's .env,
 *      and paste the same token into SHARED_TOKEN below.
 */

const SHEET_NAME = "Triage";
const SHARED_TOKEN = "REPLACE_WITH_SAME_TOKEN_AS_PYTHON_ENV";

// Column order as displayed in the sheet. Reorder freely - all logic below
// keys off header names, not positions.
const COLUMNS = [
  "Status",
  "Score",
  "Difficulty",
  "Sub",
  "Title",
  "Summary",
  "URL",
  "Mention Product?",
  "Suggested draft",
  "Owner",
  "Age",
  "Upvotes",
  "Comments",
  "Posted URL",
  "Notes",
];

// Maps incoming JSON field -> sheet header name.
const FIELD_TO_HEADER = {
  status: "Status",
  score: "Score",
  difficulty: "Difficulty",
  sub: "Sub",
  title: "Title",
  summary: "Summary",
  url: "URL",
  mention_product: "Mention Product?",
  suggested_draft: "Suggested draft",
  owner: "Owner",
  age: "Age",
  upvotes: "Upvotes",
  comments: "Comments",
  posted_url: "Posted URL",
  notes: "Notes",
};

// Dropdown validations by header name.
const DROPDOWNS = {
  "Status": ["New", "Drafting", "Posted", "Replied", "Skipped"],
  "Difficulty": ["Easy", "Medium", "Technical"],
  "Mention Product?": ["Yes", "Soft", "No"],
};

// Column widths by header name.
const WIDTHS = {
  "Status": 100,
  "Score": 70,
  "Difficulty": 110,
  "Sub": 130,
  "Title": 300,
  "Summary": 320,
  "URL": 260,
  "Mention Product?": 150,
  "Suggested draft": 540,
  "Owner": 120,
  "Age": 70,
  "Upvotes": 70,
  "Comments": 80,
  "Posted URL": 220,
  "Notes": 240,
};

// Headers whose cells should wrap (long text).
const WRAP_HEADERS = ["Title", "Summary", "Suggested draft", "Notes"];

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.token !== SHARED_TOKEN) {
      return _json({ ok: false, error: "invalid token" });
    }
    const rows = body.rows || [];
    const sheet = _getSheet();
    rows.forEach((r) => _appendRow(sheet, r));
    return _json({ ok: true, appended: rows.length });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}

function _getSheet() {
  const ss = SpreadsheetApp.getActive();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    _initSheet(sheet);
  }
  return sheet;
}

function _headerMap(sheet) {
  const headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0];
  const map = {};
  headers.forEach((h, i) => { map[String(h).trim()] = i; });
  return { headers: headers, map: map };
}

function _appendRow(sheet, r) {
  const { headers, map } = _headerMap(sheet);
  const rowArr = new Array(headers.length).fill("");
  Object.keys(FIELD_TO_HEADER).forEach((field) => {
    const colIdx = map[FIELD_TO_HEADER[field]];
    if (colIdx === undefined) return;
    let val = r[field];
    if (val === undefined || val === null) {
      val = (field === "status") ? "New"
          : (field === "upvotes" || field === "comments" || field === "score") ? 0
          : "";
    }
    rowArr[colIdx] = val;
  });
  sheet.appendRow(rowArr);
}

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Run once from the editor to (re)initialize the Triage sheet: header row,
 * dropdowns, widths, wrap, frozen header. Clears existing content first.
 * All formatting keys off header names, so it follows COLUMNS order.
 */
function setupSheet() {
  const ss = SpreadsheetApp.getActive();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.insertSheet(SHEET_NAME);

  sheet.clear();
  // sheet.clear() does NOT remove data validations - stale dropdowns survive
  // column reorders and stick to the wrong column. Clear them explicitly.
  sheet.getRange(1, 1, sheet.getMaxRows(), sheet.getMaxColumns()).clearDataValidations();
  sheet.appendRow(COLUMNS);
  sheet.getRange(1, 1, 1, COLUMNS.length)
    .setFontWeight("bold")
    .setBackground("#f0f3f5")
    .setHorizontalAlignment("left");
  sheet.setFrozenRows(1);

  const colIndex = {};
  COLUMNS.forEach((name, i) => { colIndex[name] = i + 1; });  // 1-based

  Object.keys(DROPDOWNS).forEach((header) => {
    const ci = colIndex[header];
    if (!ci) return;
    const rule = SpreadsheetApp.newDataValidation()
      .requireValueInList(DROPDOWNS[header], true)
      .setAllowInvalid(false)
      .build();
    sheet.getRange(2, ci, sheet.getMaxRows() - 1, 1).setDataValidation(rule);
  });

  Object.keys(WIDTHS).forEach((header) => {
    const ci = colIndex[header];
    if (ci) sheet.setColumnWidth(ci, WIDTHS[header]);
  });

  WRAP_HEADERS.forEach((header) => {
    const ci = colIndex[header];
    if (ci) sheet.getRange(2, ci, sheet.getMaxRows() - 1, 1).setWrap(true).setVerticalAlignment("top");
  });

  // Score color scale (white -> yellow -> green over 0/50/100).
  const scoreCol = colIndex["Score"];
  if (scoreCol) {
    const scoreRange = sheet.getRange(2, scoreCol, sheet.getMaxRows() - 1, 1);
    const scoreRule = SpreadsheetApp.newConditionalFormatRule()
      .setGradientMinpointWithValue("#ffffff", SpreadsheetApp.InterpolationType.NUMBER, "0")
      .setGradientMidpointWithValue("#fce8b2", SpreadsheetApp.InterpolationType.NUMBER, "50")
      .setGradientMaxpointWithValue("#57bb8a", SpreadsheetApp.InterpolationType.NUMBER, "100")
      .setRanges([scoreRange])
      .build();
    sheet.setConditionalFormatRules([scoreRule]);
  }
}

/**
 * One-off repair: removes any basic filter left on the sheet and unhides all
 * rows. A prior version of setupSheet applied a basic filter that hid every
 * row (its setHiddenValues criteria matched even New rows under some states),
 * so this exists to recover a stuck sheet without touching the data. To hide
 * completed rows, use a personal Filter View (View > Filter views) instead -
 * it's non-destructive and per-user, so the cron's appends stay visible to
 * everyone else.
 */
function removeArchiveFilter() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) return;
  const existing = sheet.getFilter();
  if (existing) existing.remove();
  sheet.showRows(1, sheet.getMaxRows());
}
