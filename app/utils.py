import datetime


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
    clean = str(value_str).upper().replace("EUR", "").replace("\u20ac", "").strip()
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
