# OpenAI-Compatible Proxy

```bash
pke proxy openai --port 7422
export OPENAI_BASE_URL=http://127.0.0.1:7422/v1
```

The proxy passively observes requests and responses. It does not rewrite
responses.
