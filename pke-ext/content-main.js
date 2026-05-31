// Sediment / PKE — ChatGPT conversation turn capture + pre-Send card.
//
// This script runs in the MAIN world so it can read the page's DOM and
// observe React-rendered conversation turns. It detects each completed
// user-turn and assistant-turn via MutationObserver, pairs the most
// recent user turn with the next completed assistant turn, and posts
// the pair to the ISOLATED-world bridge (`content-isolated.js`), which
// forwards it to `background.js` for the cross-origin POST to the
// local Sediment daemon at http://127.0.0.1:7421/api/v1/evidence.
//
// It also installs a capturing-phase Send interceptor: when the user
// clicks `button[data-testid="send-button"]` or presses Enter in the
// composer, we synchronously POST the draft to
// `/api/v1/intervention/check`. If the local daemon green-lights an
// intervention, we render an inline "AI 回答之前" Socratic card sitting
// between the prompt input and the Send button; otherwise (and on any
// error or > 1 s timeout) we transparently re-fire the original Send
// so the user is never blocked on a local network failure.
//
// ChatGPT DOM contract (stable across React class rewrites, 2024-2026):
//   - Each turn is rendered into a wrapper with
//     `[data-message-author-role="user"|"assistant"]` and
//     `[data-message-id="<uuid>"]`.
//   - While the assistant is still streaming, the wrapper carries
//     `data-message-streaming="true"`; when streaming finishes the
//     attribute is removed (or set to "false"). We also fall back to a
//     stability timer in case the streaming attribute is renamed.
//   - The conversation id is the last segment of `location.pathname`
//     under `/c/<uuid>`. On a fresh chat (no `/c/...` yet) we fall
//     back to a per-tab session id.

(() => {
  if (window.__PKE_CHATGPT_OBSERVER_INSTALLED__) return;
  window.__PKE_CHATGPT_OBSERVER_INSTALLED__ = true;

  const SOURCE = "browser_ext_chatgpt";
  const ASSISTANT_STABLE_MS = 1500;
  // Cap text length on the page side too; the server clips again at
  // MAX_TURN_BYTES, but trimming here keeps postMessage payloads small.
  const MAX_TURN_CHARS = 64 * 1024;

  // Per-tab fallback conversation id, used when the URL has no /c/<uuid>.
  const tabSessionId =
    "tab_" +
    Math.random().toString(36).slice(2, 10) +
    Date.now().toString(36);

  function conversationId() {
    const match = location.pathname.match(/\/c\/([0-9a-f-]{8,})/i);
    return match ? match[1] : tabSessionId;
  }

  function extractText(node) {
    // ChatGPT renders assistant turns as markdown; `innerText` collapses
    // whitespace the way the user sees it, which is fine for evidence.
    const text = (node.innerText || node.textContent || "").trim();
    return text.length > MAX_TURN_CHARS ? text.slice(0, MAX_TURN_CHARS) : text;
  }

  function postPair(payload) {
    window.postMessage({ __pke__: true, payload }, window.location.origin);
  }

  // State: track the most recent user turn keyed by conversation id.
  // We don't try to handle interleaved conversations across tabs —
  // each tab has its own conversation id.
  /** @type {Map<string, {messageId: string, text: string, t0: number, turnIndex: number}>} */
  const pendingUser = new Map();
  /** @type {Map<string, number>} */
  const turnCounters = new Map();
  /** @type {Set<string>} */
  const emittedAssistantIds = new Set();
  /** @type {Map<string, ReturnType<typeof setTimeout>>} */
  const assistantStabilityTimers = new Map();

  function nextTurnIndex(convId) {
    const current = turnCounters.get(convId) || 0;
    turnCounters.set(convId, current + 1);
    return current;
  }

  function handleUserTurn(node) {
    const messageId = node.getAttribute("data-message-id") || "";
    if (!messageId) return;
    const convId = conversationId();
    const text = extractText(node);
    if (!text) return;
    // If we already recorded this exact message id, do nothing.
    const existing = pendingUser.get(convId);
    if (existing && existing.messageId === messageId) return;
    pendingUser.set(convId, {
      messageId,
      text,
      t0: Date.now(),
      turnIndex: nextTurnIndex(convId),
    });
  }

  function emitAssistantTurn(node) {
    const messageId = node.getAttribute("data-message-id") || "";
    if (!messageId || emittedAssistantIds.has(messageId)) return;
    const convId = conversationId();
    const assistantText = extractText(node);
    if (!assistantText) return;
    const user = pendingUser.get(convId);
    // Only emit a pair once we have a preceding user turn; ChatGPT
    // never opens a conversation with an unsolicited assistant turn,
    // so dropping orphan assistant DOM (e.g. a re-rendered welcome
    // page) is the right behavior.
    if (!user) return;
    emittedAssistantIds.add(messageId);
    pendingUser.delete(convId);
    postPair({
      source: SOURCE,
      conversation_id: convId,
      turn_index: user.turnIndex,
      user_message_id: user.messageId,
      assistant_message_id: messageId,
      user_text: user.text,
      assistant_text: assistantText,
      t0: user.t0,
      url: location.href,
    });
  }

  function isStreaming(node) {
    const attr = node.getAttribute("data-message-streaming");
    return attr === "true";
  }

  function handleAssistantTurn(node) {
    const messageId = node.getAttribute("data-message-id") || "";
    if (!messageId || emittedAssistantIds.has(messageId)) return;

    // Fast path: the streaming attribute is already gone or "false".
    if (!isStreaming(node)) {
      emitAssistantTurn(node);
      return;
    }
    // Slow path: still streaming. Schedule a stability check — if no
    // mutation arrives for ASSISTANT_STABLE_MS we treat the turn as
    // done. The mutation observer below also fires `handleAssistantTurn`
    // again on every text change, so this timer is just a backstop in
    // case ChatGPT renames `data-message-streaming` in a future revision.
    if (assistantStabilityTimers.has(messageId)) {
      clearTimeout(assistantStabilityTimers.get(messageId));
    }
    assistantStabilityTimers.set(
      messageId,
      setTimeout(() => {
        assistantStabilityTimers.delete(messageId);
        emitAssistantTurn(node);
      }, ASSISTANT_STABLE_MS),
    );
  }

  function inspectNode(node) {
    if (!(node instanceof HTMLElement)) return;
    const role = node.getAttribute && node.getAttribute("data-message-author-role");
    if (role === "user") {
      handleUserTurn(node);
    } else if (role === "assistant") {
      handleAssistantTurn(node);
    }
    // Descend: a new conversation often lands as a single subtree
    // insertion containing both the user and assistant wrappers.
    const userTurns = node.querySelectorAll
      ? node.querySelectorAll('[data-message-author-role="user"]')
      : [];
    userTurns.forEach(handleUserTurn);
    const assistantTurns = node.querySelectorAll
      ? node.querySelectorAll('[data-message-author-role="assistant"]')
      : [];
    assistantTurns.forEach(handleAssistantTurn);
  }

  function onMutations(mutations) {
    for (const mutation of mutations) {
      for (const added of mutation.addedNodes) inspectNode(added);
      // Streaming updates arrive as characterData / childList changes
      // inside an existing assistant wrapper. Walk up to find that
      // wrapper and re-evaluate streaming state.
      if (mutation.type === "characterData" || mutation.target) {
        const target = mutation.target instanceof HTMLElement
          ? mutation.target
          : mutation.target && mutation.target.parentElement;
        if (!target) continue;
        const wrapper = target.closest
          ? target.closest('[data-message-author-role="assistant"]')
          : null;
        if (wrapper) handleAssistantTurn(wrapper);
        if (mutation.type === "attributes" && mutation.attributeName === "data-message-streaming") {
          const attrTarget = mutation.target;
          if (attrTarget instanceof HTMLElement) handleAssistantTurn(attrTarget);
        }
      }
    }
  }

  const observer = new MutationObserver(onMutations);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["data-message-streaming"],
  });

  // Catch-up: handle any turns already on the page when we attach.
  inspectNode(document.documentElement);

  // ---------------------------------------------------------------
  // Pre-Send Socratic intervention.
  //
  // Flow (Option A from the design survey):
  //   1. Capturing-phase listeners on document for `click` on
  //      `button[data-testid="send-button"]` and `keydown` on the
  //      composer detect a Send attempt before React's synthetic
  //      handler fires.
  //   2. We mark the event so a re-dispatched copy bypasses us, then
  //      `preventDefault` + `stopPropagation` to swallow the original.
  //   3. We synchronously POST the draft to /api/v1/intervention/check.
  //      Any non-2xx, network error, or > 1 s timeout falls open: we
  //      re-fire Send immediately so the user is never blocked on
  //      local-daemon trouble.
  //   4. On `{intervene: true}` we render an inline card between the
  //      composer and Send button. "跳过" fires `outcome=dismissed`
  //      then re-fires Send; "回答 (约 10 秒)" fires `outcome=engaged`
  //      with the typed response then re-fires Send.
  //
  // Skill resolution is deliberately punted for v1: we send
  // `skill_label="unknown"` and rely on the server's per-source
  // dismiss/daily-cap counters to keep the card from over-firing.

  const CHECK_TIMEOUT_MS = 1000;
  const CARD_ATTR = "data-pke-intervention-card";
  // Hand-rolled identity tag we set on a re-dispatched event so our own
  // capture listener knows to let it through instead of intercepting
  // the user a second time. We can't rely on `event.isTrusted` because
  // synthetic clicks/keydowns also report `isTrusted=false`.
  const PKE_PASSTHROUGH = "__pke_passthrough__";
  // Per-tab in-flight guard. While a check is in flight we keep
  // swallowing further Send attempts so the user doesn't double-fire.
  let interventionInFlight = false;
  // Currently rendered card, if any. Mounted as a sibling of the
  // composer's send-button parent so it sits between the prompt input
  // and the Send button visually.
  let currentCard = null;
  // Outstanding bridge requests keyed by request id. The MAIN world
  // can't talk to chrome.runtime directly, so we postMessage to the
  // ISOLATED-world bridge with a fresh id and resolve the promise
  // when the bridge posts back a response with the same id.
  /** @type {Map<string, {resolve: (v: any) => void, reject: (e: any) => void, timer: ReturnType<typeof setTimeout>}>} */
  const pendingBridgeCalls = new Map();
  let nextRequestId = 1;

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || !data.__pke_resp__) return;
    const pending = pendingBridgeCalls.get(data.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    pendingBridgeCalls.delete(data.id);
    pending.resolve(data.body);
  });

  // Send a POST to the local daemon via the ISOLATED-world bridge.
  // Returns the parsed JSON body, or null on timeout / error.
  function bridgePost(path, body, timeoutMs) {
    return new Promise((resolve) => {
      const id = `pke_${nextRequestId++}_${Date.now().toString(36)}`;
      const timer = setTimeout(() => {
        if (pendingBridgeCalls.delete(id)) resolve(null);
      }, timeoutMs);
      pendingBridgeCalls.set(id, { resolve, reject: () => resolve(null), timer });
      window.postMessage(
        { __pke_req__: true, id, path, body },
        window.location.origin,
      );
    });
  }

  function getComposerEditor() {
    // ChatGPT renders the composer as a ProseMirror contenteditable.
    // Prefer the ProseMirror node; fall back to any contenteditable
    // descendant of the composer form if the class name moves.
    return (
      document.querySelector('form div.ProseMirror[contenteditable="true"]') ||
      document.querySelector('form [contenteditable="true"]')
    );
  }

  function getSendButton() {
    return document.querySelector('button[data-testid="send-button"]');
  }

  function readDraftText() {
    const editor = getComposerEditor();
    if (!editor) return "";
    const text = (editor.innerText || editor.textContent || "").trim();
    return text.length > MAX_TURN_CHARS ? text.slice(0, MAX_TURN_CHARS) : text;
  }

  // Fire-and-forget /outcome. We don't await the response — outcome
  // logging is best-effort and we never want to block the user's
  // Send re-fire on the local round-trip.
  function postOutcomeBeacon(outcome, userResponse) {
    void bridgePost(
      "/api/v1/intervention/outcome",
      {
        source: SOURCE,
        outcome,
        user_response: userResponse ?? null,
      },
      CHECK_TIMEOUT_MS,
    );
  }

  async function checkIntervention(draftText) {
    const data = await bridgePost(
      "/api/v1/intervention/check",
      {
        source: SOURCE,
        // v1: skip skill resolution — the server falls back to
        // "unknown" / "this skill" defaults, and the dismiss-cap
        // gating still works on per-source counters.
        skill_label: "unknown",
        context_summary: draftText.slice(0, 2000),
      },
      CHECK_TIMEOUT_MS,
    );
    if (!data || typeof data !== "object") return { intervene: false };
    return data;
  }

  // Re-fire the user's original Send. We can't replay the swallowed
  // native event reliably (React listens via its synthetic system and
  // the event object has already had `preventDefault` called on it),
  // so we synthesise a fresh `click` on the Send button with the
  // PKE_PASSTHROUGH flag so our own capture listener leaves it alone.
  function refireSend() {
    const button = getSendButton();
    if (!button) return;
    const click = new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      view: window,
    });
    // Stamp our marker. We use a non-enumerable property so it
    // survives event-system roundtrips without leaking into other
    // listeners.
    Object.defineProperty(click, PKE_PASSTHROUGH, {
      value: true,
      enumerable: false,
    });
    button.dispatchEvent(click);
  }

  function removeCard() {
    if (currentCard && currentCard.parentNode) {
      currentCard.parentNode.removeChild(currentCard);
    }
    currentCard = null;
  }

  function buildCard(payload) {
    // Mount as a fresh element with our sentinel attribute so the
    // existing MutationObserver / inspectNode walker ignores it
    // (it filters on `data-message-author-role`, but the sentinel
    // also lets future code grep for "is this our own DOM?").
    const card = document.createElement("div");
    card.setAttribute(CARD_ATTR, "");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-label", "Sediment Socratic check");
    // Inline styles only — we don't want to depend on the host page's
    // CSS, and we don't ship a stylesheet for the MAIN-world script.
    card.style.cssText = [
      "margin: 8px 0",
      "padding: 14px 16px",
      "border: 1px solid rgba(120, 120, 120, 0.35)",
      "border-radius: 12px",
      "background: rgba(255, 255, 255, 0.96)",
      "color: #1f1f1f",
      "font-family: inherit",
      "font-size: 14px",
      "line-height: 1.45",
      "box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08)",
      "max-width: 720px",
    ].join("; ");

    const header = document.createElement("div");
    header.style.cssText = "display: flex; align-items: center; gap: 8px; margin-bottom: 10px;";

    const logo = document.createElement("span");
    logo.textContent = "沉淀";
    logo.style.cssText = [
      "display: inline-flex",
      "align-items: center",
      "justify-content: center",
      "width: 28px",
      "height: 22px",
      "font-size: 11px",
      "font-weight: 600",
      "color: #ffffff",
      "background: #3b82f6",
      "border-radius: 6px",
      "letter-spacing: 0.5px",
    ].join("; ");
    header.appendChild(logo);

    const heading = document.createElement("strong");
    heading.textContent = "AI 回答之前";
    heading.style.cssText = "font-size: 14px; color: #1f1f1f;";
    header.appendChild(heading);
    card.appendChild(header);

    const question = document.createElement("p");
    question.textContent =
      (payload && payload.question) || "在 AI 回答之前,你会先检查什么?";
    question.style.cssText = "margin: 0 0 10px 0; color: #1f1f1f;";
    card.appendChild(question);

    const input = document.createElement("textarea");
    input.rows = 3;
    input.placeholder = "写下你的想法 (大约一两句话即可)";
    input.style.cssText = [
      "width: 100%",
      "box-sizing: border-box",
      "padding: 8px 10px",
      "border: 1px solid rgba(120, 120, 120, 0.4)",
      "border-radius: 8px",
      "font: inherit",
      "color: #1f1f1f",
      "background: #ffffff",
      "resize: vertical",
    ].join("; ");
    card.appendChild(input);

    const buttonRow = document.createElement("div");
    buttonRow.style.cssText =
      "display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px;";

    const skipButton = document.createElement("button");
    skipButton.type = "button";
    skipButton.textContent = "跳过";
    skipButton.style.cssText = [
      "padding: 6px 14px",
      "border: 1px solid rgba(120, 120, 120, 0.4)",
      "border-radius: 8px",
      "background: transparent",
      "color: #1f1f1f",
      "cursor: pointer",
      "font: inherit",
    ].join("; ");
    skipButton.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      postOutcomeBeacon("dismissed", null);
      removeCard();
      refireSend();
    });
    buttonRow.appendChild(skipButton);

    const answerButton = document.createElement("button");
    answerButton.type = "button";
    answerButton.textContent = "回答 (约 10 秒)";
    answerButton.style.cssText = [
      "padding: 6px 14px",
      "border: 1px solid #3b82f6",
      "border-radius: 8px",
      "background: #3b82f6",
      "color: #ffffff",
      "cursor: pointer",
      "font: inherit",
    ].join("; ");
    answerButton.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const userResponse = (input.value || "").trim();
      postOutcomeBeacon("engaged", userResponse || null);
      removeCard();
      refireSend();
    });
    buttonRow.appendChild(answerButton);

    card.appendChild(buttonRow);
    // Autofocus the textarea so the user can start typing immediately.
    setTimeout(() => input.focus(), 0);
    return card;
  }

  // Mount the card between the chat transcript and the composer.
  // ChatGPT wraps the composer in a `<form>`; we insert the card as
  // its immediate previous sibling so it sits visually above the
  // prompt input and the Send button while still being part of the
  // composer region (and therefore moves with the input on layout
  // changes).
  function mountCard(payload) {
    removeCard();
    const button = getSendButton();
    const form = button ? button.closest("form") : null;
    const editor = getComposerEditor();
    const host = form || (editor && editor.closest("form"));
    if (!host || !host.parentNode) return false;
    const card = buildCard(payload);
    host.parentNode.insertBefore(card, host);
    currentCard = card;
    return true;
  }

  async function handleSendAttempt(event) {
    // Our own re-fired event has the passthrough marker — let it
    // through unmolested.
    if (event && event[PKE_PASSTHROUGH]) return;
    // If a card is already up, the user is mid-decision; swallow the
    // event so they don't double-submit, but otherwise do nothing.
    if (currentCard) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (interventionInFlight) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    const draftText = readDraftText();
    if (!draftText) return; // empty composer — let Send no-op naturally
    event.preventDefault();
    event.stopPropagation();
    interventionInFlight = true;
    try {
      const result = await checkIntervention(draftText);
      if (result && result.intervene && result.payload) {
        const mounted = mountCard(result.payload);
        if (mounted) return; // card now owns the re-fire decision
      }
      // Fail-open path: no intervention, mount failed, or server said no.
      refireSend();
    } finally {
      interventionInFlight = false;
    }
  }

  function onSendClickCapture(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const button = target.closest('button[data-testid="send-button"]');
    if (!button) return;
    void handleSendAttempt(event);
  }

  function onComposerKeydownCapture(event) {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    // Only intercept keydown inside the composer's contenteditable.
    if (!target.closest('form [contenteditable="true"]')) return;
    void handleSendAttempt(event);
  }

  document.addEventListener("click", onSendClickCapture, /* capture */ true);
  document.addEventListener("keydown", onComposerKeydownCapture, /* capture */ true);

  // Expose a tiny surface for manual smoke-testing from the page
  // console. Intentionally undocumented; safe because the daemon is
  // local-only.
  window.__PKE_PRE_SEND__ = {
    triggerCheck: () => handleSendAttempt(new MouseEvent("click")),
    readDraft: readDraftText,
  };
})();
