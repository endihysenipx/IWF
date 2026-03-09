import hashlib
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.storage.blob import BlobServiceClient, ContentSettings

from app.config import Settings, get_settings
from app.models import JobRecord, JobStatus, QueueMessage, utc_now


def _hash_idempotency_key(idempotency_key):
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def _iso_to_datetime(value):
    if not value:
        return utc_now()
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class BlobStorage:
    def upload_bytes(self, blob_name, payload, content_type):
        raise NotImplementedError

    def download_bytes(self, blob_name):
        raise NotImplementedError


class JobStore:
    def create_job(self, record):
        raise NotImplementedError

    def get_job(self, job_id):
        raise NotImplementedError

    def get_job_by_idempotency(self, idempotency_key):
        raise NotImplementedError

    def update_job(self, job_id, **updates):
        raise NotImplementedError


class ReceivedQueueMessage:
    def __init__(self, message):
        self.message = message

    def complete(self):
        raise NotImplementedError

    def abandon(self):
        raise NotImplementedError


class JobQueue:
    def enqueue(self, message):
        raise NotImplementedError

    def dequeue(self, max_wait_time=5):
        raise NotImplementedError


class WebhookDispatcher:
    def deliver(self, record, payload):
        raise NotImplementedError


class AzureBlobStorage(BlobStorage):
    def __init__(self, connection_string, container_name):
        self._service = BlobServiceClient.from_connection_string(connection_string)
        self._container = self._service.get_container_client(container_name)
        try:
            self._container.create_container()
        except ResourceExistsError:
            pass

    def upload_bytes(self, blob_name, payload, content_type):
        client = self._container.get_blob_client(blob_name)
        client.upload_blob(
            payload,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        return blob_name

    def download_bytes(self, blob_name):
        client = self._container.get_blob_client(blob_name)
        return client.download_blob().readall()


class AzureTableJobStore(JobStore):
    def __init__(self, connection_string, table_name):
        service = TableServiceClient.from_connection_string(connection_string)
        self._table = service.create_table_if_not_exists(table_name=table_name)

    def create_job(self, record):
        self._table.upsert_entity(mode=UpdateMode.REPLACE, entity=self._record_to_entity(record))
        if record.idempotency_key:
            self._table.upsert_entity(
                mode=UpdateMode.REPLACE,
                entity={
                    "PartitionKey": "idempotency",
                    "RowKey": _hash_idempotency_key(record.idempotency_key),
                    "job_id": record.job_id,
                    "created_at": record.created_at.isoformat(),
                },
            )
        return record

    def get_job(self, job_id):
        try:
            entity = self._table.get_entity(partition_key="job", row_key=job_id)
        except ResourceNotFoundError:
            return None
        return self._entity_to_record(entity)

    def get_job_by_idempotency(self, idempotency_key):
        try:
            entity = self._table.get_entity(
                partition_key="idempotency",
                row_key=_hash_idempotency_key(idempotency_key),
            )
        except ResourceNotFoundError:
            return None
        return self.get_job(entity.get("job_id"))

    def update_job(self, job_id, **updates):
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(f"Unknown job id {job_id}")
        payload = current.model_copy(update={"updated_at": utc_now(), **updates})
        self._table.upsert_entity(mode=UpdateMode.REPLACE, entity=self._record_to_entity(payload))
        return payload

    @staticmethod
    def _record_to_entity(record):
        data = record.model_dump()
        data["PartitionKey"] = "job"
        data["RowKey"] = record.job_id
        data["status"] = record.status.value if isinstance(record.status, JobStatus) else str(record.status)
        data["created_at"] = record.created_at.isoformat()
        data["updated_at"] = record.updated_at.isoformat()
        return data

    @staticmethod
    def _entity_to_record(entity):
        payload = {k: v for k, v in entity.items() if k not in {"PartitionKey", "RowKey", "etag"}}
        payload["job_id"] = entity["RowKey"]
        payload["status"] = JobStatus(payload["status"])
        payload["created_at"] = _iso_to_datetime(payload.get("created_at"))
        payload["updated_at"] = _iso_to_datetime(payload.get("updated_at"))
        return JobRecord(**payload)


class AzureReceivedQueueMessage(ReceivedQueueMessage):
    def __init__(self, client, receiver, raw_message, message):
        super().__init__(message)
        self._client = client
        self._receiver = receiver
        self._raw_message = raw_message
        self._closed = False

    def complete(self):
        if self._closed:
            return
        self._receiver.complete_message(self._raw_message)
        self._close()

    def abandon(self):
        if self._closed:
            return
        self._receiver.abandon_message(self._raw_message)
        self._close()

    def _close(self):
        if self._closed:
            return
        self._receiver.close()
        self._client.close()
        self._closed = True


class AzureServiceBusQueue(JobQueue):
    def __init__(self, connection_string, queue_name):
        self._connection_string = connection_string
        self._queue_name = queue_name

    def enqueue(self, message):
        client = ServiceBusClient.from_connection_string(self._connection_string)
        try:
            with client:
                sender = client.get_queue_sender(queue_name=self._queue_name)
                with sender:
                    sender.send_messages(ServiceBusMessage(message.model_dump_json()))
        finally:
            client.close()

    def dequeue(self, max_wait_time=5):
        client = ServiceBusClient.from_connection_string(self._connection_string)
        receiver = client.get_queue_receiver(
            queue_name=self._queue_name,
            max_wait_time=max_wait_time,
        )
        receiver.__enter__()
        messages = receiver.receive_messages(max_message_count=1, max_wait_time=max_wait_time)
        if not messages:
            receiver.close()
            client.close()
            return None
        raw_message = messages[0]
        body = b"".join(raw_message.body)
        message = QueueMessage.model_validate_json(body.decode("utf-8"))
        return AzureReceivedQueueMessage(client, receiver, raw_message, message)


class RequestsWebhookDispatcher(WebhookDispatcher):
    def __init__(self, timeout_seconds, max_attempts, backoff_seconds):
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds

    def deliver(self, record, payload):
        attempts = 0
        last_status_code = None
        last_error = None
        session = requests.Session()
        for attempt in range(1, self._max_attempts + 1):
            attempts = attempt
            try:
                response = session.post(record.callback_url, json=payload, timeout=self._timeout_seconds)
                last_status_code = response.status_code
                if response.status_code < 500:
                    return {
                        "attempts": attempts,
                        "status_code": last_status_code,
                        "error": None,
                    }
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)

            if attempt < self._max_attempts:
                delay_seconds = self._backoff_seconds ** attempt
                import time

                time.sleep(delay_seconds)

        return {
            "attempts": attempts,
            "status_code": last_status_code,
            "error": last_error,
        }


class InMemoryBlobStorage(BlobStorage):
    def __init__(self):
        self._objects = {}

    def upload_bytes(self, blob_name, payload, content_type):
        self._objects[blob_name] = {
            "bytes": payload,
            "content_type": content_type,
        }
        return blob_name

    def download_bytes(self, blob_name):
        return self._objects[blob_name]["bytes"]


class InMemoryJobStore(JobStore):
    def __init__(self):
        self._jobs = {}
        self._idempotency = {}

    def create_job(self, record):
        self._jobs[record.job_id] = record
        if record.idempotency_key:
            self._idempotency[record.idempotency_key] = record.job_id
        return record

    def get_job(self, job_id):
        return self._jobs.get(job_id)

    def get_job_by_idempotency(self, idempotency_key):
        job_id = self._idempotency.get(idempotency_key)
        if not job_id:
            return None
        return self._jobs.get(job_id)

    def update_job(self, job_id, **updates):
        current = self._jobs[job_id]
        updated = current.model_copy(update={"updated_at": utc_now(), **updates})
        self._jobs[job_id] = updated
        return updated


class InMemoryReceivedQueueMessage(ReceivedQueueMessage):
    def __init__(self, queue, message):
        super().__init__(message)
        self._queue = queue
        self.completed = False
        self.abandoned = False

    def complete(self):
        self.completed = True

    def abandon(self):
        self.abandoned = True
        self._queue.requeue(self.message)


class InMemoryJobQueue(JobQueue):
    def __init__(self):
        self._messages = deque()

    def enqueue(self, message):
        self._messages.append(message)

    def dequeue(self, max_wait_time=5):
        if not self._messages:
            return None
        return InMemoryReceivedQueueMessage(self, self._messages.popleft())

    def requeue(self, message):
        self._messages.appendleft(message)

    def depth(self):
        return len(self._messages)


class RecordingWebhookDispatcher(WebhookDispatcher):
    def __init__(self, result=None):
        self.calls = []
        self._result = result or {"attempts": 1, "status_code": 200, "error": None}

    def deliver(self, record, payload):
        self.calls.append({"record": record, "payload": payload})
        return dict(self._result)


@dataclass
class ServiceContainer:
    settings: Settings
    blob_storage: BlobStorage
    job_store: JobStore
    job_queue: JobQueue
    webhook_dispatcher: WebhookDispatcher


def build_service_container(settings=None):
    resolved_settings = settings or get_settings()
    if not resolved_settings.azure_service_bus_connection_string:
        raise RuntimeError("Missing AZURE_SERVICE_BUS_CONNECTION_STRING.")
    if not resolved_settings.azure_storage_connection_string:
        raise RuntimeError("Missing AZURE_STORAGE_CONNECTION_STRING.")
    return ServiceContainer(
        settings=resolved_settings,
        blob_storage=AzureBlobStorage(
            connection_string=resolved_settings.azure_storage_connection_string,
            container_name=resolved_settings.azure_blob_container_name,
        ),
        job_store=AzureTableJobStore(
            connection_string=resolved_settings.azure_storage_connection_string,
            table_name=resolved_settings.azure_table_name,
        ),
        job_queue=AzureServiceBusQueue(
            connection_string=resolved_settings.azure_service_bus_connection_string,
            queue_name=resolved_settings.azure_service_bus_queue_name,
        ),
        webhook_dispatcher=RequestsWebhookDispatcher(
            timeout_seconds=resolved_settings.webhook_timeout_seconds,
            max_attempts=resolved_settings.webhook_max_attempts,
            backoff_seconds=resolved_settings.webhook_retry_backoff_seconds,
        ),
    )
