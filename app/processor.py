import base64
from uuid import uuid4

from app.api_client import extract_document_no_from_ab, fetch_order_xml_from_api
from app.infrastructure import ServiceContainer
from app.models import JobStatus
from app.ocr_extractor import extract_data_from_scanned_pdf
from app.ordrsp_builder import generate_ordrsp_xml


class DocumentProcessingError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentJobProcessor:
    def __init__(self, services):
        self._services = services

    def process_job(self, job_id):
        record = self._services.job_store.get_job(job_id)
        if record is None:
            return None
        if record.status == JobStatus.COMPLETED:
            return record

        record = self._services.job_store.update_job(
            job_id,
            status=JobStatus.PROCESSING,
            error_code=None,
            error_message=None,
        )

        try:
            pdf_bytes = self._services.blob_storage.download_bytes(record.input_blob_name)
            ab_data = extract_data_from_scanned_pdf(pdf_bytes)
            if not ab_data:
                raise DocumentProcessingError("OCR_EMPTY_RESULT", "PDF extraction returned no content.")
            if ab_data.get("error"):
                raise DocumentProcessingError("OCR_EXTRACTION_FAILED", ab_data["error"])

            order_document_number = extract_document_no_from_ab(ab_data)
            if not order_document_number:
                raise DocumentProcessingError(
                    "ORDER_DOCUMENT_NOT_FOUND",
                    "Order number could not be extracted from the PDF.",
                )

            order_xml_bytes = fetch_order_xml_from_api(order_document_number)
            ordrsp_xml_bytes = generate_ordrsp_xml(order_xml_bytes, ab_data)
            document_number = (ab_data.get("document_info", {}) or {}).get("document_number")
            output_blob_name = self._build_output_blob_name(job_id, document_number or order_document_number)
            self._services.blob_storage.upload_bytes(
                output_blob_name,
                ordrsp_xml_bytes,
                "application/xml",
            )

            completed = self._services.job_store.update_job(
                job_id,
                status=JobStatus.COMPLETED,
                output_blob_name=output_blob_name,
                document_number=document_number,
                order_document_number=order_document_number,
            )
            self._deliver_completion(completed, ordrsp_xml_bytes)
            return completed
        except DocumentProcessingError as exc:
            failed = self._services.job_store.update_job(
                job_id,
                status=JobStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )
            self._deliver_failure(failed)
            return failed
        except Exception as exc:
            failed = self._services.job_store.update_job(
                job_id,
                status=JobStatus.FAILED,
                error_code="UNEXPECTED_ERROR",
                error_message=str(exc),
            )
            self._deliver_failure(failed)
            return failed

    @staticmethod
    def _build_output_blob_name(job_id, document_number):
        sanitized = "".join(ch for ch in document_number if ch.isalnum() or ch in ("-", "_")) or str(uuid4())
        return f"output/{job_id}/ORDRSP_{sanitized}.xml"

    def _deliver_completion(self, record, xml_bytes):
        payload = {
            "job_id": record.job_id,
            "correlation_id": record.correlation_id,
            "status": JobStatus.COMPLETED.value,
            "document_number": record.document_number,
            "order_document_number": record.order_document_number,
            "ordrsp_xml_base64": base64.b64encode(xml_bytes).decode("utf-8"),
        }
        delivery = self._services.webhook_dispatcher.deliver(record, payload)
        self._services.job_store.update_job(
            record.job_id,
            callback_attempts=delivery["attempts"],
            callback_last_status_code=delivery["status_code"],
            callback_last_error=delivery["error"],
        )

    def _deliver_failure(self, record):
        payload = {
            "job_id": record.job_id,
            "correlation_id": record.correlation_id,
            "status": JobStatus.FAILED.value,
            "error_code": record.error_code,
            "message": record.error_message,
        }
        delivery = self._services.webhook_dispatcher.deliver(record, payload)
        self._services.job_store.update_job(
            record.job_id,
            callback_attempts=delivery["attempts"],
            callback_last_status_code=delivery["status_code"],
            callback_last_error=delivery["error"],
        )
