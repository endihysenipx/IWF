from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utc_now():
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class QueueMessage(BaseModel):
    job_id: str
    input_blob_name: str
    callback_url: str
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    callback_url: str
    correlation_id: Optional[str] = None
    input_blob_name: Optional[str] = None
    output_blob_name: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    document_number: Optional[str] = None
    order_document_number: Optional[str] = None
    idempotency_key: Optional[str] = None
    callback_attempts: int = 0
    callback_last_status_code: Optional[int] = None
    callback_last_error: Optional[str] = None


class DocumentJobAcceptedResponse(BaseModel):
    job_id: str
    status: JobStatus
    submitted_at: datetime


class DocumentJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    correlation_id: Optional[str] = None
    input_blob_name: Optional[str] = None
    output_blob_name: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    document_number: Optional[str] = None
    order_document_number: Optional[str] = None
    callback_attempts: int = 0
    callback_last_status_code: Optional[int] = None
    callback_last_error: Optional[str] = None

    @classmethod
    def from_record(cls, record):
        return cls(
            job_id=record.job_id,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            correlation_id=record.correlation_id,
            input_blob_name=record.input_blob_name,
            output_blob_name=record.output_blob_name,
            error_code=record.error_code,
            error_message=record.error_message,
            document_number=record.document_number,
            order_document_number=record.order_document_number,
            callback_attempts=record.callback_attempts,
            callback_last_status_code=record.callback_last_status_code,
            callback_last_error=record.callback_last_error,
        )
