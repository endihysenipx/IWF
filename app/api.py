from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.infrastructure import ServiceContainer, build_service_container
from app.models import DocumentJobAcceptedResponse, DocumentJobStatusResponse, JobRecord, JobStatus, QueueMessage


bearer_scheme = HTTPBearer(auto_error=False)


def create_app(services=None):
    resolved_services = services or build_service_container()
    application = FastAPI(title="EvoFern Document Processing API", version="1.0.0")
    application.state.services = resolved_services

    @application.get("/health")
    def healthcheck():
        return {"status": "ok"}

    @application.get("/ready")
    def readiness():
        try:
            report = resolved_services.check_readiness()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": "error",
                    "message": str(exc),
                },
            ) from exc
        return {"status": "ok", **report}

    @application.post(
        "/v1/document-jobs",
        response_model=DocumentJobAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_document_job(
        request: Request,
        file: Annotated[UploadFile, File(...)],
        callback_url: Annotated[str, Form(...)],
        correlation_id: Annotated[str | None, Form()] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        _: HTTPAuthorizationCredentials = Depends(require_bearer_auth),
    ):
        services = get_services(request)
        if not file.filename:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing PDF filename.")
        if not _is_pdf_upload(file):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF uploads are supported.")
        if idempotency_key:
            existing = services.job_store.get_job_by_idempotency(idempotency_key)
            if existing is not None:
                return DocumentJobAcceptedResponse(
                    job_id=existing.job_id,
                    status=existing.status,
                    submitted_at=existing.created_at,
                )

        pdf_bytes = await file.read()
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PDF payload.")

        job_id = str(uuid4())
        input_blob_name = f"input/{job_id}/{_safe_filename(file.filename)}"
        services.blob_storage.upload_bytes(input_blob_name, pdf_bytes, file.content_type or "application/pdf")

        record = JobRecord(
            job_id=job_id,
            status=JobStatus.QUEUED,
            callback_url=callback_url,
            correlation_id=correlation_id,
            input_blob_name=input_blob_name,
            idempotency_key=idempotency_key,
        )
        try:
            services.job_store.create_job(record)
            services.job_queue.enqueue(
                QueueMessage(
                    job_id=job_id,
                    input_blob_name=input_blob_name,
                    callback_url=callback_url,
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
            )
        except Exception as exc:
            services.job_store.update_job(
                job_id,
                status=JobStatus.FAILED,
                error_code="QUEUE_SUBMISSION_FAILED",
                error_message=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to enqueue document job.",
            ) from exc
        return DocumentJobAcceptedResponse(
            job_id=record.job_id,
            status=record.status,
            submitted_at=record.created_at,
        )

    @application.get("/v1/document-jobs/{job_id}", response_model=DocumentJobStatusResponse)
    def get_document_job(job_id: str, request: Request, _: HTTPAuthorizationCredentials = Depends(require_bearer_auth)):
        services = get_services(request)
        record = services.job_store.get_job(job_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
        return DocumentJobStatusResponse.from_record(record)

    return application


def get_services(request):
    return request.app.state.services


def require_bearer_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    services = get_services(request)
    expected = services.settings.api_bearer_token
    if not expected or credentials is None or credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
        )
    return credentials


def _is_pdf_upload(file):
    filename = (file.filename or "").lower()
    content_type = (file.content_type or "").lower()
    return filename.endswith(".pdf") or content_type == "application/pdf"


def _safe_filename(filename):
    return "".join(ch for ch in filename if ch.isalnum() or ch in (".", "-", "_")) or "upload.pdf"
