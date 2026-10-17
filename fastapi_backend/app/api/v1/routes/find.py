"""Find resources by any attribute: faces, voices, entities, topics, kind, content type, dates and the rest (domain/find.py)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Acl, Cfg, CurrentUser, Db, domain_errors
from app.api.media import sign_urls
from app.domain import find as findmod
from app.schemas.find import FindQuery, FindResults

router = APIRouter(tags=["search"])


@router.post("/find")
def find_resources(body: FindQuery, acl: Acl, user: CurrentUser, db: Db, cfg: Cfg) -> FindResults:
    """Assets you can read that match every filter given: the people seen in them (faces) and heard (speakers), the
    entities they mention, the topics they're about, their kind and content type, when they were made, their tags,
    language, objects seen, emotions heard, length, status, collection and origin, and words said or written in them.
    Faces, people heard, entities and topics can be named instead of numbered; `resolved` says what each name matched, and
    a name that matches nothing is a 400 naming the closest ones. Each asset says `why` it matched, with up to three
    moments per reason to cite (a time, or a page). With `text`, the best matching come first."""
    spaces, within = acl.scope(body.namespace)
    spec = body.model_dump(exclude={"namespace", "sort", "limit", "offset"}, exclude_none=True)
    with domain_errors():
        res = findmod.find(db, cfg, spaces, within, spec, sort=body.sort, limit=body.limit, offset=body.offset)
    for x in res["items"]:
        x["role"] = acl.role_in(x["space"], x.get("collection"))
    return FindResults.model_validate(sign_urls(res, full=True))
