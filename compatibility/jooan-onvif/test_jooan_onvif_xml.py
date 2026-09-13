"""Synthetic fixtures; these are NOT captured camera packets."""
import asyncio
import sys
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from jooan_onvif_xml import install, normalize

URL = "http://192.0.2.10:8899/onvif/device_service"
ENDPOINTS = {("http", "192.0.2.10", 8899)}
BODY = b'''<ns0:Envelope xmlns:ns0="http://www.w3.org/2003/05/soap-envelope"
 xmlns:ns1="http://www.onvif.org/ver10/device/wsdl"
 xmlns:ws="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
 <ns0:Header><ws:Security><ws:UsernameToken><ws:Username>example</ws:Username>
 <ws:Password Type="example-digest-type">synthetic-digest</ws:Password>
 </ws:UsernameToken></ws:Security></ns0:Header>
 <ns0:Body><ns1:GetCapabilities><ns1:Category>All</ns1:Category>
 </ns1:GetCapabilities></ns0:Body></ns0:Envelope>'''


class NormalizationTests(unittest.TestCase):
    def test_operation_and_authentication_values_preserved(self):
        args = {"data": BODY, "headers": {"Content-Length": "999", "SOAPAction": "example"}, "timeout": 10}
        result = normalize("POST", URL, args, ENDPOINTS)
        before, after = ET.fromstring(BODY), ET.fromstring(result["data"])
        describe = lambda root: [(e.tag, e.attrib, e.text) for e in root.iter()]
        self.assertEqual(describe(before), describe(after))
        self.assertIn(b"<tds:GetCapabilities>", result["data"])
        self.assertEqual(result["headers"], {"SOAPAction": "example"})
        self.assertEqual(result["timeout"], 10)
        self.assertEqual(args["headers"]["Content-Length"], "999")
        self.assertIs(args["data"], BODY)

    def test_unrelated_requests_unchanged(self):
        args = {"data": BODY}
        for method, url in [("GET", URL), ("POST", URL.replace(".10:", ".11:")),
                            ("POST", URL.replace("8899", "8971")),
                            ("POST", URL.replace("/onvif/device_service", "/api/login")),
                            ("POST", URL.replace("http:", "https:"))]:
            with self.subTest(method=method, url=url):
                self.assertIs(normalize(method, url, args, ENDPOINTS), args)
        self.assertIs(normalize("POST", URL, args, set()), args)

    def test_nonsoap_unchanged(self):
        args = {"data": b"<message/>"}
        self.assertIs(normalize("POST", URL, args, ENDPOINTS), args)

    def test_signed_xml_rejected(self):
        body = BODY.replace(b"<ns0:Header>", b'<ns0:Header><sig:Signature xmlns:sig="http://www.w3.org/2000/09/xmldsig#"/>')
        with self.assertRaisesRegex(ValueError, "Signed XML"):
            normalize("POST", URL, {"data": body}, ENDPOINTS)

    def test_qname_attribute_rejected(self):
        body = BODY.replace(b"<ns1:Category>", b'<ns1:Category xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="ns1:Example">')
        with self.assertRaisesRegex(ValueError, "QName"):
            normalize("POST", URL, {"data": body}, ENDPOINTS)

    def test_adapter_calls_original_and_restores(self):
        received = []
        class Session:
            async def _request(self, method, url, **kwargs):
                received.append(kwargs)
                return "response"
        original = Session._request
        with patch.dict(sys.modules, {"aiohttp": types.SimpleNamespace(ClientSession=Session)}):
            restore = install(ENDPOINTS)
            try:
                self.assertEqual(asyncio.run(Session()._request("POST", URL, data=BODY)), "response")
                self.assertIn(b"<tds:GetCapabilities>", received[0]["data"])
                with self.assertRaises(RuntimeError):
                    install(ENDPOINTS)
            finally:
                restore()
            self.assertIs(Session._request, original)


if __name__ == "__main__":
    unittest.main()
