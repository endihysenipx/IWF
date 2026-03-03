import io
import re
import uuid
import json
import base64
import gzip
import zipfile
import requests

from app.config import (
    IWF_API_URL, IWF_API_EMAIL, IWF_API_PASSWORD,
    IWF_MESSAGE_TYPE, IWF_SUPPLIER_GLN, IWF_BUYER_GLN,
)


def _clean_document_no(value):
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if any(ch.isalpha() for ch in text):
        if "AB" in text.upper():
            return ""
    if text.isdigit():
        return text
    digits_only = re.sub(r"\D", "", text)
    if 6 <= len(digits_only) <= 12:
        return digits_only
    return ""


def extract_document_no_from_ab(ab_data):
    if not ab_data:
        return ""
    order_refs = ab_data.get("order_references", {}) or {}
    doc_info = ab_data.get("document_info", {}) or {}
    for val in (order_refs.get("your_order_number"), doc_info.get("document_number")):
        cleaned = _clean_document_no(val)
        if cleaned:
            return cleaned
    return ""


def _pick_first_document(api_json):
    if isinstance(api_json, list) and api_json:
        return api_json[0]
    if isinstance(api_json, dict):
        obj = api_json.get("Object")
        if isinstance(obj, dict):
            if isinstance(obj.get("Documents"), list) and obj["Documents"]:
                return obj["Documents"][0]
        if isinstance(api_json.get("Documents"), list) and api_json["Documents"]:
            return api_json["Documents"][0]
        if "Data" in api_json:
            return api_json
        for key in ("Result", "result", "data", "Data"):
            val = api_json.get(key)
            if isinstance(val, list) and val:
                return val[0]
            if isinstance(val, dict) and "Data" in val:
                return val
    return None


def _decode_document_data(data_value):
    if data_value is None:
        return b""
    if isinstance(data_value, bytes):
        raw = data_value
    else:
        text = str(data_value).strip()
        if text.startswith("b'") or text.startswith('b"'):
            text = text[2:-1] if len(text) >= 3 else ""
        if text.lstrip().startswith("<"):
            return text.encode("utf-8")
        compact = "".join(text.split())
        try:
            raw = base64.b64decode(compact)
        except Exception:
            raw = base64.b64decode(text)

    if raw.startswith(b"\x1f\x8b"):
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    if raw.startswith(b"PK\x03\x04") or raw.startswith(b"PK\x05\x06") or raw.startswith(b"PK\x07\x08"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = zf.namelist()
                if names:
                    xml_name = next((n for n in names if n.lower().endswith(".xml")), names[0])
                    raw = zf.read(xml_name)
        except Exception:
            pass
    return raw


def _summarize_api_response(api_json):
    if isinstance(api_json, dict):
        for key in ("ErrorMessage", "Message", "StatusMessage", "Status", "ResultMessage"):
            val = api_json.get(key)
            if val:
                return f"{key}: {val}"
        if isinstance(api_json.get("Errors"), list) and api_json["Errors"]:
            return f"Errors: {api_json['Errors']}"
        if isinstance(api_json.get("Object"), dict):
            return f"Object keys: {', '.join(api_json['Object'].keys())}"
        return f"keys: {', '.join(api_json.keys())}"
    if isinstance(api_json, list):
        return f"list[{len(api_json)}]"
    return f"type: {type(api_json).__name__}"


def _write_api_debug(api_json):
    try:
        with open("temp_api_response.json", "w", encoding="utf-8") as f:
            json.dump(api_json, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def fetch_order_xml_from_api(document_no):
    if not document_no:
        raise ValueError("Missing document number from AB.")

    payload = {
        "RequestOID": str(uuid.uuid4()),
        "Email": IWF_API_EMAIL,
        "Password": IWF_API_PASSWORD,
        "MessageType": IWF_MESSAGE_TYPE,
        "SupplierGLN": IWF_SUPPLIER_GLN,
        "BuyerGLN": IWF_BUYER_GLN,
        "DocumentNo": document_no,
    }

    response = requests.post(IWF_API_URL, json=payload, timeout=30)
    response.raise_for_status()
    try:
        api_json = response.json()
    except Exception:
        snippet = response.text[:500] if response.text else ""
        raise ValueError(f"Non-JSON API response (status {response.status_code}). {snippet}")
    doc = _pick_first_document(api_json)
    if not doc:
        _write_api_debug(api_json)
        summary = _summarize_api_response(api_json)
        raise ValueError(f"No document found in API response. {summary}")

    xml_bytes = _decode_document_data(doc.get("Data"))
    if not xml_bytes:
        raise ValueError("Empty document data from API response.")

    return xml_bytes
