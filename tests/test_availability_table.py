"""What the board shares with the old availability table (#342): "+2" shows a
row's other tenants in place.  The Availability page itself is on Nuxt (#463),
tested in frontend/tests/availability.test.ts."""

from tests.js_modules import run_js


def test_the_more_button():
    run_js("""
import { revealTenantRest } from "@js/lib/html.js";
const rest = element("rest", { hidden: true });
let removed = false;
const button = { previousElementSibling: rest, remove() { removed = true; } };
revealTenantRest({ target: { closest: (sel) => (sel === ".tenant-more-btn" ? button : null) } });
check(rest.hidden === false && removed, "shown");
revealTenantRest({ target: { closest: () => null } });
""")
