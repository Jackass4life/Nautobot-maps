// What the board's Maint. panel and the Maintenance page share when planning
// a window (#343): the device checklist and the request.  No page access at import.
import { escHtml } from "./html.js";
import { whenRequest } from "./when.js";

// One site's monitored devices to tick.
export function devicesHtml(devices) {
  if (!devices.length) return '<p class="maint-empty">No monitored devices.</p>';
  return devices.map((device) => `
    <label class="case-device-option">
      <input type="checkbox" class="maint-device" value="${escHtml(device.id)}" />
      <span>${escHtml(device.name || device.id)}</span>
      <span class="maint-device-meta">${[device.location_path, device.role, device.status].filter(Boolean).map(escHtml).join(" · ")}</span>
    </label>`).join("");
}

// The request for new windows, or {error}.  values: {siteIds, scope ("site" or
// "devices"), deviceIds, reason, ...when (lib/when.js)}; *maxSites*: the
// server's limit for one request (maintenance.MAX_SITES).
export function windowRequest(values, maxSites = 200) {
  const { siteIds } = values;
  if (!siteIds.length) return { error: "Choose at least one site." };
  if (siteIds.length > maxSites) return { error: `At most ${maxSites} sites at once.` };
  const reason = (values.reason || "").trim();
  if (!reason) return { error: "Enter a reason." };
  let body;
  if (values.scope === "devices") {
    if (siteIds.length !== 1) return { error: "Choose devices for one site at a time." };
    if (!values.deviceIds.length) return { error: "Tick at least one device, or choose the whole site." };
    body = { site_id: siteIds[0], reason, device_ids: values.deviceIds };
  } else {
    body = siteIds.length === 1 ? { site_id: siteIds[0], reason } : { site_ids: siteIds, reason };
  }
  const when = whenRequest(values);
  return when.error ? when : { ...body, ...when };
}
