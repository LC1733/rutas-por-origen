#!/usr/bin/env python3
"""Refresh public official sources without credentials or paid APIs.

The collector stores factual values and document metadata, not full third-party
documents. Each source fails independently. A failure never erases its last
successful payload or makes it look freshly published.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from pypdf import PdfReader

GATUN_URL = "https://evtms-rpts.pancanal.com/eng/h2o/GatunWaterIndicators.pdf"
ADVISORIES_URL = "https://pancanal.com/en/maritime-services/advisory-to-shipping/"
NOAA_URL = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
ENFEN_URL = "https://enfen.imarpe.gob.pe/comunicados/"
DHN_URL = "https://www.dhn.mil.pe/portal/avisos-especiales"
DICAPI_URL = "https://www.dicapi.mil.pe/estado-de-puertos"
ALLOWED_HOSTS = frozenset({
    "evtms-rpts.pancanal.com", "pancanal.com", "www.pancanal.com",
    "www.cpc.ncep.noaa.gov", "cpc.ncep.noaa.gov",
    "enfen.imarpe.gob.pe", "www.dhn.mil.pe", "dhn.mil.pe",
})
MAX_BYTES = 8_000_000
TIMEOUT_SECONDS = 25
USER_AGENT = "MARTILINE-Global-Control/2.0 (public-source monitor; hourly)"
SOURCE_META = {
    "acp-gatun": ("ACP · Indicadores de Gatún", GATUN_URL),
    "acp-advisories": ("ACP · Avisos a las navieras", ADVISORIES_URL),
    "noaa": ("NOAA · ENSO", NOAA_URL),
    "enfen": ("ENFEN · Comunicados oficiales", ENFEN_URL),
    "dhn": ("DHN · Avisos de oleaje", DHN_URL),
    "dicapi": ("DICAPI · Estado por instalación", DICAPI_URL),
}


class SourceError(RuntimeError):
    """A source was unavailable, changed its format, or failed validation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).replace("\xa0", " ").split())


def plain(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn").lower()


def public_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise SourceError("La fuente enlazó un dominio o protocolo no autorizado")
    return url


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class Response:
    body: bytes
    url: str
    content_type: str


def fetch(url: str, kind: str = "html", max_bytes: int = MAX_BYTES) -> Response:
    """One bounded public GET. No cookies, login, browser, keys or paid proxy."""
    public_url(url)
    accept = "application/pdf" if kind == "pdf" else "text/html,application/xhtml+xml"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    opener = urllib.request.build_opener(OfficialRedirects())
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            public_url(response.url)
            mime = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower().strip()
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise SourceError("La respuesta supera el límite de descarga")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise SourceError("La respuesta supera el límite de descarga")
            if kind == "pdf":
                if mime not in ("application/pdf", "application/octet-stream") or not body.startswith(b"%PDF-"):
                    raise SourceError("La descarga no contiene un PDF válido")
            elif mime not in ("text/html", "application/xhtml+xml") or not body.strip():
                raise SourceError("La respuesta no contiene HTML válido")
            return Response(body, response.url, mime)
    except SourceError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceError(f"La fuente respondió HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError(f"No se pudo consultar la fuente ({type(exc).__name__})") from exc


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: Node | None = None

    def text(self) -> str:
        if self.tag in ("script", "style", "noscript"):
            return ""
        return clean(" ".join(child.text() if isinstance(child, Node) else child for child in self.children))

    def descendants(self, tag: str | None = None, css_class: str | None = None):
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if ((tag is None or child.tag == tag)
                    and (css_class is None or css_class in child.attrs.get("class", "").split())):
                yield child
            yield from child.descendants(tag, css_class)


class Document(HTMLParser):
    VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def html_document(body: bytes | str) -> Node:
    return Document(body.decode("utf-8", "replace") if isinstance(body, bytes) else body).root


MONTH_ES = {m: i + 1 for i, m in enumerate("enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}
MONTH_ES["setiembre"] = 9
MONTH_EN = {m: i + 1 for i, m in enumerate("january february march april may june july august september october november december".split())}
MONTH_EN.update({m[:3]: value for m, value in list(MONTH_EN.items())})


def iso_date(year: str | int, month: str | int, day: str | int) -> str:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except (ValueError, TypeError) as exc:
        raise SourceError("La fuente contiene una fecha no válida") from exc


def date_es(text: str) -> str | None:
    match = re.search(r"\b(\d{1,2})\s+(?:de\s+)?([a-z]+),?\s+(?:(?:de|del)\s+)?(\d{4})\b", plain(clean(text)))
    if not match or match[2] not in MONTH_ES:
        return None
    return iso_date(match[3], MONTH_ES[match[2]], match[1])


def date_en(text: str) -> str | None:
    candidates = []
    for match in re.finditer(r"\b(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})\b", text):
        if match[2].lower() in MONTH_EN:
            candidates.append((match.start(), iso_date(match[3], MONTH_EN[match[2].lower()], match[1])))
    for match in re.finditer(r"\b([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b", text):
        if match[1].lower() in MONTH_EN:
            candidates.append((match.start(), iso_date(match[3], MONTH_EN[match[1].lower()], match[2])))
    return min(candidates)[1] if candidates else None


def pdf_text(body: bytes, max_pages: int = 16) -> str:
    try:
        reader = PdfReader(BytesIO(body), strict=False)
        if reader.is_encrypted or len(reader.pages) > max_pages:
            raise SourceError("El PDF está protegido o excede el límite de páginas")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip() or len(text) > 400_000:
            raise SourceError("No se pudo extraer texto utilizable del PDF")
        return text
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("No se pudo interpretar el PDF de la fuente") from exc


def parse_gatun(text: str) -> dict:
    # Tableau inserts spaces within words. Restrict extraction to the labeled
    # official panel, never the projections or interactive calculator below it.
    compact = re.sub(r"\s+", "", text)
    water = re.search(r"(\d+(?:\.\d+)?)ftOfficialGatunWaterLevelfor([A-Za-z]+)\.?(\d{1,2}),(\d{4})", compact, re.I)
    draft = re.search(r"NeopanamaxPanamax(\d+(?:\.\d+)?)ft(\d+(?:\.\d+)?)ftOfficialMaximumDraft", compact, re.I)
    if not water or not draft or water[2].lower() not in MONTH_EN:
        raise SourceError("Cambió el formato de los indicadores oficiales de Gatún")
    lake, neo, pana = float(water[1]), float(draft[1]), float(draft[2])
    if not (40 <= lake <= 100 and 20 <= neo <= 60 and 10 <= pana <= 50):
        raise SourceError("Los indicadores de Gatún están fuera del rango de validación")
    document_date = iso_date(water[4], MONTH_EN[water[2].lower()], water[3])
    return {"sourceDate": document_date, "data": {
        "neopanamaxDraftFeet": neo, "panamaxDraftFeet": pana,
        "lakeLevelFeet": lake, "documentDate": document_date,
        "documentUrl": GATUN_URL,
        "scope": "Indicadores del PDF diario. Los límites autorizados se contrastan con los avisos ACP.",
    }}


def collect_gatun() -> dict:
    return parse_gatun(pdf_text(fetch(GATUN_URL, "pdf").body))


def parse_advisories(body: bytes | str) -> list[dict]:
    doc = html_document(body)
    found = {}
    for anchor in doc.descendants("a"):
        title = anchor.text()
        match = re.match(r"A[-–](\d{1,3})[-–](\d{4})\b", title, re.I)
        if not match:
            continue
        url = public_url(urllib.parse.urljoin(ADVISORIES_URL, anchor.attrs.get("href", "")))
        if not urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):
            continue
        number = f"A-{int(match[1]):02d}-{match[2]}"
        found[(int(match[2]), int(match[1]))] = {"number": number, "title": title, "url": url, "publishedAt": None}
    if not found:
        raise SourceError("No se reconocieron avisos oficiales en el índice ACP")
    return [found[k] for k in sorted(found, reverse=True)[:8]]


def collect_advisories() -> dict:
    items = parse_advisories(fetch(ADVISORIES_URL).body)
    latest = items[0]
    status, error = "ok", None
    try:
        header = clean(pdf_text(fetch(latest["url"], "pdf").body))[:1500]
        latest["publishedAt"] = date_en(header)
        if not latest["publishedAt"]:
            raise SourceError("No se reconoció la fecha de emisión del último aviso")
    except SourceError as exc:
        status, error = "partial", f"Índice actualizado. Fecha del último PDF sin verificar: {exc}"
    return {"sourceDate": latest["publishedAt"], "status": status, "error": error, "data": {
        "latest": latest, "items": items, "bookingSlotsRequireReview": True,
        "scope": "Detecta avisos nuevos. No cambia automáticamente cupos, tarifas ni límites normativos.",
    }}


def parse_noaa(body: bytes | str) -> dict:
    doc = html_document(body)
    text = doc.text()
    issuance = re.search(r"CLIMATE PREDICTION CENTER/NCEP/NWS\s+(.{0,80})", text, re.I)
    published = date_en(issuance[1]) if issuance else None
    status = re.search(r"ENSO Alert System Status:\s*(.{1,130}?)\s+Synopsis:", text, re.I)
    # Read the Synopsis paragraph itself, not the subsequent body or boilerplate.
    summaries = [n.text() for n in doc.descendants("p") if re.match(r"Synopsis\s*:", n.text(), re.I)]
    summary = re.sub(r"^Synopsis\s*:\s*", "", min(summaries, key=len), flags=re.I) if summaries else None
    if not published or not status or not summary or len(summary) > 1000:
        raise SourceError("Cambió el formato de la discusión ENSO de NOAA")
    next_match = re.search(r"next ENSO Diagnostics? Discussion is scheduled for\s+(.{0,60})", text, re.I)
    next_date = date_en(next_match[1]) if next_match else None
    nino = re.search(r"([+-]\d+(?:\.\d+)?)\s*°\s*C\s+in\s+Ni[ñn]o[-\s]*1\+2", text, re.I)
    latest = {"title": "ENSO Diagnostic Discussion", "url": NOAA_URL, "publishedAt": published,
              "alertStatus": clean(status[1]), "summary": clean(summary), "nextReleaseDate": next_date}
    if nino:
        value = float(nino[1])
        if -10 <= value <= 10:
            latest["nino12AnomalyC"] = value
    return {"sourceDate": published, "data": {"latest": latest, "items": [latest],
            "scope": "Boletín mensual. La fecha de consulta no es la fecha de la observación climática."}}


def collect_noaa() -> dict:
    return parse_noaa(fetch(NOAA_URL).body)


def parse_enfen_index(body: bytes | str) -> dict:
    doc = html_document(body)
    blocks = list(doc.descendants(css_class="enfen_ultimo_informe"))
    if len(blocks) != 1:
        raise SourceError("No se encontró un único bloque de último comunicado ENFEN")
    block = blocks[0]
    text = block.text()
    match = re.search(r"Comunicado Oficial Enfen\s+N[°ºo.]?\s*(\d+)\s*[-–]\s*(\d{4})", text, re.I)
    published = date_es(text)
    links = [a for a in block.descendants("a") if "wpdmdl=" in a.attrs.get("href", "")]
    if not match or not published or len(links) != 1:
        raise SourceError("Cambió el formato del último comunicado ENFEN")
    url = public_url(urllib.parse.urljoin(ENFEN_URL, links[0].attrs["href"]))
    return {"number": f"{int(match[1])}-{match[2]}", "title": f"Comunicado Oficial ENFEN N.º {int(match[1])}-{match[2]}",
            "url": url, "publishedAt": published}


def collect_enfen() -> dict:
    latest = parse_enfen_index(fetch(ENFEN_URL).body)
    status, error = "ok", None
    try:
        text = pdf_text(fetch(latest["url"], "pdf").body)
        header = clean(text[:1600])
        found_number = re.search(r"COMUNICADO OFICIAL ENFEN\s+N[°ºo.]?\s*(\d+)\s*[-–]\s*(\d{4})", header, re.I)
        found_date = date_es(header)
        if (not found_number or f"{int(found_number[1])}-{found_number[2]}" != latest["number"]
                or found_date != latest["publishedAt"]):
            raise SourceError("El PDF y el índice no coinciden en número y fecha")
        lines = [clean(line) for line in text[:1600].splitlines() if clean(line)]
        headlines = [line for line in lines if len(line) > 35 and line == line.upper()
                     and re.match(r"(?:EL NIÑO|LA NIÑA|CONDICIONES)", line)]
        if headlines:
            latest["summary"] = headlines[0]
        alert = re.search(r"(?:Alerta|Vigilancia) de (?:El Niño|La Niña) Costero?a?", header, re.I)
        if alert:
            latest["alertStatus"] = alert[0]
        next_match = re.search(r"pr[oó]ximo Comunicado Oficial.{0,180}", clean(text), re.I)
        latest["nextReleaseDate"] = date_es(next_match[0]) if next_match else None
        latest["documentVerified"] = True
    except SourceError as exc:
        latest["documentVerified"] = False
        status, error = "partial", f"Comunicado identificado en el índice. PDF sin verificar: {exc}"
    return {"sourceDate": latest["publishedAt"], "status": status, "error": error,
            "data": {"latest": latest, "items": [latest], "scope": "Metadatos del comunicado oficial y comprobación del PDF enlazado."}}


def parse_validity(title: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"valido\s+del?\s+(\d{1,2})(?:\s+de\s+([a-z]+))?(?:\s+(?:de|del)\s+(\d{4}))?"
        r"\s+al\s+(\d{1,2})\s+de\s+([a-z]+)\s+(?:de|del)\s+(\d{4})", plain(title))
    if not match:
        return None, None
    end_month = MONTH_ES.get(match[5])
    start_month = MONTH_ES.get(match[2]) if match[2] else end_month
    if not end_month or not start_month:
        return None, None
    end_year = int(match[6])
    start_year = int(match[3]) if match[3] else end_year - (start_month > end_month)
    first = iso_date(start_year, start_month, match[1])
    last = iso_date(end_year, end_month, match[4])
    if first > last or (date.fromisoformat(last) - date.fromisoformat(first)).days > 45:
        raise SourceError("El intervalo del aviso DHN no supera la validación")
    return first, last


def parse_dhn(body: bytes | str) -> dict:
    doc = html_document(body)
    items = {}
    for heading in doc.descendants(css_class="accordion-title"):
        title = re.sub(r"\s*\[Oleaje\]\s*$", "", heading.text(), flags=re.I)
        if not re.match(r"AVISO ESPECIAL DE OLEAJE", title, re.I):
            continue
        number = re.search(r"NRO?\.?\s*(\d+)\s*[-–]\s*(\d{2}|\d{4})\b", title, re.I)
        container = heading.parent.parent if heading.parent else None
        contents = list(container.descendants(css_class="accordion-content")) if container else []
        if not number or len(contents) != 1:
            continue
        content = contents[0].text()
        issue = re.search(r"Callao,\s*(.{0,65})", content, re.I)
        published = date_es(issue[1]) if issue else None
        if not published:
            continue
        first, last = parse_validity(title)
        pdfs = [a.attrs.get("href") for a in container.descendants("a")
                if a.attrs.get("href", "").lower().endswith(".pdf")]
        pdf_url = public_url(urllib.parse.urljoin(DHN_URL, pdfs[0])) if pdfs else None
        num = f"{int(number[1]):02d}-{number[2]}"
        items[(published, int(number[1]))] = {"number": num, "title": title,
            "url": DHN_URL, "pdfUrl": pdf_url, "publishedAt": published,
            "validFrom": first, "validUntil": last,
            "summary": "Aviso de oleaje. Revisar las condiciones específicas de cada zona en la publicación oficial.",
            "documentVerified": False, "evidenceFormat": "HTML oficial"}
    if not items:
        raise SourceError("No se reconocieron avisos de oleaje con fecha en DHN")
    ordered = [items[k] for k in sorted(items, reverse=True)[:3]]
    latest = ordered[0]
    partial = not latest["validFrom"] or not latest["validUntil"]
    return {"sourceDate": latest["publishedAt"], "status": "partial" if partial else "ok",
            "error": "Aviso identificado, intervalo de vigencia sin interpretar" if partial else None,
            "data": {"latest": latest, "items": ordered,
                     "scope": "Pronóstico de oleaje. No constituye el estado oficial de apertura o cierre de un puerto."}}


def collect_dhn() -> dict:
    return parse_dhn(fetch(DHN_URL).body)


def canonical_data(value):
    """Ignore ENFEN's cache-busting refresh token in change detection."""
    if isinstance(value, dict):
        return {k: canonical_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canonical_data(v) for v in value]
    if isinstance(value, str) and value.startswith("https://enfen.imarpe.gob.pe/"):
        parsed = urllib.parse.urlsplit(value)
        query = urllib.parse.urlencode(sorted((k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k != "refresh"))
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    return value


def checksum(data: dict) -> str:
    return hashlib.sha256(json.dumps(canonical_data(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_payload(source_id: str, payload: dict, previous: dict | None = None) -> None:
    if not isinstance(payload, dict) or payload.get("status", "ok") not in ("ok", "partial"):
        raise SourceError("La fuente no produjo un resultado aceptable")
    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        raise SourceError("La respuesta no contiene datos utilizables")
    source_date = payload.get("sourceDate")
    if source_date:
        try:
            parsed = date.fromisoformat(source_date)
        except (TypeError, ValueError) as exc:
            raise SourceError("La fecha de publicación no es válida") from exc
        if parsed > datetime.now(timezone.utc).date() + timedelta(days=1):
            raise SourceError("La publicación tiene una fecha futura inesperada")
        old_date = (previous or {}).get("sourceDate")
        if old_date and source_date < old_date:
            raise SourceError("La fuente devolvió una publicación anterior a la ya conservada")
    if source_id == "acp-gatun":
        if not source_date or not all(isinstance(data.get(k), (int, float)) for k in ("neopanamaxDraftFeet", "panamaxDraftFeet", "lakeLevelFeet")):
            raise SourceError("Faltan indicadores oficiales de Gatún")
    elif source_id == "dicapi":
        ports = data.get("ports")
        if not isinstance(ports, list) or not ports or any(not isinstance(p, dict) or not all(p.get(k) is not None for k in ("id", "name", "captaincy", "status")) for p in ports):
            raise SourceError("DICAPI no devolvió instalaciones reconocibles")
    else:
        latest = data.get("latest")
        if not isinstance(latest, dict) or not latest.get("title") or not latest.get("url"):
            raise SourceError("No se identificó una publicación oficial")


def refresh_one(source_id: str, collector, previous: dict | None = None, checked_at: str | None = None) -> dict:
    checked_at = checked_at or utc_now()
    name, url = SOURCE_META[source_id]
    base = {"id": source_id, "name": name, "url": url, "checkedAt": checked_at}
    try:
        payload = collector()
        validate_payload(source_id, payload, previous)
        digest = checksum({"sourceDate": payload.get("sourceDate"), "data": payload["data"]})
        unchanged = previous and digest == previous.get("checksum")
        return {**base, "lastSuccessAt": checked_at, "status": payload.get("status", "ok"),
                "sourceDate": payload.get("sourceDate"), "error": payload.get("error"),
                "data": payload["data"], "checksum": digest,
                "changedAt": previous.get("changedAt", checked_at) if unchanged else checked_at}
    except Exception as exc:
        # Preserve the last verified record and its timestamps. Do not replace a
        # failed source with an empty object, zero, or an apparently fresh date.
        old = previous or {}
        return {**base, "lastSuccessAt": old.get("lastSuccessAt"), "status": "error",
                "sourceDate": old.get("sourceDate"),
                "error": str(exc)[:350] if isinstance(exc, SourceError) or type(exc).__name__ == "DicapiError" else f"No se pudo actualizar ({type(exc).__name__})",
                "data": old.get("data", {}), "checksum": old.get("checksum"), "changedAt": old.get("changedAt")}


def collectors() -> dict:
    from dicapi import collect_dicapi
    return {"acp-gatun": collect_gatun, "acp-advisories": collect_advisories,
            "noaa": collect_noaa, "enfen": collect_enfen, "dhn": collect_dhn,
            "dicapi": collect_dicapi}


def workflow_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run = os.environ.get("GITHUB_RUN_ID")
    if server == "https://github.com" and repo and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) and run and run.isdigit():
        return f"{server}/{repo}/actions/runs/{run}"
    return None


def build_snapshot(previous: dict | None = None, selected_collectors: dict | None = None) -> dict:
    selected_collectors = selected_collectors if selected_collectors is not None else collectors()
    old_sources = (previous or {}).get("sources", {})
    current = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        work = {pool.submit(refresh_one, key, fn, old_sources.get(key)): key for key, fn in selected_collectors.items()}
        for future in as_completed(work):
            current[work[future]] = future.result()
    return {"schemaVersion": 1, "generatedAt": utc_now(), "runUrl": workflow_url(),
            "scheduleMinutes": 60, "sources": {key: current[key] for key in SOURCE_META if key in current}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "data" / "live.json"))
    args = parser.parse_args()
    output = Path(args.output)
    previous = None
    if output.exists():
        try:
            previous = json.loads(output.read_text())
            if previous.get("schemaVersion") != 1 or not isinstance(previous.get("sources"), dict):
                raise ValueError("unknown schema")
        except (ValueError, TypeError, OSError) as exc:
            print("Se conserva el archivo existente: no se pudo validar su contenido.", file=sys.stderr)
            return 2
    result = build_snapshot(previous)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(output.name + ".tmp")
    staging.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    staging.replace(output)
    for source_id, source in result["sources"].items():
        print(json.dumps({"source": source_id, "status": source["status"], "sourceDate": source["sourceDate"], "error": source["error"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
