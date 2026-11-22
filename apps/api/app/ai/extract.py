"""Text extraction from uploaded files.

Returns normalized text plus a page map so citations can point at a page and a
character range. Unsupported inputs fail loudly with a reason the user can act
on — a scanned PDF is not a mystery to be hidden behind a spinner.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field


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


def extract_pdf(data: bytes) -> ExtractionResult:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # malformed file
        raise ExtractionError(f"Could not read PDF: {exc}") from exc

    parts: list[str] = []
    page_map: list[tuple[int, int, int]] = []
    offset = 0
    empty_pages = 0

    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if not page_text.strip():
            empty_pages += 1
        segment = page_text.strip()
        if segment:
            start = offset
            parts.append(segment)
            offset += len(segment) + 2  # "\n\n" separator
            page_map.append((start, offset, index))

    text = "\n\n".join(parts)
    if not text.strip():
        raise ExtractionError(
            "This PDF has no text layer (it looks like a scan). OCR is not supported yet.",
            code="no_text_layer",
        )

    warnings = []
    if empty_pages:
        warnings.append(f"{empty_pages} page(s) contained no extractable text")

    return ExtractionResult(
        text=text,
        page_count=len(reader.pages),
        page_map=page_map,
        method="pypdf",
        warnings=warnings,
    )


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


def extract_text(data: bytes) -> ExtractionResult:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if not text.strip():
        raise ExtractionError("This file contains no text.", code="empty_document")
    return ExtractionResult(text=text, method="plain")


def extract(data: bytes, mime_type: str, filename: str) -> ExtractionResult:
    lower = filename.lower()
    if mime_type == "application/pdf" or lower.endswith(".pdf"):
        return extract_pdf(data)
    if "wordprocessingml" in mime_type or lower.endswith(".docx"):
        return extract_docx(data)
    if mime_type.startswith("text/") or lower.endswith((".txt", ".md", ".markdown")):
        return extract_text(data)
    raise ExtractionError(
        f"Unsupported file type: {mime_type or filename}", code="unsupported_file_type"
    )
