"""Opt-in reference workaround; see README for evidence and limitations."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

NAMESPACES = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
}


def normalize(method, url, kwargs, endpoints):
    """Rewrite only explicitly selected HTTP SOAP 1.2 ONVIF POST requests.

    endpoints contains (scheme, hostname, port) tuples. No network or file I/O.
    Nonmatching requests are returned unchanged, including object identity.
    """
    address = urlsplit(str(url))
    if (method.upper() != "POST" or address.scheme != "http"
            or (address.scheme, address.hostname, address.port) not in endpoints
            or not address.path.startswith("/onvif/")
            or address.username is not None):
        return kwargs
    body = kwargs.get("data")
    if not isinstance(body, (str, bytes)):
        return kwargs
    if len(body) > 1024 * 1024:
        raise ValueError("SOAP request exceeds reference implementation limit")
    root = ET.fromstring(body)
    if root.tag != "{" + NAMESPACES["s"] + "}Envelope":
        return kwargs
    for element in root.iter():
        if element.tag == "{http://www.w3.org/2000/09/xmldsig#}Signature":
            raise ValueError("Signed XML must not be normalized")
        if "{http://www.w3.org/2001/XMLSchema-instance}type" in element.attrib:
            raise ValueError("QName-valued xsi:type needs a namespace-aware serializer")
    for prefix, namespace in NAMESPACES.items():
        ET.register_namespace(prefix, namespace)
    result = dict(kwargs)
    result["data"] = ET.tostring(root, encoding="utf-8")
    result["headers"] = {
        key: value for key, value in (kwargs.get("headers") or {}).items()
        if key.lower() != "content-length"
    }
    return result


def install(endpoints):
    """Install in this process explicitly; return a restoration callable.

    Intended for isolated experiments, not an upstream integration API.
    aiohttp is imported only when the adapter is requested.
    """
    import aiohttp

    endpoints = frozenset(endpoints)
    if not endpoints:
        raise ValueError("An explicit endpoint allowlist is required")
    original = aiohttp.ClientSession._request
    if getattr(original, "_jooan_reference_patch", False):
        raise RuntimeError("Reference patch is already installed")

    async def request(self, method, url, **kwargs):
        return await original(self, method, url, **normalize(method, url, kwargs, endpoints))

    request._jooan_reference_patch = True
    aiohttp.ClientSession._request = request

    def restore():
        if aiohttp.ClientSession._request is not request:
            raise RuntimeError("HTTP adapter changed since installation")
        aiohttp.ClientSession._request = original

    return restore
