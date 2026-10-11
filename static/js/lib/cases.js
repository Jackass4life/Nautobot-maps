// Case numbers and their ITSM tickets (#289).
import { escHtml } from "./html.js";

// "https://itsm.example.com/ticket/{case}"; "" when not set.  The page sets
// it from its config (CASE_URL_TEMPLATE) before rendering anything.
let caseUrlTemplate = "";

export function setCaseUrlTemplate(template) {
  caseUrlTemplate = template || "";
}

// A case number's ticket (#289): the template with the encoded number, or "".
export function caseUrl(value, template = caseUrlTemplate) {
  return template && value ? template.split("{case}").join(encodeURIComponent(value)) : "";
}

// A case number as a pill: a link to its ticket when there is a template.
export function casePill(value, template = caseUrlTemplate) {
  const url = caseUrl(value, template);
  return url
    ? `<a class="case-pill" href="${escHtml(url)}" target="_blank" rel="noopener noreferrer" title="Open the ticket">${escHtml(value)}</a>`
    : `<span class="case-pill">${escHtml(value)}</span>`;
}

// "INC-1 (https://itsm…/INC-1)" in plain text (#289); the number alone without a template.
export function caseWithUrl(value) {
  const url = caseUrl(value);
  return url ? `${value} (${url})` : value;
}
