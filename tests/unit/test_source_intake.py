"""Source Intake — paste, URL, and upload ingestion."""

from __future__ import annotations

import httpx
import pytest

from api.pipeline.source_intake import (
    IntakeError,
    ingest_paste,
    ingest_upload,
    ingest_url,
)

# ---------------------------------------------------------------------------
# Paste
# ---------------------------------------------------------------------------


def test_strips_trailing_whitespace_and_normalises_newlines():
    out = ingest_paste("hello \r\nworld\t \n\n\n\nmore\n")
    assert out.body == "hello\nworld\n\nmore"
    assert out.char_count == len(out.body)


def test_decodes_bytes():
    out = ingest_paste(b"hi there")
    assert out.body == "hi there"


def test_rejects_empty_paste():
    with pytest.raises(ValueError):
        ingest_paste("\n\n   \t\n")


def test_collapses_runs_of_blank_lines():
    text = "a\n\n\n\n\nb"
    assert ingest_paste(text).body == "a\n\nb"


def test_nfc_normalises_combining_characters():
    # "é" composed vs decomposed.
    composed = "café"
    out = ingest_paste(composed)
    assert out.body == "café"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_ingest_upload_utf8():
    content = b"The quick brown fox jumped over the lazy dog.\n"
    result = ingest_upload(content, "story.txt")
    assert "quick brown fox" in result.body
    assert result.char_count == len(result.body)


def test_ingest_upload_utf8_bom():
    text = "Hello BOM world. This is a longer line so trafilatura stays happy."
    content = b"\xef\xbb\xbf" + text.encode("utf-8")
    result = ingest_upload(content, "story.txt")
    assert "Hello BOM world" in result.body


def test_ingest_upload_windows1252():
    # The € sign (U+20AC) maps to 0x80 in Windows-1252 and exists in no other
    # Latin-1-family encoding, making it a reliable detection marker.
    text = "The café cost €2.50. Señor García approved."
    content = text.encode("windows-1252")
    result = ingest_upload(content, "story.txt")
    assert "caf" in result.body.lower()
    assert result.char_count > 0


def test_ingest_upload_md_extension():
    content = b"# Heading\n\nSome markdown content here.\n"
    result = ingest_upload(content, "notes.md")
    assert result.char_count > 0


def test_ingest_upload_rejects_unsupported_extension():
    with pytest.raises(IntakeError, match="not supported"):
        ingest_upload(b"hello", "file.pdf")


def test_ingest_upload_rejects_empty_file():
    with pytest.raises(IntakeError, match="empty"):
        ingest_upload(b"", "story.txt")


def test_ingest_upload_rejects_binary_content():
    # Null bytes and high-entropy binary — charset-normalizer should detect
    # these as non-text or an unsupported encoding.
    binary = bytes(range(256)) * 4  # clearly not text
    with pytest.raises(IntakeError):
        ingest_upload(binary, "data.txt")


# ---------------------------------------------------------------------------
# URL (stubbed HTTP client)
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    """Return a canned HTTP response for any request."""

    def __init__(
        self,
        body: str | bytes = "",
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self._body = body.encode() if isinstance(body, str) else body
        self._status = status
        self._content_type = content_type

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            self._status,
            content=self._body,
            headers={"content-type": self._content_type},
        )


def _html(text: str) -> str:
    return (
        "<html><head><title>Test</title></head>"
        f"<body><article><p>{text}</p></article></body></html>"
    )


def _make_client(
    body: str | bytes = "",
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_MockTransport(body, status, content_type))


@pytest.mark.asyncio
async def test_ingest_url_success():
    article = (
        "A young lady appeared at the window. She told us of strange whistles "
        "in the night and of her sister's dying words. Holmes listened intently "
        "and then rose to examine the curious details of the case at hand."
    )
    client = _make_client(_html(article))
    result = await ingest_url("https://example.com/article", client=client)
    assert len(result.body) > 10
    assert result.char_count == len(result.body)


@pytest.mark.asyncio
async def test_ingest_url_rejects_non_http_scheme():
    with pytest.raises(IntakeError, match="scheme"):
        await ingest_url("ftp://example.com/file")


@pytest.mark.asyncio
async def test_ingest_url_raises_on_404():
    client = _make_client(status=404)
    with pytest.raises(IntakeError, match="404"):
        await ingest_url("https://example.com/missing", client=client)


@pytest.mark.asyncio
async def test_ingest_url_raises_on_non_html_content_type():
    client = _make_client(b"\x89PNG\r\n\x1a\n", content_type="image/png")
    with pytest.raises(IntakeError, match="content-type"):
        await ingest_url("https://example.com/image.png", client=client)


@pytest.mark.asyncio
async def test_ingest_url_raises_on_timeout():
    class _TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out", request=request)

    client = httpx.AsyncClient(transport=_TimeoutTransport())
    with pytest.raises(IntakeError, match="timed out"):
        await ingest_url("https://example.com/slow", client=client)
