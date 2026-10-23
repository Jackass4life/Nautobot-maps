"""What the old pages share with the Maintenance page (#343, #353); the page
itself is on Nuxt (#463), tested in frontend/tests/maintenance.test.ts."""

from tests.js_modules import run_js


def test_the_boards_maintenance_request():
    """The board's Maint. panel's request (lib/maintenance-form.js); the
    Maintenance page's own is frontend/app/utils/maintenance.ts (#463)."""
    run_js(
        """
import { windowRequest as planRequest } from "@js/lib/maintenance-form.js";
const ids = Array.from({ length: 40 }, (_, i) => `s${i}`);
const when = { when: "now", duration: "120" };
let body = planRequest({ siteIds: ids, scope: "site", deviceIds: [], reason: " Core ", ...when });
check(planRequest({ siteIds: ["s1"], scope: "site", deviceIds: [], reason: "x", ...when }).site_id === "s1", "one site");
check(body.site_ids.length === 40 && body.reason === "Core" && body.duration_minutes === 120 && !("site_id" in body), JSON.stringify(body).slice(0, 80));
body = planRequest({ siteIds: ["s1"], scope: "devices", deviceIds: ["d1"], reason: "PSU", ...when });
check(body.site_id === "s1" && body.device_ids[0] === "d1" && !("site_ids" in body), JSON.stringify(body));
check(planRequest({ siteIds: [], scope: "site", deviceIds: [], reason: "x", ...when }).error === "Choose at least one site.", "no site");
check(planRequest({ siteIds: ids, scope: "site", deviceIds: [], reason: " ", ...when }).error === "Enter a reason.", "reason");
check(planRequest({ siteIds: ["a", "b"], scope: "devices", deviceIds: ["d"], reason: "x", ...when }).error.includes("one site"), "devices");
check(planRequest({ siteIds: ids, scope: "site", deviceIds: [], reason: "x", ...when }, 30).error === "At most 30 sites at once.", "the limit");
"""
    )


def test_history_links():
    run_js(
        """
import { maintenanceHistoryLink } from "@js/alerts/rows.js";
import { buildMaintenanceHistoryLink } from "@js/map/panel.js";
// A site's history from the board's ⋯ menu and the map's panel, escaped (#353).
const link = maintenanceHistoryLink({ id: "loc a&b", name: "Oslo <HQ>" });
check(link.includes('href="/maintenance?view=history&amp;site_id=loc+a%26b"') && link.includes("for Oslo &lt;HQ&gt;"), link);
check(maintenanceHistoryLink({}) === "", "no id");
check(buildMaintenanceHistoryLink({ id: "loc-1" }).includes('href="/maintenance?view=history&amp;site_id=loc-1"'), "map");
"""
    )
