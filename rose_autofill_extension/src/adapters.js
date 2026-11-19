/**
 * R.O.S.E Autofill - ATS Site Adapters (Greenhouse, Lever, Ashby, Workable, Indeed + Generic)
 */
(function (global) {
  "use strict";

  function detectSiteAdapter(urlStr, doc) {
    const u = String(urlStr || "").toLowerCase();
    if (u.includes("greenhouse.io") || (doc && doc.querySelector && doc.querySelector("#application_form, #grnhse_app"))) {
      return "greenhouse";
    }
    if (u.includes("lever.co") || (doc && doc.querySelector && doc.querySelector("form.application-form, input[name='urls[LinkedIn]']"))) {
      return "lever";
    }
    if (u.includes("ashbyhq.com") || (doc && doc.querySelector && doc.querySelector("[name^='_systemfield_'], [class*='ashby-application']"))) {
      return "ashby";
    }
    if (u.includes("workable.com") || (doc && doc.querySelector && doc.querySelector("[data-ui='application-form']"))) {
      return "workable";
    }
    if (u.includes("indeed.com") || (doc && doc.querySelector && doc.querySelector("#ia-container, [data-testid*='indeedApply']"))) {
      return "indeed";
    }
    return "generic";
  }

  /**
   * Maps a DOM field descriptor + adapter context into a semantic target key.
   */
  function resolveFieldSemanticKey(el, desc, adapterId) {
    const tag = (el.tagName || "").toLowerCase();
    const type = ((el.getAttribute && el.getAttribute("type")) || el.type || "").toLowerCase();
    const ac = desc.autocomplete || "";
    const nid = (desc.name + " " + desc.id + " " + desc.dataQa).trim();
    const rawText = desc.combined || "";
    const text = rawText.replace(/[_-]+/g, " ");

    // 1. Never auto-fill file inputs; classify accurately for user toast
    if (type === "file") {
      if (text.includes("cover") || text.includes("letter")) {
        return "file_cover_letter";
      }
      if (text.includes("resume") || text.includes("cv") || text.includes("curriculum") || nid === "resume") {
        return "file_resume";
      }
      return "file_other";
    }

    // 2. Site-specific explicit mappings
    if (adapterId === "greenhouse") {
      if (nid.includes("first_name")) return "first_name";
      if (nid.includes("last_name")) return "last_name";
      if (nid === "email" || nid.includes("job_application[email]")) return "email";
      if (nid === "phone" || nid.includes("job_application[phone]")) return "phone";
      if (nid.includes("location")) return "location";
      if (text.includes("cover letter")) return "cover_letter";
    } else if (adapterId === "lever") {
      if (nid === "name" || nid.endsWith("[name]")) return "full_name";
      if (nid === "email") return "email";
      if (nid === "phone") return "phone";
      if (nid.includes("urls[linkedin]")) return "linkedin";
      if (nid.includes("urls[github]")) return "github";
      if (nid.includes("urls[portfolio]") || nid.includes("urls[website]")) return "portfolio";
      if (nid === "comments" || text.includes("additional information") || text.includes("cover letter")) return "cover_letter";
    } else if (adapterId === "ashby") {
      if (nid.includes("_systemfield_name")) return "full_name";
      if (nid.includes("_systemfield_email")) return "email";
      if (nid.includes("_systemfield_phone")) return "phone";
      if (nid.includes("_systemfield_location")) return "location";
    } else if (adapterId === "workable") {
      if (nid.includes("firstname")) return "first_name";
      if (nid.includes("lastname")) return "last_name";
      if (nid === "email") return "email";
      if (nid === "phone") return "phone";
      if (nid.includes("address") || nid.includes("location")) return "location";
    } else if (adapterId === "indeed") {
      if (nid.includes("firstname") || text.includes("first name")) return "first_name";
      if (nid.includes("lastname") || text.includes("last name")) return "last_name";
      if (nid.includes("email") || type === "email") return "email";
      if (nid.includes("phonenumber") || nid.includes("phone") || type === "tel") return "phone";
      if (nid.includes("city") || nid.includes("location")) return "location";
    }

    // 3. Standard HTML autocomplete attribute tokens
    if (ac) {
      if (ac.includes("given-name")) return "first_name";
      if (ac.includes("family-name")) return "last_name";
      if (ac === "name") return "full_name";
      if (ac === "email") return "email";
      if (ac.startsWith("tel")) return "phone";
      if (ac === "country-name" || ac === "country") return "country";
      if (ac === "address-level2") return "city";
      if (ac.includes("street-address") || ac.includes("address")) return "location";
      if (ac === "url") return "portfolio";
    }

    // 4. Explicit cover letter / summary textareas & inputs
    if (text.includes("cover letter") || text.includes("cover_letter") || text.includes("coverletter") || nid === "comments") {
      return "cover_letter";
    }

    // 5. Contact fields
    if (type === "email" || /\be-?mail\b/.test(text)) {
      return "email";
    }
    if (type === "tel" || /\b(phone|mobile|telephone|whatsapp)\b/.test(text)) {
      return "phone";
    }

    // 6. Names (guard against company / school / reference / file / username false positives!)
    const nonCandidateName = /\b(company|employer|organization|org|school|university|reference|emergency|manager|supervisor|user\s?name|username|file\s?name|preferred pronoun|middle)\b/.test(
      text
    );
    if (!nonCandidateName) {
      if (/\b(first[\s_-]?name|given[\s_-]?name|fname)\b/.test(text)) return "first_name";
      if (/\b(last[\s_-]?name|family[\s_-]?name|surname|lname)\b/.test(text)) return "last_name";
      if (/\b(full[\s_-]?name|legal[\s_-]?name|your[\s_-]?name|candidate[\s_-]?name)\b/.test(text) || nid === "name" || desc.name === "name" || desc.id === "name") {
        return "full_name";
      }
      if (desc.labelText && /^name\b/.test(desc.labelText.trim())) {
        return "full_name";
      }
    }

    // 7. Links (LinkedIn, GitHub, Portfolio/Website)
    if (text.includes("linkedin")) return "linkedin";
    if (text.includes("github") || text.includes("gitlab")) return "github";
    if (/\b(portfolio|personal website|personal site|website|homepage)\b/.test(text) || (type === "url" && !text.includes("linkedin") && !text.includes("github"))) {
      return "portfolio";
    }

    // 8. Work authorization / sponsorship / relocation
    if (/\b(visa\s*sponsor[a-z]*|require[a-z]*\s*(?:visa\s*)?sponsor[a-z]*|need\s*(?:visa\s*)?sponsor[a-z]*|sponsorship)\b/.test(text)) {
      return "requires_sponsorship";
    }
    if (/\b(relocat[a-z]*|willing\s*to\s*relocate)\b/.test(text)) {
      return "relocation_required";
    }
    if (/\b(work\s*authoriz[a-z]*|legally\s*authorized|eligible\s*to\s*work|right\s*to\s*work|authorized\s*to\s*work)\b/.test(text)) {
      if (tag === "textarea") return "work_auth_summary";
      return "authorized_to_work";
    }

    // 9. Location / City / Country
    if (/\b(country)\b/.test(text) && !text.includes("authorized")) return "country";
    if (/\b(city|town)\b/.test(text)) return "city";
    if (/\b(location|where\s+are\s+you\s+based|current\s+residence|time\s*zone|address)\b/.test(text)) return "location";

    // 10. Experience & Salary
    if (/\b(years\s*(?:of\s*)?experience|how\s*many\s*years|experience\s*\(?\s*years\s*\)?|yoe|total\s*experience)\b/.test(text)) {
      return "years_experience";
    }
    if (/\b(salary|compensation|pay\s*expectation|desired\s*pay|expected\s*rate|hourly\s*rate)\b/.test(text)) {
      return "salary_expectation";
    }

    // 11. Project highlights / technical work summary
    if (
      /\b(project\s*highlights?|projects?\s*you|notable\s*projects?|recent\s*projects?|proud\s*of|architecture\s*example|technical\s*highlights?|tell\s*us\s*about\s*a\s*project)\b/.test(
        text
      )
    ) {
      return "project_highlights";
    }

    return null;
  }

  const api = {
    detectSiteAdapter,
    resolveFieldSemanticKey
  };

  global.RoseAdapters = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
