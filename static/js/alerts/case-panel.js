// The + Case panel (#179, #273) and acknowledgements (#288).
import { casePill } from "../lib/cases.js";
import { escHtml } from "../lib/html.js";
import { getJson } from "../lib/http.js";
import { showError } from "../lib/toast.js";
import { loadAlertBoard } from "./board.js";
import { hideSidePanel, openSidePanel, restorePanelFocus } from "./panels.js";
import { caseDevices } from "./rows.js";
import { els, findSite } from "./state.js";

function devicesLabel(count) {
  return `${count} device${count === 1 ? "" : "s"}`;
}

// *alreadyCount*: ticked devices that already have the typed case number
// (#273); saving it there again would change nothing.
export function caseButtonLabel(selectedCount, alreadyCount) {
  if (selectedCount === 0) return "Select devices";
  const already = alreadyCount || 0;
  if (already === selectedCount) return `Already on ${devicesLabel(already)}`;
  if (already) return `Add case to ${devicesLabel(selectedCount - already)} (already on ${already})`;
  return `Add case to ${devicesLabel(selectedCount)}`;
}

// A device's open case numbers, as the panel stores them (newline-separated).
function deviceCaseList(box) {
  return (box.dataset.cases || "").split("\n").filter(Boolean);
}

// Whether *box*'s device already has *caseNumber*, ignoring case: "inc-881"
// next to "INC-881" would be a second ticket by mistake.
export function deviceHasCase(box, caseNumber) {
  const wanted = (caseNumber || "").trim().toLowerCase();
  return Boolean(wanted) && deviceCaseList(box).some((value) => value.toLowerCase() === wanted);
}

// The ticked devices that get *caseNumber*: those that don't have it yet.
// The button label and the request both use this, so they always agree.
export function caseTargetIds(form, caseNumber) {
  return Array.from(form.querySelectorAll(".case-device-checkbox"))
    .filter((box) => box.checked && !deviceHasCase(box, caseNumber))
    .map((box) => box.value);
}

export function syncCaseSelection(form) {
  const boxes = Array.from(form.querySelectorAll(".case-device-checkbox"));
  const ticked = boxes.filter((box) => box.checked);
  const selected = ticked.length;
  const selectAll = form.querySelector(".case-select-all");
  if (selectAll) {
    selectAll.checked = selected === boxes.length;
    selectAll.indeterminate = selected > 0 && selected < boxes.length;
  }
  const already = selected - caseTargetIds(form, form.querySelector(".case-input")?.value).length;
  const button = form.querySelector(".case-save-btn");
  button.textContent = caseButtonLabel(selected, already);
  button.disabled = selected === 0 || already === selected;
}

export function renderCaseForm(item) {
  const devices = caseDevices(item);
  const casesOf = (d) => (Array.isArray(d.case_numbers) ? d.case_numbers : []);
  // One case often covers several devices (e.g. a whole site down), so every
  // down device gets a checkbox.  Devices that already have a case start
  // unticked and show it, so a second ticket isn't added by mistake (#273).
  const deviceChoices = devices.map((d) => {
    const cases = casesOf(d);
    const pills = cases.map((value) => casePill(value)).join("");
    return `
        <label class="case-device-option">
          <input type="checkbox" class="case-device-checkbox" value="${escHtml(d.device_id)}" data-cases="${escHtml(cases.join("\n"))}"${cases.length ? "" : " checked"} />
          <span>${escHtml(d.device_name || d.device_id)}</span>${pills ? `<span class="case-device-cases">${pills}</span>` : ""}
        </label>`;
  }).join("");
  const ticked = devices.filter((d) => !casesOf(d).length).length;
  const siteCases = [...new Set(devices.flatMap(casesOf))];
  const existing = siteCases.length ? `
      <p class="case-existing">Open cases on this site: ${siteCases.map((value) => casePill(value)).join(" ")}</p>` : "";
  const allHaveOne = devices.length > 0 && ticked === 0;
  const note = allHaveOne ? `
      <p class="case-note">Every down device already has a case. Ticking a device adds a second case next to it; it doesn't replace it.</p>` : "";
  const selectAll = devices.length > 1 ? `
        <label class="case-device-option case-select-all-option">
          <input type="checkbox" class="case-select-all"${ticked === devices.length ? " checked" : ""} />
          <span>All down devices (${devices.length})</span>
        </label>` : "";
  return `
    <form class="case-form" data-site-id="${escHtml(item.id || "")}">${existing}${note}
      <fieldset class="case-devices">
        <legend>Devices for this case</legend>
        ${selectAll}
        <div class="case-device-list">${deviceChoices}</div>
      </fieldset>
      <label class="case-input-label" for="case-input">Case number</label>
      <input id="case-input" class="case-input" type="text" autocomplete="off" placeholder="Case #" />
      <button class="case-save-btn" type="submit"${ticked ? "" : " disabled"}>${caseButtonLabel(ticked)}</button>
      <div class="ack-form">
        <label class="case-input-label" for="ack-note">Or acknowledge without a case (note, optional)</label>
        <input id="ack-note" class="ack-note-input" type="text" autocomplete="off" maxlength="200" placeholder="e.g. On my way" />
        <button class="ack-form-btn" type="button">Acknowledge ticked</button>
      </div>
    </form>
  `;
}

export function openCasePanel(siteId) {
  const item = findSite(siteId);
  const { casePanel, caseTitle, caseContent } = els;
  if (!casePanel || !item) return;
  caseTitle.textContent = `Add case · ${item.name || siteId}`;
  caseContent.innerHTML = renderCaseForm(item);
  // Part-ticked select-all can only be set from script.
  const form = caseContent.querySelector(".case-form");
  if (form) syncCaseSelection(form);
  openSidePanel(casePanel, `.case-open-btn[data-site-id="${CSS.escape(siteId)}"]`, caseContent.querySelector(".case-input"));
}

// The acknowledge request (#288): *deviceIds* empty = every down device, sent
// as null; the API refuses an empty list, so bad input never means "all".
export function ackRequestBody(siteId, deviceIds, note) {
  return { site_id: siteId, device_ids: deviceIds && deviceIds.length ? deviceIds : null, note: note || "" };
}

// Acknowledge (#288) or remove it; reloads the board.
export async function sendAck(siteId, deviceIds, note, remove) {
  await getJson(remove ? "/api/alert-acks/remove" : "/api/alert-acks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(ackRequestBody(siteId, deviceIds, note)),
  });
  await loadAlertBoard(false);
}

async function saveCase(form) {
  const siteId = form.dataset.siteId;
  const caseBtn = form.querySelector(".case-save-btn");
  const caseInput = form.querySelector(".case-input");
  const caseNumber = (caseInput?.value || "").trim();
  // Not the devices that already have this number (Copilot review on #274).
  const deviceIds = caseTargetIds(form, caseNumber);
  const deviceNameById = Object.fromEntries(
    Array.from(form.querySelectorAll(".case-device-checkbox")).map((box) => [box.value, box.nextElementSibling.textContent]),
  );
  if (!siteId || !deviceIds.length || !caseNumber) {
    showError("Select at least one device and enter a case number first.");
    return;
  }
  caseBtn.disabled = true;
  try {
    await getJson("/api/alert-cases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site_id: siteId, device_ids: deviceIds, case_number: caseNumber }),
    });
    // Saved: close the panel.  The server drops its cached board when a case
    // is added; a plain reload is enough and must not trigger a full
    // inventory sync.  Focus returns to the row's (re-rendered) + Case button.
    hideSidePanel(els.casePanel);
    await loadAlertBoard(false);
    restorePanelFocus();
  } catch (err) {
    // Devices that came back up meanwhile: the API names them.
    const missingIds = err.payload?.missing_device_ids;
    const missing = Array.isArray(missingIds) && missingIds.length
      ? ` (no longer down: ${missingIds.map((id) => deviceNameById[id] || id).join(", ")} – untick and try again)`
      : "";
    showError(`Failed to add case: ${err.message}${missing}`);
  } finally {
    caseBtn.disabled = false;
  }
}

async function acknowledgeTicked(button) {
  const form = button.closest(".case-form");
  const deviceIds = Array.from(form.querySelectorAll(".case-device-checkbox")).filter((box) => box.checked).map((box) => box.value);
  if (!deviceIds.length) {
    showError("Tick at least one device to acknowledge.");
    return;
  }
  button.disabled = true;
  try {
    hideSidePanel(els.casePanel);
    await sendAck(form.dataset.siteId, deviceIds, form.querySelector(".ack-note-input")?.value, false);
    restorePanelFocus();
  } catch (err) {
    showError(`Could not acknowledge: ${err.message}`);
    restorePanelFocus();
  } finally {
    button.disabled = false;
  }
}

export function initCasePanel() {
  const { casePanel } = els;
  if (!casePanel) return;

  casePanel.addEventListener("change", (event) => {
    const form = event.target.closest(".case-form");
    if (!form) return;
    if (event.target.classList.contains("case-select-all")) {
      form.querySelectorAll(".case-device-checkbox").forEach((box) => {
        box.checked = event.target.checked;
      });
    }
    if (event.target.matches(".case-select-all, .case-device-checkbox")) syncCaseSelection(form);
  });

  // The typed number may already be on the ticked devices (#273).
  casePanel.addEventListener("input", (event) => {
    const form = event.target.closest(".case-form");
    if (form && event.target.classList.contains("case-input")) syncCaseSelection(form);
  });

  casePanel.addEventListener("click", (event) => {
    const button = event.target.closest(".ack-form-btn");
    if (button) acknowledgeTicked(button);
  });

  // Enter in the note acknowledges; it must not submit the case form (#288).
  casePanel.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !event.target.classList.contains("ack-note-input")) return;
    event.preventDefault();
    event.target.closest(".case-form")?.querySelector(".ack-form-btn")?.click();
  });

  casePanel.addEventListener("submit", (event) => {
    const form = event.target.closest(".case-form");
    if (!form) return;
    event.preventDefault();
    saveCase(form);
  });
}
