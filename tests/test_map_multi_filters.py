"""The staff links the tenant picks produce on the board (#412).  The map's
filters are on Nuxt since #463: frontend/tests/map.test.ts."""

from tests.js_modules import run_js


def test_staff_navigation_links_keep_all_tenant_picks():
    run_js(
        """
import { readScope, updateTenantLinks } from "@js/lib/scope.js";
const links = ["/", "/alerts", "/alerts?view=wall", "/availability"].map((href) =>
  element("link", { attrs: { href } }));
document.querySelectorAll = () => links;
const scope = readScope("?tenant=Original&q=DNK");
updateTenantLinks(["Acme & Co", "Nordic Net"], scope);
for (const link of links.slice(0, 3)) {
  const url = new URL(link.attrs.href, "http://localhost");
  check(url.searchParams.getAll("tenant").join() === "Acme & Co,Nordic Net", "every pick");
  check(url.searchParams.get("q") === "DNK", "original text scope");
}
check(new URL(links[2].attrs.href, "http://localhost").searchParams.get("view") === "wall", "wall");
check(links[3].attrs.href === "/availability", "other pages unchanged");
updateTenantLinks([], scope);
check(links[0].attrs.href === "/?tenant=Original&q=DNK", "clearing restores incoming scope");
"""
    )
