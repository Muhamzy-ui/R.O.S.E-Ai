/**
 * R.O.S.E Autofill Popup Controller
 * Stores profile in chrome.storage.local, imports rose_profile.json, triggers active-tab fill.
 */
"use strict";

const STORAGE_KEY = "rose_profile";

const TEXT_FIELDS = [
  "full_name",
  "first_name",
  "last_name",
  "email",
  "phone",
  "location",
  "years_experience",
  "github",
  "linkedin",
  "portfolio",
  "project_highlights",
  "cover_letter"
];

function showFeedback(text, isError) {
  const box = document.getElementById("feedback");
  box.textContent = text;
  box.hidden = false;
  box.className = isError ? "feedback err" : "feedback";
}

function setBadge(loaded) {
  const badge = document.getElementById("status-badge");
  if (loaded) {
    badge.textContent = "Profile Ready";
    badge.className = "badge ok";
  } else {
    badge.textContent = "Import Needed";
    badge.className = "badge warn";
  }
}

function populateForm(p) {
  if (!p) return;
  for (const k of TEXT_FIELDS) {
    const el = document.getElementById("f-" + k);
    if (el) el.value = p[k] || (k === "portfolio" ? p.website || "" : "") || "";
  }
  const auth = p.work_authorization || {};
  document.getElementById("f-authorized_to_work").value = auth.authorized_to_work || "Yes";
  document.getElementById("f-requires_sponsorship").value = auth.requires_sponsorship || "No";
  document.getElementById("f-autofill_cover_letter").checked = Boolean(p.autofill_cover_letter);
  setBadge(Boolean(p.full_name || p.email));
}

function collectForm(existing) {
  const base = Object.assign({}, existing || {});
  for (const k of TEXT_FIELDS) {
    const el = document.getElementById("f-" + k);
    if (el) base[k] = el.value.trim();
  }
  base.website = base.portfolio || base.github || base.website || "";
  base.work_authorization = Object.assign({}, base.work_authorization || {}, {
    authorized_to_work: document.getElementById("f-authorized_to_work").value,
    requires_sponsorship: document.getElementById("f-requires_sponsorship").value
  });
  base.autofill_cover_letter = document.getElementById("f-autofill_cover_letter").checked;
  return base;
}

document.addEventListener("DOMContentLoaded", async () => {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  let currentProfile = stored[STORAGE_KEY] || null;
  if (currentProfile) {
    populateForm(currentProfile);
  } else {
    setBadge(false);
    showFeedback("Click 'Import rose_profile.json' to load your R.O.S.E profile.", false);
  }

  document.getElementById("file-import").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    try {
      const txt = await file.text();
      const parsed = JSON.parse(txt);
      if (!parsed || typeof parsed !== "object") {
        throw new Error("Invalid JSON object");
      }
      currentProfile = parsed;
      await chrome.storage.local.set({ [STORAGE_KEY]: currentProfile });
      populateForm(currentProfile);
      showFeedback(`Imported ${file.name} into Chrome storage.`, false);
    } catch (err) {
      showFeedback(`Could not import profile JSON: ${err.message}`, true);
    }
  });

  document.getElementById("btn-export").addEventListener("click", () => {
    const payload = collectForm(currentProfile);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "rose_profile.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  document.getElementById("profile-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    currentProfile = collectForm(currentProfile);
    await chrome.storage.local.set({ [STORAGE_KEY]: currentProfile });
    setBadge(true);
    showFeedback("Saved R.O.S.E profile to Chrome local storage.", false);
  });

  document.getElementById("btn-fill-now").addEventListener("click", async () => {
    if (!currentProfile) {
      showFeedback("Import rose_profile.json first before filling.", true);
      return;
    }
    // Persist any unsaved popup edits before filling
    currentProfile = collectForm(currentProfile);
    await chrome.storage.local.set({ [STORAGE_KEY]: currentProfile });

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      showFeedback("No active Chrome tab found.", true);
      return;
    }

    chrome.runtime.sendMessage({ type: "ROSE_REQUEST_FILL", tabId: tab.id }, (resp) => {
      if (chrome.runtime.lastError) {
        showFeedback(chrome.runtime.lastError.message, true);
        return;
      }
      if (!resp || !resp.ok) {
        showFeedback((resp && resp.error) || "Fill failed — reload page and retry.", true);
        return;
      }
      const s = resp.summary || { total: 0, filled: 0, skipped: [] };
      const skipTxt = s.skipped && s.skipped.length ? ` Skipped: ${s.skipped.join(", ")}.` : "";
      showFeedback(`Filled ${s.filled}/${s.total} fields.${skipTxt}`, false);
    });
  });
});

