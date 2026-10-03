"""Reading RDF into a namespace (domain/rdf_import.py)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel


class RdfImport(RequestModel):
    data: str = Field(description="the RDF: Turtle, N-Triples or JSON-LD (with its @context inline)")
    format: str | None = Field(None, description="turtle, nt or json-ld; guessed from the data when left out")
    dry_run: bool = Field(True, description="report what would change without changing it")


class RdfImportItem(ResponseModel):
    subject: str
    recording: int
    fields: list[str] = Field(description="the metadata fields it changes (or would)")
    notes: list[str]
    statements: int = Field(description="other statements kept with the recording")


class RdfUnmatched(ResponseModel):
    subject: str
    title: str | None = None


class RdfImportResult(ResponseModel):
    dry_run: bool
    triples: int
    matched: int
    changed: int
    items: list[RdfImportItem]
    unmatched: list[RdfUnmatched]
