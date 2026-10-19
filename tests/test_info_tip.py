"""The (i) tooltip (#263): a click or a tap keeps it open (#390)."""

from tests.js_modules import run_js


def test_a_click_pins_the_tip_until_a_click_elsewhere_escape_or_scroll():
    run_js(
        """
        setGlobal("Element", class {});
        const handlers = {};
        document.addEventListener = (type, fn) => { handlers[type] = fn; };
        setGlobal("window", { innerWidth: 1000, innerHeight: 800, addEventListener: (type, fn) => { handlers[`window-${type}`] = fn; } });
        document.createElement = (tag) => {
          const node = element(tag, { style: {}, offsetWidth: 200, offsetHeight: 40 });
          node.contains = (other) => other === node;
          return node;
        };
        const box = () => document.body.children[0];

        function glyph(name, text) {
          const node = element(name, {
            dataset: { infoTip: text },
            getBoundingClientRect: () => ({ left: 10, top: 10, width: 10, height: 10, bottom: 20 }),
          });
          node.closest = () => node;
          node.contains = (other) => other === node;
          node.isConnected = true;
          return Object.setPrototypeOf(node, Element.prototype);
        }
        const tip = glyph("tip", "Retail bank. 24/7 SLA");
        const other = glyph("other", "Regional ISP");
        const elsewhere = Object.setPrototypeOf(element("elsewhere"), Element.prototype);

        const { initInfoTips } = await import("@js/lib/tooltip.js");
        initInfoTips();
        const shown = () => box() && !box().hidden ? box().textContent : "";

        // Hover alone: open while the pointer is on it, closed when it leaves (as before).
        handlers.mouseover({ target: tip });
        check(shown() === "Retail bank. 24/7 SLA", "hover shows");
        handlers.mouseout({ target: tip, relatedTarget: elsewhere });
        check(shown() === "", "leaving closes an unpinned tip");

        // A click (or a tap) pins it, as a browser sends it: focus, then the click.
        handlers.focusin({ target: tip });
        handlers.click({ target: tip });
        handlers.mouseout({ target: tip, relatedTarget: elsewhere });
        check(shown() === "Retail bank. 24/7 SLA", "a click keeps it open");

        // Pinned, hover and focus elsewhere don't replace it.
        handlers.mouseover({ target: other });
        handlers.focusin({ target: other });
        check(shown() === "Retail bank. 24/7 SLA", "hovering another (i) keeps the pinned one");

        // A click in the tip's box moves focus off the glyph: it stays.
        handlers.focusout({ target: tip });
        handlers.click({ target: box() });
        check(shown() !== "", "a click in the box keeps it");

        // A click anywhere else closes it.
        handlers.click({ target: elsewhere });
        check(shown() === "", "a click elsewhere closes it");

        // A second click on the same (i) closes it, also after a refresh drew it again.
        handlers.click({ target: tip });
        check(shown() !== "", "pinned again");
        handlers.click({ target: tip });
        check(shown() === "", "a second click closes it");
        handlers.click({ target: tip });
        tip.isConnected = false;  // the row was drawn again
        const redrawn = glyph("tip", "Retail bank. 24/7 SLA");
        handlers.click({ target: redrawn });
        check(shown() === "", "the same (i), drawn again, closes it");

        // A click on another (i) moves the pin there.
        handlers.click({ target: redrawn });
        handlers.click({ target: other });
        check(shown() === "Regional ISP", "another (i) takes over");

        // Escape closes it, and nothing behind it.
        let stopped = false;
        handlers.keydown({ key: "Escape", stopPropagation: () => { stopped = true; } });
        check(shown() === "" && stopped, "Escape closes it alone");
        stopped = false;
        handlers.keydown({ key: "Escape", stopPropagation: () => { stopped = true; } });
        check(!stopped, "with no tip open, Escape goes on to the page");

        // A scroll elsewhere keeps it; one of the page or of its panel closes it.
        const panel = Object.setPrototypeOf(element("panel", { contains: (node) => node === other }), Element.prototype);
        const list = Object.setPrototypeOf(element("list", { contains: () => false }), Element.prototype);
        handlers.click({ target: other });
        handlers.scroll({ target: list });
        check(shown() === "Regional ISP", "an unrelated scroll keeps it");
        handlers.scroll({ target: panel });
        check(shown() === "", "its panel scrolling closes it");
        handlers.click({ target: other });
        handlers.scroll({ target: document });
        check(shown() === "", "the page scrolling closes it");
        """
    )
