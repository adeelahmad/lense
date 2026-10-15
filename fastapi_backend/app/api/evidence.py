"""Evidence PDFs as downloads (evidence.py), for a recording or a collection."""

from __future__ import annotations

import urllib.error
from typing import Any

from fastapi import HTTPException, Query
from fastapi.responses import Response

from app.api.streaming import LOCKED
from app.domain import anytopdf, evidence, evidence_report, keyring
from app.domain.store import DB
from app.schemas.recordings import EvidencePart

PDF: dict[int | str, dict[str, Any]] = {200: {"content": {"application/pdf": {}}}}
INCLUDE = Query(
    None,
    description="the parts of the PDF; repeat for several. Without it: every part but notes. "
    + "; ".join(f"{k}: {v}" for k, v in evidence_report.PARTS.items()),
)


def download(
    db: DB, cfg: dict[str, Any], rids: list[int], title: str, include: list[EvidencePart] | None = None, places: set[int] | None = None
) -> Response:
    """The evidence PDF of these resources as a download. `places`: the ones whose places the reader may see."""
    try:
        data = evidence.make(db, cfg, rids, include, places)
    except keyring.Locked:
        raise HTTPException(423, LOCKED) from None
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None
    except KeyError:
        raise HTTPException(404, "not found") from None
    except ValueError as e:  # nothing in it, too much, or anytopdf couldn't read a file
        raise HTTPException(400, str(e)) from None
    except (evidence.Unavailable, anytopdf.Unavailable) as e:
        raise HTTPException(503, str(e)) from None
    except (urllib.error.URLError, OSError, RuntimeError) as e:  # anytopdf couldn't be fetched
        raise HTTPException(503, f"anytopdf couldn't be fetched ({getattr(e, 'reason', e)})") from None
    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{evidence.file_name(title)}"', "Cache-Control": "private, no-store"},
    )
