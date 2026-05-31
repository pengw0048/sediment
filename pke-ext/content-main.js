// Sediment / PKE — conversation turn capture for ChatGPT, Claude.ai, and Gemini.
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
// The script is intentionally read-only: it does not intercept the
// composer Send button. Adding a pre-AI Socratic intervention would
// attach here — see the "PRE_SEND_INTERVENTION" TODO below — and
// requires a server-side "draft prompt -> skill_id guess" endpoint
// that does not exist yet.
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
//   `data-message-streaming`.
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

  function inspectNode(node) {
    if (!(node instanceof HTMLElement)) return;
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

  // PRE_SEND_INTERVENTION TODO: this is where a Socratic-card prompt
  // would attach. The flow would be:
  //   1. Add a capturing-phase `keydown` listener for Enter on the
  //      composer textarea, plus a capturing `click` on the site's
  //      send button, both with `event.preventDefault()` while a
  //      check is in flight.
  //   2. Read the draft prompt out of the composer.
  //   3. POST to /api/v1/intervention/check with a server-derived
  //      skill_id + unaided_mastery. Today the server requires
  //      both fields, and the extension has no path to populate
  //      `unaided_mastery` pre-AI — that endpoint needs a
  //      "draft prompt -> skill_id guess" helper first.
  //   4. If `intervene:true`, render an inline modal; if dismissed
  //      or answered, re-dispatch the original keydown/click after
  //      removing the interceptor. Synthetic React events rarely
  //      work, so the standard pattern is to remove the listener
  //      and re-fire the original event.
  // Out of scope for this multi-site PR — that work lives in the
  // pre-send worktree.
})();
