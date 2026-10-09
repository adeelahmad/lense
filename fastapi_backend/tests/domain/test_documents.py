"""Reading documents: pdftotext's blocks, OCR lines put into paragraphs, plain text in blocks, blocks as segments, and
summaries of documents citing pages."""

from __future__ import annotations

from app.domain import analyze, documents

BBOX = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><doc>
<page width="600" height="800"><flow>
  <block xMin="60" yMin="80" xMax="300" yMax="120">
    <line xMin="60" yMin="80" xMax="300" yMax="96"><word>The</word><word>harbour</word><word>re-</word></line>
    <line xMin="60" yMin="100" xMax="200" yMax="120"><word>port</word><word>arrived.</word></line>
  </block>
  <block xMin="60" yMin="200" xMax="120" yMax="210"><line><word> </word></line></block>
</flow></page>
<page width="600" height="800"></page>
</doc></body></html>"""


def test_blocks_of_a_pdf_page_with_where_they_are():
    pages = documents.bbox_pages(BBOX)
    assert pages == [[{"text": "The harbour report arrived.", "box": [0.1, 0.1, 0.4, 0.05]}], []]


def test_ocr_lines_become_paragraphs():
    lines = [
        {"text": "Second paragraph", "conf": 80, "box": [0.1, 0.5, 0.5, 0.03]},
        {"text": "The keeper of the light-", "conf": 90, "box": [0.1, 0.1, 0.6, 0.03]},
        {"text": "house wrote twice.", "conf": 70, "box": [0.1, 0.135, 0.4, 0.03]},
        {"text": "A note in the margin", "conf": 60, "box": [0.8, 0.12, 0.15, 0.03]},
    ]
    paras = documents.merge_lines(lines)
    assert [p["text"] for p in paras] == ["The keeper of the light-\nhouse wrote twice.", "A note in the margin", "Second paragraph"]
    assert paras[0]["conf"] == 80 and paras[0]["box"] == [0.1, 0.1, 0.6, 0.065]

    class Engine:  # an engine with lines only: they're put together, and noise is left out
        name = "lines"

        def lines(self, path):
            return lines + [
                {"text": "~~ ||", "conf": 90, "box": [0.1, 0.9, 0.1, 0.02]},
                {"text": "faint", "conf": 20, "box": [0, 0.95, 0.1, 0.02]},
            ]

    blocks = documents.ocr_blocks(Engine(), "page.png")
    assert [b["text"] for b in blocks] == ["The keeper of the lighthouse wrote twice.", "A note in the margin", "Second paragraph"]


def test_plain_text_in_blocks_and_blocks_as_segments():
    text = "First paragraph\nstill the first.\n\n" + "\n".join(f"line {i} of a long one" for i in range(60))
    blocks = documents.plain_blocks(text, most=200)
    assert blocks[0] == {"text": "First paragraph still the first.", "box": None}
    assert len(blocks) > 3 and all(len(b["text"]) <= 200 for b in blocks)
    segs = documents.segments_of({1: [{"text": "two words", "box": [0, 0, 1, 1]}], 0: [{"text": "one", "box": None}]})
    assert segs == [
        {"t0": 0, "t1": 385, "text": "one", "page": 0},
        {"t0": 385, "t1": 1155, "text": "two words", "page": 1, "box": [0, 0, 1, 1]},
    ]
    assert (documents.kind_of("Scan.TIFF"), documents.kind_of("a.pdf"), documents.kind_of("a.wav")) == ("image", "document", None)
    assert documents.content_type("x.webp") == "image/webp" and documents.content_type("x.xyz") is None
    assert documents.content_type("notes.TXT") == "text/plain" and documents.kind_of("mail.eml") == "document"


def test_summaries_of_documents_cite_pages():
    starts = {"p. 1": 0, "p. 3": 4200, "0:12": 12000}
    assert analyze._cited("p. 3", starts, 9000) == 4200
    assert analyze._cited("[p. 3]", starts, 9000) == 4200
    assert analyze._cited("page 1", starts, 9000) == 0
    assert analyze._cited("p. 9", starts, 9000) is None  # no such page
    assert analyze._cited("0:12", starts, 99000) == 12000  # times as before
    assert analyze._cited("", starts, 9000) is None


def test_words_with_their_boxes_become_lines_and_paragraphs():
    def w(text, x, y, width=0.1, height=0.02):
        return {"text": text, "box": [x, y, width, height]}

    words = [w("The", 0.1, 0.1), w("keeper", 0.25, 0.1), w("wrote", 0.1, 0.125), w("twice.", 0.25, 0.126), w("Later", 0.1, 0.4)]
    assert [ln["text"] for ln in documents.word_lines(words)] == ["The keeper", "wrote twice.", "Later"]
    assert documents.word_blocks(words) == [
        {"text": "The keeper wrote twice.", "box": [0.1, 0.1, 0.25, 0.046]},
        {"text": "Later", "box": [0.1, 0.4, 0.1, 0.02]},
    ]
    # a column beside: back to the left, so a new line, then its own paragraph
    side = [w("Left", 0.1, 0.1), w("Right", 0.6, 0.1), w("again", 0.1, 0.1)]
    assert [ln["text"] for ln in documents.word_lines(side)] == ["Left Right", "again"]


def test_the_words_anytopdf_embeds_in_its_pdf(tmp_path):
    from app.domain import anytopdf
    from tests.fake_anytopdf import with_chunks
    from tests.helpers import text_pdf

    word = {"text": "Harbour", "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.05}
    chunks = [
        {"kind": "visual", "pages": {"first": 1, "last": 1}, "providers": ["pdftotext"], "words": [word, {**word, "text": " "}]},
        {"kind": "visual", "pages": {"first": 2, "last": 2}, "providers": ["tesseract"], "words": [word]},
        {"kind": "visual", "pages": {"first": 3, "last": 3}, "providers": ["tesseract"], "words": [word]},
        {"kind": "text", "pages": {"first": 3, "last": 3}, "text": "an email's own text, without words"},
        {"kind": "visual", "pages": {"first": 4, "last": 4}, "providers": ["tesseract"]},
    ]
    pdf = tmp_path / "made.pdf"
    pdf.write_bytes(with_chunks(text_pdf([["a"], ["b"], ["c"], ["d"]]), chunks))
    got = anytopdf.page_words(pdf)
    box = {"text": "Harbour", "box": [0.1, 0.2, 0.3, 0.05]}
    assert got == {0: {"words": [box], "ocr": False}, 1: {"words": [box], "ocr": True}}

    plain = tmp_path / "plain.pdf"
    plain.write_bytes(text_pdf([["a"]]))
    assert anytopdf.page_words(plain) == {} and anytopdf.page_words(tmp_path / "missing.pdf") == {}
