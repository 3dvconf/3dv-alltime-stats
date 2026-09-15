from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from http.client import RemoteDisconnected
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = (
    "3DV-Through-the-Years-Metadata/0.3 "
    "(research metadata maintenance; contact: 3dv conference organizers)"
)

ADMIN_TITLE_PATTERNS = (
    "copyright notice",
    "copyright page",
    "front cover",
    "back cover",
    "table of contents",
    "author index",
    "message from",
    "welcome message",
    "proceedings of",
    "organizing committee",
    "program committee",
    "reviewers",
)


def today_iso() -> str:
    return date.today().isoformat()


def normalize_whitespace(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def strip_html(text: Any) -> str:
    value = str(text or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return normalize_whitespace(value)


def normalize_title(text: Any) -> str:
    value = unicodedata.normalize("NFKD", strip_html(text)).casefold()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return normalize_whitespace(value)


def normalize_doi(value: Any) -> str:
    doi = normalize_whitespace(value).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.rstrip(" .;,)")


def title_similarity(a: Any, b: Any) -> float:
    aa, bb = normalize_title(a), normalize_title(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    return SequenceMatcher(None, aa, bb).ratio()


def is_admin_record(title: Any) -> bool:
    norm = normalize_title(title)
    if not norm:
        return True
    return any(pattern in norm for pattern in ADMIN_TITLE_PATTERNS)


def stable_record_id(year: int, title: str, dblp_key: str = "") -> str:
    if dblp_key:
        return dblp_key.replace("/", ":")
    digest = hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:12]
    return f"3dv{year}:{digest}"


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_dblp_authors(info: Mapping[str, Any]) -> list[str]:
    raw = info.get("authors", {}).get("author", [])
    authors: list[str] = []
    for item in ensure_list(raw):
        if isinstance(item, str):
            name = item
        elif isinstance(item, Mapping):
            name = item.get("text") or item.get("#text") or item.get("name") or ""
        else:
            name = str(item)
        name = normalize_whitespace(name)
        if name:
            authors.append(name)
    return authors


def extract_doi_from_info(info: Mapping[str, Any]) -> str:
    direct = normalize_doi(info.get("doi", ""))
    if direct:
        return direct
    for candidate in ensure_list(info.get("ee")):
        doi = normalize_doi(candidate)
        if doi.startswith("10."):
            return doi
    return ""


def reconstruct_openalex_abstract(inverted_index: Any) -> str:
    if not isinstance(inverted_index, Mapping) or not inverted_index:
        return ""
    positioned: list[tuple[int, str]] = []
    for token, positions in inverted_index.items():
        for position in ensure_list(positions):
            try:
                positioned.append((int(position), str(token)))
            except (TypeError, ValueError):
                continue
    positioned.sort(key=lambda x: x[0])
    return normalize_whitespace(" ".join(token for _, token in positioned))


def flatten_names(items: Any) -> list[str]:
    names: list[str] = []
    for item in ensure_list(items):
        if isinstance(item, str):
            name = item
        elif isinstance(item, Mapping):
            name = (
                item.get("display_name")
                or item.get("name")
                or item.get("topic", {}).get("display_name")
                or item.get("field")
                or ""
            )
        else:
            name = str(item)
        name = normalize_whitespace(name)
        if name:
            names.append(name)
    return names


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load_json_cell(value: Any) -> Any:
    if value in (None, ""):
        return []
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return []


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(
    path: str | Path,
    rows: Iterable[Mapping[str, Any]],
    fieldnames: list[str] | tuple[str, ...] | None = None,
) -> None:
    rows = [dict(row) for row in rows]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(target)


def fetch_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: int = 60,
    retries: int = 5,
    backoff: float = 1.6,
) -> Any:
    merged_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    request = Request(url, data=data, headers=merged_headers, method=method)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                raise
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = (
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else backoff**attempt
            )
        except (
            URLError,
            TimeoutError,
            RemoteDisconnected,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            delay = backoff**attempt
        if attempt + 1 < retries:
            time.sleep(min(delay, 30.0))
    if last_error:
        raise last_error
    raise RuntimeError(f"Unable to fetch {url}")


def merge_semicolon(existing: Any, additions: Iterable[Any]) -> str:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in [*str(existing or "").split(";"), *[str(x) for x in additions]]:
        clean = normalize_whitespace(value)
        key = clean.casefold()
        if clean and key not in seen:
            ordered.append(clean)
            seen.add(key)
    return "; ".join(ordered)
