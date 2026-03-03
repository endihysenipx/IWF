import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.text import MIMEText

from app.config import EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_RECEIVER


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
