/**
 * R.O.S.E Autofill - Shadow DOM Traversal, Context Extraction & Native React/Vue/Angular Setters
 */
(function (global) {
  "use strict";

  const IGNORED_INPUT_TYPES = new Set([
    "hidden",
    "submit",
    "button",
    "reset",
    "image",
    "password",
    "search"
  ]);

  function normalizeText(str) {
    return String(str || "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function isInsideRoseUi(el) {
    let cur = el;
    while (cur) {
      if (cur.id === "rose-autofill-host" || cur.id === "rose-autofill-root") {
        return true;
      }
      if (cur.parentElement) {
        cur = cur.parentElement;
      } else if (cur.getRootNode && cur.getRootNode().host) {
        cur = cur.getRootNode().host;
      } else {
        cur = null;
      }
    }
    return false;
  }

  function isFormElementVisible(el, doc) {
    if (!el || isInsideRoseUi(el)) return false;
    // Keep <input type="file"> even when wrapped inside custom dropzones
    const tag = (el.tagName || "").toLowerCase();
    const type = (el.getAttribute && (el.getAttribute("type") || el.type || "")).toLowerCase();
    if (tag === "input" && type === "file") {
      return !el.disabled;
    }

    const win = (doc && doc.defaultView) || (typeof window !== "undefined" ? window : null);
    let cur = el;
    while (cur && cur.nodeType === 1) {
      if (cur.hidden) return false;
      if (cur.getAttribute && cur.getAttribute("aria-hidden") === "true" && cur === el) {
        // Still allow styled SR selects/checkboxes if explicitly form-bound, skip hidden containers
      }
      if (win && typeof win.getComputedStyle === "function") {
        try {
          const st = win.getComputedStyle(cur);
          if (st && (st.display === "none" || st.visibility === "hidden")) {
            return false;
          }
        } catch (_) {
          // Ignore detached style errors
        }
      }
      cur = cur.parentElement || (cur.getRootNode && cur.getRootNode().host) || null;
    }
    return true;
  }

  /**
   * Walks document and all open shadow roots recursively to collect form controls.
   */
  function collectFormControlsDeep(root, ownerDoc) {
    const out = [];
    const visited = new Set();
    const doc = ownerDoc || (root && root.ownerDocument) || (typeof document !== "undefined" ? document : null);

    function walk(node) {
      if (!node || isInsideRoseUi(node)) return;

      const children = node.children || node.childNodes || [];
      for (let i = 0; i < children.length; i++) {
        const el = children[i];
        if (!el || el.nodeType !== 1) continue;
        if (isInsideRoseUi(el)) continue;

        const tag = (el.tagName || "").toLowerCase();
        if (!visited.has(el)) {
          visited.add(el);

          if (tag === "input" || tag === "textarea" || tag === "select") {
            const typ = ((el.getAttribute && el.getAttribute("type")) || el.type || "text").toLowerCase();
            const disabled = Boolean(el.disabled || (el.getAttribute && el.getAttribute("disabled") !== null));
            const readonly = Boolean(el.readOnly || (el.getAttribute && el.getAttribute("readonly") !== null));
            if (!disabled && !readonly && !IGNORED_INPUT_TYPES.has(typ)) {
              if (isFormElementVisible(el, doc)) {
                out.push(el);
              }
            }
          } else {
            const role = ((el.getAttribute && el.getAttribute("role")) || "").toLowerCase();
            if (role === "radiogroup" || role === "combobox") {
              if (isFormElementVisible(el, doc)) {
                out.push(el);
              }
            }
          }
        }

        // Pierce open shadow roots recursively
        if (el.shadowRoot) {
          walk(el.shadowRoot);
        }
        walk(el);
      }
    }

    walk(root);
    return out;
  }

  function safeCssEscape(str) {
    if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
      return CSS.escape(str);
    }
    return String(str).replace(/([ #;?%&,.+*~\':"!^$[\]()=>|\/@])/g, "\\$1");
  }

  function getLabelById(id, el, doc) {
    if (!id) return "";
    const rootNode = el.getRootNode ? el.getRootNode() : null;
    const esc = safeCssEscape(id);
    if (rootNode && typeof rootNode.querySelector === "function") {
      const n = rootNode.querySelector("#" + esc);
      if (n) return normalizeText(n.textContent);
    }
    if (doc && typeof doc.getElementById === "function") {
      const n = doc.getElementById(id);
      if (n) return normalizeText(n.textContent);
    }
    return "";
  }

  /**
   * Reads autocomplete, name, id, placeholder, aria-label/labelledby, <label>, and container label text.
   */
  function extractFieldDescriptors(el, doc) {
    const ownerDoc = doc || el.ownerDocument || (typeof document !== "undefined" ? document : null);
    const autocomplete = normalizeText(el.getAttribute ? el.getAttribute("autocomplete") : el.autocomplete);
    const name = normalizeText(el.getAttribute ? el.getAttribute("name") : el.name);
    const id = normalizeText(el.getAttribute ? el.getAttribute("id") : el.id);
    const placeholder = normalizeText(el.getAttribute ? el.getAttribute("placeholder") : el.placeholder);
    const ariaLabel = normalizeText(el.getAttribute ? el.getAttribute("aria-label") : "");
    const dataQa = normalizeText(
      (el.getAttribute && (el.getAttribute("data-qa") || el.getAttribute("data-testid") || el.getAttribute("data-field"))) || ""
    );

    let ariaLabelledByText = "";
    const labelledBy = normalizeText(el.getAttribute ? el.getAttribute("aria-labelledby") : "");
    if (labelledBy) {
      ariaLabelledByText = labelledBy
        .split(/\s+/)
        .map((refId) => getLabelById(refId, el, ownerDoc))
        .filter(Boolean)
        .join(" ");
    }

    let explicitLabel = "";
    const rootNode = el.getRootNode ? el.getRootNode() : ownerDoc;
    if (id && rootNode && typeof rootNode.querySelector === "function") {
      try {
        const lbl = rootNode.querySelector('label[for="' + safeCssEscape(id) + '"]');
        if (lbl) explicitLabel = normalizeText(lbl.textContent);
      } catch (_) {}
    }
    if (!explicitLabel && id && ownerDoc && rootNode !== ownerDoc && typeof ownerDoc.querySelector === "function") {
      try {
        const lbl = ownerDoc.querySelector('label[for="' + safeCssEscape(id) + '"]');
        if (lbl) explicitLabel = normalizeText(lbl.textContent);
      } catch (_) {}
    }

    let ancestorLabel = "";
    if (typeof el.closest === "function") {
      const parentLbl = el.closest("label");
      if (parentLbl) ancestorLabel = normalizeText(parentLbl.textContent);
    }

    let nearbyLabel = "";
    if (!explicitLabel && !ancestorLabel) {
      // Check previous sibling label or text node wrapper
      let prev = el.previousElementSibling;
      for (let step = 0; step < 2 && prev; step++) {
        const ptag = (prev.tagName || "").toLowerCase();
        if (ptag === "label" || ptag === "span" || ptag === "p" || ptag === "div" || ptag.startsWith("h")) {
          const txt = normalizeText(prev.textContent);
          if (txt && txt.length <= 140) {
            nearbyLabel = txt;
            break;
          }
        }
        prev = prev.previousElementSibling;
      }

      if (!nearbyLabel && typeof el.closest === "function") {
        const group = el.closest(
          ".field, .form-group, .application-question, .application-field, fieldset, [class*='question'], [class*='field'], [data-field], [data-qa]"
        );
        if (group) {
          const lbl = group.querySelector("label, legend, .label, [class*='label'], [class*='question-title'], h3, h4, p");
          if (lbl && lbl !== el) {
            const txt = normalizeText(lbl.textContent);
            if (txt && txt.length <= 180) nearbyLabel = txt;
          }
        }
      }

      // Check ShadowRoot host element attributes/label text if inside a single-field custom element
      if (!nearbyLabel && rootNode && rootNode.host) {
        const h = rootNode.host;
        const hLbl = h.getAttribute && (h.getAttribute("label") || h.getAttribute("aria-label") || h.getAttribute("data-label"));
        if (hLbl) nearbyLabel = normalizeText(hLbl);
      }
    }

    const labelText = explicitLabel || ancestorLabel || ariaLabelledByText || nearbyLabel;
    const combined = normalizeText([autocomplete, name, id, placeholder, ariaLabel, labelText, dataQa].join(" ")).toLowerCase();
    const friendlyLabel =
      normalizeText((labelText || ariaLabel || placeholder || name || id || "unnamed field").replace(/\*+$/, "")) ||
      "unnamed field";

    return {
      autocomplete: autocomplete.toLowerCase(),
      name: name.toLowerCase(),
      id: id.toLowerCase(),
      placeholder: placeholder.toLowerCase(),
      ariaLabel: ariaLabel.toLowerCase(),
      labelText: labelText.toLowerCase(),
      dataQa: dataQa.toLowerCase(),
      combined,
      friendlyLabel
    };
  }

  function dispatchFormEvents(el, doc) {
    const win = (doc && doc.defaultView) || (typeof window !== "undefined" ? window : null);
    const EventCtor = (win && win.Event) || (typeof Event !== "undefined" ? Event : null);
    const InputEventCtor = (win && win.InputEvent) || (typeof InputEvent !== "undefined" ? InputEvent : EventCtor);
    if (!EventCtor) return;

    try {
      el.dispatchEvent(new InputEventCtor("input", { bubbles: true, composed: true }));
    } catch (_) {
      el.dispatchEvent(new EventCtor("input", { bubbles: true, composed: true }));
    }
    el.dispatchEvent(new EventCtor("change", { bubbles: true, composed: true }));
    el.dispatchEvent(new EventCtor("blur", { bubbles: true, composed: true }));
  }

  /**
   * Uses native prototype value setter + _valueTracker reset so React/Vue/Angular controlled inputs accept updates.
   */
  function setNativeValue(el, value) {
    if (!el) return false;
    const strVal = String(value == null ? "" : value);
    const tag = (el.tagName || "").toLowerCase();
    const win = (el.ownerDocument && el.ownerDocument.defaultView) || (typeof window !== "undefined" ? window : global);

    try {
      if (typeof el.focus === "function") el.focus();
    } catch (_) {}

    const prevVal = el.value;
    let proto = null;
    if (tag === "textarea" && win && win.HTMLTextAreaElement) {
      proto = win.HTMLTextAreaElement.prototype;
    } else if (tag === "select" && win && win.HTMLSelectElement) {
      proto = win.HTMLSelectElement.prototype;
    } else if (win && win.HTMLInputElement) {
      proto = win.HTMLInputElement.prototype;
    }

    const descriptor = proto ? Object.getOwnPropertyDescriptor(proto, "value") : null;
    if (descriptor && typeof descriptor.set === "function") {
      descriptor.set.call(el, strVal);
    } else {
      el.value = strVal;
    }

    // Notify React's internal value tracker that the value changed from prevVal
    if (el._valueTracker && typeof el._valueTracker.setValue === "function") {
      el._valueTracker.setValue(String(prevVal == null ? "" : prevVal));
    }

    dispatchFormEvents(el, el.ownerDocument);
    return el.value === strVal;
  }

  function setNativeChecked(el, checked) {
    if (!el) return false;
    const target = Boolean(checked);
    const win = (el.ownerDocument && el.ownerDocument.defaultView) || (typeof window !== "undefined" ? window : global);
    const prevChecked = Boolean(el.checked);

    const proto = win && win.HTMLInputElement ? win.HTMLInputElement.prototype : null;
    const descriptor = proto ? Object.getOwnPropertyDescriptor(proto, "checked") : null;
    if (descriptor && typeof descriptor.set === "function") {
      descriptor.set.call(el, target);
    } else {
      el.checked = target;
    }

    if (el._valueTracker && typeof el._valueTracker.setValue === "function") {
      el._valueTracker.setValue(String(prevChecked));
    }

    dispatchFormEvents(el, el.ownerDocument);
    return Boolean(el.checked) === target;
  }

  function isPlaceholderOption(opt) {
    if (!opt) return true;
    const v = normalizeText(opt.value).toLowerCase();
    const t = normalizeText(opt.textContent || opt.text).toLowerCase();
    if (!v && (!t || t.includes("select") || t.includes("choose") || t === "-" || t === "--")) {
      return true;
    }
    return false;
  }

  function setNativeSelect(selectEl, candidates) {
    if (!selectEl || !selectEl.options) return false;
    const opts = Array.from(selectEl.options).filter((o) => !o.disabled && !isPlaceholderOption(o));
    const candList = (Array.isArray(candidates) ? candidates : [candidates])
      .map((c) => normalizeText(c).toLowerCase())
      .filter(Boolean);

    let chosen = null;

    // 1. Exact value or text match
    for (const cand of candList) {
      chosen = opts.find(
        (o) => normalizeText(o.value).toLowerCase() === cand || normalizeText(o.textContent || o.text).toLowerCase() === cand
      );
      if (chosen) break;
    }

    // 2. Substring word/phrase match
    if (!chosen) {
      for (const cand of candList) {
        chosen = opts.find((o) => {
          const ov = normalizeText(o.value).toLowerCase();
          const ot = normalizeText(o.textContent || o.text).toLowerCase();
          return ot.includes(cand) || ov.includes(cand);
        });
        if (chosen) break;
      }
    }

    if (!chosen) return false;
    return setNativeValue(selectEl, chosen.value);
  }

  const api = {
    normalizeText,
    collectFormControlsDeep,
    extractFieldDescriptors,
    dispatchFormEvents,
    setNativeValue,
    setNativeChecked,
    setNativeSelect
  };

  global.RoseDomUtils = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);

