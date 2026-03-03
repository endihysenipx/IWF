import os
import io
import json
import pandas as pd
from flask import request, render_template, send_file, redirect, url_for

from app.config import TEMP_FILE_PATH, TEMP_PDF_PATH
from app.parsers import parse_order_xml
from app.pdf_generator import create_supplier_pdf
from app.email_service import send_supplier_email
from app.ocr_extractor import extract_data_from_scanned_pdf
from app.ordrsp_builder import generate_ordrsp_xml
from app.api_client import extract_document_no_from_ab, fetch_order_xml_from_api


def load_xml_data():
    if os.path.exists(TEMP_FILE_PATH):
        try: return parse_order_xml(TEMP_FILE_PATH)
        except: pass
    return None, None, None


def register_routes(app):

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
        return render_template("index.html", xml_data=xml_data, header=header, parties=parties, items=items, ab_data=ab_data, error_msg=error_msg)

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
        return render_template("index.html", xml_data=xml_data, header=header, parties=parties, items=items, ab_data=ab_data, error_msg=error_msg)

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
