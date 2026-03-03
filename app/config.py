import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

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
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "00primex.eu@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "oovm iwnc bzul pzfw")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "00primex.eu@gmail.com")

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
