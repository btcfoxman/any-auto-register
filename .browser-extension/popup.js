const COOKIE_TARGETS = [
  "https://lingya.qq.com/",
  "https://pbaccess.lingya.qq.com/",
  "https://v.qq.com/",
  "https://qq.com/"
];

const COOKIE_DOMAINS = [
  ".lingya.qq.com",
  "lingya.qq.com",
  ".v.qq.com",
  "v.qq.com",
  ".qq.com",
  "qq.com",
  ".tencent.com",
  "tencent.com"
];

// Derived from request-side ext.cookies in tmp/fetch_xhr.ndjson.
const SYNC_COOKIE_NAMES = new Set([
  "_new_next_refresh_time",
  "_qimei_fingerprint",
  "_qimei_h38",
  "_qimei_q36",
  "_qimei_uuid42",
  "avatar",
  "env",
  "last_refresh_second",
  "last_refresh_time",
  "last_refresh_vuserid",
  "min_expire_time",
  "nick",
  "v_login_time_init",
  "v_main_login",
  "v_next_refresh_time",
  "v_t_access_token",
  "v_t_appid",
  "v_t_openid",
  "v_t_refresh_token",
  "v_vurefresh",
  "v_vuserid",
  "v_vusession",
  "vdevice_guid",
  "video_appid",
  "video_platform",
  "vqq_vuserid",
  "vqq_vusession",
  "vuserid",
  "vusession"
]);

const DEFAULTS = {
  serviceUrl: "http://192.168.3.5:8000",
  apiKey: "",
  accountName: "",
  proxyUrl: "",
  maxConcurrency: 1
};
const LEGACY_DEFAULT_SERVICE_URLS = new Set([
  "http://127.0.0.1:8787",
  "http://192.168.3.3:8787"
]);
const LEGACY_DEFAULT_API_KEYS = new Set(["sk-test-api-key"]);

const $ = (id) => document.getElementById(id);
let scannedCookies = [];
let lastScanDiagnostics = [];
let lastRawCookieNames = [];
let saveTimer = null;

document.addEventListener("DOMContentLoaded", async () => {
  const saved = await storageGet(DEFAULTS);
  const serviceUrl = defaultServiceUrl(saved.serviceUrl);
  const apiKey = defaultApiKey(saved.apiKey);
  $("serviceUrl").value = serviceUrl;
  $("apiKey").value = apiKey;
  $("accountName").value = saved.accountName || "";
  $("proxyUrl").value = saved.proxyUrl || "";
  $("maxConcurrency").value = saved.maxConcurrency || 1;
  if (saved.serviceUrl !== serviceUrl || saved.apiKey !== apiKey) {
    await storageSet({ serviceUrl, apiKey });
  }

  $("importButton").addEventListener("click", importCookies);
  $("openConsoleButton").addEventListener("click", openConsole);
  $("refreshCookiesButton").addEventListener("click", () => scanAndRenderCookies(true));
  $("grantAccessButton").addEventListener("click", grantServiceAccess);
  for (const id of ["serviceUrl", "apiKey", "accountName", "proxyUrl", "maxConcurrency"]) {
    $(id).addEventListener("input", persistSettingsSoon);
    $(id).addEventListener("change", persistSettingsSoon);
  }
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && (changes.assistLastStatus || changes.assistLastKind || changes.assistLastUpdatedAt)) {
      renderAssistStatus();
    }
  });

  await storageSet(readSettings());
  await renderAssistStatus();
  await renderServiceAccessStatus();
  await scanAndRenderCookies(!saved.accountName);
});

function defaultServiceUrl(value) {
  const serviceUrl = String(value || "").trim().replace(/\/+$/, "");
  if (!serviceUrl || LEGACY_DEFAULT_SERVICE_URLS.has(serviceUrl)) {
    return DEFAULTS.serviceUrl;
  }
  return serviceUrl;
}

function defaultApiKey(value) {
  const apiKey = String(value || "").trim();
  return LEGACY_DEFAULT_API_KEYS.has(apiKey) ? "" : apiKey;
}

async function importCookies() {
  setStatus("Collecting Lingya cookies for local import...");
  try {
    const settings = readSettings();
    await ensureServiceAccess(settings.serviceUrl, { requestIfMissing: true });
    await storageSet(settings);

    const cookies = scannedCookies.length ? scannedCookies : await scanAndRenderCookies(true);
    if (!cookies.length) {
      throw new Error(noCookieMessage());
    }

    const payload = {
      platform: "lingya_qq",
      name: settings.accountName || defaultAccountName(cookies),
      cookies: cookies.map(toPayloadCookie),
      user_agent: navigator.userAgent || "",
      sec_ch_ua: getSecChUa(),
      sec_ch_ua_platform: getSecChUaPlatform(),
      proxy_url: settings.proxyUrl,
      max_concurrency: settings.maxConcurrency
    };

    const response = await fetch(apiUrl(settings.serviceUrl, "/api/browser/import-account"), {
      method: "POST",
      headers: buildHeaders(settings.apiKey),
      body: JSON.stringify(payload)
    });
    const data = await readJsonOrText(response);
    if (!response.ok) {
      throw new Error(errorMessage(data, response.status));
    }

    const account = data.account || {};
    const action = data.action === "updated" ? "Updated" : "Imported";
    setStatus(`${action} local account #${account.id || "-"} ${account.name || payload.name}.`, "ok");
  } catch (error) {
    setStatus(error.message || String(error), "bad");
  }
}

async function scanAndRenderCookies(forceNameFromCookie = false) {
  setCookieSummary("Scanning Lingya cookies...");
  try {
    scannedCookies = await collectLingyaCookies();
    const defaultName = defaultAccountName(scannedCookies);
    if (defaultName && (forceNameFromCookie || !$("accountName").value.trim() || $("accountName").value.startsWith("browser-"))) {
      $("accountName").value = defaultName;
    }
    renderCookiePreview(scannedCookies);
    return scannedCookies;
  } catch (error) {
    scannedCookies = [];
    setCookieSummary(error.message || String(error), "bad");
    renderScanDiagnostics(lastScanDiagnostics);
    $("cookieList").textContent = "";
    return scannedCookies;
  }
}

async function openConsole() {
  const settings = readSettings();
  try {
    await ensureServiceAccess(settings.serviceUrl, { requestIfMissing: true });
  } catch (error) {
    setStatus(error.message || String(error), "bad");
    return;
  }
  await storageSet(settings);
  const url = settings.apiKey
    ? `${settings.serviceUrl.replace(/\/+$/, "")}/?token=${encodeURIComponent(settings.apiKey)}`
    : `${settings.serviceUrl.replace(/\/+$/, "")}/`;
  chrome.tabs.create({ url });
}

async function collectLingyaCookies() {
  const all = [];
  lastScanDiagnostics = [];
  const stores = await getCookieStores();
  for (const store of stores) {
    const storeId = store.id;
    const storePrefix = stores.length > 1 ? `store:${storeId} ` : "";

    for (const url of COOKIE_TARGETS) {
      const cookies = await readCookies({ url, storeId }, `${storePrefix}url:${url}`);
      all.push(...cookies);
    }

    for (const domain of COOKIE_DOMAINS) {
      const cookies = await readCookies({ domain, storeId }, `${storePrefix}domain:${domain}`);
      all.push(...cookies);
    }

    const storeCookies = await readCookies({ storeId }, `${storePrefix}all-accessible`);
    all.push(...storeCookies);
  }

  const activeTab = await getActiveTab();
  const activeUrl = activeTab && activeTab.url ? activeTab.url : "";
  if (activeUrl && isLingyaUrl(activeUrl)) {
    const cookies = await readCookies({ url: activeUrl }, `active:${activeUrl}`);
    all.push(...cookies);
    const documentCookies = await readDocumentCookiesFromTab(activeTab);
    all.push(...documentCookies);
  } else {
    lastScanDiagnostics.push({ label: "active-lingya-tab", count: 0, error: "not-active" });
  }

  const deduped = dedupeCookies(all);
  const candidates = deduped.filter((cookie) => isAllowedCookieDomain(String(cookie.domain || "").toLowerCase()));
  const filtered = dedupeSyncCookies(candidates.filter(isSyncCookie));
  lastRawCookieNames = uniqueSorted(candidates.map((cookie) => `${cookie.name}@${cookie.domain || "-"}`));
  lastScanDiagnostics.push({ label: "all-read-total", count: deduped.length });
  lastScanDiagnostics.push({ label: "raw-total", count: candidates.length });
  lastScanDiagnostics.push({ label: "sync-total", count: filtered.length });
  return filtered;
}

async function getCookieStores() {
  try {
    const stores = await chrome.cookies.getAllCookieStores();
    if (stores && stores.length) return stores;
  } catch (error) {
    lastScanDiagnostics.push({ label: "stores", count: 0, error: error.message || String(error) });
  }
  return [{ id: undefined }];
}

async function readCookies(query, label) {
  try {
    const cleanQuery = Object.fromEntries(Object.entries(query).filter(([, value]) => value !== undefined));
    const cookies = await chrome.cookies.getAll(cleanQuery);
    lastScanDiagnostics.push({ label, count: cookies.length });
    return cookies;
  } catch (error) {
    lastScanDiagnostics.push({ label, count: 0, error: error.message || String(error) });
    return [];
  }
}

async function readDocumentCookiesFromTab(tab) {
  if (!tab || !tab.id || !isLingyaUrl(tab.url || "")) return [];
  try {
    const frames = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        cookie: document.cookie,
        host: location.hostname,
        path: location.pathname || "/"
      })
    });
    const result = frames && frames[0] && frames[0].result ? frames[0].result : {};
    const cookies = parseCookieHeader(result.cookie || "").map((cookie) => ({
      ...cookie,
      domain: result.host || "lingya.qq.com",
      path: "/",
      source: "document.cookie"
    }));
    lastScanDiagnostics.push({ label: `document:${result.host || "unknown"}`, count: cookies.length });
    return cookies;
  } catch (error) {
    lastScanDiagnostics.push({ label: "document-cookie", count: 0, error: error.message || String(error) });
    return [];
  }
}

function parseCookieHeader(header) {
  return String(header || "")
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const index = part.indexOf("=");
      if (index < 0) return { name: part, value: "" };
      return {
        name: part.slice(0, index).trim(),
        value: part.slice(index + 1).trim()
      };
    })
    .filter((cookie) => cookie.name);
}

function isSyncCookie(cookie) {
  if (!SYNC_COOKIE_NAMES.has(cookie.name)) return false;
  const domain = String(cookie.domain || "").toLowerCase();
  return isAllowedCookieDomain(domain);
}

function isAllowedCookieDomain(domain) {
  return (
    domain === "qq.com"
    || domain.endsWith(".qq.com")
    || domain === "tencent.com"
    || domain.endsWith(".tencent.com")
  );
}

function dedupeCookies(cookies) {
  const byKey = new Map();
  for (const cookie of cookies) {
    const key = `${cookie.name}\n${cookie.domain || ""}\n${cookie.path || "/"}`;
    byKey.set(key, cookie);
  }
  return [...byKey.values()].sort((a, b) => {
    if (a.name !== b.name) return a.name.localeCompare(b.name);
    return (b.path || "").length - (a.path || "").length;
  });
}

function dedupeSyncCookies(cookies) {
  const byName = new Map();
  for (const cookie of cookies) {
    const existing = byName.get(cookie.name);
    if (!existing || syncCookieScore(cookie) > syncCookieScore(existing)) {
      byName.set(cookie.name, cookie);
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function syncCookieScore(cookie) {
  const domain = String(cookie.domain || "").toLowerCase();
  let score = cookie.value ? 10 : 0;
  if (domain === ".lingya.qq.com") score += 6;
  else if (domain.endsWith("lingya.qq.com")) score += 5;
  else if (domain === ".qq.com") score += 4;
  else if (domain.endsWith(".qq.com")) score += 3;
  else if (domain.endsWith(".tencent.com")) score += 2;
  if (cookie.source === "document.cookie") score += 1;
  return score;
}

function toPayloadCookie(cookie) {
  return {
    name: cookie.name,
    value: cookie.value || "",
    domain: cookie.domain || "",
    path: cookie.path || "/",
    secure: Boolean(cookie.secure),
    httpOnly: Boolean(cookie.httpOnly),
    sameSite: cookie.sameSite || "",
    expirationDate: cookie.expirationDate || null
  };
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

function defaultAccountName(cookies) {
  const map = new Map(cookies.map((cookie) => [cookie.name, cookie.value]));
  const nick = decodeCookieValue(map.get("nick") || map.get("v_nick") || map.get("vqq_nick") || "");
  if (nick) return nick;
  const key = map.get("v_vuserid") || map.get("vuserid") || map.get("vdevice_guid");
  return key ? `browser-${key}` : `browser-${new Date().toISOString().slice(0, 10)}`;
}

function renderCookiePreview(cookies) {
  const list = $("cookieList");
  list.textContent = "";
  if (!cookies.length) {
    setCookieSummary(noCookieMessage(), "bad");
    renderScanDiagnostics(lastScanDiagnostics);
    return;
  }

  const map = new Map(cookies.map((cookie) => [cookie.name, cookie.value]));
  const nick = decodeCookieValue(map.get("nick") || map.get("v_nick") || "");
  const userId = map.get("v_vuserid") || map.get("vuserid") || "-";
  const device = map.get("vdevice_guid") || "-";
  setCookieSummary(`${cookies.length}/${SYNC_COOKIE_NAMES.size} cookies. nick=${nick || "-"} user=${maskValue(userId)} device=${maskValue(device)}`, "ok");
  renderScanDiagnostics([]);

  const visible = cookies.slice(0, 16);
  const more = cookies.length > visible.length ? `<span class="cookie-more">+${cookies.length - visible.length}</span>` : "";
  list.innerHTML = visible.map((cookie) => `
    <div class="cookie-row">
      <code title="${escapeHtml(cookie.name)}">${escapeHtml(shortCookieName(cookie.name))}</code>
      <span class="value">${escapeHtml(maskValue(cookie.value || ""))}</span>
    </div>
  `).join("") + more;
}

function renderScanDiagnostics(items) {
  const debug = $("scanDebug");
  debug.className = "scan-debug";
  debug.textContent = "";
  if (!items.length) return;
  const rows = items.map((item) => {
    const suffix = item.error ? ` error=${item.error}` : "";
    return `<span>${escapeHtml(item.label)} = ${escapeHtml(item.count)}${escapeHtml(suffix)}</span>`;
  });
  if (lastRawCookieNames.length) {
    rows.push(`<span>raw names = ${escapeHtml(lastRawCookieNames.join(", "))}</span>`);
  }
  debug.className = "scan-debug visible";
  debug.innerHTML = rows.join("");
}

function noCookieMessage() {
  const raw = lastScanDiagnostics.find((item) => item.label === "raw-total");
  if (raw && raw.count > 0) {
    return `Read ${raw.count} cookies, but none match the fetch_xhr.ndjson request-cookie allowlist. Keep an active signed-in lingya.qq.com tab open, then reopen this popup.`;
  }
  return "No Lingya request cookies found. Reload the extension after permission changes, then open lingya.qq.com in this same browser profile and sign in.";
}

function readSettings() {
  return {
    serviceUrl: ($("serviceUrl").value || DEFAULTS.serviceUrl).replace(/\/+$/, ""),
    apiKey: $("apiKey").value || "",
    accountName: $("accountName").value.trim(),
    proxyUrl: $("proxyUrl").value.trim(),
    maxConcurrency: clampNumber(Number($("maxConcurrency").value || 1), 1, 10)
  };
}

function persistSettingsSoon() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    saveTimer = null;
    await storageSet(readSettings());
    await renderServiceAccessStatus();
    await renderAssistStatus();
  }, 250);
}

async function grantServiceAccess() {
  const settings = readSettings();
  setStatus(`Requesting access to ${safeOrigin(settings.serviceUrl) || settings.serviceUrl}...`);
  try {
    await ensureServiceAccess(settings.serviceUrl, { requestIfMissing: true });
    await storageSet({ ...settings, serviceAccessGrantedAt: new Date().toISOString() });
    await renderServiceAccessStatus();
    await renderAssistStatus();
    setStatus(`Service access granted for ${safeOrigin(settings.serviceUrl)}.`, "ok");
  } catch (error) {
    await renderServiceAccessStatus();
    setStatus(error.message || String(error), "bad");
  }
}

async function renderAssistStatus() {
  const data = await storageGet({
    assistLastStatus: "Task assistant idle.",
    assistLastKind: "",
    assistLastUpdatedAt: ""
  });
  const status = $("assistStatus");
  const updated = $("assistUpdated");
  status.className = `assist-status ${data.assistLastKind || ""}`.trim();
  status.textContent = data.assistLastStatus || "Task assistant idle.";
  updated.textContent = data.assistLastUpdatedAt ? new Date(data.assistLastUpdatedAt).toLocaleTimeString() : "Listening";
}

async function renderServiceAccessStatus() {
  const status = $("serviceAccessStatus");
  if (!status) return;
  const serviceUrl = readSettings().serviceUrl;
  const origin = safeOrigin(serviceUrl);
  try {
    const allowed = await hasServiceAccess(serviceUrl);
    status.className = `service-access-status ${allowed ? "ok" : "bad"}`;
    status.textContent = allowed
      ? `Access granted: ${origin}`
      : `Access required: ${origin || serviceUrl}`;
  } catch (error) {
    status.className = "service-access-status bad";
    status.textContent = error.message || String(error);
  }
}

async function ensureServiceAccess(serviceUrl, { requestIfMissing = false } = {}) {
  const pattern = serviceOriginPattern(serviceUrl);
  if (!pattern) {
    throw new Error(`Invalid Service URL: ${serviceUrl || "-"}`);
  }
  if (!chrome.permissions || !chrome.permissions.contains) {
    return true;
  }
  const allowed = await chrome.permissions.contains({ origins: [pattern] });
  if (allowed) return true;
  if (!requestIfMissing) return false;
  if (!chrome.permissions.request) {
    throw new Error(`Extension access is missing for ${safeOrigin(serviceUrl)}.`);
  }
  const granted = await chrome.permissions.request({ origins: [pattern] });
  if (!granted) {
    throw new Error(`Extension access was not granted for ${safeOrigin(serviceUrl)}.`);
  }
  return true;
}

async function hasServiceAccess(serviceUrl) {
  return ensureServiceAccess(serviceUrl, { requestIfMissing: false });
}

function serviceOriginPattern(serviceUrl) {
  try {
    const url = new URL(String(serviceUrl || "").trim());
    if (!/^https?:$/.test(url.protocol)) return "";
    return `${url.protocol}//${url.hostname}/*`;
  } catch {
    return "";
  }
}

function safeOrigin(serviceUrl) {
  try {
    return new URL(String(serviceUrl || "")).origin;
  } catch {
    return "";
  }
}

function buildHeaders(apiKey) {
  const headers = { "Content-Type": "application/json" };
  if (apiKey) {
    headers["Authorization"] = `Bearer ${apiKey}`;
    headers["X-API-Key"] = apiKey;
  }
  return headers;
}

function apiUrl(base, path) {
  return new URL(path, `${base.replace(/\/+$/, "")}/`).toString();
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
  return `Request failed with HTTP ${status}.`;
}

function getSecChUa() {
  const brands = navigator.userAgentData && navigator.userAgentData.brands;
  if (!brands || !brands.length) return "";
  return brands.map((item) => `"${String(item.brand).replace(/"/g, "\\\"")}";v="${item.version}"`).join(", ");
}

function getSecChUaPlatform() {
  const platform = navigator.userAgentData && navigator.userAgentData.platform;
  return platform ? `"${String(platform).replace(/"/g, "\\\"")}"` : "";
}

function clampNumber(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, Math.trunc(value)));
}

function setStatus(message, kind = "") {
  const status = $("status");
  status.className = kind;
  status.textContent = message;
}

function setCookieSummary(message, kind = "") {
  const summary = $("cookieSummary");
  summary.className = `cookie-summary ${kind}`.trim();
  summary.textContent = message;
}

function maskValue(value) {
  const text = decodeCookieValue(value);
  if (!text) return "(empty)";
  if (text.length <= 6) return "***";
  return `${text.slice(0, 3)}...${text.slice(-3)}`;
}

function shortCookieName(name) {
  return String(name)
    .replace(/^_new_next_refresh_time$/, "new_refresh")
    .replace(/^last_refresh_second$/, "refresh_sec")
    .replace(/^last_refresh_time$/, "refresh_time")
    .replace(/^min_expire_time$/, "min_expire")
    .replace(/^v_login_time_init$/, "login_init")
    .replace(/^v_next_refresh_time$/, "next_refresh")
    .replace(/^v_t_access_token$/, "t_access")
    .replace(/^v_t_refresh_token$/, "t_refresh")
    .replace(/^v_t_openid$/, "t_openid")
    .replace(/^v_t_appid$/, "t_appid")
    .replace(/^v_vusession$/, "v_session")
    .replace(/^vqq_vusession$/, "qq_session")
    .replace(/^vusession$/, "session")
    .replace(/^v_vurefresh$/, "v_refresh")
    .replace(/^v_vuserid$/, "v_userid")
    .replace(/^vqq_vuserid$/, "qq_userid")
    .replace(/^vdevice_guid$/, "device")
    .replace(/^video_appid$/, "appid")
    .replace(/^video_platform$/, "platform")
    .replace(/^_qimei_fingerprint$/, "qimei_fp")
    .replace(/^_qimei_uuid42$/, "qimei_id")
    .replace(/^_qimei_h38$/, "qimei_h38")
    .replace(/^_qimei_q36$/, "qimei_q36");
}

function decodeCookieValue(value) {
  try {
    return decodeURIComponent(String(value || ""));
  } catch {
    return String(value || "");
  }
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function uniqueSorted(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function storageGet(defaults) {
  return chrome.storage.local.get(defaults);
}

function storageSet(values) {
  return chrome.storage.local.set(values);
}
