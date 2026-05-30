"""Batch extraction runner that persists skill candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pke.db.sqlite import SQLiteStore
from pke.evidence.models import iso_utc, new_ulid
from pke.extraction.llm_client import LLMClient, LocalClient
from pke.extraction.prompts import render as render_prompt
from pke.extraction.schema import POLARITY_TO_EVIDENCE_KIND, ExtractedSkill, ExtractedSpan, Polarity

# The extraction system prompt is static, large, and reused on every LLM call
# in this layer. Render it once at import so the same string is sent every time
# and Anthropic's prompt cache stays warm.
SYSTEM_PROMPT = render_prompt("extract_skills.system.j2")


def parse_extracted_skills(payload: dict[str, object]) -> list[ExtractedSkill]:
    """Parse and validate LLM extraction payload."""
    raw_skills = payload.get("skills", [])
    if not isinstance(raw_skills, list):
        raise ValueError("skills must be a list")
    parsed: list[ExtractedSkill] = []
    for item in raw_skills:
        if not isinstance(item, dict):
            continue
        polarity = Polarity(str(item.get("polarity", "asked-about")))
        raw_name = str(item.get("name") or item.get("raw_name") or "").strip()
        if not raw_name:
            continue
        parsed.append(
            ExtractedSkill(
                raw_name=raw_name,
                normalized_name=" ".join(raw_name.lower().split()),
                description=str(item.get("description") or ""),
                polarity=polarity,
                confidence=float(item.get("confidence", 0.0)),
                span=ExtractedSpan(
                    start=int(item["span_start"]) if item.get("span_start") is not None else None,
                    end=int(item["span_end"]) if item.get("span_end") is not None else None,
                ),
            )
        )
    return parsed


@dataclass(kw_only=True, slots=True)
class ExtractionRunner:
    """Run extraction and persist candidates."""

    sqlite: SQLiteStore
    client: LLMClient = field(default_factory=LocalClient)

    async def extract_text(self, text: str) -> list[ExtractedSkill]:
        """Extract skills from raw text."""
        from pke.extraction.llm_client import call_kind

        user_prompt = render_prompt("extract_skills.user.j2", evidence_text=text)
        with call_kind("extract"):
            payload = await self.client.complete_json(system=SYSTEM_PROMPT, user=user_prompt)
        return parse_extracted_skills(payload)

    async def extract_pending(self, *, limit: int = 100) -> int:
        """Extract pending evidence rows into skill_candidates."""
        rows = self.sqlite.conn.execute(
            """
            SELECT id, content FROM evidence_events
            WHERE extraction_state = 'pending'
            ORDER BY occurred_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        count = 0
        for row in rows:
            try:
                skills = await self.extract_text(str(row["content"]))
                self.persist_candidates(str(row["id"]), skills)
                self.sqlite.conn.execute(
                    "UPDATE evidence_events SET extraction_state = 'done' WHERE id = ?",
                    (row["id"],),
                )
            except Exception as exc:
                self.sqlite.conn.execute(
                    """
                    UPDATE evidence_events
                    SET extraction_state = 'error', extraction_error = ?
                    WHERE id = ?
                    """,
                    (str(exc), row["id"]),
                )
            count += 1
        self.sqlite.conn.commit()
        return count

    def persist_candidates(self, evidence_id: str, skills: list[ExtractedSkill]) -> None:
        """Persist parsed skill candidates for one evidence row."""
        now = iso_utc()
        for skill in skills:
            self.sqlite.conn.execute(
                """
                INSERT INTO skill_candidates (
                  id, evidence_id, raw_name, normalized_name, description, span_start, span_end,
                  evidence_kind, confidence, embedding, resolved_skill_id, resolution_state, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'pending', ?)
                """,
                (
                    new_ulid(),
                    evidence_id,
                    skill.raw_name,
                    skill.normalized_name,
                    skill.description,
                    skill.span.start,
                    skill.span.end,
                    POLARITY_TO_EVIDENCE_KIND[skill.polarity],
                    skill.confidence,
                    now,
                ),
            )
        self.sqlite.conn.commit()


def snapshot_payload(skills: list[ExtractedSkill]) -> str:
    """Stable JSON used by snapshot tests."""
    return json.dumps(
        [
            {
                "raw_name": skill.raw_name,
                "normalized_name": skill.normalized_name,
                "polarity": skill.polarity.value,
                "confidence": skill.confidence,
            }
            for skill in skills
        ],
        sort_keys=True,
        indent=2,
    )
