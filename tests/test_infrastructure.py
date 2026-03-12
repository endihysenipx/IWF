import pytest

from app.infrastructure import ConfigurationError, build_service_container
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
