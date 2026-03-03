from flask import Flask, render_template_string, request, send_file, redirect, url_for
import xml.etree.ElementTree as ET
import pandas as pd
import io
import os
import json
import base64
import datetime
import uuid
import gzip
import re
import zipfile
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.text import MIMEText
from pdf2image import convert_from_path
from openai import OpenAI

app = Flask(__name__)

# ----------------------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------------------
TEMP_FILE_PATH = "temp_uploaded_order.xml"
TEMP_PDF_PATH = "temp_incoming_ab.pdf"

# CRUCIAL: Set this to your local poppler bin folder
POPPLER_PATH = r"C:\Users\Admin\Downloads\Release-25.12.0-0\poppler-25.12.0\Library\bin"
# 0 means all pages
MAX_AB_PAGES = int(os.getenv("MAX_AB_PAGES", "0"))

# SMTP Settings (Technical Sender)
EMAIL_ADDRESS = "00primex.eu@gmail.com" 
EMAIL_PASSWORD = "oovm iwnc bzul pzfw"
EMAIL_RECEIVER = "00primex.eu@gmail.com"

# API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# IWF API (ORDERS lookup)
IWF_API_URL = os.getenv("IWF_API_URL", "https://www.iwofurn.com/addvityapi/api/Documents/FindDocuments")
IWF_API_EMAIL = os.getenv("IWF_API_EMAIL", "Testapi.Wetzel@iwofurn.com")
IWF_API_PASSWORD = os.getenv("IWF_API_PASSWORD", "IWOfurn2025!")
IWF_MESSAGE_TYPE = os.getenv("IWF_MESSAGE_TYPE", "ORDERS")
IWF_SUPPLIER_GLN = os.getenv("IWF_SUPPLIER_GLN", "4031865000009")
IWF_BUYER_GLN = os.getenv("IWF_BUYER_GLN", "4260129840000")

# ----------------------------------------------------------------------------------
# 1. HELPER FUNCTIONS
# ----------------------------------------------------------------------------------
def date_to_xml_fmt(date_str):
    if not date_str: return ""
    date_str = str(date_str).strip()
    try:
        dt = datetime.datetime.strptime(date_str, "%d.%m.%Y")
        return dt.strftime("%Y%m%d")
    except:
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%Y%m%d")
        except:
            return ""

def clean_money_string(value_str):
    if not value_str: return "0.00"
    clean = str(value_str).upper().replace("EUR", "").replace("€", "").strip()
    if "," in clean and "." in clean:
        if clean.find(",") > clean.find("."): clean = clean.replace(".", "").replace(",", ".")
        else: clean = clean.replace(",", "")
    elif "," in clean: clean = clean.replace(",", ".")
    return clean

def normalize_decimal(val, decimals=2):
    """Normalize number strings to dot decimals with fixed precision."""
    try:
        num = float(clean_money_string(val))
    except Exception:
        num = 0.0
    return f"{num:.{decimals}f}"

def date_to_xml_fmt_or_empty(date_str):
    """Convert common date formats to YYYYMMDD, return empty string if not parseable."""
    if not date_str: return ""
    date_str = str(date_str).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(date_str, fmt).strftime("%Y%m%d")
        except Exception:
            continue
    return ""

def to_float_safe(val):
    try:
        return float(clean_money_string(val))
    except:
        return 0.0

def has_text(val):
    return val is not None and str(val).strip() != ""

def to_float_optional(val):
    if not has_text(val):
        return None
    try:
        return float(clean_money_string(val))
    except Exception:
        return None

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

def parse_edifact_orders(m_orders):
    """Parse EDIFACT-like ORDERS structure (List/M_ORDERS) into header, parties, items."""
    def ft(elem, path, default=""):
        found = elem.find(path)
        return found.text.strip() if found is not None and found.text else default

    # Document header
    doc_number = ft(m_orders, './S_BGM/C_C106/D_1004')
    doc_date = ""
    delivery_week = ""
    for dtm in m_orders.findall('./S_DTM'):
        code = ft(dtm, './C_C507/D_2005')
        val = ft(dtm, './C_C507/D_2380')
        if code == "137" and val:
            doc_date = val
        elif code == "64" and val:
            delivery_week = val
    header = {
        "document_number": doc_number,
        "document_date": doc_date,
        "commission": doc_number,
        "delivery_week": delivery_week,
    }

    # Parties
    parties = {}
    for group in m_orders.findall('./G_SG2'):
        nad = group.find('./S_NAD')
        if nad is None:
            continue
        role = ft(nad, './D_3035')
        vat_id = ""
        for rff in group.findall('./G_SG3/S_RFF_2'):
            if ft(rff, './C_C506_2/D_1153_2') == "VA":
                vat_id = ft(rff, './C_C506_2/D_1154_2')
                if vat_id:
                    break
        parties[role] = {
            "name": ft(nad, './C_C080/D_3036'),
            "street": ft(nad, './C_C059/D_3042'),
            "zip": ft(nad, './D_3251'),
            "city": ft(nad, './D_3164'),
            "country": ft(nad, './D_3207'),
            "gln": ft(nad, './C_C082/D_3039'),
            "vat_id": vat_id,
        }

    # Items
    items = []
    for lin in m_orders.findall('.//G_SG28'):
        line_number = ft(lin, './S_LIN/D_1082')
        gtin = ft(lin, './S_LIN/C_C212/D_7140')
        supplier_num = ""
        customer_num = ""
        for pia in lin.findall('./S_PIA'):
            qual = ft(pia, './C_C212_2/D_7143_4')
            val = ft(pia, './C_C212_2/D_7140_2')
            if qual == "SA" and val:
                supplier_num = val
            elif qual == "IN" and val:
                customer_num = val
        qty = ft(lin, './S_QTY_2/C_C186_2/D_6060_2')
        unit = ft(lin, './S_QTY_2/C_C186_2/D_6411_8')
        line_text = ft(lin, './S_FTX_2/C_C108_2/D_4440_6')
        items.append({
            "line_number": line_number,
            "gtin": gtin,
            "supplier_article_number": supplier_num,
            "customer_article_number": customer_num,
            "quantity": qty,
            "quantity_unit": unit,
            "line_text": line_text,
        })

    return header, parties, items

# ----------------------------------------------------------------------------------
# 2. XML PARSING (Refined for PDF Roles)
# ----------------------------------------------------------------------------------
def parse_order_xml(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    orders = root.find('.//ORDERS')
    if orders is None:
        # Try EDIFACT-like structure (List/M_ORDERS)
        m_orders = root.find('.//M_ORDERS')
        if m_orders is None:
            raise ValueError("Invalid XML: missing ORDERS element")
        return parse_edifact_orders(m_orders)
    head = orders.find('HEAD')
    if head is None:
        raise ValueError("Invalid XML: missing ORDERS/HEAD element")
    
    header = {
        "document_number": head.findtext('DocumentNumber'),
        "document_date": head.findtext('DocumentDate'),
        "commission": head.findtext('Commission'),
        "delivery_week": head.findtext('./AdditionalDate'),
    }

    # Extract Parties mapped by Role (BY=Buyer, SU=Supplier, DP=DeliveryParty)
    parties = {}
    for nad in orders.findall('.//NAD'):
        role = nad.findtext('FlagOfParty')
        party_obj = {
            "name": nad.findtext('Name1') or "",
            "street": nad.findtext('Street1') or "",
            "zip": nad.findtext('PostalCode') or "",
            "city": nad.findtext('City') or "",
            "country": nad.findtext('ISOCountryCode') or "",
            "gln": nad.findtext('AdressGLN') or "",
            "vat_id": nad.findtext('VATId') or ""
        }
        parties[role] = party_obj

    items = []
    for line in orders.findall('.//LINE'):
        unit_attr = line.find('./OrderQuantity').attrib.get('Unit') if line.find('./OrderQuantity') is not None else ""
        items.append({
            "line_number": line.findtext('LineItemNumber'),
            "gtin": line.findtext('./ProductID/GTIN'),
            "supplier_article_number": line.findtext('./ProductID/Number'),
            "customer_article_number": line.findtext('./ProductID/CustomerNumber'),
            "quantity": line.findtext('OrderQuantity'),
            "quantity_unit": unit_attr,
            "line_text": line.findtext('./LTXT/LineText'),
        })

    return header, parties, items

# ----------------------------------------------------------------------------------
# 3. PDF GENERATION (KHG BRANDING)
# ----------------------------------------------------------------------------------
def create_supplier_pdf(header, parties, items):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left_margin = 20 * mm
    
    # Identify Roles
    buyer = parties.get('BY', {'name': 'KHG GmbH & Co. KG', 'city': 'Schönefeld'})
    supplier = parties.get('SU', {'name': 'Lieferant'})
    delivery = parties.get('DP', {'name': 'Lager'})

    # --- 1. HEADER (Sender = KHG) ---
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(colors.black)
    c.drawString(left_margin, height - 20*mm, buyer['name'])
    
    c.setFont("Helvetica", 9)
    c.drawString(left_margin, height - 25*mm, f"{buyer.get('street','')}, {buyer.get('zip','')} {buyer.get('city','')}")
    
    # --- 2. ADDRESS WINDOW (Recipient = Supplier) ---
    addr_y = height - 55 * mm
    c.setFont("Helvetica", 11)
    c.drawString(left_margin, addr_y, supplier['name'])
    c.drawString(left_margin, addr_y - 5*mm, supplier.get('street', ''))
    c.drawString(left_margin, addr_y - 10*mm, f"{supplier.get('zip', '')} {supplier.get('city', '')}")
    c.drawString(left_margin, addr_y - 15*mm, supplier.get('country', 'DE'))

    # --- 3. ORDER INFO BLOCK (Right Side) ---
    info_x = 125 * mm
    info_y = height - 55 * mm
    
    c.setFont("Helvetica-Bold", 14)
    c.drawString(info_x, info_y + 10*mm, "BESTELLUNG")
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(info_x, info_y, "Bestell-Nr.:")
    c.setFont("Helvetica", 10)
    c.drawString(info_x + 35*mm, info_y, header.get('document_number', ''))
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(info_x, info_y - 5*mm, "Datum:")
    c.setFont("Helvetica", 10)
    c.drawString(info_x + 35*mm, info_y - 5*mm, header.get('document_date', ''))

    c.setFont("Helvetica-Bold", 10)
    c.drawString(info_x, info_y - 10*mm, "Lieferwoche:")
    c.setFont("Helvetica", 10)
    c.drawString(info_x + 35*mm, info_y - 10*mm, header.get('delivery_week', ''))

    c.setFont("Helvetica-Bold", 10)
    c.drawString(info_x, info_y - 15*mm, "Kommission:")
    c.setFont("Helvetica", 10)
    c.drawString(info_x + 35*mm, info_y - 15*mm, header.get('commission', ''))

    # --- 4. DELIVERY ADDRESS (Versandanschrift) ---
    ship_y = addr_y - 40 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left_margin, ship_y, "Lieferanschrift / Versandadresse:")
    c.setFont("Helvetica", 10)
    c.drawString(left_margin, ship_y - 5*mm, delivery['name'])
    c.drawString(left_margin, ship_y - 10*mm, f"{delivery.get('street', '')}")
    c.drawString(left_margin, ship_y - 15*mm, f"{delivery.get('zip', '')} {delivery.get('city', '')}")

    # --- 5. ITEM TABLE ---
    table_y = ship_y - 30 * mm
    
    # Columns: Pos, GTIN, Art.Nr(Supp), Text, Qty, Unit
    table_data = [["Pos", "EAN / GTIN", "Art.Nr.", "Bezeichnung", "Menge", "Einh."]]
    
    for item in items:
        # Truncate long text
        txt = item["line_text"] or ""
        if len(txt) > 35: txt = txt[:32] + "..."
        
        table_data.append([
            item["line_number"],
            item["gtin"],
            item["supplier_article_number"],
            txt,
            item["quantity"],
            item["quantity_unit"]
        ])

    col_widths = [15*mm, 35*mm, 35*mm, 65*mm, 15*mm, 15*mm]
    
    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")), # KHG Corporate Blue/Grey
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (4, 1), (4, -1), "RIGHT"), # Qty Right
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
    ]))
    
    table.wrapOn(c, width, height)
    table.drawOn(c, left_margin, table_y - (len(table_data) * 7 * mm))

    # --- 6. FOOTER ---
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    footer_text = f"KHG GmbH & Co. KG | {buyer.get('street','')} | {buyer.get('zip','')} {buyer.get('city','')} | Tel: +49 30 37444-2450 | Email: einkauf_gardinen@khg.de"
    c.drawCentredString(width/2, 15*mm, footer_text)
    
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

def send_supplier_email(header, parties, items, pdf_bytes, receiver_email=None, subject=None, body=None):
    supplier_name = parties.get('SU', {}).get('name', 'Lieferant')
    to_email = receiver_email or EMAIL_RECEIVER
    subject_text = subject or f"Bestellung {header.get('document_number')} - KHG GmbH"

    msg = MIMEMultipart()
    # Display Name: KHG Einkauf (Actual sender remains authenticated email)
    msg["From"] = f"KHG Einkauf <{EMAIL_ADDRESS}>"
    msg["To"] = to_email
    msg["Subject"] = subject_text

    # Professional Body
    body_text = body or f"""
Sehr geehrte Damen und Herren bei {supplier_name},

anbei erhalten Sie unsere Bestellung Nr. {header.get('document_number')} vom {header.get('document_date')}.

Bitte bestätigen Sie den Erhalt dieser Bestellung und senden Sie uns zeitnah eine Auftragsbestätigung.
Wir bitten um Einhaltung der Lieferwoche: {header.get('delivery_week')}.

Für Rückfragen stehen wir Ihnen gerne zur Verfügung.

Mit freundlichen Grüßen

Ihr KHG Einkaufsteam
KHG GmbH & Co. KG
Am Rondell 1
12529 Schönefeld
Deutschland
    """
    msg.attach(MIMEText(body_text, "plain"))

    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=f"Bestellung_{header.get('document_number')}.pdf")
    msg.attach(attachment)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)

# ----------------------------------------------------------------------------------
# 4. AI EXTRACTION (UNCHANGED)
# ----------------------------------------------------------------------------------
def extract_data_from_scanned_pdf(pdf_path):
    if not OPENAI_API_KEY or client is None:
        return {"error": "Missing OPENAI_API_KEY environment variable."}
    try:
        images = convert_from_path(pdf_path, poppler_path=POPPLER_PATH)
        if MAX_AB_PAGES > 0:
            images = images[:MAX_AB_PAGES]
    except Exception as e:
        return {"error": f"Poppler Error: {str(e)}"}

    if not images: return {"error": "No images found."}

    content_list = [{"type": "text", "text": "Extract ALL details from this German Order Confirmation. Be extremely precise."}]
    
    for idx, img in enumerate(images, start=1):
        if img.height > 1500:
            ratio = 1500 / img.height
            img = img.resize((int(img.width * ratio), 1500))
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        content_list.append(
            {
                "type": "text",
                "text": f"Page {idx} image:",
            }
        )
        content_list.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_str}"}})

    content_list.append({"type": "text", "text": """
    Return a VALID JSON object with this exact structure (extract everything visible):
    {
        "document_info": {
            "title": "Document Title",
            "document_number": "The DocumentNumber which probably says Belegnummer in the pdf",
            "document_date": "Date",
            "customer_number": "Kunde Number",
            "representative": "Vertreter Name",
            "delivery_week": "Lieferwoche",
            "delivery_terms": "Lieferung"
        },
        "order_references": {
            "your_order_number": "Ihre Bestellung",
            "your_order_date": "Ihre Bestellung vom"
        },
        "supplier_info": {
            "name": "Company Name",
            "address": "Full Address lines",
            "phone": "Telefon",
            "fax": "Fax",
            "email": "E-Mail",
            "managing_director": "Geschäftsführer",
            "registry": "Handelsregister",
            "vat_id": "USt-IdNr",
            "bank_name": "Bankverbindung",
            "iban": "IBAN",
            "bic": "BIC"
        },
        "customer_address": {
            "raw_text": "Full block of customer address",
            "gln": "GLN",
            "city": "City/Zip"
        },
        "shipping_address": {
            "raw_text": "Full block of Versandanschrift",
            "name": "Name",
            "street": "Street",
            "city": "City"
        },
        "line_items": [
            {
                "pos_number": "Bestellnummer",
                "description": "Artikelbezeichnung",
                "customer_reference": "Reference under description",
                "technical_reference": "Dessin / Tech Ref",
                "ean": "EAN",
                "quantity": "Menge",
                "unit": "Unit",
                "unit_price": "Einzelpreis",
                "total_price": "Calculated total"
            }
        ],
        "financials": {
            "discount_text": "Text",
            "discount_amount": "Amount",
            "net_sum": "Nettosumme",
            "tax_text": "Steuer text",
            "tax_amount": "Tax amount",
            "total_gross_amount": "Auftragssumme",
            "currency": "EUR"
        },
        "payment_conditions": "Full text of Zahlungskonditionen"
    }
    """})

    try:
        response = client.chat.completions.create(
            model="gpt-5-chat-latest", 
            messages=[{"role": "user", "content": content_list}],
            max_tokens=4000,
            temperature=0
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"): content = content.replace("```json", "").replace("```", "")
        if content.startswith("```"): content = content.replace("```", "")
        return json.loads(content)
    except Exception as e:
        return {"error": f"OpenAI Error: {str(e)}"}

# ----------------------------------------------------------------------------------
# 5. XML GENERATION
# ----------------------------------------------------------------------------------
def generate_ordrsp_xml(original_xml_path, ab_data):
    def norm(val):
        return str(val or "").strip()

    def add_text(parent, tag, value, **attrs):
        if not has_text(value):
            return None
        elem = ET.SubElement(parent, tag, **attrs)
        elem.text = str(value).strip()
        return elem

    def clone_element(elem):
        return ET.fromstring(ET.tostring(elem, encoding='utf-8'))

    def parse_discount_rate(financials):
        for key in ("discount_text", "discount_amount"):
            raw = financials.get(key)
            if not has_text(raw):
                continue
            match = re.search(r'(-?\d+(?:[.,]\d+)?)\s*%', str(raw))
            if match:
                try:
                    return abs(float(match.group(1).replace(',', '.'))) / 100.0
                except Exception:
                    continue
        return 0.0

    def parse_tax_rate(financials):
        raw = financials.get("tax_text")
        if has_text(raw):
            match = re.search(r'(-?\d+(?:[.,]\d+)?)\s*%', str(raw))
            if match:
                try:
                    return abs(float(match.group(1).replace(',', '.')))
                except Exception:
                    pass
        tax_amt = to_float_optional(financials.get("tax_amount"))
        net_sum = to_float_optional(financials.get("net_sum"))
        if tax_amt is not None and net_sum:
            return abs((tax_amt / net_sum) * 100)
        gross_sum = to_float_optional(financials.get("total_gross_amount"))
        if gross_sum is not None and net_sum:
            return abs(((gross_sum - net_sum) / net_sum) * 100)
        return None

    def prune_empty(elem):
        for child in list(elem):
            prune_empty(child)
            if (child.text is None or not child.text.strip()) and len(child) == 0:
                elem.remove(child)

    parsed_header, parsed_parties, parsed_items = parse_order_xml(original_xml_path)

    tree = ET.parse(original_xml_path)
    orig_root = tree.getroot()
    orig_orders = orig_root.find('.//ORDERS')
    orig_head = orig_orders.find('HEAD') if orig_orders is not None else None
    orig_doc_num = orig_head.findtext('DocumentNumber') if orig_head is not None else parsed_header.get("document_number")
    orig_doc_date = orig_head.findtext('DocumentDate') if orig_head is not None else parsed_header.get("document_date")

    ai_doc = ab_data.get('document_info', {}) or {}
    ai_fin = ab_data.get('financials', {}) or {}
    ai_items = ab_data.get('line_items', []) or []

    discount_rate = parse_discount_rate(ai_fin)
    vat_percent_val = parse_tax_rate(ai_fin)

    # Build a lookup map for AB line items using multiple keys
    ai_item_map = {}
    for item in ai_items:
        keys = [
            norm(item.get('pos_number')),
            norm(item.get('ean')),
            norm(item.get('technical_reference')),
            norm(item.get('customer_reference')),
            norm(item.get('description')),
        ]
        for k in keys:
            if k:
                ai_item_map.setdefault(k, item)

    orders_vat_by_role = {}
    if orig_orders is not None:
        for nad in orig_orders.findall('.//NAD'):
            role = norm(nad.findtext('FlagOfParty'))
            if role:
                orders_vat_by_role[role] = norm(nad.findtext('VATId'))
    else:
        for role, pdata in (parsed_parties or {}).items():
            if role:
                orders_vat_by_role[role] = norm((pdata or {}).get('vat_id'))

    ab_supplier_vat = norm((ab_data.get('supplier_info', {}) or {}).get('vat_id'))
    wetzel_vat = ab_supplier_vat or orders_vat_by_role.get('SU', '')

    root = ET.Element('OrdrspMessage')
    root.set('xmlns:xs', "http://www.w3.org/2001/XMLSchema")
    ordrsp = ET.SubElement(root, 'ORDRSP')

    head = ET.SubElement(ordrsp, 'HEAD')
    ver = ET.SubElement(head, 'VersionNumber')
    add_text(ver, 'VersionName', "XML.Einrichten")
    add_text(ver, 'VersionNo', "1.3")
    add_text(ver, 'WorkflowDestination', "L")
    add_text(head, 'DocumentType', "231")
    add_text(head, 'DocumentFunctionSymbol', "29")

    add_text(head, 'DocumentNumber', ai_doc.get('document_number', 'UNKNOWN'))
    add_text(head, 'DocumentDate', date_to_xml_fmt(ai_doc.get('document_date')), FormatCode="102")

    # TechnicalSender is always Wetzel (4031865000009)
    # TechnicalReceiver should be whoever sent the original ORDER (their TechnicalSender)
    orig_technical_sender = orig_head.findtext('TechnicalSender') if orig_head is not None else None
    technical_receiver = orig_technical_sender or "4260129840000"  # Fallback to KHG GLN
    
    add_text(head, 'TechnicalSender', "4031865000009")
    add_text(head, 'TechnicalReceiver', technical_receiver)

    # Delivery date selection with week handling and guaranteed non-empty result
    raw_delivery = (
        ai_doc.get("delivery_week")
        or ai_doc.get("delivery_terms")
        or (orig_head.findtext("AdditionalDate") if orig_head is not None else None)
        or ai_doc.get("document_date")
        or orig_doc_date
    )

    def week_to_date(week_str):
        """
        Convert week format (WW/YYYY) to Friday of that week in YYYYMMDD format.
        Example: "03/2026" -> "20260116" (Friday of week 3, 2026)
        """
        try:
            w, y = week_str.split("/")
            week_num = int(w)
            year_num = int(y)
            # ISO weekday: Monday=1, Tuesday=2, ..., Friday=5, Saturday=6, Sunday=7
            friday_date = datetime.date.fromisocalendar(year_num, week_num, 5)
            return friday_date.strftime("%Y%m%d")
        except Exception as e:
            # Log the error for debugging
            print(f"Week conversion error for '{week_str}': {e}")
            return ""

    final_delivery_date = ""
    if raw_delivery and "/" in str(raw_delivery):
        final_delivery_date = week_to_date(str(raw_delivery))
    if not final_delivery_date:
        final_delivery_date = date_to_xml_fmt(raw_delivery)
    if not final_delivery_date:
        final_delivery_date = date_to_xml_fmt(orig_doc_date)

    add_text(head, 'RequestedDeliveryDate', final_delivery_date, FormatCode="102")
    add_text(head, 'ConfirmedDeliveryDate', final_delivery_date, FormatCode="102")
    add_text(head, 'PartialDelivery', "X1")

    if has_text(orig_doc_num) or has_text(orig_doc_date):
        ord_ref = ET.SubElement(head, 'OrderNumberRef')
        add_text(ord_ref, 'DocRefNumber', orig_doc_num)
        add_text(ord_ref, 'DocDate', orig_doc_date, FormatCode="102")

    add_text(head, 'Commission', orig_head.findtext('Commission') if orig_head is not None else parsed_header.get("commission"))

    allowed_roles = ["BY", "SU", "DP", "IV"]

    def apply_vat_override(nad_elem, vat_value):
        if vat_value is None:
            return
        for existing in list(nad_elem.findall('VATId')):
            nad_elem.remove(existing)
        if has_text(vat_value):
            ET.SubElement(nad_elem, 'VATId').text = str(vat_value).strip()

    existing_nads = orig_orders.findall('.//NAD') if orig_orders is not None else []
    for role in allowed_roles:
        found = None
        for nad_orig in existing_nads:
            if norm(nad_orig.findtext('FlagOfParty')) == role:
                found = nad_orig
                break
        if found is not None:
            nad = clone_element(found)
        else:
            nad = ET.Element('NAD')
            add_text(nad, 'FlagOfParty', role)
            pdata = parsed_parties.get(role, {}) if parsed_parties else {}
            add_text(nad, 'AdressGLN', (pdata or {}).get('gln'))
            add_text(nad, 'Name1', (pdata or {}).get('name'))
            add_text(nad, 'Street1', (pdata or {}).get('street'))
            add_text(nad, 'PostalCode', (pdata or {}).get('zip'))
            add_text(nad, 'City', (pdata or {}).get('city'))
            add_text(nad, 'ISOCountryCode', (pdata or {}).get('country'))

        vat_override = None
        if role == "SU":
            vat_override = wetzel_vat
        elif role in ("BY", "IV"):
            vat_override = orders_vat_by_role.get(role, "")
        apply_vat_override(nad, vat_override)
        prune_empty(nad)
        head.append(nad)

    line_totals = []

    def add_line(line_num, orig_art_num, orig_gtin, orig_unit, orig_qty, ltxt_text, orig_customer_num=""):
        line = ET.SubElement(ordrsp, 'LINE')
        add_text(line, 'LineItemNumber', line_num)
        if has_text(orig_doc_num) or has_text(line_num):
            ord_line_ref = ET.SubElement(line, 'OrderLineRef')
            add_text(ord_line_ref, 'DocRefNumber', orig_doc_num)
            add_text(ord_line_ref, 'DocRefLineNumber', line_num)

        matched_ai_item = None
        # Matching priority: GTIN, supplier number, pos number, description contains
        for key in (orig_gtin, orig_art_num, line_num):
            if has_text(key) and key in ai_item_map:
                matched_ai_item = ai_item_map[key]
                break
        if not matched_ai_item:
            # Fallback: partial match on description/technical reference
            for k, v in ai_item_map.items():
                if has_text(orig_art_num) and orig_art_num in k:
                    matched_ai_item = v
                    break

        # Only add ProductID if at least one field has data
        if (
            has_text(orig_gtin)
            or has_text(orig_art_num)
            or has_text(orig_customer_num)
        ):
            prod_id = ET.SubElement(line, 'ProductID')
            add_text(prod_id, 'GTIN', orig_gtin)
            add_text(prod_id, 'Number', orig_art_num)
            add_text(prod_id, 'CustomerNumber', orig_customer_num)

        add_text(line, 'TypeOfProduct', "TU")
        add_text(line, 'RequestedDeliveryDate', final_delivery_date, FormatCode="102")
        add_text(line, 'ConfirmedDeliveryDate', final_delivery_date, FormatCode="102")

        confirmed_qty_raw = matched_ai_item.get('quantity') if matched_ai_item else orig_qty
        confirmed_qty_val = to_float_optional(confirmed_qty_raw)
        confirmed_qty = normalize_decimal(confirmed_qty_val, 2) if confirmed_qty_val is not None else None

        ordered_qty_val = to_float_optional(orig_qty)
        ordered_qty = normalize_decimal(ordered_qty_val, 2) if ordered_qty_val is not None else None

        gross_unit_price_val = to_float_optional(matched_ai_item.get('unit_price')) if matched_ai_item else None
        net_unit_price_exact = gross_unit_price_val * (1 - discount_rate) if gross_unit_price_val is not None else None
        net_unit_price_val = round(net_unit_price_exact, 3) if net_unit_price_exact is not None else None

        line_total_val = None
        if net_unit_price_exact is not None and confirmed_qty_val is not None:
            line_total_val = round(net_unit_price_exact * confirmed_qty_val, 2)
            line_totals.append(line_total_val)

        currency = (ai_fin.get('currency') or "EUR").strip()

        add_text(line, 'OrderResponseQuantity', confirmed_qty, Unit=orig_unit or "PCE")

        if gross_unit_price_val is not None:
            gross_price = ET.SubElement(line, 'GrossUnitPrice')
            add_text(gross_price, 'Value', normalize_decimal(gross_unit_price_val, 2))
            add_text(gross_price, 'Currency', currency)
            add_text(gross_price, 'PriceQuantity', "1", Unit=orig_unit or "PCE")

        if net_unit_price_val is not None:
            net_price = ET.SubElement(line, 'NetUnitPrice')
            add_text(net_price, 'Value', normalize_decimal(net_unit_price_val, 3))
            add_text(net_price, 'Currency', currency)
            add_text(net_price, 'PriceQuantity', "1", Unit=orig_unit or "PCE")

        if line_total_val is not None:
            add_amt = ET.SubElement(line, 'AdditionalLineAmount')
            add_text(add_amt, 'Value', normalize_decimal(line_total_val, 2))
            add_text(add_amt, 'Currency', currency)
            add_text(add_amt, 'Qualifier', "66")

        if ordered_qty is not None:
            add_refs = ET.SubElement(line, 'AdditionalLineReferences')
            add_text(add_refs, 'AdditionalQuantity', ordered_qty, Unit=orig_unit or "PCE", Qualifier="21")

        if has_text(ltxt_text):
            ltxt = ET.SubElement(line, 'LTXT')
            add_text(ltxt, 'LineText', ltxt_text, Type="ZZZ")

    if orig_orders is not None:
        for line_orig in orig_orders.findall('.//LINE'):
            line_num = line_orig.findtext('LineItemNumber')
            orig_art_num = (line_orig.findtext('./ProductID/Number') or '').strip()
            orig_gtin = (line_orig.findtext('./ProductID/GTIN') or '').strip()
            orig_customer_num = (line_orig.findtext('./ProductID/CustomerNumber') or '').strip()
            orig_unit = "PCE"
            orig_qty = line_orig.findtext('OrderQuantity')
            oq = line_orig.find('OrderQuantity')
            if oq is not None:
                orig_unit = oq.attrib.get('Unit', orig_unit)
            ltxt_text = line_orig.findtext('./LTXT/LineText')
            add_line(line_num, orig_art_num, orig_gtin, orig_unit, orig_qty, ltxt_text, orig_customer_num)
    else:
        # Build lines from parsed items
        for item in parsed_items:
            add_line(
                item.get("line_number", ""),
                item.get("supplier_article_number", ""),
                item.get("gtin", ""),
                item.get("quantity_unit", "PCE"),
                item.get("quantity"),
                item.get("line_text", ""),
                item.get("customer_article_number", ""),
            )

    foot = ET.SubElement(ordrsp, 'FOOT')
    add_text(foot, 'SendingDate', datetime.datetime.now().strftime("%Y%m%d"), FormatCode="102")

    vat_total = ET.SubElement(foot, 'VatTotal')
    vat_value = ET.SubElement(vat_total, 'VatValue')
    vat_base = ET.SubElement(vat_total, 'VatBase')
    net_item_amt = ET.SubElement(vat_total, 'NetItemAmount')
    disc_total = ET.SubElement(vat_total, 'DiscountsConditionsTotal')

    computed_net_total = round(sum(line_totals), 2) if line_totals else None
    net_sum_val = to_float_optional(ai_fin.get('net_sum'))
    total_net = net_sum_val if net_sum_val is not None else computed_net_total

    tax_amount_val = to_float_optional(ai_fin.get('tax_amount'))
    gross_total_val = to_float_optional(ai_fin.get('total_gross_amount'))

    if vat_percent_val is None and tax_amount_val is not None and total_net:
        vat_percent_val = abs((tax_amount_val / total_net) * 100)

    vat_val = tax_amount_val
    if vat_val is None and vat_percent_val is not None and total_net is not None:
        vat_val = round(total_net * (vat_percent_val / 100), 2)

    total_amount = gross_total_val
    if total_amount is None and total_net is not None and vat_val is not None:
        total_amount = round(total_net + vat_val, 2)

    currency_footer = (ai_fin.get('currency') or "EUR").strip()

    if vat_val is not None:
        add_text(vat_value, 'Value', normalize_decimal(vat_val, 2))
        add_text(vat_value, 'Currency', currency_footer)
    if vat_percent_val is not None:
        add_text(vat_total, 'VatPercentage', normalize_decimal(vat_percent_val, 2))

    if total_net is not None:
        add_text(vat_base, 'Value', normalize_decimal(total_net, 2))
        add_text(vat_base, 'Currency', currency_footer)

        add_text(net_item_amt, 'Value', normalize_decimal(total_net, 2))
        add_text(net_item_amt, 'Currency', currency_footer)

    discount_value = None
    discount_amount_raw = ai_fin.get("discount_amount")
    if has_text(discount_amount_raw) and "%" not in str(discount_amount_raw):
        discount_value = to_float_optional(discount_amount_raw)
        if discount_value is not None:
            discount_value = -abs(discount_value)
    if discount_value is not None:
        add_text(disc_total, 'Value', normalize_decimal(discount_value, 2))
        add_text(disc_total, 'Currency', currency_footer)

    if total_amount is not None:
        add_amt = ET.SubElement(foot, 'AdditionalAmounts')
        add_text(add_amt, 'Value', normalize_decimal(total_amount, 2))
        add_text(add_amt, 'Currency', currency_footer)
        add_text(add_amt, 'Qualifier', "86")

    prune_empty(root)

    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding='utf-8', method='xml')

# ----------------------------------------------------------------------------------
# ROUTES
# ----------------------------------------------------------------------------------
def load_xml_data():
    if os.path.exists(TEMP_FILE_PATH):
        try: return parse_order_xml(TEMP_FILE_PATH)
        except: pass
    return None, None, None

@app.route("/", methods=["GET", "POST"])
def index():
    header, parties, items = load_xml_data()
    xml_data = True if header else False
    ab_data = None
    error_msg = None
    if request.method == "POST" and "xmlfile" in request.files:
        request.files["xmlfile"].save(TEMP_FILE_PATH)
        try:
            header, parties, items = parse_order_xml(TEMP_FILE_PATH)
            xml_data = True
        except Exception as exc:
            error_msg = f"XML parse error: {exc}"
            xml_data = False
    return render_template_string(HTML_TEMPLATE, xml_data=xml_data, header=header, parties=parties, items=items, ab_data=ab_data, error_msg=error_msg)

@app.route("/extract_ab", methods=["POST"])
def extract_ab():
    header, parties, items = load_xml_data()
    xml_data = True if header else False
    ab_data = None
    error_msg = None
    if "pdffile" in request.files:
        request.files["pdffile"].save(TEMP_PDF_PATH)
        ab_data = extract_data_from_scanned_pdf(TEMP_PDF_PATH)
        if ab_data and not ab_data.get("error"):
            document_no = extract_document_no_from_ab(ab_data)
            if document_no:
                try:
                    xml_bytes = fetch_order_xml_from_api(document_no)
                    with open(TEMP_FILE_PATH, "wb") as f:
                        f.write(xml_bytes)
                    header, parties, items = parse_order_xml(TEMP_FILE_PATH)
                    xml_data = True
                except Exception as exc:
                    error_msg = f"Order XML API error for DocumentNo {document_no}: {exc}"
            else:
                error_msg = "Order number not found in AB data."
        elif ab_data and ab_data.get("error"):
            error_msg = ab_data.get("error")
    return render_template_string(HTML_TEMPLATE, xml_data=xml_data, header=header, parties=parties, items=items, ab_data=ab_data, error_msg=error_msg)

@app.route("/download_ordrsp_xml", methods=["POST"])
def download_ordrsp_xml():
    json_str = request.form.get('ab_json_data')
    if not json_str: return "Missing Data"
    ab_data = json.loads(json_str)
    try:
        xml_bytes = generate_ordrsp_xml(TEMP_FILE_PATH, ab_data)
        filename = f"ORDRSP_{ab_data.get('document_info', {}).get('document_number', 'DATA')}.xml"
        return send_file(io.BytesIO(xml_bytes), download_name=filename, as_attachment=True, mimetype='application/xml')
    except Exception as e: return f"Error: {e}"

@app.route("/download_ab_excel", methods=["POST"])
def download_ab_excel():
    json_str = request.form.get('ab_json_data')
    if not json_str: return "Missing Data"
    ab_data = json.loads(json_str)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        flat = {}
        for k,v in ab_data.items():
            if isinstance(v, dict): 
                for subk, subv in v.items(): flat[f"{k}_{subk}"] = subv
            elif not isinstance(v, list): flat[k] = v
        pd.DataFrame([flat]).to_excel(writer, sheet_name='Overview', index=False)
        if 'line_items' in ab_data: pd.DataFrame(ab_data['line_items']).to_excel(writer, sheet_name='Lines', index=False)
    output.seek(0)
    return send_file(output, download_name="AB_Data.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route("/download_order_xml", methods=["POST"])
def download_order_xml():
    if not os.path.exists(TEMP_FILE_PATH):
        return "Missing Data"
    filename = "ORDER.xml"
    try:
        header, _, _ = parse_order_xml(TEMP_FILE_PATH)
        doc_num = (header or {}).get("document_number")
        if doc_num:
            filename = f"ORDER_{doc_num}.xml"
    except Exception:
        pass
    return send_file(TEMP_FILE_PATH, download_name=filename, as_attachment=True, mimetype='application/xml')

@app.route("/generate_pdf", methods=["POST"])
def generate_pdf():
    header, parties, items = parse_order_xml(TEMP_FILE_PATH)
    return send_file(create_supplier_pdf(header, parties, items), as_attachment=True, download_name="bestellung.pdf", mimetype="application/pdf")

@app.route("/send_email", methods=["POST"])
def send_email():
    header, parties, items = parse_order_xml(TEMP_FILE_PATH)
    try:
        pdf_bytes = create_supplier_pdf(header, parties, items).read()
        send_supplier_email(header, parties, items, pdf_bytes)
        return "Email sent! <a href='/'>Back</a>"
    except Exception as e: return f"Error: {e}"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>KHG Automation</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<nav class="navbar navbar-light bg-white shadow-sm mb-4">
    <div class="container"><a class="navbar-brand fw-bold text-dark" href="/"><i class="fas fa-boxes"></i> KHG Order Manager</a></div>
</nav>
<div class="container pb-5">
    <div class="row">
        <div class="col-md-4">
            <div class="card shadow-sm mb-4">
                <div class="card-body">
                    <h6 class="text-primary fw-bold">1. Upload Order (XML)</h6>
                    <form method="POST" enctype="multipart/form-data" action="/">
                        <input type="file" name="xmlfile" accept=".xml" class="form-control mb-3" required>
                        <button class="btn btn-primary w-100">Load XML</button>
                    </form>
                    {% if xml_data %}
                    <hr>
                    <h6 class="text-secondary fw-bold">2. Supplier Communication</h6>
                    <form action="/generate_pdf" method="POST"><button class="btn btn-outline-dark w-100 mb-2"><i class="fas fa-file-pdf"></i> Preview PDF</button></form>
                    <form action="/send_email" method="POST"><button class="btn btn-dark w-100"><i class="fas fa-paper-plane"></i> Send Email</button></form>
                    {% endif %}
                </div>
            </div>
            <div class="card shadow-sm border-success">
                <div class="card-body">
                    <h6 class="text-success fw-bold">3. Process Confirmation (AB)</h6>
                    <form action="/extract_ab" method="POST" enctype="multipart/form-data">
                        <input type="file" name="pdffile" accept=".pdf" class="form-control mb-3" required>
                        <button class="btn btn-success w-100"><i class="fas fa-magic"></i> Extract Data</button>
                    </form>
                    {% if ab_data and not ab_data['error'] %}
                    <hr>
                    <form action="/download_ordrsp_xml" method="POST" class="mb-2">
                        <input type="hidden" name="ab_json_data" value='{{ ab_data | tojson }}'>
                        <button class="btn btn-primary w-100">Download ORDRSP XML</button>
                    </form>
                    <form action="/download_ab_excel" method="POST">
                        <input type="hidden" name="ab_json_data" value='{{ ab_data | tojson }}'>
                        <button class="btn btn-success w-100">Download AB Excel</button>
                    </form>
                    <form action="/download_order_xml" method="POST" class="mt-2">
                        <button class="btn btn-outline-primary w-100">Download Order XML</button>
                    </form>
                    {% endif %}
                </div>
            </div>
        </div>
        <div class="col-md-8">
            {% if error_msg %}
                <div class="alert alert-danger">{{ error_msg }}</div>
            {% endif %}
            {% if xml_data %}
            <div class="card shadow-sm mb-4">
                <div class="card-header bg-white fw-bold">Active Order: {{ header.document_number }}</div>
                <div class="card-body">
                    <div class="row mb-3">
                        <div class="col-md-6"><strong>Supplier:</strong> {{ parties['SU']['name'] }}</div>
                        <div class="col-md-6"><strong>Date:</strong> {{ header.document_date }}</div>
                    </div>
                    <table class="table table-sm table-striped">
                        <thead><tr><th>Pos</th><th>Art #</th><th>Qty</th><th>Desc</th></tr></thead>
                        <tbody>
                        {% for item in items %}
                            <tr>
                                <td>{{ item.line_number }}</td>
                                <td>{{ item.supplier_article_number }}</td>
                                <td>{{ item.quantity }}</td>
                                <td>{{ item.line_text }}</td>
                            </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            {% endif %}
            
            {% if ab_data %}
                {% if ab_data['error'] %}
                    <div class="alert alert-danger">{{ ab_data['error'] }}</div>
                {% else %}
                    <div class="card shadow-sm border-success">
                        <div class="card-header bg-success text-white fw-bold">Extracted AB Data</div>
                        <div class="card-body">
                            <div class="row mb-3">
                                <div class="col-md-6"><strong>AB No:</strong> {{ ab_data['document_info']['document_number'] }}</div>
                                <div class="col-md-6"><strong>Total:</strong> {{ ab_data['financials']['total_gross_amount'] }}</div>
                            </div>
                            <table class="table table-sm table-bordered">
                                <thead><tr><th>Pos</th><th>Desc</th><th>Price</th><th>Total</th></tr></thead>
                                <tbody>
                                {% for item in ab_data['line_items'] %}
                                    <tr>
                                        <td>{{ item['pos_number'] }}</td>
                                        <td>{{ item['description'] }}</td>
                                        <td>{{ item['unit_price'] }}</td>
                                        <td>{{ item['total_price'] }}</td>
                                    </tr>
                                {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                {% endif %}
            {% endif %}
        </div>
    </div>
</div>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True)
