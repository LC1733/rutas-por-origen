"""Meaningful parser and last-good-state regressions, using small fixtures."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect_sources as c


class FakeHtmlResponse:
    url = c.ENFEN_URL
    headers = {"Content-Type": "text/html"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return b"<html>official source</html>"


class TransportTests(unittest.TestCase):
    def test_tls_verification_failure_is_identified_and_never_retried(self):
        error = c.urllib.error.URLError(c.ssl.SSLCertVerificationError(1, "certificate verify failed"))
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(c.urllib.request, "build_opener", return_value=opener), patch.object(c.time, "sleep") as sleep:
            with self.assertRaisesRegex(c.SourceError, "Error TLS.*certificado"):
                c.fetch(c.ENFEN_URL)
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_temporary_dns_error_gets_one_retry(self):
        opener = Mock()
        opener.open.side_effect = [c.urllib.error.URLError(c.socket.gaierror(c.socket.EAI_AGAIN, "Temporary failure")), FakeHtmlResponse()]
        with patch.object(c.urllib.request, "build_opener", return_value=opener), patch.object(c.time, "sleep") as sleep:
            value = c.fetch(c.ENFEN_URL)
        self.assertEqual(value.content_type, "text/html")
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_permanent_dns_error_is_identified_without_retry(self):
        opener = Mock()
        opener.open.side_effect = c.urllib.error.URLError(c.socket.gaierror(c.socket.EAI_NONAME, "Name or service not known"))
        with patch.object(c.urllib.request, "build_opener", return_value=opener), patch.object(c.time, "sleep") as sleep:
            with self.assertRaisesRegex(c.SourceError, "Error DNS: nombre no resuelto"):
                c.fetch(c.ENFEN_URL)
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_timeout_is_capped_at_two_attempts_with_useful_error(self):
        opener = Mock()
        opener.open.side_effect = c.urllib.error.URLError(TimeoutError("timed out"))
        with patch.object(c.urllib.request, "build_opener", return_value=opener), patch.object(c.time, "sleep") as sleep:
            with self.assertRaisesRegex(c.SourceError, "Tiempo de espera.*tras 2 intentos"):
                c.fetch(c.ENFEN_URL)
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_http_503_gets_one_retry_but_403_does_not(self):
        for status, count in [(503, 2), (403, 1)]:
            with self.subTest(status=status):
                opener = Mock()
                opener.open.side_effect = c.urllib.error.HTTPError(c.ENFEN_URL, status, "error", {}, None)
                with patch.object(c.urllib.request, "build_opener", return_value=opener), patch.object(c.time, "sleep") as sleep:
                    with self.assertRaisesRegex(c.SourceError, f"HTTP {status}"):
                        c.fetch(c.ENFEN_URL)
                self.assertEqual(opener.open.call_count, count)
                self.assertEqual(sleep.call_count, count - 1)

    def test_long_retry_after_is_deferred_instead_of_ignored(self):
        opener = Mock()
        opener.open.side_effect = c.urllib.error.HTTPError(c.ENFEN_URL, 429, "rate limit", {"Retry-After": "120"}, None)
        with patch.object(c.urllib.request, "build_opener", return_value=opener), patch.object(c.time, "sleep") as sleep:
            with self.assertRaisesRegex(c.SourceError, "HTTP 429.*siguiente ciclo"):
                c.fetch(c.ENFEN_URL)
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()


class SourceParserTests(unittest.TestCase):
    def test_gatun_uses_official_panel_not_projection_or_calculator(self):
        text = """87.1 ft
O fficial G atun W ater Level for N ov. 07, 2026
0.08% surcharge N eopanam ax P anam ax
49.0 ft 39.5 ft O fficial M axim um D raft
Projection 42.0 ft Calculator 37.5 ft"""
        result = c.parse_gatun(text)
        self.assertEqual(result["sourceDate"], "2026-11-07")
        self.assertEqual(result["data"]["neopanamaxDraftFeet"], 49.0)
        self.assertEqual(result["data"]["panamaxDraftFeet"], 39.5)

    def test_gatun_rejects_values_without_official_labels(self):
        with self.assertRaises(c.SourceError):
            c.parse_gatun("Projection 49.0 ft, 39.5 ft, Nov. 07, 2026")

    def test_advisories_use_id_order_without_inventing_publication_dates(self):
        page = '<a href="https://pancanal.com/a.pdf">A-39-2026 Older</a><a href="https://pancanal.com/b.pdf">A-01-2027 New year</a>'
        values = c.parse_advisories(page)
        self.assertEqual(values[0]["number"], "A-01-2027")
        self.assertIsNone(values[0]["publishedAt"])

    def test_advisory_header_date_precedes_a_later_effective_date(self):
        self.assertEqual(c.date_en("October 30, 2026. This change takes effect on 1 November 2026."), "2026-10-30")

    def test_official_index_cannot_redirect_collector_to_untrusted_host(self):
        with self.assertRaises(c.SourceError):
            c.parse_advisories('<a href="https://other.example/a.pdf">A-40-2026 Untrusted</a>')

    def test_noaa_separates_issue_date_next_release_and_monthly_anomaly(self):
        page = '''<p>CLIMATE PREDICTION CENTER/NCEP/NWS 8 October 2026</p>
<p>ENSO Alert System Status: El Niño Advisory</p>
<p>Synopsis: Warm conditions continue.</p>
<p>+3.9°C in Niño-1+2. The next ENSO Diagnostics Discussion is scheduled for 12 November 2026.</p>'''
        value = c.parse_noaa(page)
        self.assertEqual(value["sourceDate"], "2026-10-08")
        self.assertEqual(value["data"]["latest"]["nextReleaseDate"], "2026-11-12")
        self.assertEqual(value["data"]["latest"]["nino12AnomalyC"], 3.9)
        self.assertEqual(value["data"]["latest"]["summary"], "Warm conditions continue.")

    def test_enfen_cache_nonce_does_not_count_as_a_document_change(self):
        one = {"url": "https://enfen.imarpe.gob.pe/download/notice/?wpdmdl=2200&refresh=a"}
        two = {"url": "https://enfen.imarpe.gob.pe/download/notice/?refresh=b&wpdmdl=2200"}
        self.assertEqual(c.checksum(one), c.checksum(two))

    def test_enfen_reads_latest_block_not_an_arbitrary_archive_date(self):
        html = '''<p>2020 archive</p><div class="enfen_ultimo_informe">
ULTIMO COMUNICADO<br>Comunicado Oficial Enfen N° 19-2026<br><i>30 Octubre, 2026</i>
<a href="https://enfen.imarpe.gob.pe/download/notice/?wpdmdl=2200&refresh=a">Ver Comunicado</a></div>'''
        value = c.parse_enfen_index(html)
        self.assertEqual(value["number"], "19-2026")
        self.assertEqual(value["publishedAt"], "2026-10-30")

    def test_dhn_validity_handles_cross_month_and_cross_year(self):
        self.assertEqual(c.parse_validity("Válido del 03 al 08 de noviembre del 2026"), ("2026-11-03", "2026-11-08"))
        self.assertEqual(c.parse_validity("Válido del 29 de octubre al 02 de noviembre del 2026"), ("2026-10-29", "2026-11-02"))
        self.assertEqual(c.parse_validity("Válido del 30 de diciembre al 03 de enero del 2027"), ("2026-12-30", "2027-01-03"))

    def test_dhn_does_not_invent_validity_from_unrecognized_title(self):
        self.assertEqual(c.parse_validity("Aviso publicado hoy"), (None, None))


class LastGoodStateTests(unittest.TestCase):
    def setUp(self):
        self.old = {"lastSuccessAt": "2026-11-07T15:00:00Z", "sourceDate": "2026-10-08",
                    "data": {"latest": {"title": "ENSO", "url": c.NOAA_URL}},
                    "checksum": "previous-checksum", "changedAt": "2026-10-08T12:00:00Z"}

    def test_failure_preserves_last_good_data_and_success_timestamp(self):
        def fail():
            raise c.SourceError("HTTP 503")
        result = c.refresh_one("noaa", fail, self.old, "2026-11-07T16:00:00Z")
        self.assertEqual(result["status"], "error")
        for key in ("data", "lastSuccessAt", "sourceDate", "checksum", "changedAt"):
            self.assertEqual(result[key], self.old[key])
        self.assertEqual(result["checkedAt"], "2026-11-07T16:00:00Z")

    def test_empty_or_regressed_data_cannot_erase_a_newer_publication(self):
        for candidate in ({"data": {}, "sourceDate": None},
                          {"data": self.old["data"], "sourceDate": "2026-09-01"}):
            with self.subTest(candidate=candidate):
                value = c.refresh_one("noaa", lambda: candidate, self.old)
                self.assertEqual(value["status"], "error")
                self.assertEqual(value["data"], self.old["data"])
                self.assertEqual(value["sourceDate"], "2026-10-08")

    def test_unchanged_document_keeps_changed_at_but_updates_check_time(self):
        payload = {"data": self.old["data"], "sourceDate": self.old["sourceDate"]}
        self.old["checksum"] = c.checksum(payload)
        value = c.refresh_one("noaa", lambda: payload, self.old, "2026-11-07T17:00:00Z")
        self.assertEqual(value["changedAt"], self.old["changedAt"])
        self.assertEqual(value["lastSuccessAt"], "2026-11-07T17:00:00Z")

    def test_partial_data_is_explicit_and_source_failure_is_isolated(self):
        payload = {"data": self.old["data"], "sourceDate": "2026-10-08", "status": "partial", "error": "PDF unavailable"}
        result = c.build_snapshot({"sources": {"noaa": self.old}}, {
            "noaa": lambda: payload,
            "dhn": lambda: (_ for _ in ()).throw(c.SourceError("Unavailable")),
        })
        self.assertEqual(result["sources"]["noaa"]["status"], "partial")
        self.assertEqual(result["sources"]["dhn"]["status"], "error")
        self.assertIsNone(result["sources"]["dhn"]["lastSuccessAt"])


if __name__ == "__main__":
    unittest.main()
