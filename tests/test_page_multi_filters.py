"""Board, Availability and Maintenance use OR picks and AND filter fields."""

SELECT = """
function select(values = []) {
  const node = element("select", {
    appendChild(option) { this.options.push(option); return option; },
  });
  Object.defineProperty(node, "selectedOptions", { get() { return this.options.filter((o) => o.selected); } });
  Object.defineProperty(node, "textContent", { set() { this.options = []; } });
  node.options = values.map((value) => ({ value, selected: false }));
  return node;
}
"""
