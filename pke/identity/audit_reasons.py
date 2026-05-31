"""Reason codes carried in ``pending_audits.payload_json``.

A ``pending_audits`` row's ``audit_type`` column is CHECK-constrained
to a closed set (``split``, ``merge``, ``candidate_review``), so finer
distinctions between why a row landed in the queue live in the
``reason`` field of its JSON payload. This module is the single import
point for those reason strings — query / count code, audit-surface UI,
and the resolver itself all import from here so a string typo cannot
silently divorce a writer from its reader.
"""

from __future__ import annotations

from enum import StrEnum


class AuditReason(StrEnum):
    """Reason codes that may appear in ``pending_audits.payload_json``."""

    # Daily leaky-bucket cap on new-skill creation was reached; the
    # candidate would have become a brand-new skill but was demoted to
    # ``pending`` instead. See ``IdentityResolver.daily_new_skill_cap``.
    LEAKY_BUCKET_BLOCK = "leaky_bucket_block"
