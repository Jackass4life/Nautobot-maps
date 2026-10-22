"""The shared multi-select runs its real helpers and control in Node."""

from tests.js_modules import run_js

DOM = """
function domNode(tag) {
  const listeners = new Map();
  const node = element(tag, {
    tagName: tag.toUpperCase(), parentNode: null, ownerDocument: document,
    removeAttribute(key) { delete this.attrs[key]; },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    appendChild(child) {
      if (child.parentNode) {
        child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
      }
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    insertBefore(child, before) {
      child.parentNode = this;
      this.children.splice(this.children.indexOf(before), 0, child);
    },
    contains(child) {
      return child === this || this.children.some((item) => item.contains(child));
    },
    dispatchEvent(event) {
      if (!event.target) Object.defineProperty(event, "target", { value: this });
      if (!event.path) {
        event.path = [];
        for (let node = this; node; node = node.parentNode) event.path.push(node);
        event.composedPath = () => event.path;
      }
      for (const fn of listeners.get(event.type) || []) fn(event);
      if (event.bubbles && !event.cancelBubble) this.parentNode?.dispatchEvent(event);
      return !event.defaultPrevented;
    },
    focus() { document.activeElement = this; focused.push(tag); },
  });
  let text = "";
  Object.defineProperty(node, "textContent", {
    get() { return text || this.children.map((child) => child.textContent).join(""); },
    set(value) {
      text = String(value);
      for (const child of this.children) child.parentNode = null;
      this.children = [];
    },
  });
  if (tag === "select") {
    Object.defineProperty(node, "options", { get() { return this.children; } });
    Object.defineProperty(node, "selectedOptions", {
      get() { return this.options.filter((option) => option.selected); },
    });
  }
  return node;
}
document.createElement = domNode;
const root = domNode("main");
document.addEventListener = root.addEventListener.bind(root);
document.defaultView = { Event };
function selectWith(values, label = "All tenants") {
  const select = domNode("select");
  select.id = "tenant-filter";
  select.setAttribute("aria-label", "Tenants");
  for (const value of ["", ...values]) {
    const option = domNode("option");
    option.value = value;
    option.textContent = value || label;
    option.selected = !value;
    select.appendChild(option);
  }
  root.appendChild(select);
  return select;
}
function fire(node, type, props = {}) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  for (const [key, value] of Object.entries(props)) {
    Object.defineProperty(event, key, { value });
  }
  node.dispatchEvent(event);
  return event;
}
function key(node, value) { return fire(node, "keydown", { key: value }); }
"""


def test_pure_matching_labels_and_selected_values():
    run_js("""
    import {
      selectedValues, matchesValues, matchOptions, filterLabel, setSelectedValues,
    } from "@js/lib/multi-select.js";
    check(selectedValues(element("legacy", {value: "Acme"})).join() === "Acme", "scalar fallback");
    check(selectedValues(element()).length === 0, "empty scalar");
    const select = { selectedOptions: [{value: ""}, {value: "A"}, {value: "B"}] };
    check(selectedValues(select).join() === "A,B", "native selected options");
    check(matchesValues("A", []) && matchesValues(["A"], ""), "all");
    check(matchesValues(["B", "A"], ["A", "C"]), "any array match");
    check(matchesValues("A", "A"), "scalar selected");
    check(!matchesValues("AA", ["A"]) && !matchesValues("a", ["A"]), "exact");
    check(!matchesValues(null, ["A"]), "missing actual");
    check(filterLabel([], "All") === "All", "all summary");
    check(filterLabel(["A"], "All") === "A", "single summary");
    check(filterLabel(["A", "B", "C"], "All") === "A +2", "multiple summary");
    const values = Array.from({length: 500}, (_, i) => `Tenant ${i}`);
    const page = matchOptions(values, "");
    check(page.shown.length === 50 && page.remaining === 450, "bounded 500 choices");
    const narrowed = matchOptions(values, "TENANT 49");
    check(narrowed.shown.length === 11 && narrowed.remaining === 0, "case insensitive");
    check(matchOptions(values, "absent").shown.length === 0, "no matches");
    check(matchOptions(values, "", 3).remaining === 497, "custom limit");
    const legacy = element("legacy", { value: "old" });
    setSelectedValues(legacy, []);
    check(legacy.value === "", "clear scalar stand-in");
    """)


def test_initialization_refresh_and_safe_option_labels():
    run_js(
        DOM
        + """
    import {
      initMultiSelect, selectedValues, setSelectedValues, setFilterOptions,
    } from "@js/lib/multi-select.js";
    const select = selectWith(["Acme", "Beta", "<img src=x onerror=alert(1)>"]);
    const control = initMultiSelect(select);
    check(initMultiSelect(select) === control, "idempotent");
    check(root.children.length === 1 && control.wrapper.children.length === 3, "one wrapper");
    check(select.id === "tenant-filter" && select.multiple && select.hidden, "native backing");
    check(control.button.getAttribute("aria-label") === "Tenants", "original label");
    check(control.button.textContent === "All tenants", "first option label");
    check(control.search.getAttribute("aria-label") === "Search Tenants", "search label");
    check(control.list.getAttribute("aria-multiselectable") === "true", "multiple list");
    check(control.button.getAttribute("aria-controls") === control.list.id, "linked list");
    let events = 0;
    select.addEventListener("change", () => events++);
    select.addEventListener("input", () => events++);
    setSelectedValues(select, ["Acme", "Beta"]);
    check(control.button.textContent === "Acme +1", "summary updates");
    check(events === 0, "setter never dispatches");
    setFilterOptions(select, ["Beta", "Gamma"], "All customers");
    check(selectedValues(select).join() === "Beta", "refresh intersects selection");
    check(control.button.textContent === "Beta" && events === 0, "refresh stays silent");
    setSelectedValues(select, []);
    check(selectedValues(select).length === 0, "empty setter clears");
    check(control.button.textContent === "All customers", "new all label");
    setFilterOptions(select, ["<script>unsafe</script>", "Other"], "All");
    check(select.options[1].textContent === "<script>unsafe</script>", "safe native text");
    check(control.list.children[1].children[1].textContent === "<script>unsafe</script>",
      "safe row text");
    select.options[1].selected = true;
    fire(select, "change");
    check(control.button.textContent === "<script>unsafe</script>", "external native change");
    setFilterOptions(select, ["Other"], "All");
    check(control.wrapper.hidden, "singleton hidden by default");
    setFilterOptions(select, [], "All");
    check(control.wrapper.hidden && selectedValues(select).length === 0, "empty refresh");
    setFilterOptions(select, ["Other", "Third"], "All");
    check(!control.wrapper.hidden, "choices restore wrapper");
    """
    )


def test_plain_page_stubs_keep_scalar_selections_and_visibility():
    run_js("""
    import {
      initMultiSelect, selectedValues, setSelectedValues, setFilterOptions,
    } from "@js/lib/multi-select.js";
    const select = element("tenant", { value: "Beta" });
    check(initMultiSelect(select) === null, "no parent means no generated DOM");
    setFilterOptions(select, ["Acme", "Beta"], "All tenants");
    check(!select.hidden && select.value === "Beta", "scalar selection preserved");
    check(select.children[0].value === "" &&
      select.children[0].textContent === "All tenants", "All option");
    setFilterOptions(select, ["Acme"], "All tenants");
    check(select.hidden && select.value === "", "singleton hidden, stale scalar removed");
    setFilterOptions(select, ["Acme", "Beta"], "All tenants");
    check(!select.hidden, "multiple choices shown");
    setSelectedValues(select, ["Acme"]);
    check(selectedValues(select).join() === "Acme", "scalar setter");
    setSelectedValues(select, []);
    check(select.value === "", "scalar clear");
    check(selectedValues(null).length === 0 && initMultiSelect(null) === null, "absent controls");
    check(setFilterOptions(null, [], "All") === null, "absent filter");
    setSelectedValues(null, []);
    """)


def test_mouse_keyboard_events_focus_and_local_search():
    run_js(
        DOM
        + """
    import { initMultiSelect, selectedValues } from "@js/lib/multi-select.js";
    const select = selectWith(Array.from({length: 500}, (_, i) => `Tenant ${i}`));
    const control = initMultiSelect(select);
    let input = 0, change = 0;
    root.addEventListener("input", (event) => { if (event.target === select) input++; });
    root.addEventListener("change", (event) => { if (event.target === select) change++; });
    fire(control.button, "click");
    check(control.button.getAttribute("aria-expanded") === "true", "opens");
    check(document.activeElement === control.search, "search focused");
    check(control.list.children.length === 50, "bounded rendered list");
    check(control.wrapper.children[1].children[2].textContent.includes("451 more"), "overflow hint");
    fire(control.list.children[1], "click");
    fire(control.list.children[2], "click");
    check(selectedValues(select).join() === "Tenant 0,Tenant 1", "multiple mouse picks");
    check(control.button.getAttribute("aria-expanded") === "true", "mouse picks stay open");
    check(input === 2 && change === 2, "both bubbling native events");
    check(control.list.children[1].getAttribute("aria-selected") === "true", "tick aria");
    check(control.list.children[1].children[0].textContent === "✓", "visible tick");
    control.search.value = "tENANT 499";
    fire(control.search, "input");
    check(control.list.children.length === 1, "local search beyond initial 50");
    check(!key(control.search, " ").defaultPrevented, "spaces type in search");
    key(control.search, "Enter");
    check(selectedValues(select).includes("Tenant 499"), "enter picks first result");
    key(control.list, " ");
    check(!selectedValues(select).includes("Tenant 499"), "space toggles in list");
    control.search.value = "not found";
    fire(control.search, "input");
    key(control.search, "Enter");
    check(control.list.children.length === 0, "no result enter harmless");
    check(control.wrapper.children[1].children[2].textContent === "No matches", "no match message");
    const escape = key(control.search, "Escape");
    check(escape.defaultPrevented && escape.cancelBubble, "escape handled locally");
    check(document.activeElement === control.button, "escape restores button focus");
    check(control.button.getAttribute("aria-expanded") === "false", "escape closes");
    key(control.button, "ArrowUp");
    check(control.search.getAttribute("aria-activedescendant").endsWith("-49"), "arrow up last");
    key(control.search, "ArrowDown");
    check(control.search.getAttribute("aria-activedescendant").endsWith("-0"), "arrow wraps");
    key(control.search, "ArrowDown");
    key(control.search, "Enter");
    check(!selectedValues(select).includes("Tenant 0"), "arrow chooses active option");
    fire(control.list.children[0], "click");
    check(!selectedValues(select).length, "All clears selection");
    fire(control.wrapper, "focusout", {relatedTarget: control.search});
    check(control.button.getAttribute("aria-expanded") === "true", "internal focus stays open");
    fire(control.wrapper, "focusout", {relatedTarget: root});
    check(control.button.getAttribute("aria-expanded") === "false", "outside focus closes");
    control.open();
    fire(root, "click");
    check(control.button.getAttribute("aria-expanded") === "false", "outside click closes");
    select.disabled = true;
    fire(select, "change");
    control.open();
    check(control.button.disabled &&
      control.button.getAttribute("aria-expanded") === "false", "disabled stays closed");
    """
    )
