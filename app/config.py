import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def _get_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    api_bearer_token: str
    local_dev_mode: bool
    local_data_dir: str
    openai_api_key: str
    poppler_path: str | None
    max_ab_pages: int
    email_address: str
    email_password: str
    email_receiver: str
    iwf_api_url: str
    iwf_api_email: str
    iwf_api_password: str
    iwf_message_type: str
    iwf_supplier_gln: str
    iwf_buyer_gln: str
    azure_service_bus_connection_string: str
    azure_service_bus_queue_name: str
    azure_storage_connection_string: str
    azure_blob_container_name: str
    azure_table_name: str
    webhook_timeout_seconds: int
    webhook_max_attempts: int
    webhook_retry_backoff_seconds: int
    worker_poll_seconds: int
    worker_max_messages: int
    openai_input_per_million_usd: float
    openai_output_per_million_usd: float
    order_api_request_usd: float
    blob_read_request_usd: float
    blob_write_request_usd: float
    queue_enqueue_request_usd: float
    queue_dequeue_request_usd: float
    webhook_request_usd: float


@lru_cache(maxsize=1)
def get_settings():
    local_data_dir = os.getenv("LOCAL_DATA_DIR", os.path.join(os.getcwd(), ".localdata"))
    return Settings(
        api_bearer_token=os.getenv("API_BEARER_TOKEN", ""),
        local_dev_mode=os.getenv("LOCAL_DEV_MODE", "").strip().lower() in {"1", "true", "yes", "on"},
        local_data_dir=local_data_dir,
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        poppler_path=os.getenv("POPPLER_PATH") or None,
        max_ab_pages=_get_int("MAX_AB_PAGES", 0),
        email_address=os.getenv("EMAIL_ADDRESS", ""),
        email_password=os.getenv("EMAIL_PASSWORD", ""),
        email_receiver=os.getenv("EMAIL_RECEIVER", ""),
        iwf_api_url=os.getenv(
            "IWF_API_URL",
            "https://www.iwofurn.com/addvityapi/api/Documents/FindDocuments",
        ),
        iwf_api_email=os.getenv("IWF_API_EMAIL", "Testapi.Wetzel@iwofurn.com"),
        iwf_api_password=os.getenv("IWF_API_PASSWORD", "IWOfurn2025!"),
        iwf_message_type=os.getenv("IWF_MESSAGE_TYPE", "ORDERS"),
        iwf_supplier_gln=os.getenv("IWF_SUPPLIER_GLN", "4031865000009"),
        iwf_buyer_gln=os.getenv("IWF_BUYER_GLN", "4260129840000"),
        azure_service_bus_connection_string=os.getenv("AZURE_SERVICE_BUS_CONNECTION_STRING", ""),
        azure_service_bus_queue_name=os.getenv("AZURE_SERVICE_BUS_QUEUE_NAME", "document-processing"),
        azure_storage_connection_string=os.getenv("AZURE_STORAGE_CONNECTION_STRING", ""),
        azure_blob_container_name=os.getenv("AZURE_BLOB_CONTAINER_NAME", "document-jobs"),
        azure_table_name=os.getenv("AZURE_TABLE_NAME", "DocumentJobs"),
        webhook_timeout_seconds=_get_int("WEBHOOK_TIMEOUT_SECONDS", 15),
        webhook_max_attempts=_get_int("WEBHOOK_MAX_ATTEMPTS", 3),
        webhook_retry_backoff_seconds=_get_int("WEBHOOK_RETRY_BACKOFF_SECONDS", 2),
        worker_poll_seconds=_get_int("WORKER_POLL_SECONDS", 5),
        worker_max_messages=_get_int("WORKER_MAX_MESSAGES", 1),
        openai_input_per_million_usd=_get_float("OPENAI_INPUT_PER_MILLION_USD", 1.25),
        openai_output_per_million_usd=_get_float("OPENAI_OUTPUT_PER_MILLION_USD", 10.0),
        order_api_request_usd=_get_float("ORDER_API_REQUEST_USD", 0.0),
        blob_read_request_usd=_get_float("BLOB_READ_REQUEST_USD", 0.0),
        blob_write_request_usd=_get_float("BLOB_WRITE_REQUEST_USD", 0.0),
        queue_enqueue_request_usd=_get_float("QUEUE_ENQUEUE_REQUEST_USD", 0.0),
        queue_dequeue_request_usd=_get_float("QUEUE_DEQUEUE_REQUEST_USD", 0.0),
        webhook_request_usd=_get_float("WEBHOOK_REQUEST_USD", 0.0),
    )


settings = get_settings()

# Backwards-compatible module constants for the existing extraction/parsing code.
TEMP_FILE_PATH = "temp_uploaded_order.xml"
TEMP_PDF_PATH = "temp_incoming_ab.pdf"
OPENAI_API_KEY = settings.openai_api_key
POPPLER_PATH = settings.poppler_path
MAX_AB_PAGES = settings.max_ab_pages
EMAIL_ADDRESS = settings.email_address
EMAIL_PASSWORD = settings.email_password
EMAIL_RECEIVER = settings.email_receiver
IWF_API_URL = settings.iwf_api_url
IWF_API_EMAIL = settings.iwf_api_email
IWF_API_PASSWORD = settings.iwf_api_password
IWF_MESSAGE_TYPE = settings.iwf_message_type
IWF_SUPPLIER_GLN = settings.iwf_supplier_gln
IWF_BUYER_GLN = settings.iwf_buyer_gln

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
