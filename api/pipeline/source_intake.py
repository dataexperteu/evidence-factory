"""Source Intake — paste, URL fetch, and file upload paths.

All three modes produce a normalised SourceText consumed by the downstream
pipeline. Errors surface as IntakeError (a ValueError subclass) so callers
can map them to user-readable HTTP 400 responses.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import httpx
import trafilatura
from charset_normalizer import from_bytes


@dataclass(frozen=True)
class SourceText:
    body: str
    char_count: int


class IntakeError(ValueError):
    """Raised when ingestion cannot produce usable text."""


_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_EXTENSIONS = {".txt", ".md"}
# charset-normalizer encoding names that map to accepted single-byte codecs.
# cp1250/cp1252 are nearly identical — detection tools often confuse them on
# short texts, so we accept any detected Windows codepage and all iso-8859
# variants (same spirit as the issue requirement: Windows-1252 / ISO-8859-1).
_ACCEPTED_ENCODINGS = {
    "windows-1252",
    "cp1252",
    "cp1250",
    "cp1251",
    "cp1253",
    "cp1254",
    "cp1255",
    "cp1256",
    "cp1257",
    "cp1258",
    "iso-8859-1",
    "iso-8859-2",
    "iso-8859-3",
    "iso-8859-4",
    "iso-8859-5",
    "iso-8859-6",
    "iso-8859-7",
    "iso-8859-8",
    "iso-8859-9",
    "iso-8859-15",
    "latin-1",
    "iso_8859-1",
    "iso8859-1",
}


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_WS.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def ingest_paste(raw: str | bytes) -> SourceText:
    """Normalise a pasted source story into a SourceText.

    Decodes utf-8 if bytes were supplied, applies NFC normalisation, strips
    trailing whitespace on each line, and collapses 3+ blank lines to 2.
    Raises IntakeError on empty input.
    """
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = raw
    text = _normalise(text)
    if not text:
        raise IntakeError("source paste was empty after normalisation")
    return SourceText(body=text, char_count=len(text))


async def ingest_url(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
) -> SourceText:
    """Fetch a URL over HTTP/HTTPS and extract the main readable text.

    Uses trafilatura for readability extraction. Raises IntakeError on fetch
    failures (non-2xx, timeout, non-HTML content-type) or when trafilatura
    cannot extract any content.
    """
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in _ALLOWED_SCHEMES:
        raise IntakeError(f"URL scheme {scheme!r} is not supported; use http or https")

    async def _fetch(c: httpx.AsyncClient) -> SourceText:
        try:
            resp = await c.get(url, follow_redirects=True, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise IntakeError(f"request to {url!r} timed out after {timeout:.0f}s") from exc
        except httpx.RequestError as exc:
            raise IntakeError(f"network error fetching {url!r}: {exc}") from exc

        if resp.status_code >= 400:
            raise IntakeError(f"server returned HTTP {resp.status_code} for {url!r}")

        ct = resp.headers.get("content-type", "")
        if "html" not in ct and "xml" not in ct and "text" not in ct:
            raise IntakeError(f"expected HTML content but got content-type {ct!r} from {url!r}")

        extracted = trafilatura.extract(resp.text)
        if not extracted or not extracted.strip():
            raise IntakeError(
                f"could not extract readable text from {url!r}; "
                "the page may be empty or JavaScript-rendered"
            )

        text = _normalise(extracted)
        if not text:
            raise IntakeError(f"extracted text from {url!r} was empty after normalisation")
        return SourceText(body=text, char_count=len(text))

    if client is not None:
        return await _fetch(client)
    async with httpx.AsyncClient() as c:
        return await _fetch(c)


def ingest_upload(content: bytes, filename: str) -> SourceText:
    """Decode and normalise an uploaded .txt or .md file.

    Encoding detection order:
    1. UTF-8 BOM (EF BB BF)
    2. UTF-8 (strict)
    3. charset-normalizer detection → accept only Windows-1252 / ISO-8859-1
    4. Refuse with IntakeError

    Raises IntakeError for unsupported extensions, empty files, or binary
    content that cannot be decoded.
    """
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in _ALLOWED_EXTENSIONS:
        raise IntakeError(f"file extension {suffix!r} is not supported; upload a .txt or .md file")

    if not content:
        raise IntakeError("uploaded file is empty")

    text = _decode_bytes(content, filename)
    text = _normalise(text)
    if not text:
        raise IntakeError("uploaded file contained no text after decoding")
    return SourceText(body=text, char_count=len(text))


def _decode_bytes(content: bytes, filename: str) -> str:
    # UTF-8 BOM
    if content.startswith(b"\xef\xbb\xbf"):
        try:
            return content[3:].decode("utf-8")
        except UnicodeDecodeError:
            pass

    # UTF-16 BOMs — refuse rather than silently garble
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise IntakeError(f"{filename!r} appears to be UTF-16 encoded; please re-save as UTF-8")

    # Pure UTF-8 (no BOM)
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # Detect via charset-normalizer
    results = from_bytes(content)
    best = results.best()
    if best is None:
        raise IntakeError(
            f"could not detect encoding for {filename!r}; "
            "upload a UTF-8, Windows-1252, or ISO-8859-1 file"
        )

    enc = str(best.encoding).lower().replace("_", "-")
    if enc not in _ACCEPTED_ENCODINGS:
        raise IntakeError(
            f"{filename!r} appears to use {enc!r} encoding which is not supported; "
            "upload a UTF-8, Windows-1252, or ISO-8859-1 file"
        )

    try:
        return content.decode(best.encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise IntakeError(f"failed to decode {filename!r} as {enc!r}: {exc}") from exc
