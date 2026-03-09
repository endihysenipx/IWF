import io
import json
import base64
from pdf2image import convert_from_bytes, convert_from_path

from app.config import OPENAI_API_KEY, client, POPPLER_PATH, MAX_AB_PAGES


OCR_MODEL = "gpt-5-chat-latest"


def extract_data_from_scanned_pdf(pdf_path):
    if not OPENAI_API_KEY or client is None:
        return {"error": "Missing OPENAI_API_KEY environment variable."}
    try:
        if isinstance(pdf_path, (bytes, bytearray)):
            images = convert_from_bytes(pdf_path, poppler_path=POPPLER_PATH)
        else:
            images = convert_from_path(pdf_path, poppler_path=POPPLER_PATH)
        if MAX_AB_PAGES > 0:
            images = images[:MAX_AB_PAGES]
    except Exception as e:
        return {"error": f"Poppler Error: {str(e)}"}

    if not images: return {"error": "No images found."}

    content_list = [{"type": "text", "text": "Extract ALL details from this German Order Confirmation. Be extremely precise."}]
    rendered_image_bytes = 0

    for idx, img in enumerate(images, start=1):
        if img.height > 1500:
            ratio = 1500 / img.height
            img = img.resize((int(img.width * ratio), 1500))
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG")
        image_bytes = buffered.getvalue()
        rendered_image_bytes += len(image_bytes)
        img_str = base64.b64encode(image_bytes).decode("utf-8")
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
            model=OCR_MODEL,
            messages=[{"role": "user", "content": content_list}],
            max_tokens=4000,
            temperature=0
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"): content = content.replace("```json", "").replace("```", "")
        if content.startswith("```"): content = content.replace("```", "")
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)
        return {
            "data": json.loads(content),
            "billing": {
                "model": OCR_MODEL,
                "pdf_pages": len(images),
                "rendered_image_bytes": rendered_image_bytes,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }
    except Exception as e:
        return {"error": f"OpenAI Error: {str(e)}"}
