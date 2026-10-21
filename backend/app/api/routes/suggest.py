"""Autocomplete endpoint: local prefix suggestions, never a provider call.

The dropdown must not turn every keystroke into a third-party request, so this
route queries the local Meilisearch index only (title and domain) and never
touches the external shield. The query is not logged or stored, like the
search endpoint.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.models import SuggestResponse, SuggestionItem
from app.services.search import SearchService
from app.utils.query import parse_query

router = APIRouter(tags=["search"])

MAX_SUGGESTIONS = 6


@router.get(
    "/suggest",
    response_model=SuggestResponse,
    summary="Local suggestions",
    description=(
        "Prefix search on the titles and domains of the local index, at most "
        "six entries. It never calls an external provider, so the in-progress "
        "query stays on the server. Operators are parsed the same way as in "
        "/search; a query without searchable text returns no suggestions."
    ),
)
async def suggest(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=80, description="Partial query")],
) -> SuggestResponse:
    parsed = parse_query(" ".join(q.split()))
    if not parsed.text:
        return SuggestResponse(query="", suggestions=[])

    service: SearchService = request.app.state.search_service
    hits = await service.suggest(parsed.text, limit=MAX_SUGGESTIONS)

    suggestions: list[SuggestionItem] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.url in seen or not hit.title:
            continue
        seen.add(hit.url)
        suggestions.append(
            SuggestionItem(title=hit.title, url=hit.url, domain=hit.domain)
        )

    return SuggestResponse(query=parsed.text, suggestions=suggestions)
