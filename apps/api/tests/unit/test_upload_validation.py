"""Upload validation: extension, declared type, and actual content.

Rule 9 of the security architecture. The tests are grouped by what an attacker
actually tries, not by function name:

  * lie about the extension (`.exe` renamed `.pdf`);
  * lie about the MIME type in the JSON;
  * lie about both, consistently;
  * send something huge.

Each must fail at the earliest point where it is detectable, because the later
the failure, the more of the pipeline has already trusted the file.
"""

from __future__ import annotations

import pytest
from app.core.upload_validation import (
    DOCX,
    PDF,
    DeclaredFile,
    UploadValidationError,
    extension_of,
    is_valid_docx,
    sniff,
    validate_content,
    validate_declared,
)

ALLOWED = (
    PDF,
    DOCX,
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/html",
)
MAX_BYTES = 1024 * 1024


def declared(filename: str, mime: str = PDF, size: int = 1024) -> DeclaredFile:
    return DeclaredFile(filename=filename, mime_type=mime, file_size=size)


def check(file: DeclaredFile) -> str:
    return validate_declared(file, allowed_mime=ALLOWED, max_bytes=MAX_BYTES)


class TestExtensionOf:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("report.pdf", ".pdf"),
            ("REPORT.PDF", ".pdf"),
            ("report.final.v2.pdf", ".pdf"),
            ("notes.markdown", ".markdown"),
            ("archive.tar.gz", ".gz"),
            ("no-extension", ""),
            ("trailing.", ""),
            (".hidden", ""),
            ("../../etc/passwd", ""),
            ("/path/to/file.txt", ".txt"),
        ],
    )
    def test_parses_the_final_dot_only(self, name, expected):
        assert extension_of(name) == expected

    def test_a_path_cannot_smuggle_an_extension(self):
        """`x.pdf/../y.exe` must not read as a PDF.

        Only the last segment is considered, so the trailing name decides — and
        that name has no extension.
        """
        assert extension_of("report.pdf/../evil.exe") == ".exe"


class TestDeclaration:
    def test_a_normal_pdf_passes(self):
        assert check(declared("report.pdf")) == ".pdf"

    def test_a_markdown_file_passes(self):
        assert check(declared("notes.md", "text/markdown")) == ".md"

    def test_size_is_checked_first(self):
        with pytest.raises(UploadValidationError) as exc:
            check(declared("report.pdf", size=MAX_BYTES + 1))
        assert exc.value.code == "file_too_large"

    def test_an_empty_file_is_refused(self):
        with pytest.raises(UploadValidationError) as exc:
            check(declared("report.pdf", size=0))
        assert exc.value.code == "empty_file"

    def test_an_unsupported_mime_is_refused(self):
        with pytest.raises(UploadValidationError) as exc:
            check(declared("thing.exe", "application/x-msdownload"))
        assert exc.value.code == "unsupported_file_type"

    def test_a_missing_extension_is_refused(self):
        """No extension means no idea which parser to use — refuse, do not guess."""
        with pytest.raises(UploadValidationError) as exc:
            check(declared("README"))
        assert exc.value.code == "missing_extension"

    def test_an_unsupported_extension_is_refused(self):
        with pytest.raises(UploadValidationError):
            check(declared("data.xlsx", "text/plain"))

    @pytest.mark.parametrize(
        ("filename", "mime"),
        [
            ("report.pdf", "text/plain"),  # a PDF claiming to be text
            ("notes.txt", PDF),  # text claiming to be a PDF
            ("plan.docx", PDF),  # OOXML claiming to be a PDF
            ("page.html", "text/plain"),  # HTML claiming to be plain text
            ("file.md", "text/html"),
        ],
    )
    def test_extension_and_mime_must_agree(self, filename, mime):
        """The spoofing case: both fields are client-supplied and can disagree.

        Whichever parser is chosen for this file, one of the two claims is false.
        """
        with pytest.raises(UploadValidationError) as exc:
            check(declared(filename, mime))
        assert exc.value.code == "extension_mime_mismatch"

    def test_mime_parameters_are_ignored(self):
        """`text/plain; charset=utf-8` is still text/plain. Browsers send this."""
        assert check(declared("notes.txt", "text/plain; charset=utf-8")) == ".txt"


class TestSniff:
    def test_pdf_magic(self):
        assert sniff(b"%PDF-1.7\n...") == PDF

    def test_zip_magic(self):
        assert sniff(b"PK\x03\x04rest") == DOCX

    def test_legacy_office_is_identified_so_it_can_be_refused(self):
        assert sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") == "application/x-ole-storage"

    def test_text_has_no_signature(self):
        assert sniff(b"just some words") is None


class TestContent:
    def test_a_real_pdf_header_passes(self):
        validate_content(b"%PDF-1.7\n...", mime_type=PDF, filename="a.pdf")

    def test_a_pdf_that_is_not_a_pdf_is_refused(self):
        """The renamed-executable case. This is the check the declaration cannot do."""
        with pytest.raises(UploadValidationError) as exc:
            validate_content(b"MZ\x90\x00\x03\x00", mime_type=PDF, filename="a.pdf")
        assert exc.value.code == "content_type_mismatch"

    def test_a_docx_that_is_not_a_zip_is_refused(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_content(b"%PDF-1.7", mime_type=DOCX, filename="a.docx")
        assert exc.value.code == "content_type_mismatch"

    def test_text_that_is_actually_a_pdf_is_refused(self):
        """The other direction: a binary file wearing a .txt extension."""
        with pytest.raises(UploadValidationError) as exc:
            validate_content(b"%PDF-1.4\n", mime_type="text/plain", filename="a.txt")
        assert exc.value.code == "content_type_mismatch"

    def test_text_with_a_null_byte_is_refused(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_content(
                b"looks like text\x00but is not", mime_type="text/plain", filename="a.txt"
            )
        assert exc.value.code == "content_type_mismatch"

    def test_plain_text_passes(self):
        validate_content(b"# Title\n\nSome prose.\n", mime_type="text/markdown", filename="a.md")

    def test_utf8_text_passes(self):
        validate_content("héllo wörld — ünïcode".encode(), mime_type="text/plain", filename="a.txt")

    def test_html_must_look_like_html(self):
        validate_content(
            b"<!DOCTYPE html><html><body>x</body></html>",
            mime_type="text/html",
            filename="a.html",
        )
        with pytest.raises(UploadValidationError):
            validate_content(b"just plain prose", mime_type="text/html", filename="a.html")


class TestDocxContainer:
    def test_a_zip_without_a_word_directory_is_not_a_docx(self):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("random.txt", "not a document")
        assert is_valid_docx(buffer.getvalue()) is False

    def test_a_zip_with_word_parts_is_a_docx(self):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "<document/>")
        assert is_valid_docx(buffer.getvalue()) is True

    def test_garbage_is_not_a_docx(self):
        assert is_valid_docx(b"not a zip at all") is False
