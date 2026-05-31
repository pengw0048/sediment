// Sediment / PKE — conversation turn capture for ChatGPT, Claude.ai,
// and Gemini, plus a pre-Send Socratic card on all three sites.
//
// This script runs in the MAIN world so it can read the page's DOM and
// observe React/Angular-rendered conversation turns. It detects each
// completed user-turn and assistant-turn via MutationObserver, pairs
// the most recent user turn with the next completed assistant turn,
// and posts the pair to the ISOLATED-world bridge
// (`content-isolated.js`), which forwards it to `background.js` for
// the cross-origin POST to the local Sediment daemon at
// http://127.0.0.1:7421/api/v1/evidence.
//
// On every supported site we also install a capturing-phase Send
// interceptor: when the user clicks the send button or presses Enter
// in the composer, we synchronously POST the draft to
// `/api/v1/intervention/check`. The server embeds the draft, looks for
// an overlap with an existing skill node, and (if found) runs the gate
// chain against that skill's real unaided_mastery. On a positive
// response we render an inline "AI 回答之前" Socratic card between the
// composer and Send button; otherwise (and on any error or > 1 s
// timeout) we transparently re-fire the original Send so the user is
// never blocked on a local network failure.
//
// Site detection: we pick exactly one site profile by matching
// `location.href` against the entries in `SITE_PROFILES`. Each profile
// owns its selectors, streaming attribute, and conversation-id parser.
// All site-specific DOM contracts live in this one constant, so DOM
// rot is a single-file fix.
//
// Site-specific notes:
//
// - ChatGPT (`chatgpt.com`, `chat.openai.com`): the only fully stable
//   contract — every turn carries `[data-message-author-role]` and
//   `[data-message-id]`, and assistant streaming is signalled by
//   `data-message-streaming`. Also the only site that arms the
//   pre-Send Socratic card.
//
// - Claude.ai: no public, durable selector contract. Anthropic ships
//   Tailwind-generated class names that churn weekly; `data-testid`
//   exists on some turns but is inconsistent. We use a small bag of
//   `data-testid` selectors with fallbacks, and rely on the stability
//   timer for completion detection because there is no
//   streaming attribute we can trust. Expect this to break and need
//   updates as the DOM evolves.
//
// - Gemini (`gemini.google.com`): Angular custom elements
//   (`<user-query>`, `<model-response>`) inside heavy shadow DOM.
//   `querySelectorAll` from `document` finds the host elements, and
//   `.innerText` on the host pierces open shadow roots, which is
//   enough for evidence capture. There is no streaming attribute, so
//   we use a longer stability timer (Gemini streams slower than
//   ChatGPT). Synthesized message ids = content hash.

(() => {
  if (window.__PKE_OBSERVER_INSTALLED__) return;

  // Cap text length on the page side too; the server clips again at
  // MAX_TURN_BYTES, but trimming here keeps postMessage payloads small.
  const MAX_TURN_CHARS = 64 * 1024;

  // Per-tab fallback conversation id, used when the URL has no
  // recognisable conversation segment yet (fresh-chat landing pages).
  const tabSessionId =
    "tab_" +
    Math.random().toString(36).slice(2, 10) +
    Date.now().toString(36);

  // All site DOM contracts. Keep this as the single source of truth so
  // future selector fixes have one editing point. Order matters only
  // when host patterns overlap (they don't today).
  // Per-site Send selectors and composer hooks. Each profile owns:
  //   sendSelector          — CSS for the Send button (click target)
  //   enterTargetSelector   — CSS that matches the composer element
  //                           receiving Enter-to-send (typically the
  //                           same contenteditable we read the draft
  //                           from)
  //   promptTextExtractor   — () => string returning the current draft
  //                           text from the composer (queried at
  //                           click/Enter time, not cached)
  //   fallbackTurnSelector  — secondary selector pair used purely for
  //                           the "primary saw zero matches for 10 s"
  //                           dead-DOM watchdog (logged to the
  //                           background page, no behavior change)
  const SITE_PROFILES = [
    {
      id: "chatgpt",
      match: /^https:\/\/(chat\.openai\.com|chatgpt\.com)\//,
      source: "browser_ext_chatgpt",
      userSelector: '[data-message-author-role="user"]',
      assistantSelector: '[data-message-author-role="assistant"]',
      streamingAttr: "data-message-streaming",
      messageIdAttr: "data-message-id",
      stableMs: 1500,
      conversationFromPath: (p) => {
        const m = p.match(/\/c\/([0-9a-f-]{8,})/i);
        return m ? m[1] : null;
      },
      sendSelector: 'button[data-testid="send-button"]',
      enterTargetSelector: 'form [contenteditable="true"]',
      promptTextExtractor: () => {
        const editor =
          document.querySelector('form div.ProseMirror[contenteditable="true"]') ||
          document.querySelector('form [contenteditable="true"]');
        if (!editor) return "";
        return (editor.innerText || editor.textContent || "").trim();
      },
      // Generic "any article-shaped turn" — if data-message-author-role
      // disappears we want to know before the user does. Counts toward
      // the dead-DOM warning, never used for capture.
      fallbackTurnSelector: '[role="article"]',
    },
    {
      id: "claude_ai",
      match: /^https:\/\/claude\.ai\//,
      source: "browser_ext_claude_ai",
      // Claude.ai DOM is unstable; list primary + fallbacks. We match
      // against a comma-separated selector list so any one of them is
      // enough. The streaming attribute is best-effort.
      userSelector:
        '[data-testid="user-message"], div[data-testid^="user-message-"]',
      assistantSelector:
        '[data-is-streaming], [data-testid="assistant-message"], div[data-testid^="assistant-message-"]',
      streamingAttr: "data-is-streaming",
      messageIdAttr: "data-message-id",
      stableMs: 2000,
      conversationFromPath: (p) => {
        const m = p.match(/\/chat\/([0-9a-f-]{8,})/i);
        return m ? m[1] : null;
      },
      // Claude.ai's Send button carries `aria-label="Send Message"`;
      // pair it with the form-wrapped contenteditable composer.
      sendSelector:
        'button[aria-label="Send Message"], button[aria-label="Send message"]',
      enterTargetSelector:
        'div[contenteditable="true"][role="textbox"], div.ProseMirror[contenteditable="true"]',
      promptTextExtractor: () => {
        const editor =
          document.querySelector(
            'div[contenteditable="true"][role="textbox"]',
          ) ||
          document.querySelector('div.ProseMirror[contenteditable="true"]') ||
          document.querySelector('[contenteditable="true"]');
        if (!editor) return "";
        return (editor.innerText || editor.textContent || "").trim();
      },
      // Tailwind class names rot weekly; a generic "looks like a
      // message div" fallback gives us a second eye on capture
      // breakage.
      fallbackTurnSelector: 'div[class*="message"]',
    },
    {
      id: "gemini",
      match: /^https:\/\/gemini\.google\.com\//,
      source: "browser_ext_gemini",
      // Gemini renders turns as Angular custom elements. The host
      // elements are reachable from `document`; `.innerText` pierces
      // their open shadow roots, which is enough for evidence capture.
      userSelector: 'user-query, [data-test-id="user-query"]',
      assistantSelector: 'model-response, [data-test-id="model-response"]',
      // Gemini does not expose a streaming attribute we can trust; the
      // stability timer is the only completion signal. Bump it
      // generously — Gemini streams slower than ChatGPT and a tight
      // timer produces false positives mid-stream.
      streamingAttr: null,
      messageIdAttr: null,
      stableMs: 2500,
      conversationFromPath: (p) => {
        const m = p.match(/\/app\/([0-9a-f]{8,})/i);
        return m ? m[1] : null;
      },
      // Gemini's send affordance is a Material icon button keyed by
      // the `send` fonticon. Both Angular custom elements above and
      // the rich-textarea composer are inside open shadow DOM, but the
      // send button itself lives in light DOM and is queryable.
      sendSelector:
        'button[aria-label="Send message"], button:has(mat-icon[fonticon="send"])',
      enterTargetSelector:
        'rich-textarea [contenteditable="true"], div[contenteditable="true"]',
      promptTextExtractor: () => {
        const editor =
          document.querySelector(
            'rich-textarea [contenteditable="true"]',
          ) ||
          document.querySelector('div[contenteditable="true"]');
        if (!editor) return "";
        return (editor.innerText || editor.textContent || "").trim();
      },
      fallbackTurnSelector: 'div[class*="message"]',
    },
  ];

  const PROFILE = SITE_PROFILES.find((p) => p.match.test(location.href));
  if (!PROFILE) return;
  window.__PKE_OBSERVER_INSTALLED__ = true;

  function conversationId() {
    return PROFILE.conversationFromPath(location.pathname) || tabSessionId;
  }

  function extractText(node) {
    // `innerText` collapses whitespace the way the user sees it and
    // also pierces open shadow roots (important for Gemini's Angular
    // custom elements). Fall back to `textContent` for completeness.
    const text = (node.innerText || node.textContent || "").trim();
    return text.length > MAX_TURN_CHARS ? text.slice(0, MAX_TURN_CHARS) : text;
  }

  function postPair(payload) {
    window.postMessage({ __pke__: true, payload }, window.location.origin);
  }

  // Synthesize a stable id for sites that do not stamp one on each
  // turn (Gemini). We use a short non-crypto hash of the text — good
  // enough to dedup within a single tab/session.
  function synthesizeMessageId(text) {
    let hash = 0;
    for (let i = 0; i < text.length; i++) {
      hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
    }
    return "syn_" + (hash >>> 0).toString(36) + "_" + text.length;
  }

  function getMessageId(node, text) {
    if (PROFILE.messageIdAttr) {
      const attr = node.getAttribute(PROFILE.messageIdAttr);
      if (attr) return attr;
    }
    return synthesizeMessageId(text);
  }

  // State: track the most recent user turn keyed by conversation id.
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
    const text = extractText(node);
    if (!text) return;
    const messageId = getMessageId(node, text);
    if (!messageId) return;
    const convId = conversationId();
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
    const assistantText = extractText(node);
    if (!assistantText) return;
    const messageId = getMessageId(node, assistantText);
    if (!messageId || emittedAssistantIds.has(messageId)) return;
    const convId = conversationId();
    const user = pendingUser.get(convId);
    // Only emit a pair once we have a preceding user turn; no site we
    // capture opens a conversation with an unsolicited assistant turn,
    // so dropping orphan assistant DOM (re-rendered welcome page,
    // example cards, etc.) is the right behavior.
    if (!user) return;
    emittedAssistantIds.add(messageId);
    pendingUser.delete(convId);
    postPair({
      source: PROFILE.source,
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
    if (!PROFILE.streamingAttr) return false;
    const attr = node.getAttribute(PROFILE.streamingAttr);
    // ChatGPT uses "true"/absent; Claude.ai's `data-is-streaming` is
    // present-when-streaming. Treat both shapes as streaming.
    return attr === "true" || attr === "" || attr === "1";
  }

  function handleAssistantTurn(node) {
    const text = extractText(node);
    if (!text) return;
    const messageId = getMessageId(node, text);
    if (!messageId || emittedAssistantIds.has(messageId)) return;

    // Fast path: the streaming signal says we're done (or there is no
    // streaming signal at all — Gemini).
    if (!isStreaming(node)) {
      // No streaming attr: still wait stableMs for the DOM to settle,
      // otherwise we'd emit a partial mid-stream. With a streaming
      // attr that has cleared, we can emit immediately.
      if (PROFILE.streamingAttr) {
        emitAssistantTurn(node);
        return;
      }
    }
    if (assistantStabilityTimers.has(messageId)) {
      clearTimeout(assistantStabilityTimers.get(messageId));
    }
    assistantStabilityTimers.set(
      messageId,
      setTimeout(() => {
        assistantStabilityTimers.delete(messageId);
        emitAssistantTurn(node);
      }, PROFILE.stableMs),
    );
  }

  // Selector mode flips to "fallback" when the dead-DOM watchdog
  // detects the primary site selectors no longer match. In fallback
  // mode the broader `fallbackTurnSelector` finds candidate nodes and
  // a role heuristic decides whether each one is user or assistant.
  let selectorMode = "primary";

  function probeRoleFromFallback(node) {
    // Direct attribute on the node or any descendant (the ChatGPT
    // pattern: data-message-author-role).
    const roled =
      (node.getAttribute && node.getAttribute("data-message-author-role")) ||
      (node.querySelector &&
        node.querySelector("[data-message-author-role]") &&
        node
          .querySelector("[data-message-author-role]")
          .getAttribute("data-message-author-role"));
    if (roled === "user") return "user";
    if (roled === "assistant") return "assistant";
    // Class-name heuristic: many sites name wrappers with a role
    // token. Conservative match on whole words.
    const className = String((node.className && node.className.toString()) || "");
    if (/\b(user|human|prompt|you)\b/i.test(className)) return "user";
    if (/\b(assistant|model|ai-response|response|answer)\b/i.test(className)) {
      return "assistant";
    }
    return null;
  }

  function inspectInFallback(node) {
    if (!(node instanceof HTMLElement)) return;
    if (!PROFILE.fallbackTurnSelector) return;
    const route = (candidate) => {
      const role = probeRoleFromFallback(candidate);
      if (role === "user") handleUserTurn(candidate);
      else if (role === "assistant") handleAssistantTurn(candidate);
    };
    if (node.matches && node.matches(PROFILE.fallbackTurnSelector)) route(node);
    if (node.querySelectorAll) {
      node.querySelectorAll(PROFILE.fallbackTurnSelector).forEach(route);
    }
  }

  function inspectNode(node) {
    if (!(node instanceof HTMLElement)) return;
    if (selectorMode === "fallback") {
      inspectInFallback(node);
      return;
    }
    // Top-level node may itself be a user or assistant wrapper.
    if (node.matches && node.matches(PROFILE.userSelector)) {
      handleUserTurn(node);
    } else if (node.matches && node.matches(PROFILE.assistantSelector)) {
      handleAssistantTurn(node);
    }
    // Descend: a fresh conversation often lands as a single subtree
    // insertion containing both the user and assistant wrappers.
    if (node.querySelectorAll) {
      node.querySelectorAll(PROFILE.userSelector).forEach(handleUserTurn);
      node.querySelectorAll(PROFILE.assistantSelector).forEach(handleAssistantTurn);
    }
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
          ? target.closest(PROFILE.assistantSelector)
          : null;
        if (wrapper) handleAssistantTurn(wrapper);
        if (
          mutation.type === "attributes"
          && PROFILE.streamingAttr
          && mutation.attributeName === PROFILE.streamingAttr
        ) {
          const attrTarget = mutation.target;
          if (attrTarget instanceof HTMLElement) handleAssistantTurn(attrTarget);
        }
      }
    }
  }

  const observer = new MutationObserver(onMutations);
  // attributeFilter must include only the attribute(s) we actually
  // watch; on Gemini there are none, so we drop attribute observation
  // entirely on that site (cheaper, less observer noise).
  const observerConfig = {
    childList: true,
    subtree: true,
    characterData: true,
  };
  if (PROFILE.streamingAttr) {
    observerConfig.attributes = true;
    observerConfig.attributeFilter = [PROFILE.streamingAttr];
  }
  observer.observe(document.documentElement, observerConfig);

  // Catch-up: handle any turns already on the page when we attach.
  inspectNode(document.documentElement);

  // ---------------------------------------------------------------
  // Dead-DOM watchdog. Sites mutate their data attributes regularly;
  // when the primary selector stops matching but the broader
  // `fallbackTurnSelector` still finds nodes, we switch capture to
  // fallback mode after a short confirmation window (30 s of
  // consecutive misses) and start routing via `probeRoleFromFallback`.
  // The first dead interval logs a warning so the developer can
  // inspect; the activation logs again so it's clear capture has
  // degraded. If the primary selector ever matches again, we swap
  // back automatically.
  if (PROFILE.fallbackTurnSelector) {
    let consecutiveZeroIntervals = 0;
    let warnedThisWindow = false;
    const watchdog = () => {
      const primaryMatches =
        document.querySelectorAll(
          PROFILE.userSelector + ", " + PROFILE.assistantSelector,
        ).length;
      const fallbackMatches = document.querySelectorAll(
        PROFILE.fallbackTurnSelector,
      ).length;
      if (primaryMatches === 0 && fallbackMatches > 0) {
        consecutiveZeroIntervals += 1;
        if (!warnedThisWindow) {
          warnedThisWindow = true;
          window.postMessage(
            {
              __pke_log__: true,
              level: "warn",
              event: "pke_selector_dead",
              site: PROFILE.id,
              primary: PROFILE.userSelector + " | " + PROFILE.assistantSelector,
              fallbackMatches,
              url: location.href,
            },
            window.location.origin,
          );
        }
        if (consecutiveZeroIntervals >= 3 && selectorMode === "primary") {
          selectorMode = "fallback";
          window.postMessage(
            {
              __pke_log__: true,
              level: "warn",
              event: "pke_selector_fallback_activated",
              site: PROFILE.id,
              fallbackMatches,
              url: location.href,
            },
            window.location.origin,
          );
          inspectInFallback(document.documentElement);
        }
      } else if (primaryMatches > 0) {
        consecutiveZeroIntervals = 0;
        warnedThisWindow = false;
        if (selectorMode === "fallback") {
          selectorMode = "primary";
          window.postMessage(
            {
              __pke_log__: true,
              level: "info",
              event: "pke_selector_primary_restored",
              site: PROFILE.id,
              url: location.href,
            },
            window.location.origin,
          );
        }
      }
    };
    setInterval(watchdog, 10000);
  }

  // ---------------------------------------------------------------
  // Pre-Send Socratic intervention (all supported sites).
  //
  // Flow:
  //   1. Capturing-phase listeners on document for `click` on the
  //      site's send button and `keydown` on the composer's
  //      contenteditable detect a Send attempt before the host page's
  //      handler fires.
  //   2. We mark the event so a re-dispatched copy bypasses us, then
  //      `preventDefault` + `stopPropagation` to swallow the original.
  //   3. We synchronously POST the draft text to
  //      `/api/v1/intervention/check`. The server embeds it and tries
  //      to resolve it to a real skill node so the per-skill throttle
  //      and the real unaided_mastery drive the gate chain. Any
  //      non-2xx, network error, or > 1 s timeout falls open: we
  //      re-fire Send immediately so the user is never blocked on
  //      local-daemon trouble.
  //   4. On `{intervene: true}` we render an inline card between the
  //      composer and Send button. "跳过" fires `outcome=dismissed`
  //      then re-fires Send; "回答 (约 10 秒)" fires `outcome=engaged`
  //      with the typed response then re-fires Send.
  if (!PROFILE.sendSelector || !PROFILE.enterTargetSelector) return;

  const SOURCE = PROFILE.source;
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

  function getSendButton() {
    return document.querySelector(PROFILE.sendSelector);
  }

  function readDraftText() {
    const raw = PROFILE.promptTextExtractor ? PROFILE.promptTextExtractor() : "";
    const text = (raw || "").trim();
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
        // Server-side: embed the draft, look for an overlap with an
        // existing skill node, and run the gate chain against THAT
        // skill_id (with its real unaided_mastery from
        // skill_mastery_state). On no match the server returns
        // intervene=false rather than firing the card on an ungrounded
        // "unknown" skill.
        prompt_text: draftText,
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

  // Mount the card just above the composer. We pick the nearest
  // ancestor that anchors the composer in layout (a `<form>` on
  // ChatGPT, the form-like wrapper on Claude.ai, the rich-textarea
  // host on Gemini) and insert the card as its previous sibling so it
  // moves with the composer on resize.
  function mountCard(payload) {
    removeCard();
    const button = getSendButton();
    const editor = document.querySelector(PROFILE.enterTargetSelector);
    const fromButton = button ? button.closest("form") || button.parentElement : null;
    const fromEditor = editor ? editor.closest("form") || editor.parentElement : null;
    const host = fromButton || fromEditor;
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
    const button = target.closest(PROFILE.sendSelector);
    if (!button) return;
    void handleSendAttempt(event);
  }

  function onComposerKeydownCapture(event) {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    // Only intercept keydown inside the composer's contenteditable.
    if (!target.closest(PROFILE.enterTargetSelector)) return;
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
