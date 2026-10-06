"use strict";

// The (i) next to a tenant (#263): hover or keyboard focus shows its
// description.  One floating box, placed in the window (position: fixed),
// because the board table and the map's site panel both scroll and would
// cut off a tooltip drawn inside them.  Any element with data-info-tip
// works; rows are re-rendered often, so the listeners are on the document.

const INFO_TIP_GAP_PX = 8;
const INFO_TIP_MARGIN_PX = 8;

let infoTipBox = null;
let infoTipAnchor = null;

function infoTipElement() {
  if (!infoTipBox) {
    infoTipBox = document.createElement("div");
    infoTipBox.className = "info-tip-box";
    infoTipBox.setAttribute("role", "tooltip");
    infoTipBox.hidden = true;
    document.body.appendChild(infoTipBox);
  }
  return infoTipBox;
}

// Below the glyph, or above it when there is no room; never outside the window.
function placeInfoTip(anchorRect, boxWidth, boxHeight, viewportWidth, viewportHeight) {
  const maxLeft = Math.max(INFO_TIP_MARGIN_PX, viewportWidth - boxWidth - INFO_TIP_MARGIN_PX);
  const centred = anchorRect.left + anchorRect.width / 2 - boxWidth / 2;
  const left = Math.min(Math.max(INFO_TIP_MARGIN_PX, centred), maxLeft);
  let top = anchorRect.bottom + INFO_TIP_GAP_PX;
  if (top + boxHeight > viewportHeight - INFO_TIP_MARGIN_PX) {
    top = Math.max(INFO_TIP_MARGIN_PX, anchorRect.top - INFO_TIP_GAP_PX - boxHeight);
  }
  return { left, top };
}

function showInfoTip(anchor) {
  const text = anchor.dataset.infoTip;
  if (!text) return;
  const box = infoTipElement();
  box.textContent = text;
  box.hidden = false;
  const { left, top } = placeInfoTip(
    anchor.getBoundingClientRect(),
    box.offsetWidth,
    box.offsetHeight,
    window.innerWidth,
    window.innerHeight,
  );
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
  infoTipAnchor = anchor;
}

function hideInfoTip() {
  if (infoTipBox) infoTipBox.hidden = true;
  infoTipAnchor = null;
}

function infoTipAnchorOf(target) {
  return target instanceof Element ? target.closest("[data-info-tip]") : null;
}

document.addEventListener("mouseover", (event) => {
  const anchor = infoTipAnchorOf(event.target);
  if (anchor && anchor !== infoTipAnchor) showInfoTip(anchor);
});
document.addEventListener("mouseout", (event) => {
  if (infoTipAnchor && infoTipAnchorOf(event.target) === infoTipAnchor && !infoTipAnchor.contains(event.relatedTarget)) {
    hideInfoTip();
  }
});
document.addEventListener("focusin", (event) => {
  const anchor = infoTipAnchorOf(event.target);
  if (anchor) showInfoTip(anchor);
});
document.addEventListener("focusout", (event) => {
  if (infoTipAnchorOf(event.target) === infoTipAnchor) hideInfoTip();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && infoTipAnchor) hideInfoTip();
});
// The box is fixed in the window: when the page or a panel scrolls, it would
// no longer sit next to its glyph.
document.addEventListener("scroll", hideInfoTip, true);
window.addEventListener("resize", hideInfoTip);
