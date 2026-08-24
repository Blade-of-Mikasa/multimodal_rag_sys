"""Main-content and explicit source-time extraction for untrusted HTML."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import trafilatura

from .domain import (
    ExtractedPage,
    FetchedPage,
    SourceTime,
    SourceTimeKind,
    WebExtractionError,
)


_PUBLISHED_KEYS = {
    "article:published_time",
    "date",
    "datepublished",
    "parsely-pub-date",
    "pubdate",
    "publishdate",
}
_MODIFIED_KEYS = {
    "article:modified_time",
    "datemodified",
    "last-modified",
    "lastmodified",
}
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TrafilaturaPageExtractor:
    def __init__(self, *, max_text_chars: int) -> None:
        if max_text_chars < 256:
            raise ValueError("extracted web text limit must be at least 256 characters")
        self._max_text_chars = max_text_chars

    def extract(self, page: FetchedPage) -> ExtractedPage:
        metadata = _ExplicitMetadataParser(page.final_url)
        try:
            metadata.feed(page.html)
            metadata.close()
            raw = trafilatura.extract(
                page.html,
                url=page.final_url,
                output_format="json",
                with_metadata=True,
                include_comments=False,
                include_tables=True,
                deduplicate=True,
                favor_precision=True,
            )
        except Exception as error:
            raise WebExtractionError(
                "HTML_PARSE_ERROR", "web page HTML could not be parsed"
            ) from error
        if not raw:
            raise WebExtractionError(
                "NO_MAIN_CONTENT", "web page contains no extractable main content"
            )
        try:
            extracted = json.loads(raw)
            text = extracted["text"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise WebExtractionError(
                "INVALID_EXTRACTION", "web extractor returned an invalid result"
            ) from error
        if not isinstance(text, str):
            raise WebExtractionError(
                "INVALID_EXTRACTION", "web extractor returned non-text content"
            )
        text = _normalize_text(text)[: self._max_text_chars].strip()
        if not text:
            raise WebExtractionError(
                "NO_MAIN_CONTENT", "web page contains no extractable main content"
            )
        title = extracted.get("title")
        if not isinstance(title, str) or not title.strip():
            title = metadata.title
        title = _normalize_inline(title or "")[:4_096]
        canonical_url = metadata.canonical_url or page.final_url

        published = _build_source_time(
            SourceTimeKind.PUBLISHED, metadata.published_values
        )
        modified = _build_source_time(
            SourceTimeKind.MODIFIED, metadata.modified_values
        )
        if modified is None and page.http_last_modified is not None:
            modified = SourceTime(
                kind=SourceTimeKind.MODIFIED,
                value=page.http_last_modified,
                source="http_last_modified",
                precision="second",
                raw_value=page.http_last_modified.isoformat(),
            )
        encoded = text.encode("utf-8")
        return ExtractedPage(
            canonical_url=canonical_url,
            title=title,
            text=text,
            content_sha256=sha256(encoded).hexdigest(),
            published_time=published,
            modified_time=modified,
        )


class _ExplicitMetadataParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self.title = ""
        self.canonical_url: str | None = None
        self.published_values: list[tuple[str, str]] = []
        self.modified_values: list[tuple[str, str]] = []
        self._title_parts: list[str] = []
        self._in_title = False
        self._json_ld_depth = 0
        self._json_ld_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        elif lowered == "meta":
            key = (
                values.get("property")
                or values.get("name")
                or values.get("itemprop")
            ).strip().lower()
            content = values.get("content", "").strip()
            if key in {"og:title", "twitter:title"} and content and not self.title:
                self.title = content
            self._record_time(key, content, f"meta:{key}")
        elif lowered == "link" and "canonical" in values.get("rel", "").lower().split():
            self._accept_canonical(values.get("href", ""))
        elif lowered == "time":
            raw = values.get("datetime", "").strip()
            marker = " ".join(
                (values.get("class", ""), values.get("itemprop", ""), values.get("name", ""))
            ).lower()
            if "modif" in marker or "update" in marker:
                self.modified_values.append(("time:modified", raw))
            elif "publish" in marker or "date" in marker:
                self.published_values.append(("time:published", raw))
        elif (
            lowered == "script"
            and values.get("type", "").lower().split(";", 1)[0]
            == "application/ld+json"
        ):
            self._json_ld_depth += 1
            self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
            if not self.title:
                self.title = "".join(self._title_parts).strip()
        elif lowered == "script" and self._json_ld_depth:
            self._json_ld_depth -= 1
            raw = "".join(self._json_ld_parts).strip()
            self._json_ld_parts = []
            if raw:
                try:
                    self._walk_json_ld(json.loads(raw))
                except (json.JSONDecodeError, RecursionError):
                    pass

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._json_ld_depth:
            self._json_ld_parts.append(data)

    def _record_time(self, key: str, value: str, source: str) -> None:
        if not value:
            return
        if key in _PUBLISHED_KEYS:
            self.published_values.append((source, value))
        elif key in _MODIFIED_KEYS:
            self.modified_values.append((source, value))

    def _walk_json_ld(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered = key.lower()
                if lowered == "datepublished" and isinstance(nested, str):
                    self.published_values.append(("jsonld:datePublished", nested))
                elif lowered == "datemodified" and isinstance(nested, str):
                    self.modified_values.append(("jsonld:dateModified", nested))
                else:
                    self._walk_json_ld(nested)
        elif isinstance(value, list):
            for nested in value:
                self._walk_json_ld(nested)

    def _accept_canonical(self, href: str) -> None:
        if not href or self.canonical_url is not None:
            return
        candidate = urljoin(self._base_url, href)
        base = urlsplit(self._base_url)
        parsed = urlsplit(candidate)
        try:
            base_port = base.port or (443 if base.scheme.lower() == "https" else 80)
            parsed_port = parsed.port or (
                443 if parsed.scheme.lower() == "https" else 80
            )
        except ValueError:
            return
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.scheme.lower() == base.scheme.lower()
            and parsed.hostname
            and parsed.hostname.lower() == (base.hostname or "").lower()
            and parsed_port == base_port
            and parsed.username is None
            and parsed.password is None
        ):
            self.canonical_url = candidate.split("#", 1)[0]


def _build_source_time(
    kind: SourceTimeKind, candidates: list[tuple[str, str]]
) -> SourceTime | None:
    for source, raw_value in sorted(candidates, key=lambda item: _time_priority(item[0])):
        parsed = _parse_source_time(raw_value)
        if parsed is None:
            continue
        value, precision, assumed = parsed
        return SourceTime(
            kind=kind,
            value=value,
            source=source,
            precision=precision,
            timezone_assumed=assumed,
            raw_value=raw_value[:512],
        )
    return None


def _time_priority(source: str) -> int:
    if source.startswith("meta:article:"):
        return 0
    if source.startswith("jsonld:"):
        return 1
    if source.startswith("meta:"):
        return 2
    if source.startswith("time:"):
        return 3
    return 4


def _parse_source_time(raw_value: str) -> tuple[datetime, str, bool] | None:
    value = raw_value.strip()
    if not value or len(value) > 512:
        return None
    precision = "date" if _DATE_ONLY.fullmatch(value) else "second"
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    assumed = parsed.tzinfo is None or parsed.utcoffset() is None
    if assumed:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), precision, assumed


def _normalize_text(value: str) -> str:
    lines = (_normalize_inline(line) for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _normalize_inline(value: str) -> str:
    return " ".join(value.split())
