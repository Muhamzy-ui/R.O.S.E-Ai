/**
 * R.O.S.E Autofill Engine - Honest, Multi-Frame, React/Shadow-DOM-Safe Filling
 * Never clicks Submit/Apply buttons. Never sets file inputs (reports skipped cleanly).
 */
(function (global) {
  "use strict";

  function getUtils() {
    if (global.RoseDomUtils) return global.RoseDomUtils;
    if (typeof require === "function") return require("./dom_utils.js");
    throw new Error("RoseDomUtils not loaded");
  }

  function getAdapters() {
    if (global.RoseAdapters) return global.RoseAdapters;
    if (typeof require === "function") return require("./adapters.js");
    throw new Error("RoseAdapters not loaded");
  }

  function resolveValueForKey(key, profile) {
    const p = profile || {};
    const auth = p.work_authorization && typeof p.work_authorization === "object" ? p.work_authorization : {};

    switch (key) {
      case "first_name":
        return p.first_name || (p.full_name ? String(p.full_name).split(/\s+/)[0] : "");
      case "last_name":
        return p.last_name || (p.full_name ? String(p.full_name).split(/\s+/).slice(1).join(" ") : "");
      case "full_name":
        return p.full_name || [p.first_name, p.last_name].filter(Boolean).join(" ");
      case "email":
        return p.email || "";
      case "phone":
        return p.phone || "";
      case "location":
        return p.location || [p.city, p.country].filter(Boolean).join(", ");
      case "city":
        return p.city || (p.location ? String(p.location).split(",")[0].trim() : "");
      case "country":
        return p.country || "Nigeria";
      case "github":
        return p.github || "";
      case "linkedin":
        return p.linkedin || "";
      case "portfolio":
        return p.portfolio || p.website || p.github || "";
      case "years_experience":
        return String(p.years_experience != null ? p.years_experience : "3");
      case "authorized_to_work":
        return String(auth.authorized_to_work || p.authorized_to_work || "Yes");
      case "requires_sponsorship":
        return String(auth.requires_sponsorship || p.requires_sponsorship || "No");
      case "relocation_required":
        return String(auth.relocation_required || "No");
      case "work_auth_summary":
        return (
          auth.summary ||
          "Authorized to work remotely from Nigeria (WAT, UTC+1); no visa sponsorship required for remote contracts or employment."
        );
      case "project_highlights":
        return p.project_highlights || p.projects_summary || "";
      case "salary_expectation":
        return p.salary_expectation || "";
      case "cover_letter":
        // Only fill cover_letter when explicitly enabled AND user supplied a custom draft
        if (p.autofill_cover_letter && p.cover_letter && String(p.cover_letter).trim()) {
          return String(p.cover_letter);
        }
        return null;
      default:
        return null;
    }
  }

  function buildSelectCandidates(key, val) {
    const low = String(val || "").trim().toLowerCase();
    if (key === "authorized_to_work" || key === "requires_sponsorship" || key === "relocation_required") {
      if (low === "yes" || low === "true") {
        return ["yes", "authorized", "i am authorized", "true", "y"];
      }
      if (low === "no" || low === "false") {
        return ["no", "do not require", "not require", "false", "n"];
      }
    }
    if (key === "years_experience") {
      const yrs = parseInt(val, 10) || 3;
      return [
        String(yrs),
        `${yrs} years`,
        `${yrs}+ years`,
        "2-4 years",
        "2-5 years",
        "3-5 years",
        "1-3 years",
        "2-4",
        "3-5",
        "junior",
        "mid"
      ];
    }
    if (key === "country") {
      return [val, "nigeria", "ng"];
    }
    if (key === "location") {
      return [val, "abuja", "nigeria", "remote", "worldwide"];
    }
    return [val];
  }

  function flashElement(el) {
    if (!el || !el.style) return;
    const prevOutline = el.style.outline;
    const prevOffset = el.style.outlineOffset;
    el.style.outline = "2px solid #22D3EE";
    el.style.outlineOffset = "1px";
    setTimeout(() => {
      try {
        el.style.outline = prevOutline;
        el.style.outlineOffset = prevOffset;
      } catch (_) {}
    }, 2200);
  }

  function handleRadioGroup(radios, desc, semanticKey, targetVal, utils, doc) {
    const lowTarget = String(targetVal || "").trim().toLowerCase();
    for (const r of radios) {
      const rDesc = utils.extractFieldDescriptors(r, doc);
      const valText = (String(r.value || "") + " " + rDesc.labelText + " " + rDesc.ariaLabel).toLowerCase();
      const wantsYes = lowTarget === "yes" || lowTarget === "true";
      const wantsNo = lowTarget === "no" || lowTarget === "false";
      if (wantsYes && /\byes\b/.test(valText)) {
        return utils.setNativeChecked(r, true);
      }
      if (wantsNo && /\bno\b/.test(valText)) {
        return utils.setNativeChecked(r, true);
      }
      if (valText.includes(lowTarget) && lowTarget.length > 0) {
        return utils.setNativeChecked(r, true);
      }
    }
    return false;
  }

  function fillDocument(doc, profile, opts) {
    const utils = getUtils();
    const adapters = getAdapters();
    const targetDoc = doc || (typeof document !== "undefined" ? document : null);
    if (!targetDoc) {
      return { total: 0, filled: 0, skipped: [], needsManualResume: false, adapter: "generic" };
    }

    const urlStr = (targetDoc.location && targetDoc.location.href) || (typeof window !== "undefined" ? window.location.href : "");
    const adapterId = adapters.detectSiteAdapter(urlStr, targetDoc);
    const controls = utils.collectFormControlsDeep(targetDoc.body || targetDoc.documentElement, targetDoc);

    let total = 0;
    let filled = 0;
    const skipped = [];
    const filledFields = [];
    let needsManualResume = false;
    const seenRadioGroups = new Map();
    const rootIds = new WeakMap();
    let nextRootSeq = 1;

    function getRootId(rootNode) {
      if (!rootNode || rootNode === targetDoc) return "doc";
      if (!rootIds.has(rootNode)) {
        rootIds.set(rootNode, `sr${nextRootSeq++}`);
      }
      return rootIds.get(rootNode);
    }

    function addSkip(label) {
      const clean = utils.normalizeText(label);
      if (clean && !skipped.includes(clean)) {
        skipped.push(clean);
      }
    }

    // Group radio inputs sharing a name within the same RootNode as 1 logical field
    const orderedUnits = [];
    for (const el of controls) {
      const tag = (el.tagName || "").toLowerCase();
      const type = ((el.getAttribute && el.getAttribute("type")) || el.type || "").toLowerCase();
      if (tag === "input" && type === "radio") {
        const rootNode = el.getRootNode ? el.getRootNode() : targetDoc;
        const groupKey = (el.name || el.id || "unnamed_radio") + "::" + getRootId(rootNode);
        if (!seenRadioGroups.has(groupKey)) {
          const groupArr = [el];
          seenRadioGroups.set(groupKey, groupArr);
          orderedUnits.push({ kind: "radio_group", groupKey, radios: groupArr, rep: el });
        } else {
          seenRadioGroups.get(groupKey).push(el);
        }
      } else if ((el.getAttribute && el.getAttribute("role")) === "radiogroup") {
        const innerRadios = Array.from(el.querySelectorAll("input[type='radio'], [role='radio']"));
        if (innerRadios.length > 0 && innerRadios[0].tagName.toLowerCase() === "input") {
          // Will be handled by input[type=radio] grouping unless missed
          continue;
        }
        orderedUnits.push({ kind: "aria_radiogroup", rep: el, radios: innerRadios });
      } else {
        orderedUnits.push({ kind: "single", el });
      }
    }

    for (const unit of orderedUnits) {
      if (unit.kind === "radio_group" || unit.kind === "aria_radiogroup") {
        total++;
        const firstDesc = utils.extractFieldDescriptors(unit.rep, targetDoc);
        // Also inspect enclosing fieldset/question container for group question label
        let groupText = firstDesc.combined;
        if (typeof unit.rep.closest === "function") {
          const grp = unit.rep.closest("fieldset, .field, .form-group, .application-question, [class*='question'], [role='radiogroup']");
          if (grp) {
            const legend = grp.querySelector("legend, .label, [class*='label'], [class*='question-title'], p, h4");
            if (legend) groupText += " " + utils.normalizeText(legend.textContent).toLowerCase();
          }
        }
        const syntheticDesc = Object.assign({}, firstDesc, { combined: utils.normalizeText(groupText) });
        const key = adapters.resolveFieldSemanticKey(unit.rep, syntheticDesc, adapterId);
        const val = key ? resolveValueForKey(key, profile) : null;
        if (val != null && val !== "") {
          if (unit.kind === "radio_group" && handleRadioGroup(unit.radios, syntheticDesc, key, val, utils, targetDoc)) {
            filled++;
            filledFields.push(key);
            unit.radios.forEach((r) => flashElement(r));
            continue;
          }
        }
        addSkip(firstDesc.friendlyLabel);
        continue;
      }

      const el = unit.el;
      const tag = (el.tagName || "").toLowerCase();
      const type = ((el.getAttribute && el.getAttribute("type")) || el.type || "").toLowerCase();
      const desc = utils.extractFieldDescriptors(el, targetDoc);
      const key = adapters.resolveFieldSemanticKey(el, desc, adapterId);

      total++;

      // File inputs are never writable by Chrome extensions for security reasons
      if (type === "file") {
        if (key === "file_cover_letter") {
          addSkip("cover letter upload");
        } else {
          needsManualResume = true;
          addSkip("resume upload");
        }
        continue;
      }

      if (key === "cover_letter") {
        const cov = resolveValueForKey("cover_letter", profile);
        if (cov) {
          if (utils.setNativeValue(el, cov)) {
            filled++;
            filledFields.push("cover_letter");
            flashElement(el);
            continue;
          }
        }
        addSkip("cover letter");
        continue;
      }

      if (tag === "input" && type === "checkbox") {
        // Only fill explicit work-authorization/remote confirmation checkboxes; skip legal/unmapped consent checkboxes
        if (key === "authorized_to_work") {
          if (utils.setNativeChecked(el, true)) {
            filled++;
            filledFields.push(key);
            flashElement(el);
            continue;
          }
        } else if (key === "requires_sponsorship") {
          // Wait: checkbox for sponsorship usually means "I require sponsorship" -> keep unchecked (false) unless Yes
          const val = resolveValueForKey(key, profile);
          const wantChecked = String(val).toLowerCase() === "yes";
          if (utils.setNativeChecked(el, wantChecked)) {
            filled++;
            filledFields.push(key);
            flashElement(el);
            continue;
          }
        }
        addSkip(desc.friendlyLabel);
        continue;
      }

      const val = key ? resolveValueForKey(key, profile) : null;
      if (val == null || val === "") {
        addSkip(desc.friendlyLabel);
        continue;
      }

      if (tag === "select") {
        const candidates = buildSelectCandidates(key, val);
        if (utils.setNativeSelect(el, candidates)) {
          filled++;
          filledFields.push(key);
          flashElement(el);
        } else {
          addSkip(desc.friendlyLabel);
        }
        continue;
      }

      if (utils.setNativeValue(el, val)) {
        filled++;
        filledFields.push(key);
        flashElement(el);
      } else {
        addSkip(desc.friendlyLabel);
      }
    }

    return {
      total,
      filled,
      skipped,
      filledFields,
      needsManualResume,
      adapter: adapterId
    };
  }

  function formatToastText(summary) {
    const total = Number((summary && summary.total) || 0);
    const filled = Number((summary && summary.filled) || 0);
    const skipped = Array.isArray(summary && summary.skipped) ? summary.skipped : [];
    const needsResume = Boolean(summary && summary.needsManualResume);

    if (total === 0) {
      return {
        headline: "No fillable application fields detected on this page.",
        subline: "Open an application form page and press Alt+Shift+F or click ROSE Fill."
      };
    }

    let headline = `Filled ${filled}/${total} fields.`;
    if (skipped.length > 0) {
      const shown = skipped.slice(0, 4).join(", ");
      const extra = skipped.length > 4 ? ` (+${skipped.length - 4} more)` : "";
      headline += ` Skipped: ${shown}${extra}.`;
    }

    const subline = needsResume
      ? "Attach your resume manually before clicking Submit."
      : "Review all filled fields before submitting (R.O.S.E never auto-submits).";

    return { headline, subline };
  }

  const api = {
    fillDocument,
    resolveValueForKey,
    formatToastText
  };

  global.RoseAutofillEngine = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
