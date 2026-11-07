"""Read the public JSON used by DICAPI's Estado de Puertos page.

The endpoint URLs and field names were observed in DICAPI's own public frontend,
then verified with unauthenticated HTTP GET requests on 2026-11-07. This is a
website data interface, not a documented API contract. Never collapse a port's
summary state and its history into one supposedly authoritative state.

No PDFs are fetched or interpreted here. A resolution URL is only a link.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://www.dicapi.mil.pe/api"
PAGE_URL = "https://www.dicapi.mil.pe/estado-de-puertos"
STORAGE_URL = "https://www.dicapi.mil.pe/storage/state-ports/"
TARGETS = ("callao", "chancay")
TIMEOUT_SECONDS = 25
MAX_RESPONSE_BYTES = 8_000_000
MAX_WORKERS = 4
HISTORY_PAGE_SIZE = 10
USER_AGENT = (
    "MARTILINE-Global-Control/2.0 "
    "(+https://github.com/LC1733/rutas-por-origen)"
)


class DicapiError(RuntimeError):
    """A source failure that must not replace a previous usable snapshot."""


def _check_api_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.dicapi.mil.pe"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/api/")
    ):
        raise DicapiError("La fuente redirigió fuera del endpoint público permitido.")


class _SameSourceRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_api_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_json(url: str) -> dict[str, Any]:
    """Bounded, unauthenticated GET. Fail closed on HTML/login/schema changes."""
    _check_api_url(url)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    opener = urllib.request.build_opener(_SameSourceRedirect())
    # A real probe returned an intermittent 503 for a previously readable row.
    # Retry only one public GET, and never retry access denial or a schema error.
    for attempt in range(2):
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                _check_api_url(response.url)
                content_type = response.headers.get_content_type().lower()
                if content_type != "application/json" and not content_type.endswith("+json"):
                    raise DicapiError("DICAPI no devolvió JSON público.")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if attempt == 0 and exc.code in (429, 502, 503, 504):
                time.sleep(1)
                continue
            raise DicapiError(f"DICAPI respondió HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 0:
                time.sleep(1)
                continue
            raise DicapiError(f"No se pudo consultar DICAPI: {type(exc).__name__}.") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DicapiError("La respuesta DICAPI supera el límite de tamaño.")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DicapiError("La respuesta DICAPI no es JSON válido.") from exc
    if (
        not isinstance(result, dict)
        or type(result.get("status")) is not int
        or result["status"] != 0
        or not isinstance(result.get("data"), list)
    ):
        raise DicapiError("Cambió el esquema o DICAPI informó un error de datos.")
    return result


def _text(value: Any, maximum: int = 2000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DicapiError("Un campo de texto DICAPI cambió de formato.")
    value = value.strip()
    if len(value) > maximum:
        raise DicapiError("Un campo de texto DICAPI supera el tamaño esperado.")
    return value or None


def _positive_id(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise DicapiError("DICAPI no entregó un identificador válido.")
    return value


def _timestamp(value: Any) -> str | None:
    """Keep the source's explicit timezone; do not invent a local offset."""
    value = _text(value, 80)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DicapiError("DICAPI entregó una fecha no reconocida.") from exc
    if parsed.tzinfo is None:
        raise DicapiError("La fecha DICAPI no identifica su zona horaria.")
    return value


def _parsed_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _resolution_url(value: Any) -> str | None:
    value = _text(value, 2048)
    if not value:
        return None
    url = urllib.parse.urljoin(STORAGE_URL, value)
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.dicapi.mil.pe"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/storage/state-ports/")
        or not parsed.path.lower().endswith(".pdf")
    ):
        raise DicapiError("El enlace a resolución no corresponde al depósito oficial esperado.")
    return url


def _current_port(row: dict[str, Any], captaincy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise DicapiError("DICAPI devolvió una instalación sin estructura válida.")
    port_id = _positive_id(row.get("portId"))
    if row.get("captaincyId") != captaincy["id"]:
        raise DicapiError("La respuesta DICAPI no corresponde a la capitanía solicitada.")
    name = _text(row.get("port"), 500)
    state = _text(row.get("levelName"), 250)
    level = _text(row.get("level"), 100)
    level_id = row.get("levelId")
    if not name or not state or not level or type(level_id) is not int or level_id not in range(1, 6):
        raise DicapiError("Una instalación DICAPI carece de nombre o estado reconocido.")
    source_url = f"{BASE_URL}/ports?captaincy={captaincy['id']}"
    history_url = (
        f"{BASE_URL}/ports?history=true&portId={port_id}"
        f"&page=1&pageSize={HISTORY_PAGE_SIZE}"
    )
    return {
        "id": port_id,
        "name": name,
        "captaincy": captaincy["name"],
        "captaincyId": captaincy["id"],
        "status": state,
        "level": level,
        "levelId": level_id,
        "publishedAt": _timestamp(row.get("publication_date")),
        "updatedAt": _timestamp(row.get("updated_at")),
        "resolutionNumber": _text(row.get("resolution_number"), 300),
        "resolutionUrl": _resolution_url(row.get("resolution_url")),
        "resolutionVerified": False,
        "latest": None,
        "discrepancy": False,
        "needsValidation": False,
        "historyStatus": "pending",
        "historyScope": "Primera página del historial oficial, hasta 10 registros.",
        "historySampleSize": 0,
        "historyTotalRecords": None,
        "issues": [],
        "sourceUrl": source_url,
        "historyUrl": history_url,
    }


def _history_record(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict) or not isinstance(row.get("level"), dict):
        raise DicapiError("El historial DICAPI cambió de estructura.")
    level_id = row.get("levelId")
    state = _text(row["level"].get("name"), 250)
    level = _text(row["level"].get("level"), 100)
    published = _timestamp(row.get("publicationDate"))
    if type(level_id) is not int or level_id not in range(1, 6) or not state or not level or not published:
        raise DicapiError("Un registro histórico no contiene estado y fecha válidos.")
    return {
        "id": _positive_id(row.get("id")),
        "status": state,
        "level": level,
        "levelId": level_id,
        "reason": _text(row.get("reason"), 5000),
        "resolutionNumber": _text(row.get("resolutionNumber"), 300),
        "publishedAt": published,
        "resolutionUrl": _resolution_url(row.get("resolutionUrl")),
        "resolutionVerified": False,
    }


def _add_history(port: dict[str, Any], payload: dict[str, Any]) -> None:
    """Mutate one normalized port, keeping summary and history separately."""
    records = payload["data"]
    if len(records) > HISTORY_PAGE_SIZE:
        raise DicapiError("El endpoint ignoró la paginación de historial esperada.")
    port["historySampleSize"] = len(records)
    total = payload.get("totalRecords")
    if type(total) is int and total >= 0:
        port["historyTotalRecords"] = total
    if not records:
        port["historyStatus"] = "empty"
        port["needsValidation"] = True
        port["issues"].append("El portal no devolvió un registro histórico para esta instalación.")
        return
    history = [_history_record(row) for row in records]
    timestamps = [_parsed_date(row["publishedAt"]) for row in history]
    ordered = all(a >= b for a, b in zip(timestamps, timestamps[1:]))
    # The UI presents this page first. Check its observed ordering, and explicitly
    # retain the page scope instead of claiming we downloaded the full archive.
    index = max(range(len(history)), key=lambda i: timestamps[i])
    latest = history[index]
    port["latest"] = latest
    port["historyStatus"] = "ok" if ordered else "unordered"
    if not ordered:
        port["needsValidation"] = True
        port["issues"].append(
            "La primera página no está en orden de fecha. Se muestra el registro más reciente de esa página, sujeto a validación."
        )
    differences = []
    if port["levelId"] != latest["levelId"]:
        differences.append("el estado/nivel")
    if port["publishedAt"] and _parsed_date(port["publishedAt"]) != _parsed_date(latest["publishedAt"]):
        differences.append("la fecha")
    if port["resolutionNumber"] and latest["resolutionNumber"]:
        normalize = lambda text: "".join(text.upper().split())
        if normalize(port["resolutionNumber"]) != normalize(latest["resolutionNumber"]):
            differences.append("el número de resolución")
    if port["resolutionUrl"] and latest["resolutionUrl"] and port["resolutionUrl"] != latest["resolutionUrl"]:
        differences.append("el documento enlazado")
    if differences:
        port["discrepancy"] = True
        port["needsValidation"] = True
        port["issues"].append(
            "El resumen y el historial difieren en " + ", ".join(differences)
            + ". Confirmar el alcance y la vigencia con capitanía/terminal."
        )
    if not latest["resolutionNumber"] or not latest["resolutionUrl"]:
        port["needsValidation"] = True
        port["issues"].append("El último registro no incluye número y enlace de resolución completos.")


def collect_dicapi() -> dict[str, Any]:
    """Return source data only; the caller owns timestamps, caching and files.

    A failed current-state query raises, so the caller can preserve the previous
    complete source with an error label. Failed histories keep the checked
    current states, with explicit partial status and per-installation warnings.
    """
    payload = _read_json(f"{BASE_URL}/captaincies?pageSize=100")
    captaincies = []
    for slug in TARGETS:
        matches = [row for row in payload["data"] if isinstance(row, dict) and row.get("slug") == slug]
        if len(matches) != 1:
            raise DicapiError(f"No se identificó de forma única la capitanía {slug}.")
        row = matches[0]
        name = _text(row.get("title"), 200)
        if not name:
            raise DicapiError("La capitanía DICAPI carece de nombre.")
        captaincies.append({"id": _positive_id(row.get("id")), "name": name, "slug": slug})

    ports = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        current_requests = {
            pool.submit(_read_json, f"{BASE_URL}/ports?captaincy={captaincy['id']}"): captaincy
            for captaincy in captaincies
        }
        for future in as_completed(current_requests):
            captaincy = current_requests[future]
            rows = future.result()["data"]
            if not rows or len(rows) > 500:
                raise DicapiError(f"La lista de instalaciones de {captaincy['name']} está vacía o no es válida.")
            ports.extend(_current_port(row, captaincy) for row in rows)
    if len({port["id"] for port in ports}) != len(ports):
        raise DicapiError("La respuesta DICAPI repite identificadores de instalación.")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        history_requests = {pool.submit(_read_json, port["historyUrl"]): port for port in ports}
        for future in as_completed(history_requests):
            port = history_requests[future]
            try:
                _add_history(port, future.result())
            except (DicapiError, ValueError, TypeError, KeyError) as exc:
                port["latest"] = None
                port["historyStatus"] = "failed"
                port["needsValidation"] = True
                port["issues"].append(f"No se pudo comprobar el historial: {exc}")

    ports.sort(key=lambda port: (port["captaincy"].casefold(), port["name"].casefold(), port["id"]))
    groups = []
    for captaincy in captaincies:
        group_ports = [port for port in ports if port["captaincyId"] == captaincy["id"]]
        groups.append({
            **captaincy,
            "total": len(group_ports),
            "open": sum(port["levelId"] == 1 for port in group_ports),
            "partiallyClosed": sum(port["levelId"] in (2, 3, 4) for port in group_ports),
            "closed": sum(port["levelId"] == 5 for port in group_ports),
            "discrepancies": sum(port["discrepancy"] for port in group_ports),
        })
    conflict_count = sum(port["discrepancy"] for port in ports)
    missing_history = sum(port["historyStatus"] != "ok" for port in ports)
    requires_validation = sum(port["needsValidation"] for port in ports)
    warnings = [
        "Estado por instalación según el portal. No extender un estado a toda la capitanía o a todos los muelles.",
        "Las resoluciones están enlazadas; este proceso no comprueba el contenido ni el alcance jurídico de los PDF.",
        "Interfaz pública utilizada por la web oficial, sin contrato de API documentado. Su formato puede cambiar.",
        "El historial consultado es su primera página (hasta 10 registros); se comprueba el orden de fechas de esa página.",
    ]
    problems = []
    if conflict_count:
        problems.append(f"{conflict_count} instalación(es) con diferencias entre resumen e historial")
    if missing_history:
        problems.append(f"{missing_history} historial(es) incompleto(s) o sin orden confirmado")
    if requires_validation and not problems:
        problems.append(f"{requires_validation} instalación(es) con datos de resolución incompletos")
    return {
        "sourceDate": None,
        "status": "partial" if requires_validation else "ok",
        "error": "; ".join(problems) + "." if problems else None,
        "data": {
            "ports": ports,
            "captaincies": groups,
            "warnings": warnings,
            "dateNote": "La fuente entrega fechas por instalación, sin un corte oficial único para esta consulta.",
            "timezoneNote": "Las fechas JSON incluyen Z (UTC). La interfaz puede mostrarlas en hora de Perú.",
            "historyLabel": "Registro más reciente devuelto en la primera página del historial",
            "publicPageUrl": PAGE_URL,
            "discrepancyCount": conflict_count,
            "incompleteHistoryCount": missing_history,
        },
    }


if __name__ == "__main__":
    print(json.dumps(collect_dicapi(), ensure_ascii=False, indent=2))
