import base64
import io
import json

from pdf2image import convert_from_bytes, convert_from_path

from app.config import MAX_AB_PAGES, OPENAI_API_KEY, POPPLER_PATH, client


OCR_MODEL = "gpt-5-chat-latest"


def _build_poppler_kwargs(poppler_path):
    if not poppler_path:
        return {}
    return {"poppler_path": poppler_path}


def extract_data_from_scanned_pdf(pdf_path):
    if not OPENAI_API_KEY or client is None:
        return {"error": "Missing OPENAI_API_KEY environment variable."}
    try:
        poppler_kwargs = _build_poppler_kwargs(POPPLER_PATH)
        if isinstance(pdf_path, (bytes, bytearray)):
            images = convert_from_bytes(pdf_path, **poppler_kwargs)
        else:
            images = convert_from_path(pdf_path, **poppler_kwargs)
        if MAX_AB_PAGES > 0:
            images = images[:MAX_AB_PAGES]
    except Exception as e:
        return {"error": f"Poppler Error: {str(e)}"}

    if not images:
        return {"error": "No images found."}

    content_list = [
        {
            "type": "text",
            "text": (
                "Extract only the fields listed below from this German order confirmation. "
                "Be extremely precise. Only return values that are clearly visible on the PDF. "
                "Do not guess, infer, repair, or calculate missing values. If a value is missing or unreadable, return null."
            ),
        }
    ]
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

    content_list.append(
        {
            "type": "text",
            "text": """
    Return a VALID JSON object with this exact structure and no extra keys:
    {
        "document_info": {
            "document_number": "AB document number, often labeled Nummer or Belegnummer",
            "document_date": "AB document date",
            "delivery_week": "Lieferwoche if visible",
            "delivery_terms": "Lieferung text if visible"
        },
        "order_references": {
            "your_order_number": "Customer order number, often labeled Ihre Bestellung"
        },
        "supplier_info": {
            "vat_id": "Supplier VAT ID / USt-IdNr if clearly visible"
        },
        "line_items": [
            {
                "pos_number": "Bestellnummer or line position if visible",
                "description": "Artikelbezeichnung if visible",
                "customer_reference": "Customer reference if clearly visible",
                "technical_reference": "Dessin / technical reference if visible",
                "ean": "EAN if visible",
                "quantity": "Confirmed quantity / Menge",
                "unit_price": "Visible unit price / Einzelpreis"
            }
        ],
        "financials": {
            "discount_text": "Visible discount text, for example -8,1 % Rabatt",
            "discount_amount": "Visible discount amount only if explicitly printed",
            "net_sum": "Nettosumme",
            "tax_text": "Tax text, for example Steuer 19 % gesamt",
            "tax_amount": "Visible tax amount",
            "total_gross_amount": "Auftragssumme",
            "currency": "Currency if explicitly visible, otherwise null"
        }
    }

    Extraction notes:
    - Only extract fields that are clearly visible on the PDF.
    - Do not calculate totals.
    - Do not invent units, references, addresses, contact details, or dates.
    - If a field is missing or unreadable, return null.
    - Keep line_items in the same order as shown on the PDF.
    """,
        }
    )

    try:
        response = client.chat.completions.create(
            model=OCR_MODEL,
            messages=[{"role": "user", "content": content_list}],
            max_tokens=4000,
            temperature=0,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "")
        if content.startswith("```"):
            content = content.replace("```", "")
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
