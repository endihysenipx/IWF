from fastapi.testclient import TestClient

from app import create_app
from app.config import Settings
from app.infrastructure import InMemoryBlobStorage, InMemoryJobQueue, InMemoryJobStore, RecordingWebhookDispatcher, ServiceContainer


def build_test_settings(**overrides):
    defaults = dict(
        api_bearer_token="test-token",
        local_dev_mode=True,
        local_data_dir=".pytest-localdata",
        openai_api_key="test-openai-key",
        poppler_path="C:\\poppler\\bin",
        max_ab_pages=0,
        email_address="sender@example.com",
        email_password="password",
        email_receiver="receiver@example.com",
        iwf_api_url="https://example.com/documents",
        iwf_api_email="user@example.com",
        iwf_api_password="secret",
        iwf_message_type="ORDERS",
        iwf_supplier_gln="4031865000009",
        iwf_buyer_gln="4260129840000",
        azure_service_bus_connection_string="Endpoint=sb://local/",
        azure_service_bus_queue_name="document-processing",
        azure_storage_connection_string="UseDevelopmentStorage=true",
        azure_blob_container_name="document-jobs",
        azure_table_name="DocumentJobs",
        webhook_timeout_seconds=5,
        webhook_max_attempts=3,
        webhook_retry_backoff_seconds=2,
        worker_poll_seconds=1,
        worker_max_messages=1,
        openai_input_per_million_usd=1.25,
        openai_output_per_million_usd=10.0,
        order_api_request_usd=0.0,
        blob_read_request_usd=0.0,
        blob_write_request_usd=0.0,
        queue_enqueue_request_usd=0.0,
        queue_dequeue_request_usd=0.0,
        webhook_request_usd=0.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def build_test_services(webhook_result=None):
    settings = build_test_settings()
    return ServiceContainer(
        settings=settings,
        blob_storage=InMemoryBlobStorage(),
        job_store=InMemoryJobStore(),
        job_queue=InMemoryJobQueue(),
        webhook_dispatcher=RecordingWebhookDispatcher(result=webhook_result),
    )


def build_test_client(services):
    app = create_app(services=services)
    return TestClient(app)
