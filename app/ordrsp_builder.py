import re
import datetime
import xml.etree.ElementTree as ET

from app.utils import (
    date_to_xml_fmt, normalize_decimal, has_text, to_float_optional,
)
from app.parsers import parse_order_xml


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
