"""Text extraction from uploaded files.

Design §4.1 names one extractor per format:

    PDF      → PyMuPDF
    DOCX     → python-docx
    TXT      → Python file handling
    Markdown → Markdown parser
    HTML     → BeautifulSoup

Each returns normalized text plus a page map so a citation can point at a page and
a character range. Unsupported inputs fail loudly with a reason the user can act
on — a scanned PDF is not a mystery to be hidden behind a spinner.

Everything here is pure: bytes in, text out, no network and no database. That is
what makes the extraction stage testable without a provider or a container.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field

from app.ai.ingestion.chunker import normalize_text


@dataclass
class ExtractionResult:
    text: str
    page_count: int | None = None
    page_map: list[tuple[int, int, int]] = field(default_factory=list)
    method: str = "unknown"
    warnings: list[str] = field(default_factory=list)


class ExtractionError(Exception):
    def __init__(self, message: str, *, code: str = "unprocessable_document") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# PDF — PyMuPDF
# ---------------------------------------------------------------------------


def extract_pdf(data: bytes) -> ExtractionResult:
    """Extract per page so page numbers survive into citations.

    PyMuPDF is used rather than pypdf because it has a real text layer detector
    and returns pages in a stable order; a scan yields per-page text under a
    small threshold, which is how `no_text_layer` is decided.
    """
    import pymupdf

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # malformed file
        raise ExtractionError(f"Could not read PDF: {exc}") from exc

    parts: list[str] = []
    page_map: list[tuple[int, int, int]] = []
    offset = 0
    empty_pages = 0

    try:
        page_count = document.page_count
        for index in range(page_count):
            try:
                page_text = document.load_page(index).get_text("text")
            except Exception:
                page_text = ""

            segment = normalize_text(page_text)
            if not segment.strip():
                empty_pages += 1
                continue

            start = offset
            parts.append(segment)
            offset += len(segment) + 2  # "\n\n" separator
            page_map.append((start, offset, index + 1))
    finally:
        document.close()

    text = "\n\n".join(parts)
    if not text.strip():
        raise ExtractionError(
            "This PDF has no text layer (it looks like a scan). OCR is not supported yet.",
            code="no_text_layer",
        )

    warnings: list[str] = []
    if empty_pages:
        warnings.append(f"{empty_pages} page(s) contained no extractable text")

    return ExtractionResult(
        text=text,
        page_count=page_count,
        page_map=page_map,
        method="pymupdf",
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# DOCX — python-docx
# ---------------------------------------------------------------------------


def extract_docx(data: bytes) -> ExtractionResult:
    from docx import Document

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"Could not read DOCX: {exc}") from exc

    lines: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = getattr(paragraph.style, "name", None) or ""
        style = style_name.lower()
        if style.startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            lines.append(f"{'#' * min(int(level), 6)} {text}")
        else:
            lines.append(text)

    combined = "\n\n".join(lines)
    if not combined.strip():
        raise ExtractionError("This document contains no text.", code="empty_document")

    return ExtractionResult(text=combined, method="python-docx")


# ---------------------------------------------------------------------------
# Markdown — markdown-it-py
# ---------------------------------------------------------------------------


def extract_markdown(data: bytes) -> ExtractionResult:
    """Flatten Markdown to plain text while preserving its structure.

    A Markdown parser is used rather than "it's already text" because the
    document's *structure* is what makes citations useful: headings become the
    chunker's heading path, and list/code content must not be glued into one
    paragraph. Rendering to HTML and flattening is the reliable path — a
    hand-rolled regex stripper mangles tables, code fences and nested lists.
    """
    from markdown_it import MarkdownIt

    text = _decode(data)
    if not text.strip():
        raise ExtractionError("This file contains no text.", code="empty_document")

    try:
        html = MarkdownIt("commonmark", {"html": False}).enable("table").render(text)
    except Exception as exc:
        raise ExtractionError(f"Could not parse Markdown: {exc}") from exc

    return ExtractionResult(text=_html_to_text(html), method="markdown-it")


# ---------------------------------------------------------------------------
# HTML — BeautifulSoup
# ---------------------------------------------------------------------------


# Elements whose text is navigation, not knowledge. Dropping them is the
# difference between a readable document and a nav menu quoted as evidence.
_BOILERPLATE = ("script", "style", "noscript", "nav", "footer", "header", "form", "aside")
_BLOCK = ("p", "div", "section", "article", "li", "blockquote", "pre", "table", "tr")
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def extract_html(data: bytes) -> ExtractionResult:
    text = _decode(data)
    if not text.strip():
        raise ExtractionError("This file contains no text.", code="empty_document")

    return ExtractionResult(text=_html_to_text(text), method="beautifulsoup4")


def _html_to_text(html: str) -> str:
    """Flatten HTML to block-separated plain text with `#` heading markers.

    Headings are emitted as Markdown-style `#` lines so the existing chunker can
    derive a heading path without a second, parallel notion of structure. One
    structure, one parser.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_BOILERPLATE)):
        tag.decompose()

    lines: list[str] = []
    for element in soup.find_all(list(_HEADINGS) + list(_BLOCK)):
        # Only leaf-ish blocks: a <div> wrapping <p>s would duplicate its text.
        if element.find(list(_BLOCK) + list(_HEADINGS)):
            continue
        content = " ".join(element.get_text(" ", strip=True).split())
        if not content:
            continue
        level = _HEADINGS.get(element.name)
        lines.append(f"{'#' * level} {content}" if level else content)

    if not lines:
        # A document that is a single bare text node (no block elements).
        fallback = " ".join(soup.get_text(" ", strip=True).split())
        if not fallback:
            raise ExtractionError("This file contains no text.", code="empty_document")
        return fallback

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------------


def extract_text(data: bytes) -> ExtractionResult:
    text = _decode(data)
    if not text.strip():
        raise ExtractionError("This file contains no text.", code="empty_document")
    return ExtractionResult(text=text, method="plain")


def _decode(data: bytes) -> str:
    """UTF-8 with a latin-1 fallback; never silently drop bytes."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_MIME_EXTRACTORS: dict[str, Callable[[bytes], ExtractionResult]] = {
    "application/pdf": extract_pdf,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": extract_docx,
    "application/msword": extract_docx,
    "text/markdown": extract_markdown,
    "text/x-markdown": extract_markdown,
    "text/html": extract_html,
    "application/xhtml+xml": extract_html,
    "text/plain": extract_text,
}

# Extension is a fallback only: browsers send `text/plain` for unknown types, so
# an HTML file opened from disk often arrives mislabelled.
_EXT_EXTRACTORS: dict[str, Callable[[bytes], ExtractionResult]] = {
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".md": extract_markdown,
    ".markdown": extract_markdown,
    ".html": extract_html,
    ".htm": extract_html,
    ".txt": extract_text,
}


def extractor_for(mime_type: str, filename: str) -> Callable[[bytes], ExtractionResult] | None:
    lower = filename.lower()
    for extension, extractor in _EXT_EXTRACTORS.items():
        if lower.endswith(extension):
            return extractor
    base = (mime_type or "").split(";")[0].strip().lower()
    return _MIME_EXTRACTORS.get(base)


def extract(data: bytes, mime_type: str, filename: str) -> ExtractionResult:
    if not data:
        raise ExtractionError("The uploaded file is empty.", code="empty_document")

    extractor = extractor_for(mime_type, filename)
    if extractor is None:
        raise ExtractionError(
            f"Unsupported file type: {mime_type or filename}", code="unsupported_file_type"
        )
    return extractor(data)


def looks_like_scan(result: ExtractionResult) -> bool:
    """A PDF that produced almost no text per page is a scan, not a document."""
    if result.page_count is None or result.page_count == 0:
        return False
    return len(result.text) / max(1, result.page_count) < 100
