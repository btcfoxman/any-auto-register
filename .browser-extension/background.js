const ASSIST_DEFAULTS = {
  serviceUrl: "http://192.168.3.5:8000",
  apiKey: "sk-test-api-key",
  proxyUrl: "",
  extensionId: "",
  assistLastStatus: "Task assistant idle."
};

const ASSIST_LEGACY_SERVICE_URL = "http://127.0.0.1:8000";
const POLL_IDLE_MS = 5000;
const POLL_ACTIVE_MS = 2000;

let pollTimer = null;
let polling = false;

chrome.runtime.onInstalled.addListener(() => {
  ensureExtensionId();
  scheduleAssistPoll(500);
  setupAssistAlarm();
});

chrome.runtime.onStartup.addListener(() => {
  ensureExtensionId();
  scheduleAssistPoll(500);
  setupAssistAlarm();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "lingya-assist-poll") {
    scheduleAssistPoll(50);
  }
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.serviceUrl || changes.apiKey || changes.proxyUrl) {
    scheduleAssistPoll(250);
  }
});

ensureExtensionId();
setupAssistAlarm();
scheduleAssistPoll(1000);

function setupAssistAlarm() {
  chrome.alarms.create("lingya-assist-poll", { periodInMinutes: 0.5 });
}

function scheduleAssistPoll(delayMs = POLL_IDLE_MS) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => {
    pollTimer = null;
    pollAssistOnce();
  }, Math.max(50, delayMs));
}

async function pollAssistOnce() {
  if (polling) return;
  polling = true;
  try {
    const settings = await storageGet(ASSIST_DEFAULTS);
    settings.serviceUrl = defaultServiceUrl(settings.serviceUrl);
    if (!settings.serviceUrl) {
      await setAssistStatus("Task assistant needs a Service URL.");
      return;
    }
    const extensionId = await ensureExtensionId();
    const activeTab = await getActiveTab().catch(() => null);
    const payload = {
      extension_id: extensionId,
      platform: "lingya_qq",
      proxy_url: String(settings.proxyUrl || "").trim(),
      current_url: activeTab && activeTab.url ? activeTab.url : ""
    };
    const response = await fetch(apiUrl(settings.serviceUrl, "/api/browser/assist/claim"), {
      method: "POST",
      headers: buildHeaders(settings.apiKey),
      body: JSON.stringify(payload)
    });
    const data = await readJsonOrText(response);
    if (!response.ok) {
      throw new Error(errorMessage(data, response.status));
    }
    const request = data && data.request ? data.request : null;
    if (!request) {
      await setAssistStatus("Task assistant listening.", "ok");
      scheduleAssistPoll(Number(data && data.poll_after_ms) || POLL_IDLE_MS);
      return;
    }
    await setAssistStatus(`Claimed ${request.phone || request.local_phone || "-"} for Lingya.`, "ok");
    await handleAssistRequest(settings, extensionId, request);
    scheduleAssistPoll(POLL_ACTIVE_MS);
  } catch (error) {
    await setAssistStatus(`Task assistant error: ${error.message || String(error)}`, "bad");
    scheduleAssistPoll(POLL_IDLE_MS);
  } finally {
    polling = false;
  }
}

async function handleAssistRequest(settings, extensionId, request) {
  try {
    const tab = await ensureLingyaTab(request.page_url || "https://lingya.qq.com/");
    await reportAssistState(settings, extensionId, request.assist_id, "opened", { tab_id: tab.id });
    const result = await applyAssistToTab(tab.id, request);
    await reportAssistState(settings, extensionId, request.assist_id, result.filled ? "filled" : "visible", result);
    const label = result.filled ? "Filled" : "Displayed";
    await setAssistStatus(`${label} ${request.local_phone || request.phone || "-"} on lingya.qq.com.`, "ok");
  } catch (error) {
    await reportAssistState(settings, extensionId, request.assist_id, "failed", { error: error.message || String(error) });
    await setAssistStatus(`Lingya assist failed: ${error.message || String(error)}`, "bad");
  }
}

async function ensureLingyaTab(pageUrl) {
  const tabs = await chrome.tabs.query({ url: ["https://lingya.qq.com/*"] });
  let tab = tabs.find((item) => item.active) || tabs[0] || null;
  if (tab && tab.id) {
    const update = isLingyaUrl(tab.url || "") ? { active: true } : { active: true, url: pageUrl };
    await chrome.tabs.update(tab.id, update);
    if (tab.windowId !== undefined) {
      await chrome.windows.update(tab.windowId, { focused: true }).catch(() => null);
    }
    return waitForTabReady(tab.id);
  }
  tab = await chrome.tabs.create({ url: pageUrl, active: true });
  return waitForTabReady(tab.id);
}

function waitForTabReady(tabId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(async () => {
      chrome.tabs.onUpdated.removeListener(listener);
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (tab) resolve(tab);
      else reject(new Error("Lingya tab was closed before it became ready"));
    }, 10000);
    const listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId) return;
      if (changeInfo.status === "complete") {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(tab);
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab && tab.status === "complete") {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(tab);
      }
    }).catch(() => null);
  });
}

async function applyAssistToTab(tabId, request) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["assist_content.js"] });
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "LINGYA_ASSIST_APPLY", request });
  } catch (error) {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["assist_content.js"] });
    return chrome.tabs.sendMessage(tabId, { type: "LINGYA_ASSIST_APPLY", request });
  }
}

async function reportAssistState(settings, extensionId, assistId, state, detail = {}) {
  if (!assistId) return;
  const body = {
    extension_id: extensionId,
    state,
    error: detail.error || "",
    detail
  };
  await fetch(apiUrl(settings.serviceUrl, `/api/browser/assist/${encodeURIComponent(assistId)}/state`), {
    method: "POST",
    headers: buildHeaders(settings.apiKey),
    body: JSON.stringify(body)
  }).catch(() => null);
}

async function ensureExtensionId() {
  const saved = await storageGet({ extensionId: "" });
  if (saved.extensionId) return saved.extensionId;
  const extensionId = `ext_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`;
  await storageSet({ extensionId });
  return extensionId;
}

function defaultServiceUrl(value) {
  const serviceUrl = String(value || "").trim();
  if (!serviceUrl || serviceUrl === ASSIST_LEGACY_SERVICE_URL) {
    return ASSIST_DEFAULTS.serviceUrl;
  }
  return serviceUrl.replace(/\/+$/, "");
}

function buildHeaders(apiKey) {
  const headers = { "Content-Type": "application/json" };
  const token = String(apiKey || "").trim();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
    headers["X-API-Key"] = token;
  }
  return headers;
}

function apiUrl(base, path) {
  return new URL(path, `${String(base || "").replace(/\/+$/, "")}/`).toString();
}

async function readJsonOrText(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorMessage(data, status) {
  if (typeof data === "string" && data) return data;
  if (data && data.detail) return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
  if (data && data.error) return data.error;
  return `HTTP ${status}`;
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

function isLingyaUrl(value) {
  try {
    const url = new URL(value);
    return url.hostname === "lingya.qq.com" || url.hostname.endsWith(".lingya.qq.com");
  } catch {
    return false;
  }
}

async function setAssistStatus(message, kind = "") {
  await storageSet({
    assistLastStatus: String(message || ""),
    assistLastKind: kind,
    assistLastUpdatedAt: new Date().toISOString()
  });
}

function storageGet(defaults) {
  return chrome.storage.local.get(defaults);
}

function storageSet(values) {
  return chrome.storage.local.set(values);
}
