import base64
import time
from uuid import uuid4

from app.api_client import extract_document_no_from_ab, fetch_order_xml_from_api
from app.billing import build_billing_summary
from app.infrastructure import ServiceContainer
from app.models import JobStatus, utc_now
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
            processing_started_at=utc_now(),
            error_code=None,
            error_message=None,
        )

        started_at = time.perf_counter()
        queue_wait_seconds = max((record.processing_started_at - record.created_at).total_seconds(), 0.0)
        pdf_bytes = b""
        extraction_billing = {}
        order_xml_bytes = b""
        ordrsp_xml_bytes = b""
        output_blob_name = None
        stage_timings = {
            "queue_wait_seconds": queue_wait_seconds,
            "blob_download_seconds": 0.0,
            "ocr_seconds": 0.0,
            "iwf_lookup_seconds": 0.0,
            "xml_build_seconds": 0.0,
            "output_upload_seconds": 0.0,
            "webhook_seconds": 0.0,
        }
        try:
            blob_started_at = time.perf_counter()
            pdf_bytes = self._services.blob_storage.download_bytes(record.input_blob_name)
            stage_timings["blob_download_seconds"] = time.perf_counter() - blob_started_at
            ocr_started_at = time.perf_counter()
            extraction_result = extract_data_from_scanned_pdf(pdf_bytes)
            stage_timings["ocr_seconds"] = time.perf_counter() - ocr_started_at
            if isinstance(extraction_result, dict) and isinstance(extraction_result.get("data"), dict):
                ab_data = extraction_result["data"]
                extraction_billing = extraction_result.get("billing", {}) or {}
            else:
                ab_data = extraction_result
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

            lookup_started_at = time.perf_counter()
            order_xml_bytes = fetch_order_xml_from_api(order_document_number)
            stage_timings["iwf_lookup_seconds"] = time.perf_counter() - lookup_started_at
            xml_build_started_at = time.perf_counter()
            ordrsp_xml_bytes = generate_ordrsp_xml(order_xml_bytes, ab_data)
            stage_timings["xml_build_seconds"] = time.perf_counter() - xml_build_started_at
            document_number = (ab_data.get("document_info", {}) or {}).get("document_number")
            output_blob_name = self._build_output_blob_name(job_id, document_number or order_document_number)
            upload_started_at = time.perf_counter()
            self._services.blob_storage.upload_bytes(
                output_blob_name,
                ordrsp_xml_bytes,
                "application/xml",
            )
            stage_timings["output_upload_seconds"] = time.perf_counter() - upload_started_at

            billing_summary = self._build_job_billing_summary(
                pdf_bytes=pdf_bytes,
                extraction_billing=extraction_billing,
                order_xml_bytes=order_xml_bytes,
                ordrsp_xml_bytes=ordrsp_xml_bytes,
                output_blob_written=True,
                order_api_requested=True,
                webhook_attempt_count=0,
                processing_seconds=time.perf_counter() - started_at,
                stage_timings=stage_timings,
            )
            completed = self._services.job_store.update_job(
                job_id,
                status=JobStatus.COMPLETED,
                output_blob_name=output_blob_name,
                document_number=document_number,
                order_document_number=order_document_number,
                billing_summary=billing_summary,
            )
            self._deliver_completion(completed, ordrsp_xml_bytes)
            return completed
        except DocumentProcessingError as exc:
            billing_summary = self._build_job_billing_summary(
                pdf_bytes=pdf_bytes,
                extraction_billing=extraction_billing,
                order_xml_bytes=order_xml_bytes,
                ordrsp_xml_bytes=ordrsp_xml_bytes,
                output_blob_written=bool(output_blob_name),
                order_api_requested=bool(order_xml_bytes),
                webhook_attempt_count=0,
                processing_seconds=time.perf_counter() - started_at,
                stage_timings=stage_timings,
            )
            failed = self._services.job_store.update_job(
                job_id,
                status=JobStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
                billing_summary=billing_summary,
            )
            self._deliver_failure(failed)
            return failed
        except Exception as exc:
            billing_summary = self._build_job_billing_summary(
                pdf_bytes=pdf_bytes,
                extraction_billing=extraction_billing,
                order_xml_bytes=order_xml_bytes,
                ordrsp_xml_bytes=ordrsp_xml_bytes,
                output_blob_written=bool(output_blob_name),
                order_api_requested=bool(order_xml_bytes),
                webhook_attempt_count=0,
                processing_seconds=time.perf_counter() - started_at,
                stage_timings=stage_timings,
            )
            failed = self._services.job_store.update_job(
                job_id,
                status=JobStatus.FAILED,
                error_code="UNEXPECTED_ERROR",
                error_message=str(exc),
                billing_summary=billing_summary,
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
            "billing_summary": record.billing_summary,
            "ordrsp_xml_base64": base64.b64encode(xml_bytes).decode("utf-8"),
        }
        webhook_started_at = time.perf_counter()
        delivery = self._services.webhook_dispatcher.deliver(record, payload)
        webhook_seconds = time.perf_counter() - webhook_started_at
        billing_summary = self._build_job_billing_summary(
            pdf_bytes=b"",
            extraction_billing=(record.billing_summary or {}).get("usage", {}),
            order_xml_bytes=b"",
            ordrsp_xml_bytes=xml_bytes,
            output_blob_written=bool(record.output_blob_name),
            order_api_requested=bool(record.order_document_number),
            webhook_attempt_count=delivery["attempts"],
            processing_seconds=(record.billing_summary or {}).get("processing_seconds", 0),
            existing_summary=record.billing_summary,
            stage_timings={"webhook_seconds": webhook_seconds},
        )
        self._services.job_store.update_job(
            record.job_id,
            callback_attempts=delivery["attempts"],
            callback_last_status_code=delivery["status_code"],
            callback_last_error=delivery["error"],
            billing_summary=billing_summary,
        )

    def _deliver_failure(self, record):
        payload = {
            "job_id": record.job_id,
            "correlation_id": record.correlation_id,
            "status": JobStatus.FAILED.value,
            "error_code": record.error_code,
            "message": record.error_message,
            "billing_summary": record.billing_summary,
        }
        webhook_started_at = time.perf_counter()
        delivery = self._services.webhook_dispatcher.deliver(record, payload)
        webhook_seconds = time.perf_counter() - webhook_started_at
        billing_summary = self._build_job_billing_summary(
            pdf_bytes=b"",
            extraction_billing=(record.billing_summary or {}).get("usage", {}),
            order_xml_bytes=b"",
            ordrsp_xml_bytes=b"",
            output_blob_written=bool(record.output_blob_name),
            order_api_requested=bool(record.order_document_number),
            webhook_attempt_count=delivery["attempts"],
            processing_seconds=(record.billing_summary or {}).get("processing_seconds", 0),
            existing_summary=record.billing_summary,
            stage_timings={"webhook_seconds": webhook_seconds},
        )
        self._services.job_store.update_job(
            record.job_id,
            callback_attempts=delivery["attempts"],
            callback_last_status_code=delivery["status_code"],
            callback_last_error=delivery["error"],
            billing_summary=billing_summary,
        )

    def _build_job_billing_summary(
        self,
        *,
        pdf_bytes,
        extraction_billing,
        order_xml_bytes,
        ordrsp_xml_bytes,
        output_blob_written,
        order_api_requested,
        webhook_attempt_count,
        processing_seconds,
        existing_summary=None,
        stage_timings=None,
    ):
        usage = (existing_summary or {}).get("usage", {})
        pricing_source = extraction_billing or {}
        existing_timings = (existing_summary or {}).get("timings", {})
        merged_timings = dict(existing_timings)
        merged_timings.update(stage_timings or {})
        return build_billing_summary(
            self._services.settings,
            model=(existing_summary or {}).get("model") or pricing_source.get("model") or "gpt-5-chat-latest",
            processing_seconds=(existing_summary or {}).get("processing_seconds", processing_seconds),
            input_pdf_bytes=usage.get("input_pdf_bytes", len(pdf_bytes or b"")),
            pdf_pages=usage.get("pdf_pages", pricing_source.get("pdf_pages", 0)),
            rendered_image_bytes=usage.get("rendered_image_bytes", pricing_source.get("rendered_image_bytes", 0)),
            prompt_tokens=usage.get("prompt_tokens", pricing_source.get("prompt_tokens", 0)),
            completion_tokens=usage.get("completion_tokens", pricing_source.get("completion_tokens", 0)),
            total_tokens=usage.get("total_tokens", pricing_source.get("total_tokens", 0)),
            input_blob_write_count=1,
            input_blob_read_count=1 if usage.get("input_pdf_bytes", len(pdf_bytes or b"")) else 0,
            output_blob_write_count=1 if output_blob_written else 0,
            queue_enqueue_count=1,
            queue_dequeue_count=1,
            order_api_request_count=1 if order_api_requested else 0,
            webhook_attempt_count=webhook_attempt_count,
            order_xml_bytes=usage.get("order_xml_bytes", len(order_xml_bytes or b"")),
            output_xml_bytes=usage.get("output_xml_bytes", len(ordrsp_xml_bytes or b"")),
            timings=merged_timings,
        )
