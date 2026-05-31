// Sediment / PKE — ChatGPT conversation turn capture.
//
// This script runs in the MAIN world so it can read the page's DOM and
// observe React-rendered conversation turns. It detects each completed
// user-turn and assistant-turn via MutationObserver, pairs the most
// recent user turn with the next completed assistant turn, and posts
// the pair to the ISOLATED-world bridge (`content-isolated.js`), which
// forwards it to `background.js` for the cross-origin POST to the
// local Sediment daemon at http://127.0.0.1:7421/api/v1/evidence.
//
// The script is intentionally read-only: it does not intercept the
// composer Send button. Adding a pre-AI Socratic intervention would
// attach here — see the "PRE_SEND_INTERVENTION" TODO below — and
// requires a server-side "draft prompt -> skill_id guess" endpoint
// that does not exist yet.
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

  // PRE_SEND_INTERVENTION TODO: this is where a Socratic-card prompt
  // would attach. The flow would be:
  //   1. Add a capturing-phase `keydown` listener for Enter on the
  //      composer textarea, plus a capturing `click` on
  //      `button[data-testid="send-button"]`, both with
  //      `event.preventDefault()` while a check is in flight.
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
  // See ../docs/* for the design once the helper endpoint lands.
})();
