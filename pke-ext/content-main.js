(() => {
  const origFetch = window.fetch;
  const patterns = [
    /\/backend-api\/conversation/,
    /\/api\/organizations\/.+\/chat_conversations\/.+\/completion/,
    /BardFrontendService/
  ];

  function shouldCapture(url) {
    return !window.__PKE_DISABLED__ && patterns.some((pattern) => pattern.test(url));
  }

  function postToBridge(payload) {
    window.postMessage({ __pke__: true, payload }, window.location.origin);
  }

  window.fetch = async function patchedFetch(input, init) {
    const url = typeof input === "string" ? input : input.url;
    if (!shouldCapture(url)) return origFetch.apply(this, arguments);
    const reqBody = init && init.body ? String(init.body) : "";
    const t0 = Date.now();
    const resp = await origFetch.apply(this, arguments);
    if (!resp.body) {
      postToBridge({ kind: "non_stream", url, reqBody, status: resp.status, t0 });
      return resp;
    }
    const streams = resp.body.tee();
    const appStream = streams[0];
    const ourStream = streams[1];
    (async () => {
      const reader = ourStream.getReader();
      const decoder = new TextDecoder();
      let body = "";
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          body += decoder.decode(chunk.value, { stream: true });
        }
        postToBridge({ kind: "stream", url, reqBody, status: resp.status, t0, body });
      } catch (err) {
        postToBridge({ kind: "stream_partial", url, reqBody, status: resp.status, t0, body, err: String(err) });
      }
    })();
    return new Response(appStream, { status: resp.status, headers: resp.headers });
  };
})();
