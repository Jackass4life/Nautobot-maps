"""Run the browser's ES modules in Node for the tests (#294).

The modules in static/js/lib and static/js/alerts don't touch the page when
imported, so a test imports the real functions instead of copying their text
out of a file.  Page elements are plain objects the test puts in `els`.
"""

import os
import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
JS_ROOT = REPO_ROOT / "static" / "js"

# Before the test's code:
# - `check(condition, message)` throws when false;
# - `element(name, props)` is a stand-in for a page element; `focused` lists
#   the names of the elements that got focus, in order;
# - `document` is a page with no elements (a test can replace its methods);
# - `setGlobal(name, value)` replaces a browser global such as `navigator` or
#   `localStorage` (Node has its own, which plain assignment can't replace).
PRELUDE = """
function check(condition, message) { if (!condition) throw new Error(message); }
function setGlobal(name, value) {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}
const focused = [];
function element(name = "", props = {}) {
  const classes = new Set();
  return {
    name, textContent: "", innerHTML: "", value: "", hidden: false, disabled: false, checked: false,
    dataset: {}, attrs: {}, children: [], options: [],
    classList: {
      add: (...names) => names.forEach((c) => classes.add(c)),
      remove: (...names) => names.forEach((c) => classes.delete(c)),
      contains: (c) => classes.has(c),
      toggle(c, force) {
        const on = force === undefined ? !classes.has(c) : Boolean(force);
        if (on) classes.add(c); else classes.delete(c);
        return on;
      },
    },
    setAttribute(key, value) { this.attrs[key] = String(value); },
    getAttribute(key) { return key in this.attrs ? this.attrs[key] : null; },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener() {},
    focus() { focused.push(name); },
    closest() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    ...props,
  };
}
globalThis.document = {
  body: element("body"),
  documentElement: element("html"),
  createElement: (tag) => element(tag),
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
};
"""


def js_source(path):
    """The text of static/js/*path*, for checks on how the code is written."""
    return (JS_ROOT / path).read_text(encoding="utf-8")


def run_js(code, *, tz=None):
    """Run *code* as an ES module in Node; the test fails with its error output.

    Import paths starting with "@js/" are static/js/, e.g.
    `import { escHtml } from "@js/lib/html.js";`.  Top-level await works.
    *tz*: the time zone, e.g. "UTC", for checks on formatted times.
    """
    if shutil.which("node") is None:
        pytest.skip("node is required for the browser module tests")
    source = PRELUDE + code.replace('"@js/', f'"{JS_ROOT.as_uri()}/')
    env = {**os.environ, "TZ": tz} if tz else None
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
