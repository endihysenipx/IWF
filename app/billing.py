from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _round_usd(value: float) -> float:
    return round(float(value), 8)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_billing_summary(
    settings,
    *,
    model: str,
    processing_seconds: float,
    input_pdf_bytes: int,
    pdf_pages: int,
    rendered_image_bytes: int,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    input_blob_write_count: int,
    input_blob_read_count: int,
    output_blob_write_count: int,
    queue_enqueue_count: int,
    queue_dequeue_count: int,
    order_api_request_count: int,
    webhook_attempt_count: int,
    order_xml_bytes: int,
    output_xml_bytes: int,
    timings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    openai_input_cost = (prompt_tokens / 1_000_000) * settings.openai_input_per_million_usd
    openai_output_cost = (completion_tokens / 1_000_000) * settings.openai_output_per_million_usd
    order_api_cost = order_api_request_count * settings.order_api_request_usd
    blob_cost = (
        input_blob_write_count * settings.blob_write_request_usd
        + input_blob_read_count * settings.blob_read_request_usd
        + output_blob_write_count * settings.blob_write_request_usd
    )
    queue_cost = (
        queue_enqueue_count * settings.queue_enqueue_request_usd
        + queue_dequeue_count * settings.queue_dequeue_request_usd
    )
    webhook_cost = webhook_attempt_count * settings.webhook_request_usd
    total_estimated_cost = openai_input_cost + openai_output_cost + order_api_cost + blob_cost + queue_cost + webhook_cost

    return {
        "currency": "USD",
        "calculated_at": utc_now_iso(),
        "model": model,
        "processing_seconds": round(float(processing_seconds), 4),
        "timings": {
            key: round(float(value), 4)
            for key, value in (timings or {}).items()
            if value is not None
        },
        "usage": {
            "input_pdf_bytes": _safe_int(input_pdf_bytes),
            "pdf_pages": _safe_int(pdf_pages),
            "rendered_image_bytes": _safe_int(rendered_image_bytes),
            "prompt_tokens": _safe_int(prompt_tokens),
            "completion_tokens": _safe_int(completion_tokens),
            "total_tokens": _safe_int(total_tokens),
            "input_blob_write_count": _safe_int(input_blob_write_count),
            "input_blob_read_count": _safe_int(input_blob_read_count),
            "output_blob_write_count": _safe_int(output_blob_write_count),
            "queue_enqueue_count": _safe_int(queue_enqueue_count),
            "queue_dequeue_count": _safe_int(queue_dequeue_count),
            "order_api_request_count": _safe_int(order_api_request_count),
            "webhook_attempt_count": _safe_int(webhook_attempt_count),
            "order_xml_bytes": _safe_int(order_xml_bytes),
            "output_xml_bytes": _safe_int(output_xml_bytes),
        },
        "pricing": {
            "openai_input_per_million_usd": settings.openai_input_per_million_usd,
            "openai_output_per_million_usd": settings.openai_output_per_million_usd,
            "order_api_request_usd": settings.order_api_request_usd,
            "blob_read_request_usd": settings.blob_read_request_usd,
            "blob_write_request_usd": settings.blob_write_request_usd,
            "queue_enqueue_request_usd": settings.queue_enqueue_request_usd,
            "queue_dequeue_request_usd": settings.queue_dequeue_request_usd,
            "webhook_request_usd": settings.webhook_request_usd,
        },
        "costs": {
            "openai_input_usd": _round_usd(openai_input_cost),
            "openai_output_usd": _round_usd(openai_output_cost),
            "openai_total_usd": _round_usd(openai_input_cost + openai_output_cost),
            "order_api_usd": _round_usd(order_api_cost),
            "blob_requests_usd": _round_usd(blob_cost),
            "queue_requests_usd": _round_usd(queue_cost),
            "webhook_usd": _round_usd(webhook_cost),
            "total_estimated_usd": _round_usd(total_estimated_cost),
        },
    }
