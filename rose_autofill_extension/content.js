/**
 * R.O.S.E Autofill - All-Frames Content Script (Isolated Shadow DOM Floating Button + Toast)
 */
(function () {
  "use strict";

  const STORAGE_KEY = "rose_profile";
  const IS_TOP_FRAME = window === window.top;
  let hostEl = null;
  let shadowRootUi = null;
  let toastTimer = null;

  function isJobOrFormPage() {
    if (!IS_TOP_FRAME) return false;
    const host = (window.location.hostname || "").toLowerCase();
    if (
      host.includes("greenhouse.io") ||
      host.includes("lever.co") ||
      host.includes("ashbyhq.com") ||
      host.includes("workable.com") ||
      host.includes("indeed.com")
    ) {
      return true;
    }
    // Also show on pages containing at least 2 fillable non-search form controls or iframes
    const controls = window.RoseDomUtils.collectFormControlsDeep(document.body || document.documentElement, document);
    return controls.length >= 2 || document.querySelectorAll("iframe").length > 0;
  }

  function ensureFloatingUi(forceCreate) {
    if (!IS_TOP_FRAME || hostEl || !document.body) return;
    if (!forceCreate && !isJobOrFormPage()) return;

    hostEl = document.createElement("div");
    hostEl.id = "rose-autofill-host";
    hostEl.style.cssText = "all: initial; position: fixed; z-index: 2147483647; bottom: 20px; right: 20px;";
    shadowRootUi = hostEl.attachShadow({ mode: "open" });

    shadowRootUi.innerHTML = `
      <style>
        #rose-autofill-root {
          font-family: "Segoe UI Variable Text", "Inter", "Segoe UI", system-ui, sans-serif;
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 10px;
        }
        .rose-pill {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 10px 16px;
          border-radius: 999px;
          border: 1px solid rgba(34, 211, 238, 0.45);
          background: linear-gradient(135deg, rgba(9, 13, 24, 0.94), rgba(22, 16, 44, 0.94));
          color: #F8FAFC;
          font-size: 13px;
          font-weight: 600;
          letter-spacing: 0.2px;
          cursor: pointer;
          box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45), 0 0 18px rgba(34, 211, 238, 0.18);
          transition: transform 0.14s ease, border-color 0.14s ease;
        }
        .rose-pill:hover {
          transform: translateY(-1px);
          border-color: rgba(139, 92, 246, 0.85);
        }
        .rose-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: linear-gradient(135deg, #22D3EE, #8B5CF6);
        }
        .rose-kbd {
          font-size: 10px;
          color: #94A3B8;
          background: rgba(255, 255, 255, 0.08);
          padding: 2px 6px;
          border-radius: 6px;
        }
        .rose-toast {
          display: none;
          max-width: 340px;
          padding: 12px 14px;
          border-radius: 14px;
          border: 1px solid rgba(34, 211, 238, 0.35);
          background: rgba(9, 13, 24, 0.96);
          color: #F1F5F9;
          box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55);
          font-size: 12.5px;
          line-height: 1.45;
        }
        .rose-toast.visible {
          display: block;
        }
        .rose-toast-head {
          font-weight: 600;
          color: #22D3EE;
          margin-bottom: 3px;
        }
        .rose-toast-sub {
          color: #94A3B8;
          font-size: 11.5px;
        }
      </style>
      <div id="rose-autofill-root">
        <div id="rose-toast" class="rose-toast" role="status" aria-live="polite">
          <div id="rose-toast-head" class="rose-toast-head"></div>
          <div id="rose-toast-sub" class="rose-toast-sub"></div>
        </div>
        <button id="rose-fill-btn" type="button" class="rose-pill" title="Autofill form (Alt+Shift+F)">
          <span class="rose-dot"></span>
          <span>ROSE Fill</span>
          <span class="rose-kbd">Alt+Shift+F</span>
        </button>
      </div>
    `;

    document.body.appendChild(hostEl);
    const btn = shadowRootUi.getElementById("rose-fill-btn");
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      requestFullFill();
    });
  }

  function showToast(headline, subline, durationMs) {
    if (!IS_TOP_FRAME) return;
    ensureFloatingUi(true);
    if (!shadowRootUi) return;

    const toastEl = shadowRootUi.getElementById("rose-toast");
    const headEl = shadowRootUi.getElementById("rose-toast-head");
    const subEl = shadowRootUi.getElementById("rose-toast-sub");
    if (!toastEl || !headEl || !subEl) return;

    headEl.textContent = headline || "";
    subEl.textContent = subline || "";
    toastEl.classList.add("visible");
    toastEl.style.display = "block";

    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.classList.remove("visible");
      toastEl.style.display = "none";
    }, durationMs || 6000);
  }

  function requestFullFill() {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      chrome.runtime.sendMessage({ type: "ROSE_REQUEST_FILL" }, (resp) => {
        if (chrome.runtime.lastError || !resp || !resp.ok) {
          if (resp && resp.error === "no_profile") {
            showToast(
              "No R.O.S.E profile imported yet.",
              "Click the extension icon in Chrome toolbar and import rose_profile.json.",
              6500
            );
          } else {
            // Direct single/same-origin fallback
            chrome.storage.local.get(STORAGE_KEY, (data) => {
              const profile = data && data[STORAGE_KEY];
              if (!profile) {
                showToast("No R.O.S.E profile loaded.", "Import rose_profile.json in extension popup.", 6000);
                return;
              }
              const summary = window.RoseAutofillEngine.fillDocument(document, profile);
              const t = window.RoseAutofillEngine.formatToastText(summary);
              showToast(t.headline, t.subline);
            });
          }
        }
      });
    }
  }

  // Expose hook cleanly
  window.__ROSE_AUTOFILL_RUN__ = function (profile) {
    return window.RoseAutofillEngine.fillDocument(document, profile);
  };

  // Listen to background worker multi-frame commands & toast messages
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || !msg.type) return false;

      if (msg.type === "ROSE_RUN_FRAME_FILL") {
        const result = window.RoseAutofillEngine.fillDocument(document, msg.profile);
        chrome.runtime.sendMessage({
          type: "ROSE_FRAME_RESULT",
          runId: msg.runId,
          result
        });
        sendResponse({ ok: true });
        return false;
      }

      if (msg.type === "ROSE_SHOW_TOAST" && IS_TOP_FRAME) {
        const t = window.RoseAutofillEngine.formatToastText(msg.summary);
        showToast(t.headline, t.subline);
        return false;
      }

      if (msg.type === "ROSE_NEED_PROFILE" && IS_TOP_FRAME) {
        showToast(
          "No R.O.S.E profile imported yet.",
          "Click the R.O.S.E Autofill icon in Chrome toolbar and import rose_profile.json.",
          6500
        );
        return false;
      }
      return false;
    });
  }

  // Keyboard fallback inside page: Alt+Shift+F
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.key && e.key.toLowerCase() === "f") {
        e.preventDefault();
        requestFullFill();
      }
    },
    true
  );

  // Inject floating trigger pill once DOM stabilizes (zero idle polling; wakes on first focus for late SPAs)
  if (IS_TOP_FRAME) {
    setTimeout(() => ensureFloatingUi(false), 350);
    setTimeout(() => ensureFloatingUi(false), 1400);
    document.addEventListener("focusin", () => ensureFloatingUi(false), { once: true });
  }
})();
