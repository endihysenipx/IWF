from tests.conftest import build_test_client, build_test_services


def test_submit_job_requires_bearer_token():
    services = build_test_services()
    client = build_test_client(services)

    response = client.post(
        "/v1/document-jobs",
        files={"file": ("input.pdf", b"%PDF-1.7 test", "application/pdf")},
        data={"callback_url": "https://evosystem.example.com/callback"},
    )

    assert response.status_code == 401


def test_submit_job_rejects_non_pdf():
    services = build_test_services()
    client = build_test_client(services)

    response = client.post(
        "/v1/document-jobs",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("input.txt", b"not a pdf", "text/plain")},
        data={"callback_url": "https://evosystem.example.com/callback"},
    )

    assert response.status_code == 400


def test_submit_job_queues_once_for_idempotency_key():
    services = build_test_services()
    client = build_test_client(services)
    headers = {
        "Authorization": "Bearer test-token",
        "Idempotency-Key": "same-job",
    }

    first = client.post(
        "/v1/document-jobs",
        headers=headers,
        files={"file": ("input.pdf", b"%PDF-1.7 first", "application/pdf")},
        data={"callback_url": "https://evosystem.example.com/callback", "correlation_id": "corr-1"},
    )
    second = client.post(
        "/v1/document-jobs",
        headers=headers,
        files={"file": ("input.pdf", b"%PDF-1.7 second", "application/pdf")},
        data={"callback_url": "https://evosystem.example.com/callback", "correlation_id": "corr-1"},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert services.job_queue.depth() == 1


def test_get_job_status_returns_created_job():
    services = build_test_services()
    client = build_test_client(services)

    create_response = client.post(
        "/v1/document-jobs",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("input.pdf", b"%PDF-1.7 payload", "application/pdf")},
        data={"callback_url": "https://evosystem.example.com/callback", "correlation_id": "corr-2"},
    )
    job_id = create_response.json()["job_id"]

    response = client.get(
        f"/v1/document-jobs/{job_id}",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] == "queued"
