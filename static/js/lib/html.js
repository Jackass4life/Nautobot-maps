// HTML helpers shared by the pages (#294).

export function escHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// An (i) that shows *text* on hover or focus (js/tooltip.js, #263).
export function infoTipHtml(text, label) {
  return `<span class="info-tip" tabindex="0" role="img" aria-label="${escHtml(label)}" data-info-tip="${escHtml(text)}">i</span>`;
}

// Only the map's own keys: a tenant named "constructor" or "toString" must not
// pick up an inherited object property.
export function ownDescription(descriptions, name) {
  return descriptions && Object.hasOwn(descriptions, name) ? descriptions[name] : "";
}

// "+2" after a tenant shows the rest in place (#279): the board and the
// availability page (#342) use the same markup, a hidden .tenant-rest then the button.
export function revealTenantRest(event) {
  const button = event.target.closest(".tenant-more-btn");
  if (!button) return;
  button.previousElementSibling.hidden = false;
  button.remove();
}
