import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle


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
