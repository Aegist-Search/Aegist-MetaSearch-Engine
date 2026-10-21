"""Preview image endpoint.

The image is downloaded and cached by the backend, so rendering a result
preview never makes the browser contact the origin site.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.services.preview import PreviewService

router = APIRouter(tags=["preview"])


@router.get(
    "/preview",
    summary="Preview image for a search result",
    description=(
        "Downloads the preview image (og:image) of a result on the server side "
        "and caches it in memory. Only public http(s) hosts are accepted, "
        "redirects are re-validated, the body is capped at 4 MB and only raster "
        "image types are served."
    ),
    responses={404: {"description": "No usable preview image"}},
)
async def preview(
    request: Request,
    url: Annotated[
        str,
        Query(min_length=8, max_length=2048, description="Absolute http(s) image URL"),
    ],
) -> Response:
    service: PreviewService | None = getattr(request.app.state, "preview", None)
    if service is None:
        raise HTTPException(status_code=404, detail="Preview is not available.")

    image = await service.fetch(url)
    if image is None:
        raise HTTPException(status_code=404, detail="Preview is not available.")

    return Response(
        content=image.content,
        media_type=image.content_type,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
