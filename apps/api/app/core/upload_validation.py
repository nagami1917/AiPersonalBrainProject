"""What a file claims to be, versus what it is.

Rule 9 of the security architecture: validate extensions, MIME types, **and
actual file content**. Only the first two were checked, and both come from the
client — `UploadUrlRequest.mime_type` is a string the browser chose. A file
named `report.pdf` declaring `application/pdf` can be anything at all, and the
path it lands on decides which parser reads it:

    .pdf → PyMuPDF        .docx → python-docx        .txt/.md → decode

That matters more than it looks. Handing attacker-chosen bytes to PyMuPDF or
python-docx is handing them to a parser with a C core; the extractor running in
a worker is the only thing standing between a malformed file and the process.
So the content is inspected before the signed URL is issued **and again** in the
worker, where the bytes finally exist.

## What is actually checkable, given the upload flow

The browser uploads straight to Storage and the API never sees the bytes at
request time — so this module has two halves:

  * `validate_declared` — extension, declared MIME, declared size, and the
    agreement between them. Runs in the API request. Cheap, catches the honest
    mistakes and the lazy attack.
  * `sniff` / `validate_content` — magic bytes against the declared type. Runs
    in the worker on the real file, and fails the job (never the process).

The split is a consequence of the architecture, not a compromise: the file is
untrusted at every step, so every step checks the part it can see.

## Why a passthrough type is refused rather than guessed

`.txt` and `.md` have no magic number, so content sniffing cannot confirm them.
Rather than silently accepting, the worker decodes strictly and rejects binary
content — a UTF-8 text type that is not valid UTF-8 is not a text file, whatever
its extension. That check is in `validate_content` and is the honest answer to
"we cannot verify, so we verify what we can".
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})

# Extension → the only MIME types we will accept for it. Both directions are
# enforced below, so a `.pdf` cannot arrive declared as HTML and an HTML file
# cannot arrive named `.pdf`.
EXTENSION_MIME: dict[str, frozenset[str]] = {
    ".pdf": frozenset({PDF}),
    ".docx": frozenset({DOCX}),
    ".txt": frozenset({"text/plain"}),
    ".md": frozenset({"text/markdown", "text/x-markdown"}),
    ".markdown": frozenset({"text/markdown", "text/x-markdown"}),
    ".html": frozenset({"text/html"}),
    ".htm": frozenset({"text/html"}),
    ".xhtml": frozenset({"application/xhtml+xml"}),
}

# The extractor's dispatch table, mirrored. Kept in sync by a test rather than
# by importing the extractor (which would pull in PyMuPDF at request time).
SUPPORTED_MIME: frozenset[str] = frozenset({PDF, DOCX}) | TEXT_TYPES | HTML_TYPES

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc/.xls — refused


class UploadValidationError(ValueError):
    """A rejected upload, with a code the API maps to a 422."""

    def __init__(self, message: str, *, code: str = "unsupported_file_type") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class DeclaredFile:
    """The client's claims about a file, already extracted from the request."""

    filename: str
    mime_type: str
    file_size: int


def extension_of(filename: str) -> str:
    """Lowercased extension including the dot, or '' when there is none.

    Uses the final dot only: `report.final.v2.pdf` is a PDF, and a name with no
    extension is refused rather than guessed at from its MIME.
    """
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return ""
    return name[dot:].lower()


def validate_declared(
    declared: DeclaredFile, *, allowed_mime: tuple[str, ...], max_bytes: int
) -> str:
    """Check the parts visible before the bytes exist. Returns the extension.

    Order matters for the message the user reads: size first (the fix is
    obvious), then type, then the extension/MIME disagreement (which is the one
    that hints at a spoofed upload).
    """
    if declared.file_size <= 0:
        raise UploadValidationError("The file is empty.", code="empty_file")
    if declared.file_size > max_bytes:
        raise UploadValidationError(
            f"File is larger than the {max_bytes // (1024 * 1024)} MB limit",
            code="file_too_large",
        )

    mime = (declared.mime_type or "").split(";")[0].strip().lower()
    if mime not in allowed_mime:
        raise UploadValidationError(f"Unsupported file type: {declared.mime_type}")

    extension = extension_of(declared.filename)
    if not extension:
        raise UploadValidationError(
            "The file needs a .pdf, .docx, .txt, .md or .html extension so it can be parsed.",
            code="missing_extension",
        )

    permitted = EXTENSION_MIME.get(extension)
    if permitted is None:
        raise UploadValidationError(
            f"{extension} files are not supported. Use .pdf, .docx, .txt, .md or .html."
        )
    if mime not in permitted:
        # The interesting case: a PDF named .txt, or a .docx declaring text/plain.
        # Either the client is confused or it is trying to have one parser read
        # bytes meant for another. Both are refused with the same message.
        raise UploadValidationError(
            f"{declared.filename} is declared as {mime}, which does not match a "
            f"{extension} file.",
            code="extension_mime_mismatch",
        )
    return extension


def sniff(head: bytes) -> str | None:
    """Classify bytes by magic number. None means "no signature" (text-ish).

    ZIP is checked before the DECOMPRESSED-content checks in `validate_content`,
    because every OOXML file starts with the same PK header.
    """
    if head.startswith(_PDF_MAGIC):
        return PDF
    if head.startswith(_ZIP_MAGIC):
        return DOCX
    if head.startswith(_OLE_MAGIC):
        return "application/x-ole-storage"
    return None


def _looks_like_html(head: bytes) -> bool:
    sample = head[:2048].lstrip().lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or b"<html" in sample


def validate_content(head: bytes, *, mime_type: str, filename: str) -> None:
    """Confirm the bytes match the declared type. Raises on any disagreement.

    `head` is the first few KB — enough for every signature here. A text file
    needs more than its head to be fully validated, so this checks for binary
    content and lets the strict decode reject the rest (see the module docstring).
    """
    declared_mime = (mime_type or "").split(";")[0].strip().lower()
    detected = sniff(head)

    if declared_mime == PDF:
        if detected != PDF:
            raise UploadValidationError(
                f"{filename} is named as a PDF but its contents are not a PDF.",
                code="content_type_mismatch",
            )
        return

    if declared_mime == DOCX:
        if detected != DOCX:
            raise UploadValidationError(
                f"{filename} is named as a .docx but its contents are not a ZIP container.",
                code="content_type_mismatch",
            )
        return

    if declared_mime in TEXT_TYPES or declared_mime in HTML_TYPES:
        # A text type must not actually be a PDF/OOXML/legacy-office blob. This
        # is the direction that matters: renaming `payload.exe` to `notes.txt`
        # still fails here if it starts with a recognisable binary signature.
        if detected is not None:
            raise UploadValidationError(
                f"{filename} is declared as text but contains {detected} data.",
                code="content_type_mismatch",
            )
        if b"\x00" in head:
            raise UploadValidationError(
                f"{filename} contains binary data, so it cannot be read as text.",
                code="content_type_mismatch",
            )
        if declared_mime in HTML_TYPES and not _looks_like_html(head):
            raise UploadValidationError(
                f"{filename} is declared as HTML but does not look like an HTML document.",
                code="content_type_mismatch",
            )
        return

    raise UploadValidationError(
        f"Unsupported file type: {declared_mime}", code="unsupported_file_type"
    )


def is_valid_docx(data: bytes) -> bool:
    """A DOCX is a ZIP whose `word/` part exists — not merely a ZIP.

    Called by the extractor as a cheap pre-flight: python-docx produces a
    confusing `BadZipFile`/`KeyError` on a plain archive, and a clear rejection
    is worth the ten lines.
    """
    try:
        with zipfile.ZipFile(_Bytesish(data)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return False
    return any(name.startswith("word/") for name in names)


class _Bytesish:
    """Minimal file-like wrapper so `zipfile` can read from a bytes object lazily."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._data[self._pos :]
        else:
            chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        else:
            self._pos = len(self._data) + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return True
