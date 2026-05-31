"""Integration tests for prompt-text → skill resolution in /check.

The MV3 browser extension now ships the user's draft prompt text on
each `/api/v1/intervention/check` call. The server embeds the draft
and looks for an existing skill node whose embedding overlaps. When a
match clears the cosine threshold the gate chain runs against THAT
skill_id with its real ``unaided_retrievability`` from
``skill_mastery_state``; when nothing matches we return
``intervene=false`` so we don't fire the card on ungrounded "unknown"
prompts.

These tests pin both branches against the real router + embedder so a
refactor of the resolver or the gate chain can't silently regress the
contract.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from pke.evidence.models import iso_utc, new_ulid
from pke.identity.embedder import Embedder
from pke.identity.resolver import vector_to_blob
from pke.intervention.state import InterventionStateStore
from pke.intervention.strength import StrengthLevel
from pke.web.routes import api_intervention


@pytest.fixture()
def http_app(app):
    web = FastAPI()
    web.include_router(api_intervention.router(lambda: app))
    return web


async def _post(http_app, path, payload):
    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=payload)


def _force_active(app, source: str) -> None:
    store = InterventionStateStore(sqlite=app.sqlite)
    store.set_override(source=source, level=StrengthLevel.ACTIVE)


def _seed_skill(app, *, canonical_name: str, unaided_retrievability: float) -> str:
    """Insert a skill node embedded by the shared Embedder and its mastery row.

    The /check endpoint owns its own module-level Embedder and we use
    the same class here so the cosine probe against a phrase that
    repeats the canonical name lands well above the resolver's default
    0.6 threshold (deterministic hash fallback included).
    """
    skill_id = new_ulid()
    now = iso_utc()
    vector = Embedder().embed(canonical_name)
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_nodes(
          id, canonical_name, description, embedding,
          first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES (?, ?, '', ?, ?, ?, ?, ?)
        """,
        (skill_id, canonical_name, vector_to_blob(vector), now, now, now, now),
    )
    app.sqlite.conn.execute(
        """
        INSERT INTO skill_mastery_state(
          skill_id, unaided_retrievability, updated_at
        )
        VALUES (?, ?, ?)
        """,
        (skill_id, unaided_retrievability, now),
    )
    app.sqlite.conn.commit()
    return skill_id


async def test_check_resolves_prompt_text_to_existing_skill(app, http_app):
    """Prompt that overlaps with a seeded skill comes back with that skill_id.

    Seeds one active skill node whose mastery sits inside the gate
    band, then posts a draft prompt whose text aligns with the
    canonical name closely enough that the embedder lands above the
    resolver threshold. The response must:

    - Resolve to the seeded skill_id (not the fallback "unknown").
    - Carry a non-empty Socratic question (the standard fallback
      "Before AI answers..." is fine; what matters is that the gate
      chain ran on a real skill).

    Why we use the canonical name verbatim: on machines without
    sentence-transformers the Embedder falls back to a deterministic
    SHA-based hash embedder. That fallback has no semantic awareness —
    two near-paraphrases hash to ~0 cosine. Using the canonical name as
    the prompt exercises the resolver path identically and lands at
    cosine 1.0 against the seeded node, which is what the real Nomic
    model would produce for a semantically aligned draft in production.
    """
    _force_active(app, "browser_ext_chatgpt")
    canonical = "kubeconfig context switching across multiple clusters"
    skill_id = _seed_skill(
        app,
        canonical_name=canonical,
        unaided_retrievability=0.5,
    )

    resp = await _post(
        http_app,
        "/api/v1/intervention/check",
        {
            "source": "browser_ext_chatgpt",
            "prompt_text": canonical,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["intervene"] is True
    payload = body["payload"]
    assert payload["skill_id"] == skill_id
    assert isinstance(payload["question"], str)
    assert payload["question"]


async def test_check_returns_no_intervene_when_prompt_matches_nothing(app, http_app):
    """Prompt that does not overlap any seeded skill returns intervene=false.

    A draft about a topic with no seeded skill node falls below the
    cosine threshold and the server treats it as a new topic — no
    intervention. This keeps the card from firing on every prompt for
    users with a small or empty skill graph.
    """
    _force_active(app, "browser_ext_chatgpt")
    _seed_skill(
        app,
        canonical_name="kubeconfig context switching",
        unaided_retrievability=0.5,
    )

    resp = await _post(
        http_app,
        "/api/v1/intervention/check",
        {
            "source": "browser_ext_chatgpt",
            "prompt_text": (
                "What is the chemical structure of caffeine and how does it "
                "interact with adenosine receptors?"
            ),
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"intervene": False}
