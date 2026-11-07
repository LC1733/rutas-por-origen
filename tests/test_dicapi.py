"""Focused contract checks for summary/history disagreements and source failure."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import urllib.error


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dicapi.py"
spec = importlib.util.spec_from_file_location("dicapi_under_test", MODULE_PATH)
dicapi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dicapi)


def payload(rows, **metadata):
    return {"status": 0, "data": rows, **metadata}


def current_row(port_id, captaincy_id, level=1):
    return {
        "portId": port_id,
        "port": "Bahía prueba" if captaincy_id == 1 else "Muelle prueba",
        "captaincyId": captaincy_id,
        "levelId": level,
        "levelName": "Puerto Abierto" if level == 1 else "Cierre Parcial del Puerto",
        "level": "Nivel I" if level == 1 else "Nivel III",
        "publication_date": "2026-10-31T14:00:00.000Z",
        "updated_at": "2026-10-31T14:44:01.000Z",
        "resolution_number": "RC 269",
        "resolution_url": dicapi.STORAGE_URL + "resolution.pdf",
    }


def history_row(level=1, date="2026-10-31T14:00:00.000Z"):
    return {
        "id": 36100,
        "levelId": level,
        "level": {"name": "Puerto Abierto" if level == 1 else "Cierre Parcial del Puerto", "level": "Nivel I" if level == 1 else "Nivel III"},
        "publicationDate": date,
        "resolutionNumber": "RC 269",
        "resolutionUrl": "resolution.pdf",
        "reason": None,
    }


def fake_fetch(url):
    if "/captaincies?" in url:
        return payload([{"id": 1, "title": "Callao", "slug": "callao"}, {"id": 7, "title": "Chancay", "slug": "chancay"}])
    if "captaincy=1" in url:
        return payload([current_row(46, 1)])
    if "captaincy=7" in url:
        return payload([current_row(161, 7)])
    if "portId=46&" in url:
        return payload([history_row(3)], totalRecords=247, currentPage=1)
    if "portId=161&" in url:
        return payload([history_row(1)], totalRecords=40, currentPage=1)
    raise AssertionError(url)


class DicapiTests(unittest.TestCase):
    def test_transient_http_error_gets_only_one_retry(self):
        url = dicapi.BASE_URL + "/ports?captaincy=1"
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = url
        response.headers.get_content_type.return_value = "application/json"
        response.read.return_value = b'{"status":0,"data":[]}'
        opener = MagicMock()
        opener.open.side_effect = [urllib.error.HTTPError(url, 503, "Unavailable", {}, None), response]
        with patch.object(dicapi.urllib.request, "build_opener", return_value=opener), patch.object(dicapi.time, "sleep"):
            result = dicapi._read_json(url)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(result["data"], [])

    def test_access_denial_is_not_retried(self):
        url = dicapi.BASE_URL + "/ports?captaincy=1"
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        with patch.object(dicapi.urllib.request, "build_opener", return_value=opener), patch.object(dicapi.time, "sleep"):
            with self.assertRaises(dicapi.DicapiError):
                dicapi._read_json(url)
        self.assertEqual(opener.open.call_count, 1)

    def test_disagreement_keeps_both_states_and_partial_status(self):
        with patch.object(dicapi, "_read_json", side_effect=fake_fetch):
            result = dicapi.collect_dicapi()
        self.assertIsNone(result["sourceDate"])
        self.assertEqual(result["status"], "partial")
        port = result["data"]["ports"][0]
        self.assertEqual(port["status"], "Puerto Abierto")
        self.assertEqual(port["latest"]["status"], "Cierre Parcial del Puerto")
        self.assertTrue(port["discrepancy"])
        self.assertFalse(port["latest"]["resolutionVerified"])
        self.assertEqual(port["historyTotalRecords"], 247)
        self.assertEqual(result["data"]["discrepancyCount"], 1)

    def test_history_failure_does_not_turn_into_green_or_empty_source(self):
        def fetch(url):
            if "portId=161&" in url:
                raise dicapi.DicapiError("HTTP 503")
            return fake_fetch(url)
        with patch.object(dicapi, "_read_json", side_effect=fetch):
            result = dicapi.collect_dicapi()
        port = next(p for p in result["data"]["ports"] if p["id"] == 161)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(port["status"], "Puerto Abierto")
        self.assertIsNone(port["latest"])
        self.assertEqual(port["historyStatus"], "failed")
        self.assertTrue(port["needsValidation"])

    def test_empty_history_is_explicit(self):
        port = dicapi._current_port(current_row(46, 1), {"id": 1, "name": "Callao"})
        dicapi._add_history(port, payload([], totalRecords=0))
        self.assertEqual(port["historyStatus"], "empty")
        self.assertTrue(port["needsValidation"])
        self.assertIsNone(port["latest"])

    def test_unordered_history_does_not_claim_order_confirmed(self):
        port = dicapi._current_port(current_row(46, 1), {"id": 1, "name": "Callao"})
        dicapi._add_history(port, payload([history_row(1, "2026-10-01T10:00:00Z"), history_row(3)]))
        self.assertEqual(port["historyStatus"], "unordered")
        self.assertEqual(port["latest"]["levelId"], 3)
        self.assertTrue(port["needsValidation"])

    def test_current_failure_raises_for_wrapper_to_keep_previous_data(self):
        def fetch(url):
            if "captaincy=7" in url:
                raise dicapi.DicapiError("HTTP 503")
            return fake_fetch(url)
        with patch.object(dicapi, "_read_json", side_effect=fetch):
            with self.assertRaises(dicapi.DicapiError):
                dicapi.collect_dicapi()

    def test_resolution_links_and_redirects_stay_on_official_source(self):
        for url in ["http://www.dicapi.mil.pe/api/ports", "https://example.com/api/ports", "https://www.dicapi.mil.pe@evil.example/api/ports"]:
            with self.assertRaises(dicapi.DicapiError):
                dicapi._check_api_url(url)
        for value in ["https://example.com/secret.pdf", "../../outside.pdf", "javascript:alert(1)"]:
            with self.assertRaises(dicapi.DicapiError):
                dicapi._resolution_url(value)
        self.assertEqual(dicapi._resolution_url("resolution.pdf"), dicapi.STORAGE_URL + "resolution.pdf")


if __name__ == "__main__":
    unittest.main()
