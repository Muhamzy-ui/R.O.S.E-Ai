/**
 * Automated Node.js v24 Test Suite for R.O.S.E Manifest V3 Chrome Extension (Phase 4)
 * Tests actual extension source files (src/dom_utils.js, src/adapters.js, src/autofill_engine.js)
 * against plain, React-controlled, iframe, and nested open Shadow DOM patterns + ATS adapters.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const EXT_DIR = path.join(ROOT, "rose_autofill_extension");
const TEST_PAGES_DIR = path.join(ROOT, "tests", "autofill_test_pages");

// ── Minimal Spec-Faithful DOM Harness (No external dependencies) ─────────────
class MiniEvent {
  constructor(type, opts = {}) {
    this.type = type;
    this.bubbles = Boolean(opts.bubbles);
    this.composed = Boolean(opts.composed);
    this.target = null;
  }
}

class MiniElement {
  constructor(tagName, attrs = {}) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.parentElement = null;
    this.ownerDocument = null;
    this.style = {};
    this._listeners = new Map();
    this.shadowRoot = null;
    this.textContent = "";
    this.disabled = false;
    this.readOnly = false;
    this.hidden = false;

    for (const [k, v] of Object.entries(attrs)) {
      this.setAttribute(k, v);
    }
  }

  setAttribute(k, v) {
    const key = String(k).toLowerCase();
    const val = String(v);
    this.attributes.set(key, val);
    if (key === "disabled") this.disabled = true;
    if (key === "readonly") this.readOnly = true;
    if (key === "hidden") this.hidden = true;
    if (key === "id") this.id = val;
    if (key === "name") this.name = val;
    if (key === "type") this.type = val;
    if (key === "placeholder") this.placeholder = val;
  }

  getAttribute(k) {
    const v = this.attributes.get(String(k).toLowerCase());
    return v === undefined ? null : v;
  }

  appendChild(child) {
    child.parentElement = this;
    child.ownerDocument = this.ownerDocument;
    if (child instanceof MiniShadowRoot) {
      this.shadowRoot = child;
    } else {
      this.children.push(child);
    }
    const propagateDoc = (node, doc) => {
      node.ownerDocument = doc;
      if (node.shadowRoot) propagateDoc(node.shadowRoot, doc);
      for (const c of node.children || []) propagateDoc(c, doc);
    };
    propagateDoc(child, this.ownerDocument);
    return child;
  }

  attachShadow({ mode }) {
    const sr = new MiniShadowRoot(this, mode);
    this.shadowRoot = sr;
    sr.ownerDocument = this.ownerDocument;
    return sr;
  }

  getRootNode() {
    let cur = this;
    while (cur) {
      if (cur instanceof MiniShadowRoot) return cur;
      if (!cur.parentElement) return this.ownerDocument;
      cur = cur.parentElement;
    }
    return this.ownerDocument;
  }

  get previousElementSibling() {
    if (!this.parentElement) return null;
    const idx = this.parentElement.children.indexOf(this);
    return idx > 0 ? this.parentElement.children[idx - 1] : null;
  }

  closest(selectorStr) {
    const tokens = selectorStr.split(",").map((s) => s.trim().toLowerCase());
    let cur = this;
    while (cur && cur.nodeType === 1) {
      const tag = cur.tagName.toLowerCase();
      const cls = (cur.getAttribute("class") || "").toLowerCase();
      const role = (cur.getAttribute("role") || "").toLowerCase();
      for (const tok of tokens) {
        if (tok === tag) return cur;
        if (tok.startsWith(".") && cls.split(/\s+/).includes(tok.slice(1))) return cur;
        if (tok === "[role='radiogroup']" && role === "radiogroup") return cur;
        if (tok.includes("*='question'") && cls.includes("question")) return cur;
        if (tok.includes("*='field'") && cls.includes("field")) return cur;
      }
      cur = cur.parentElement;
    }
    return null;
  }

  querySelector(sel) {
    const matches = this.querySelectorAll(sel);
    return matches[0] || null;
  }

  querySelectorAll(sel) {
    const out = [];
    const parts = sel.split(",").map((s) => s.trim());
    const visit = (node) => {
      for (const c of node.children || []) {
        for (const p of parts) {
          if (matchesSimpleSelector(c, p)) {
            out.push(c);
            break;
          }
        }
        visit(c);
      }
    };
    visit(this);
    return out;
  }

  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }

  dispatchEvent(ev) {
    ev.target = this;
    let cur = this;
    let first = true;
    while (cur) {
      if (first || ev.bubbles) {
        const list = (cur._listeners && cur._listeners.get(ev.type)) || [];
        for (const fn of list) fn.call(cur, ev);
      }
      first = false;
      if (!ev.bubbles) break;
      if (cur.parentElement) {
        cur = cur.parentElement;
      } else if (cur instanceof MiniShadowRoot && ev.composed) {
        cur = cur.host;
      } else {
        cur = null;
      }
    }
    return true;
  }

  focus() {}
}

class MiniShadowRoot extends MiniElement {
  constructor(host, mode) {
    super("#shadow-root");
    this.host = host;
    this.mode = mode;
    this.parentElement = null;
  }
}

class MiniHTMLInputElement extends MiniElement {
  constructor(attrs = {}) {
    super("input", attrs);
    this._val = attrs.value != null ? String(attrs.value) : "";
    this._chk = Boolean(attrs.checked);
  }
}
Object.defineProperty(MiniHTMLInputElement.prototype, "value", {
  configurable: true,
  get() { return this._val; },
  set(v) { this._val = String(v == null ? "" : v); }
});
Object.defineProperty(MiniHTMLInputElement.prototype, "checked", {
  configurable: true,
  get() { return this._chk; },
  set(v) { this._chk = Boolean(v); }
});

class MiniHTMLTextAreaElement extends MiniElement {
  constructor(attrs = {}) {
    super("textarea", attrs);
    this._val = "";
  }
}
Object.defineProperty(MiniHTMLTextAreaElement.prototype, "value", {
  configurable: true,
  get() { return this._val; },
  set(v) { this._val = String(v == null ? "" : v); }
});

class MiniHTMLSelectElement extends MiniElement {
  constructor(attrs = {}, optionsList = []) {
    super("select", attrs);
    this._val = "";
    for (const o of optionsList) {
      const opt = new MiniElement("option", { value: o.value });
      opt.textContent = o.text;
      opt.text = o.text;
      opt.value = o.value;
      this.appendChild(opt);
    }
  }
  get options() {
    return this.children;
  }
}
Object.defineProperty(MiniHTMLSelectElement.prototype, "value", {
  configurable: true,
  get() { return this._val; },
  set(v) { this._val = String(v == null ? "" : v); }
});

function matchesSimpleSelector(el, sel) {
  if (!el || el.nodeType !== 1) return false;
  const tag = el.tagName.toLowerCase();
  const s = sel.trim();
  if (s === tag) return true;
  if (s.startsWith("#") && el.id === s.slice(1)) return true;
  if (s.startsWith(".") && (el.getAttribute("class") || "").split(/\s+/).includes(s.slice(1))) return true;
  const mFor = s.match(/^label\[for="(.+)"\]$/);
  if (mFor && tag === "label" && el.getAttribute("for") === mFor[1]) return true;
  if (s === "input[name='urls[LinkedIn]']" && tag === "input" && el.name === "urls[LinkedIn]") return true;
  if (s === "[name^='_systemfield_']" && el.name && el.name.startsWith("_systemfield_")) return true;
  if (s === "[data-ui='application-form']" && el.getAttribute("data-ui") === "application-form") return true;
  return false;
}

function createWindowAndDoc(url = "https://example.com/apply") {
  const doc = {
    location: { href: url },
    body: new MiniElement("body"),
    documentElement: null,
    defaultView: null,
    getElementById(id) { return this.body.querySelector("#" + id); },
    querySelector(sel) { return this.body.querySelector(sel); },
    querySelectorAll(sel) { return this.body.querySelectorAll(sel); }
  };
  doc.body.ownerDocument = doc;
  doc.documentElement = doc.body;
  const win = {
    location: { href: url },
    HTMLInputElement: MiniHTMLInputElement,
    HTMLTextAreaElement: MiniHTMLTextAreaElement,
    HTMLSelectElement: MiniHTMLSelectElement,
    Event: MiniEvent,
    InputEvent: MiniEvent,
    getComputedStyle: () => ({ display: "block", visibility: "visible" })
  };
  doc.defaultView = win;
  return { win, doc };
}

function makeLabeledField(parent, doc, labelText, control) {
  const lbl = new MiniElement("label");
  lbl.textContent = labelText;
  if (control.id) lbl.setAttribute("for", control.id);
  parent.appendChild(lbl);
  if (lbl !== control) lbl.appendChild(control);
  control.ownerDocument = doc;
  return control;
}

// Install global browser constructors BEFORE requiring extension scripts
globalThis.window = { location: { href: "https://example.com" }, HTMLInputElement: MiniHTMLInputElement, HTMLTextAreaElement: MiniHTMLTextAreaElement, HTMLSelectElement: MiniHTMLSelectElement, Event: MiniEvent, InputEvent: MiniEvent };
globalThis.HTMLInputElement = MiniHTMLInputElement;
globalThis.HTMLTextAreaElement = MiniHTMLTextAreaElement;
globalThis.HTMLSelectElement = MiniHTMLSelectElement;
globalThis.Event = MiniEvent;
globalThis.InputEvent = MiniEvent;

const domUtils = require(path.join(EXT_DIR, "src", "dom_utils.js"));
const adapters = require(path.join(EXT_DIR, "src", "adapters.js"));
const engine = require(path.join(EXT_DIR, "src", "autofill_engine.js"));

const profile = JSON.parse(fs.readFileSync(path.join(ROOT, "rose_profile.json"), "utf8"));

// ── Test 0: Manifest V3, Files & Local HTML Pages Presence ──────────────────
const manifest = JSON.parse(fs.readFileSync(path.join(EXT_DIR, "manifest.json"), "utf8"));
assert.equal(manifest.manifest_version, 3);
assert.equal(manifest.commands["trigger-autofill"].suggested_key.default, "Alt+Shift+F");
assert.equal(manifest.content_scripts[0].all_frames, true);

for (const page of ["plain_form.html", "react_controlled.html", "iframe_form.html", "iframe_child.html", "shadow_dom_form.html"]) {
  const html = fs.readFileSync(path.join(TEST_PAGES_DIR, page), "utf8");
  assert.ok(html.length > 200, `${page} exists cleanly`);
}

// ── Test 1: Plain Form (matches plain_form.html structure) ──────────────────
{
  const { win, doc } = createWindowAndDoc("file:///tests/autofill_test_pages/plain_form.html");
  globalThis.window = win;
  const form = new MiniElement("form", { id: "application_form" });
  doc.body.appendChild(form);
  let submitClicks = 0;

  const fn = makeLabeledField(form, doc, "First Name *", new MiniHTMLInputElement({ id: "first_name", name: "first_name", autocomplete: "given-name", type: "text" }));
  const ln = makeLabeledField(form, doc, "Last Name *", new MiniHTMLInputElement({ id: "last_name", name: "last_name", autocomplete: "family-name", type: "text" }));
  const em = makeLabeledField(form, doc, "Email *", new MiniHTMLInputElement({ id: "email", name: "email", autocomplete: "email", type: "email" }));
  const ph = makeLabeledField(form, doc, "Phone *", new MiniHTMLInputElement({ id: "phone", name: "phone", autocomplete: "tel", type: "tel" }));
  const loc = makeLabeledField(form, doc, "Location", new MiniHTMLInputElement({ id: "location", name: "job_application_location", type: "text" }));
  const yoe = makeLabeledField(form, doc, "Years Experience", new MiniHTMLInputElement({ id: "years_exp", name: "years_experience", type: "text" }));
  const gh = makeLabeledField(form, doc, "GitHub", new MiniHTMLInputElement({ id: "github", name: "github_url", type: "url" }));
  const li = makeLabeledField(form, doc, "LinkedIn", new MiniHTMLInputElement({ id: "linkedin", name: "linkedin_profile", type: "url" }));
  const pf = makeLabeledField(form, doc, "Portfolio", new MiniHTMLInputElement({ id: "portfolio", name: "website", type: "url" }));
  const wa = makeLabeledField(form, doc, "Authorized to work?", new MiniHTMLSelectElement({ id: "work_auth", name: "work_authorized" }, [
    { value: "", text: "-- Select --" },
    { value: "yes", text: "Yes" },
    { value: "no", text: "No" }
  ]));
  const fsAuth = new MiniElement("fieldset");
  const leg = new MiniElement("legend"); leg.textContent = "Will you require visa sponsorship?"; fsAuth.appendChild(leg);
  const rYes = new MiniHTMLInputElement({ type: "radio", name: "require_sponsorship", value: "Yes" });
  const rNo = new MiniHTMLInputElement({ type: "radio", name: "require_sponsorship", value: "No" });
  fsAuth.appendChild(rYes); fsAuth.appendChild(rNo);
  form.appendChild(fsAuth);

  const proj = makeLabeledField(form, doc, "Short Project Highlights", new MiniHTMLTextAreaElement({ id: "projects", name: "project_highlights" }));
  makeLabeledField(form, doc, "Resume", new MiniHTMLInputElement({ id: "resume", name: "resume", type: "file" }));
  makeLabeledField(form, doc, "Cover Letter", new MiniHTMLTextAreaElement({ id: "cover_letter", name: "cover_letter" }));

  const summary = engine.fillDocument(doc, profile);
  assert.equal(fn.value, "Mahmud");
  assert.equal(ln.value, "Bashir");
  assert.equal(em.value, profile.email);
  assert.equal(ph.value, profile.phone);
  assert.equal(loc.value, "Abuja, Nigeria");
  assert.equal(yoe.value, "3");
  assert.equal(gh.value, profile.github);
  assert.equal(li.value, profile.linkedin);
  assert.equal(pf.value, profile.portfolio);
  assert.equal(wa.value, "yes");
  assert.equal(rYes.checked, false);
  assert.equal(rNo.checked, true);
  assert.ok(proj.value.includes("PostgreSQL"));
  assert.equal(summary.filled, 12);
  assert.equal(summary.total, 14);
  assert.deepEqual(summary.skipped, ["resume upload", "cover letter"]);
  assert.equal(summary.needsManualResume, true);
  assert.equal(submitClicks, 0);

  const toast = engine.formatToastText(summary);
  assert.equal(toast.headline, "Filled 12/14 fields. Skipped: resume upload, cover letter.");
}

// ── Test 2: React-Style Controlled Inputs (Native Prototype Setter Trap) ───
{
  const { win, doc } = createWindowAndDoc("https://jobs.ashbyhq.com/acme/apply");
  globalThis.window = win;
  const reactStore = {};

  function bindReactTrap(el, key, propName = "value") {
    const protoDesc = Object.getOwnPropertyDescriptor(el.constructor.prototype, propName);
    let innerVal = propName === "checked" ? false : "";
    // Overrides naive `el.value = "foo"` just like react_controlled.html!
    Object.defineProperty(el, propName, {
      configurable: true,
      get() { return protoDesc.get.call(this); },
      set(_) { /* Ignore naive non-native set! */ protoDesc.set.call(this, innerVal); }
    });
    el.addEventListener("input", () => { innerVal = protoDesc.get.call(el); reactStore[key] = innerVal; });
    el.addEventListener("change", () => { innerVal = protoDesc.get.call(el); reactStore[key] = innerVal; });
    return el;
  }

  const n = bindReactTrap(new MiniHTMLInputElement({ id: "rc-name", name: "_systemfield_name", type: "text" }), "name");
  const e = bindReactTrap(new MiniHTMLInputElement({ id: "rc-email", name: "_systemfield_email", type: "email" }), "email");
  const p = bindReactTrap(new MiniHTMLInputElement({ id: "rc-phone", name: "_systemfield_phone", type: "tel" }), "phone");
  const l = bindReactTrap(new MiniHTMLInputElement({ id: "rc-loc", name: "_systemfield_location", type: "text" }), "location");
  const g = bindReactTrap(new MiniHTMLInputElement({ id: "rc-gh", placeholder: "https://github.com/...", type: "url" }), "github");
  const y = bindReactTrap(new MiniHTMLSelectElement({ id: "rc-yoe", name: "years_experience" }, [
    { value: "", text: "Choose" }, { value: "3", text: "2-4 years" }
  ]), "yoe");
  const cb = bindReactTrap(new MiniHTMLInputElement({ id: "rc-cb", name: "work_authorized", type: "checkbox" }), "auth_cb", "checked");

  [n, e, p, l, g, y, cb].forEach((c) => doc.body.appendChild(c));
  doc.body.appendChild(new MiniHTMLInputElement({ type: "file", name: "resume_upload" }));
  doc.body.appendChild(new MiniHTMLTextAreaElement({ name: "cover_letter" }));

  const summary = engine.fillDocument(doc, profile);
  assert.equal(summary.adapter, "ashby");
  assert.equal(reactStore.name, profile.full_name);
  assert.equal(reactStore.email, profile.email);
  assert.equal(reactStore.phone, profile.phone);
  assert.equal(reactStore.location, profile.location);
  assert.equal(reactStore.github, profile.github);
  assert.equal(reactStore.yoe, "3");
  assert.equal(reactStore.auth_cb, true);
  assert.equal(summary.filled, 7);
  assert.equal(summary.total, 9);
}

// ── Test 3: Multi-Frame / Nested Iframe Aggregation Pattern ────────────────
{
  const { win: wTop, doc: dTop } = createWindowAndDoc("https://company.com/careers");
  globalThis.window = wTop;
  dTop.body.appendChild(new MiniHTMLInputElement({ id: "top_name", name: "full_name", type: "text" }));
  dTop.body.appendChild(new MiniHTMLInputElement({ id: "top_email", name: "email", type: "email" }));
  const rTop = engine.fillDocument(dTop, profile);

  const { win: wChild, doc: dChild } = createWindowAndDoc("https://jobs.lever.co/acme/apply");
  globalThis.window = wChild;
  dChild.body.appendChild(new MiniHTMLInputElement({ name: "phone", type: "tel" }));
  dChild.body.appendChild(new MiniHTMLInputElement({ name: "urls[GitHub]", type: "url" }));
  dChild.body.appendChild(new MiniHTMLInputElement({ name: "urls[LinkedIn]", type: "url" }));
  dChild.body.appendChild(new MiniHTMLInputElement({ name: "resume", type: "file" }));
  dChild.body.appendChild(new MiniHTMLTextAreaElement({ name: "comments" })); // Lever cover letter/additional info
  const rChild = engine.fillDocument(dChild, profile);

  assert.equal(rTop.filled, 2);
  assert.equal(rChild.adapter, "lever");
  assert.equal(rChild.filled, 3);
  assert.equal(rTop.total + rChild.total, 7);
  assert.equal(rTop.filled + rChild.filled, 5);
  assert.deepEqual(rChild.skipped, ["resume upload", "cover letter"]);
}

// ── Test 4: Nested Open Shadow DOM Pattern + Composed Event Bubbling ─────────
{
  const { win, doc } = createWindowAndDoc("https://example.com/shadow-apply");
  globalThis.window = win;

  const hostForm = new MiniElement("rose-job-form");
  doc.body.appendChild(hostForm);
  const sr1 = hostForm.attachShadow({ mode: "open" });

  let composedBubbledToHost = 0;
  hostForm.addEventListener("input", () => { composedBubbledToHost++; });

  const sName = new MiniHTMLInputElement({ name: "full_name", autocomplete: "name", type: "text" });
  const sEmail = new MiniHTMLInputElement({ name: "email", autocomplete: "email", type: "email" });
  const sPhone = new MiniHTMLInputElement({ name: "phone", autocomplete: "tel", type: "tel" });
  const sLoc = new MiniHTMLInputElement({ name: "location", type: "text" });
  const sGh = new MiniHTMLInputElement({ name: "github", type: "url" });
  const sLi = new MiniHTMLInputElement({ name: "linkedin", type: "url" });
  const sPf = new MiniHTMLInputElement({ name: "portfolio", type: "url" });
  [sName, sEmail, sPhone, sLoc, sGh, sLi, sPf].forEach((el) => sr1.appendChild(el));

  // Nested second shadow root inside first shadow root
  const authWidget = new MiniElement("rose-auth-widget");
  sr1.appendChild(authWidget);
  const sr2 = authWidget.attachShadow({ mode: "open" });
  const sAuthSel = new MiniHTMLSelectElement({ name: "authorized_to_work" }, [
    { value: "", text: "-- Select --" }, { value: "yes", text: "Yes" }, { value: "no", text: "No" }
  ]);
  const fs = new MiniElement("fieldset");
  const leg = new MiniElement("legend"); leg.textContent = "Will you require visa sponsorship?"; fs.appendChild(leg);
  const sRadioYes = new MiniHTMLInputElement({ type: "radio", name: "require_sponsorship", value: "Yes" });
  const sRadioNo = new MiniHTMLInputElement({ type: "radio", name: "require_sponsorship", value: "No" });
  fs.appendChild(sRadioYes); fs.appendChild(sRadioNo);
  const sProj = new MiniHTMLTextAreaElement({ name: "project_highlights" });
  const sRes = new MiniHTMLInputElement({ type: "file", name: "resume" });
  const sCov = new MiniHTMLTextAreaElement({ name: "cover_letter" });
  [sAuthSel, fs, sProj, sRes, sCov].forEach((el) => sr2.appendChild(el));

  const summary = engine.fillDocument(doc, profile);
  assert.equal(summary.filled, 10);
  assert.equal(summary.total, 12);
  assert.deepEqual(summary.skipped, ["resume upload", "cover letter"]);
  assert.equal(sName.value, profile.full_name);
  assert.equal(sAuthSel.value, "yes");
  assert.equal(sRadioNo.checked, true);
  assert.ok(composedBubbledToHost >= 9, "Shadow-piercing composed events reached outer host");
}

// ── Test 5: ATS Adapters & Negative Field Guards ───────────────────────────
{
  assert.equal(adapters.detectSiteAdapter("https://boards.greenhouse.io/company/jobs/123", null), "greenhouse");
  assert.equal(adapters.detectSiteAdapter("https://jobs.lever.co/company/abc", null), "lever");
  assert.equal(adapters.detectSiteAdapter("https://jobs.ashbyhq.com/company/xyz", null), "ashby");
  assert.equal(adapters.detectSiteAdapter("https://apply.workable.com/company/j/123/", null), "workable");
  assert.equal(adapters.detectSiteAdapter("https://smartapply.indeed.com/beta/indeedapply", null), "indeed");

  // Ensure Company Name / Employer / Referral / File Name are NOT filled with Mahmud's name
  const compEl = new MiniHTMLInputElement({ name: "company_name", placeholder: "Company Name" });
  const compKey = adapters.resolveFieldSemanticKey(compEl, domUtils.extractFieldDescriptors(compEl, null), "generic");
  assert.equal(compKey, null);
}

console.log("PHASE4_EXT_NODE_TESTS_OK: All 5 suites (Plain, React Trap, Iframe, Nested Shadow DOM, 5 ATS Adapters) PASSED.");
