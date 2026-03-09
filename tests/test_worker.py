import base64

from app.models import JobRecord, JobStatus, QueueMessage
from app.processor import DocumentJobProcessor
from app.worker import DocumentWorker
from tests.conftest import build_test_services


def test_worker_processes_job_and_posts_completion(monkeypatch):
    services = build_test_services()
    job_id = "job-1"
    services.blob_storage.upload_bytes("input/job-1/source.pdf", b"%PDF-1.7 payload", "application/pdf")
    services.job_store.create_job(
        JobRecord(
            job_id=job_id,
            status=JobStatus.QUEUED,
            callback_url="https://evosystem.example.com/callback",
            correlation_id="corr-1",
            input_blob_name="input/job-1/source.pdf",
        )
    )
    services.job_queue.enqueue(
        QueueMessage(
            job_id=job_id,
            input_blob_name="input/job-1/source.pdf",
            callback_url="https://evosystem.example.com/callback",
            correlation_id="corr-1",
        )
    )

    monkeypatch.setattr(
        "app.processor.extract_data_from_scanned_pdf",
        lambda _: {"document_info": {"document_number": "AB-10"}, "order_references": {"your_order_number": "123456"}},
    )
    monkeypatch.setattr("app.processor.fetch_order_xml_from_api", lambda _: b"<Root><ORDERS><HEAD><DocumentNumber>123456</DocumentNumber><DocumentDate>2026-03-09</DocumentDate></HEAD></ORDERS></Root>")
    monkeypatch.setattr("app.processor.generate_ordrsp_xml", lambda _, __: b"<OrdrspMessage />")

    worker = DocumentWorker(services=services)

    assert worker.process_next_message(max_wait_time=0) is True

    updated = services.job_store.get_job(job_id)
    assert updated.status == JobStatus.COMPLETED
    assert updated.output_blob_name == "output/job-1/ORDRSP_AB-10.xml"
    assert updated.billing_summary is not None
    assert updated.billing_summary["usage"]["input_pdf_bytes"] == len(b"%PDF-1.7 payload")
    assert updated.billing_summary["costs"]["total_estimated_usd"] == 0.0
    assert services.webhook_dispatcher.calls[0]["payload"]["status"] == "completed"
    assert services.webhook_dispatcher.calls[0]["payload"]["billing_summary"] is not None
    assert base64.b64decode(services.webhook_dispatcher.calls[0]["payload"]["ordrsp_xml_base64"]) == b"<OrdrspMessage />"


def test_processor_marks_job_failed_when_order_number_missing(monkeypatch):
    services = build_test_services(webhook_result={"attempts": 2, "status_code": 502, "error": "HTTP 502"})
    job_id = "job-2"
    services.blob_storage.upload_bytes("input/job-2/source.pdf", b"%PDF-1.7 payload", "application/pdf")
    services.job_store.create_job(
        JobRecord(
            job_id=job_id,
            status=JobStatus.QUEUED,
            callback_url="https://evosystem.example.com/callback",
            input_blob_name="input/job-2/source.pdf",
        )
    )

    monkeypatch.setattr(
        "app.processor.extract_data_from_scanned_pdf",
        lambda _: {"document_info": {"document_number": "AB-11"}, "order_references": {}},
    )

    processor = DocumentJobProcessor(services)
    processor.process_job(job_id)

    failed = services.job_store.get_job(job_id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_code == "ORDER_DOCUMENT_NOT_FOUND"
    assert failed.callback_attempts == 2
    assert failed.callback_last_status_code == 502
    assert failed.billing_summary is not None
    assert failed.billing_summary["usage"]["input_pdf_bytes"] == len(b"%PDF-1.7 payload")
    assert services.webhook_dispatcher.calls[0]["payload"]["status"] == "failed"
