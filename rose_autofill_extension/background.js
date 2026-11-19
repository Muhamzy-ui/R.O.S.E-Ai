/**
 * R.O.S.E Autofill - Background Service Worker (Manifest V3)
 * Coordinates Alt+Shift+F command, popup triggers, and multi-frame aggregation.
 */
"use strict";

const STORAGE_KEY = "rose_profile";
const pendingRuns = new Map();

async function getStoredProfile() {
  const data = await chrome.storage.local.get(STORAGE_KEY);
  return data[STORAGE_KEY] || null;
}

function mergeResults(results) {
  let total = 0;
  let filled = 0;
  let needsManualResume = false;
  const skipped = [];
  let adapter = "generic";

  for (const r of results) {
    if (!r) continue;
    total += Number(r.total || 0);
    filled += Number(r.filled || 0);
    if (r.needsManualResume) needsManualResume = true;
    if (r.adapter && r.adapter !== "generic") adapter = r.adapter;
    if (Array.isArray(r.skipped)) {
      for (const s of r.skipped) {
        if (s && !skipped.includes(s)) skipped.push(s);
      }
    }
  }

  return { total, filled, skipped, needsManualResume, adapter };
}

async function triggerFillOnTab(tabId) {
  if (!tabId) return { ok: false, error: "No active tab." };

  const profile = await getStoredProfile();
  if (!profile || (!profile.full_name && !profile.first_name && !profile.email)) {
    try {
      await chrome.tabs.sendMessage(tabId, { type: "ROSE_NEED_PROFILE" }, { frameId: 0 });
    } catch (_) {}
    return { ok: false, error: "no_profile" };
  }

  const runId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  return new Promise((resolve) => {
    const collector = {
      results: [],
      settleTimer: null,
      maxTimer: null,
      done: false
    };

    const finalize = async () => {
      if (collector.done) return;
      collector.done = true;
      if (collector.settleTimer) clearTimeout(collector.settleTimer);
      if (collector.maxTimer) clearTimeout(collector.maxTimer);
      pendingRuns.delete(runId);

      const summary = mergeResults(collector.results);
      if (collector.results.length === 0) {
        resolve({
          ok: false,
          error: "Could not reach page frames. Reload tab (or enable 'Allow access to file URLs' for local files)."
        });
        return;
      }

      try {
        await chrome.tabs.sendMessage(tabId, { type: "ROSE_SHOW_TOAST", summary }, { frameId: 0 });
      } catch (_) {}

      resolve({ ok: true, summary });
    };

    pendingRuns.set(runId, { collector, finalize });
    collector.maxTimer = setTimeout(finalize, 420);

    chrome.tabs.sendMessage(tabId, { type: "ROSE_RUN_FRAME_FILL", runId, profile }).catch(() => {
      // Wait briefly in case secondary frames respond, else finalize promptly
      setTimeout(() => {
        if (!collector.done && collector.results.length === 0) finalize();
      }, 120);
    });
  });
}

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "trigger-autofill") return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.id) {
    await triggerFillOnTab(tab.id);
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return false;

  if (msg.type === "ROSE_FRAME_RESULT" && msg.runId) {
    const entry = pendingRuns.get(msg.runId);
    if (entry && !entry.collector.done) {
      entry.collector.results.push(msg.result);
      if (entry.collector.settleTimer) clearTimeout(entry.collector.settleTimer);
      entry.collector.settleTimer = setTimeout(entry.finalize, 160);
    }
    return false;
  }

  if (msg.type === "ROSE_REQUEST_FILL") {
    const targetTabPromise = msg.tabId
      ? Promise.resolve(msg.tabId)
      : chrome.tabs.query({ active: true, currentWindow: true }).then((tabs) => (tabs[0] ? tabs[0].id : null));

    targetTabPromise.then((tabId) => triggerFillOnTab(tabId)).then((res) => sendResponse(res));
    return true; // Async response
  }

  return false;
});

