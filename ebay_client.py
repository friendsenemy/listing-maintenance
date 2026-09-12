"""Minimal eBay Trading API client.

Credentials come from the environment and are never logged.
"""
import base64
import os
import re
import time
from html import unescape
from xml.sax.saxutils import escape as xml_escape

import requests

OAUTH = "https://api.ebay.com/identity/v1/oauth2/token"
TRADING = "https://api.ebay.com/ws/api.dll"

_token = {"value": None, "expires": 0}


def _env(name):
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"missing required secret: {name}")
    return v


def access_token():
    """Cached until five minutes before expiry."""
    if _token["value"] and time.time() < _token["expires"] - 300:
        return _token["value"]
    basic = base64.b64encode(
        f"{_env('EBAY_CLIENT_ID')}:{_env('EBAY_CLIENT_SECRET')}".encode()
    ).decode()
    r = _post(
        OAUTH,
        data={"grant_type": "refresh_token", "refresh_token": _env("EBAY_REFRESH_TOKEN")},
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    body = r.json()
    if "access_token" not in body:
        raise SystemExit("could not mint an access token; the refresh token may have been rotated")
    _token["value"] = body["access_token"]
    _token["expires"] = time.time() + int(body.get("expires_in", 7200))
    return _token["value"]


def _post(url, *, data=None, headers=None, attempts=5):
    """POST with backoff. A run of several hundred calls will meet a dropped
    connection eventually; failing the whole job over one is not acceptable."""
    delay, last = 2, None
    for n in range(attempts):
        try:
            r = requests.post(url, data=data, headers=headers, timeout=60)
            if r.status_code < 500:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as exc:
            last = type(exc).__name__
        if n < attempts - 1:
            time.sleep(delay)
            delay = min(delay * 2, 30)
    raise RuntimeError(f"request failed after {attempts} attempts: {last}")


def xe(value):
    return xml_escape(str(value))


def call(name, inner, attempts=5):
    """One Trading API call. Returns (ack, xml, [(severity, code, message)])."""
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<{name}Request xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<ErrorLanguage>en_US</ErrorLanguage><WarningLevel>High</WarningLevel>"
        f"{inner}</{name}Request>"
    )
    r = _post(
        TRADING,
        data=envelope.encode("utf-8"),
        headers={
            "X-EBAY-API-COMPATIBILITY-LEVEL": "1193",
            "X-EBAY-API-CALL-NAME": name,
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-IAF-TOKEN": access_token(),
            "Content-Type": "text/xml",
        },
        attempts=attempts,
    )
    xml = r.text
    ack = (re.search(r"<Ack>(\w+)</Ack>", xml) or [None, "Unknown"])[1]
    errors = []
    for block in re.findall(r"<Errors>(.*?)</Errors>", xml, re.S):
        pick = lambda tag, d="": (
            re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S) or ["", d]
        )[1]
        errors.append((pick("SeverityCode", "?"), pick("ErrorCode", "?"),
                       unescape(pick("ShortMessage")).strip()))
    return ack, xml, errors


def specifics_xml(pairs):
    """Item specifics block. eBay REPLACES the whole set on a revise, so this
    must always be built from the full merged set, never a partial one."""
    rows = []
    for name, value in pairs.items():
        values = value if isinstance(value, (list, tuple)) else [value]
        joined = "".join(f"<Value>{xe(v)}</Value>" for v in values)
        rows.append(f"<NameValueList><Name>{xe(name)}</Name>{joined}</NameValueList>")
    return f"<ItemSpecifics>{''.join(rows)}</ItemSpecifics>" if rows else ""


def tag(xml, name, default=""):
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", xml, re.S)
    return unescape(m.group(1)) if m else default


def read_item(item_id):
    """Returns (title, {specific: [values]}) or (None, None)."""
    ack, xml, _ = call(
        "GetItem",
        f"<ItemID>{xe(item_id)}</ItemID><IncludeItemSpecifics>true</IncludeItemSpecifics>",
    )
    if ack not in ("Success", "Warning"):
        return None, None
    specifics = {}
    block = re.search(r"<ItemSpecifics>(.*?)</ItemSpecifics>", xml, re.S)
    if block:
        for name, rest in re.findall(
            r"<Name>(.*?)</Name>(.*?)</NameValueList>", block.group(1), re.S
        ):
            specifics[unescape(name)] = [
                unescape(v) for v in re.findall(r"<Value>(.*?)</Value>", rest)
            ]
    return tag(xml, "Title"), specifics
