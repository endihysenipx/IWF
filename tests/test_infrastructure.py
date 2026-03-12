import pytest

from app.infrastructure import AzureServiceBusQueue, ConfigurationError, ServiceBusClient, build_service_container
from tests.conftest import build_test_settings


def test_build_service_container_requires_azure_settings_when_local_mode_disabled():
    settings = build_test_settings(
        local_dev_mode=False,
        api_bearer_token="",
        openai_api_key="",
        iwf_api_url="",
        iwf_api_email="",
        iwf_api_password="",
        azure_service_bus_connection_string="",
        azure_storage_connection_string="",
    )

    with pytest.raises(ConfigurationError):
        build_service_container(settings=settings)


def test_azure_service_bus_queue_readiness_does_not_use_zero_wait(monkeypatch):
    captured = {}

    class FakeReceiver:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def peek_messages(self, max_message_count):
            captured["max_message_count"] = max_message_count
            return []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_queue_receiver(self, **kwargs):
            captured["receiver_kwargs"] = kwargs
            return FakeReceiver()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(
        ServiceBusClient,
        "from_connection_string",
        staticmethod(lambda _: FakeClient()),
    )

    queue = AzureServiceBusQueue("Endpoint=sb://local/", "document-processing")
    report = queue.check_readiness()

    assert report == {"backend": "azure-servicebus", "queue": "document-processing"}
    assert captured["receiver_kwargs"] == {"queue_name": "document-processing"}
    assert captured["max_message_count"] == 1
    assert captured["closed"] is True
