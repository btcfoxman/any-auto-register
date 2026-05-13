(() => {
  const PANEL_ID = "any-auto-register-lingya-assist";
  const STYLE_ID = "any-auto-register-lingya-assist-style";

  if (window.__anyAutoRegisterLingyaAssistLoaded) return;
  window.__anyAutoRegisterLingyaAssistLoaded = true;

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || message.type !== "LINGYA_ASSIST_APPLY") return false;
    try {
      const result = applyLingyaAssist(message.request || {});
      sendResponse(result);
    } catch (error) {
      sendResponse({ ok: false, filled: false, error: error.message || String(error) });
    }
    return true;
  });

  function applyLingyaAssist(request) {
    ensureStyle();
    const phone = String(request.local_phone || request.phone || "").replace(/^\+\d{1,3}/, "").trim();
    const displayPhone = String(request.phone || request.local_phone || phone || "-");
    const proxy = String(request.proxy_url || "").trim() || "Direct";
    const input = findPhoneInput();
    let filled = false;
    let error = "";

    if (input && phone) {
      setNativeInputValue(input, phone);
      input.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: phone }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus({ preventScroll: false });
      filled = true;
    } else if (!input) {
      error = "phone input not found";
    } else {
      error = "phone is empty";
    }

    renderPanel({
      displayPhone,
      fillPhone: phone || "-",
      proxy,
      taskId: request.task_id || "-",
      filled,
      error
    });
    return { ok: true, filled, input_found: Boolean(input), error };
  }

  function findPhoneInput() {
    const exact = document.querySelector('input[type="text"][placeholder="请输入手机号"]');
    if (exact) return exact;
    const candidates = [...document.querySelectorAll("input")];
    return candidates.find((input) => String(input.placeholder || "").includes("手机号")) || null;
  }

  function setNativeInputValue(input, value) {
    const proto = Object.getPrototypeOf(input);
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value")
      || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    if (descriptor && descriptor.set) {
      descriptor.set.call(input, value);
    } else {
      input.value = value;
    }
  }

  function renderPanel({ displayPhone, fillPhone, proxy, taskId, filled, error }) {
    let panel = document.getElementById(PANEL_ID);
    if (!panel) {
      panel = document.createElement("aside");
      panel.id = PANEL_ID;
      document.documentElement.appendChild(panel);
    }
    panel.innerHTML = `
      <div class="aar-pulse"></div>
      <div class="aar-head">
        <span>ANY REGISTER</span>
        <strong>${filled ? "已填入手机号" : "等待手机号输入框"}</strong>
      </div>
      <dl>
        <div><dt>手机号</dt><dd>${escapeHtml(displayPhone)}</dd></div>
        <div><dt>填入</dt><dd>${escapeHtml(fillPhone)}</dd></div>
        <div><dt>代理</dt><dd title="${escapeHtml(proxy)}">${escapeHtml(shortProxy(proxy))}</dd></div>
        <div><dt>任务</dt><dd>${escapeHtml(shortTask(taskId))}</dd></div>
      </dl>
      ${error ? `<p>${escapeHtml(error)}</p>` : ""}
    `;
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      #${PANEL_ID} {
        position: fixed;
        top: 18px;
        right: 18px;
        z-index: 2147483647;
        width: min(312px, calc(100vw - 32px));
        border: 1px solid rgba(91, 255, 198, 0.62);
        border-radius: 8px;
        background: rgba(9, 15, 19, 0.94);
        color: #edf8f3;
        box-shadow: 0 18px 48px rgba(0, 0, 0, 0.38), inset 0 1px rgba(255, 255, 255, 0.08);
        font: 12px/1.35 "Aptos", "Segoe UI", sans-serif;
        overflow: hidden;
        backdrop-filter: blur(10px);
      }
      #${PANEL_ID} .aar-pulse {
        height: 3px;
        background: linear-gradient(90deg, #5bffc6, #ffcf5a, #5bffc6);
        background-size: 220% 100%;
        animation: aar-scan 1.1s linear infinite;
      }
      #${PANEL_ID} .aar-head {
        display: grid;
        gap: 2px;
        padding: 10px 12px 8px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      }
      #${PANEL_ID} .aar-head span {
        color: #5bffc6;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.08em;
      }
      #${PANEL_ID} .aar-head strong {
        font-size: 14px;
        letter-spacing: 0;
      }
      #${PANEL_ID} dl {
        display: grid;
        gap: 7px;
        margin: 0;
        padding: 10px 12px 12px;
      }
      #${PANEL_ID} dl div {
        display: grid;
        grid-template-columns: 52px minmax(0, 1fr);
        align-items: baseline;
        gap: 8px;
      }
      #${PANEL_ID} dt {
        color: rgba(237, 248, 243, 0.55);
      }
      #${PANEL_ID} dd {
        margin: 0;
        color: #fff;
        font-family: "Cascadia Mono", "Consolas", monospace;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${PANEL_ID} p {
        margin: -5px 12px 12px;
        color: #ffcf5a;
      }
      @keyframes aar-scan {
        0% { background-position: 0% 50%; opacity: 0.55; }
        50% { opacity: 1; }
        100% { background-position: 220% 50%; opacity: 0.55; }
      }
    `;
    document.documentElement.appendChild(style);
  }

  function shortProxy(value) {
    const text = String(value || "");
    if (text === "Direct") return text;
    if (text.length <= 38) return text;
    return `${text.slice(0, 18)}...${text.slice(-14)}`;
  }

  function shortTask(value) {
    const text = String(value || "");
    if (text.length <= 18) return text;
    return `${text.slice(0, 10)}...${text.slice(-6)}`;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
})();
