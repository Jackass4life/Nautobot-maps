// The (i) next to a tenant (#263), and any element with data-info-tip (an
// alert badge's reason): hover or keyboard focus shows it; a click or a tap
// pins it until a click elsewhere, a second click on it, Escape, or a scroll
// that moves it (#390).  One floating box, placed in the window (position: fixed),
// because the board table and the map's site panel both scroll and would
// cut off a tooltip drawn inside them.  Any element with data-info-tip
// works; rows are re-rendered often, so the listeners are on the document.

const INFO_TIP_GAP_PX = 8;
const INFO_TIP_MARGIN_PX = 8;

let infoTipBox = null;
let infoTipAnchor = null;
// Opened by a click or tap: leaving the glyph with the pointer doesn't close it.
let infoTipPinned = false;

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
export function placeInfoTip(anchorRect, boxWidth, boxHeight, viewportWidth, viewportHeight) {
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
  setPinned(false);
}

function setPinned(pinned) {
  infoTipPinned = pinned;
  if (infoTipBox) infoTipBox.classList.toggle("pinned", pinned);
}

function hideInfoTip() {
  if (infoTipBox) infoTipBox.hidden = true;
  infoTipAnchor = null;
  setPinned(false);
}

function infoTipAnchorOf(target) {
  return target instanceof Element ? target.closest("[data-info-tip]") : null;
}

// The pinned glyph, or one drawn again in its place by a refresh (#390).
function isPinnedGlyph(anchor) {
  if (!infoTipPinned || !infoTipAnchor) return false;
  return anchor === infoTipAnchor
    || (!infoTipAnchor.isConnected && anchor.dataset.infoTip === infoTipAnchor.dataset.infoTip);
}

// Called once by each page that shows (i) glyphs.  While a tip is pinned,
// only a click (or Escape, or a scroll that moves it) changes it: hover and
// focus elsewhere don't (#390).
export function initInfoTips() {
  document.addEventListener("mouseover", (event) => {
    if (infoTipPinned) return;
    const anchor = infoTipAnchorOf(event.target);
    if (anchor && anchor !== infoTipAnchor) showInfoTip(anchor);
  });
  document.addEventListener("mouseout", (event) => {
    if (infoTipPinned) return;
    if (infoTipAnchor && infoTipAnchorOf(event.target) === infoTipAnchor && !infoTipAnchor.contains(event.relatedTarget)) {
      hideInfoTip();
    }
  });
  // A click or tap on a glyph pins its tip; on the same glyph again, or
  // anywhere else but the tip itself, closes it.
  document.addEventListener("click", (event) => {
    const anchor = infoTipAnchorOf(event.target);
    if (anchor) {
      if (isPinnedGlyph(anchor)) {
        hideInfoTip();
      } else {
        showInfoTip(anchor);
        setPinned(true);
      }
      return;
    }
    if (infoTipPinned && !(infoTipBox && infoTipBox.contains(event.target))) hideInfoTip();
  });
  document.addEventListener("focusin", (event) => {
    if (infoTipPinned) return;
    const anchor = infoTipAnchorOf(event.target);
    if (anchor) showInfoTip(anchor);
  });
  document.addEventListener("focusout", (event) => {
    if (infoTipPinned) return;  // a click in the box, or another window, keeps it
    if (infoTipAnchorOf(event.target) === infoTipAnchor) hideInfoTip();
  });
  // First (capture) and alone: Escape closes the tip, not also the panel behind it.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !infoTipAnchor) return;
    hideInfoTip();
    event.stopPropagation();
  }, true);
  // The box is fixed in the window: a scroll of the page, or of a panel the
  // glyph is in, moves the glyph away from it.  Other scrolls don't.
  document.addEventListener("scroll", (event) => {
    if (!infoTipAnchor) return;
    const scrolled = event.target;
    if (scrolled === document || !(scrolled instanceof Element) || scrolled.contains(infoTipAnchor)) hideInfoTip();
  }, true);
  window.addEventListener("resize", hideInfoTip);
}
