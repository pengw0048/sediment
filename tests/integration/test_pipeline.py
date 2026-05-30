"""Layer integration pipeline tests."""

from pke.adapters.manual_cli import build_manual_event
from pke.extraction.runner import ExtractionRunner
from pke.identity.ann_index import AnnIndex
from pke.identity.embedder import Embedder
from pke.identity.resolver import IdentityResolver
from pke.mastery.selector import ItemSelector
from pke.testing import MockLLMClient


async def test_adapter_to_review_candidate_pipeline(app):
    for idx in range(3):
        app.evidence.add(build_manual_event(user=f"How do I debug FastAPI route {idx}?"))
    await ExtractionRunner(sqlite=app.sqlite, client=MockLLMClient()).extract_pending()
    IdentityResolver(sqlite=app.sqlite, embedder=Embedder(), ann=AnnIndex()).resolve_pending()
    selected = ItemSelector(sqlite=app.sqlite).select(limit=5)
    assert selected
