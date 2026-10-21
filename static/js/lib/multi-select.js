// A native select remains the source of truth; importing this module needs no DOM.
const controls = new WeakMap();
let nextId = 0;

function asValues(values) {
  return (Array.isArray(values) ? values : [values])
    .filter((value) => value !== undefined && value !== null && value !== "");
}

export function selectedValues(select) {
  if (!select?.selectedOptions) return asValues(select?.value);
  return Array.from(select.selectedOptions, (option) => option.value).filter(Boolean);
}

export function matchesValues(actual, selected) {
  const wanted = asValues(selected);
  return !wanted.length || asValues(actual).some((value) => wanted.includes(value));
}

export function matchOptions(values, query, limit = 50) {
  const needle = String(query || "").toLocaleLowerCase();
  const matched = values.filter((value) => String(value).toLocaleLowerCase().includes(needle));
  const shown = matched.slice(0, Math.max(0, limit));
  return { shown, remaining: matched.length - shown.length };
}

export function filterLabel(values, allLabel) {
  const selected = asValues(values);
  if (!selected.length) return allLabel;
  return selected.length === 1 ? String(selected[0]) : `${selected[0]} +${selected.length - 1}`;
}

export function setSelectedValues(select, values) {
  if (!select) return;
  const wanted = asValues(values).map(String);
  for (const option of Array.from(select.options || [])) {
    option.selected = option.value ? wanted.includes(option.value) : !wanted.length;
  }
  // Older page tests supply a scalar stand-in rather than a native select.
  if (!select.selectedOptions) select.value = wanted[0] || "";
  controls.get(select)?.refresh();
}

export function setFilterOptions(select, values, label) {
  if (!select) return null;
  const selected = selectedValues(select);
  const choices = [...new Set(Array.from(values, String).filter(Boolean))];
  const doc = select.ownerDocument || document;
  select.textContent = "";
  for (const value of ["", ...choices]) {
    const option = doc.createElement("option");
    option.value = String(value);
    option.textContent = value === "" ? label : String(value);
    select.appendChild(option);
  }
  setSelectedValues(select, selected.filter((value) => choices.includes(value)));
  const control = initMultiSelect(select);
  if (control) control.refresh();
  else select.hidden = choices.length < 2;
  return control;
}

export function initMultiSelect(select) {
  if (controls.has(select)) return controls.get(select);
  // Scalar stand-ins used by page tests have no place to mount a control.
  if (!select?.parentNode) return null;
  const doc = select.ownerDocument || document;
  const initial = selectedValues(select);
  const id = `multi-select-${++nextId}`;
  const name = select.getAttribute("aria-label") || select.options[0]?.textContent || "Filter";
  const make = (tag, className) => {
    const node = doc.createElement(tag);
    node.className = className;
    return node;
  };
  const wrapper = make("div", "multi-select");
  const button = make("button", "multi-select-button");
  const popup = make("div", "multi-select-popup");
  const search = make("input", "multi-select-search");
  const list = make("div", "multi-select-list");
  const status = make("div", "multi-select-status");
  button.type = "button";
  button.setAttribute("role", "combobox");
  button.setAttribute("aria-label", name);
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-controls", `${id}-list`);
  button.setAttribute("aria-expanded", "false");
  search.type = "search";
  search.placeholder = "Search…";
  search.setAttribute("aria-label", `Search ${name}`);
  search.setAttribute("role", "combobox");
  search.setAttribute("aria-autocomplete", "list");
  search.setAttribute("aria-controls", `${id}-list`);
  search.setAttribute("aria-expanded", "false");
  list.id = `${id}-list`;
  list.tabIndex = 0;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", name);
  list.setAttribute("aria-multiselectable", "true");
  status.setAttribute("role", "status");
  popup.hidden = true;
  popup.appendChild(search);
  popup.appendChild(list);
  popup.appendChild(status);
  wrapper.appendChild(button);
  wrapper.appendChild(popup);
  select.parentNode.insertBefore(wrapper, select);
  wrapper.appendChild(select);
  select.multiple = true;
  select.hidden = true;
  let shown = [];
  let active = -1;

  function close(returnFocus = false) {
    popup.hidden = true;
    button.setAttribute("aria-expanded", "false");
    search.setAttribute("aria-expanded", "false");
    if (returnFocus) button.focus();
  }

  function markActive(index) {
    active = index;
    Array.from(list.children).forEach((node, position) => {
      node.classList.toggle("is-active", position === active);
    });
    const option = list.children[active];
    for (const node of [search, list]) {
      if (option) node.setAttribute("aria-activedescendant", option.id);
      else node.removeAttribute("aria-activedescendant");
    }
    option?.scrollIntoView?.({ block: "nearest" });
  }

  function pick(option) {
    if (!option || option.disabled || select.disabled) return;
    const values = selectedValues(select);
    setSelectedValues(select, !option.value ? [] : values.includes(option.value)
      ? values.filter((value) => value !== option.value) : [...values, option.value]);
    const EventClass = doc.defaultView?.Event || Event;
    select.dispatchEvent(new EventClass("input", { bubbles: true }));
    select.dispatchEvent(new EventClass("change", { bubbles: true }));
  }

  function render() {
    const options = Array.from(select.options).filter((option) => !option.disabled);
    // Match labels, not values (a native option may have an opaque ID).
    const labels = options.map((option) => option.textContent);
    const result = matchOptions(labels, search.value);
    const needle = search.value.toLocaleLowerCase();
    shown = options.filter((option) => option.textContent.toLocaleLowerCase().includes(needle))
      .slice(0, result.shown.length);
    list.textContent = "";
    const selected = selectedValues(select);
    for (const [index, option] of shown.entries()) {
      const row = make("div", "multi-select-option");
      row.id = `${id}-option-${index}`;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(option.value
        ? selected.includes(option.value) : !selected.length));
      const tick = make("span", "multi-select-tick");
      tick.setAttribute("aria-hidden", "true");
      tick.textContent = row.getAttribute("aria-selected") === "true" ? "✓" : "";
      const text = make("span", "multi-select-option-label");
      text.textContent = option.textContent;
      row.appendChild(tick);
      row.appendChild(text);
      row.addEventListener("mousedown", (event) => event.preventDefault());
      row.addEventListener("click", () => {
        active = index;
        pick(option);
      });
      list.appendChild(row);
    }
    status.textContent = result.remaining ? `${result.remaining} more — refine your search`
      : shown.length ? "" : "No matches";
    status.hidden = !status.textContent;
    markActive(shown.length ? Math.min(Math.max(active, 0), shown.length - 1) : -1);
  }

  function refresh() {
    const values = selectedValues(select);
    const labels = values.map((value) =>
      Array.from(select.options).find((option) => option.value === value)?.textContent || value);
    const allLabel = select.options[0]?.textContent || "All";
    button.textContent = filterLabel(labels, allLabel);
    button.title = labels.length ? labels.join(", ") : allLabel;
    button.disabled = select.disabled;
    wrapper.hidden = Array.from(select.options).filter((option) => option.value).length < 2;
    if (wrapper.hidden || select.disabled) close();
    render();
  }

  function open() {
    if (select.disabled) return;
    search.value = "";
    active = -1;
    popup.hidden = false;
    button.setAttribute("aria-expanded", "true");
    search.setAttribute("aria-expanded", "true");
    render();
    search.focus();
  }

  button.addEventListener("click", () => popup.hidden ? open() : close());
  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      open();
      markActive(event.key === "ArrowUp" ? shown.length - 1 : 0);
    }
  });
  search.addEventListener("input", () => {
    active = -1;
    render();
  });
  wrapper.addEventListener("keydown", (event) => {
    if (popup.hidden) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (event.target === button) {
      return;
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (shown.length) markActive((active + (event.key === "ArrowDown" ? 1 : -1)
        + shown.length) % shown.length);
    } else if (event.key === "Enter" || (event.key === " " && event.target !== search)) {
      event.preventDefault();
      pick(shown[active < 0 ? 0 : active]);
    }
  });
  wrapper.addEventListener("focusout", (event) => {
    if (!wrapper.contains(event.relatedTarget)) close();
  });
  doc.addEventListener("click", (event) => {
    // Picking redraws the options before the click reaches the document.
    if (!event.composedPath?.().includes(wrapper) && !wrapper.contains(event.target)) close();
  });
  select.addEventListener("change", refresh);
  const controller = { wrapper, button, search, list, open, close, refresh };
  controls.set(select, controller);
  setSelectedValues(select, initial);
  return controller;
}
