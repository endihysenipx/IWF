import io
import xml.etree.ElementTree as ET


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


def _parse_order_tree(tree):
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


def parse_order_xml(source):
    if isinstance(source, ET.ElementTree):
        tree = source
    elif isinstance(source, ET.Element):
        tree = ET.ElementTree(source)
    elif isinstance(source, (bytes, bytearray)):
        tree = ET.parse(io.BytesIO(source))
    else:
        tree = ET.parse(source)
    return _parse_order_tree(tree)
