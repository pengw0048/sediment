"""Small HTTP client for third-party adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(kw_only=True, slots=True)
class AdapterClient:
    """Post normalized evidence to a local Sediment server."""

    base_url: str = "http://127.0.0.1:7421"

    async def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one payload to the local evidence endpoint."""
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(f"{self.base_url}/api/v1/evidence", json=payload)
            response.raise_for_status()
            return dict(response.json())
