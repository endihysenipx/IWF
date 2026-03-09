import hashlib
import json
import os
import sqlite3
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
        if data.get("billing_summary") is not None:
            data["billing_summary"] = json.dumps(data["billing_summary"])
        return data

    @staticmethod
    def _entity_to_record(entity):
        payload = {k: v for k, v in entity.items() if k not in {"PartitionKey", "RowKey", "etag"}}
        payload["job_id"] = entity["RowKey"]
        payload["status"] = JobStatus(payload["status"])
        payload["created_at"] = _iso_to_datetime(payload.get("created_at"))
        payload["updated_at"] = _iso_to_datetime(payload.get("updated_at"))
        if payload.get("billing_summary"):
            payload["billing_summary"] = json.loads(payload["billing_summary"])
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


class LocalFileBlobStorage(BlobStorage):
    def __init__(self, root_dir):
        self._root_dir = os.path.join(root_dir, "blobs")
        os.makedirs(self._root_dir, exist_ok=True)

    def upload_bytes(self, blob_name, payload, content_type):
        target_path = self._path_for(blob_name)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as handle:
            handle.write(payload)
        return blob_name

    def download_bytes(self, blob_name):
        with open(self._path_for(blob_name), "rb") as handle:
            return handle.read()

    def _path_for(self, blob_name):
        normalized = blob_name.replace("/", os.sep).replace("\\", os.sep)
        return os.path.join(self._root_dir, normalized)


class SqliteJobStore(JobStore):
    def __init__(self, db_path):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._initialize()

    def create_job(self, record):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, status, created_at, updated_at, callback_url, correlation_id,
                    input_blob_name, output_blob_name, error_code, error_message,
                    document_number, order_document_number, idempotency_key,
                    callback_attempts, callback_last_status_code, callback_last_error, billing_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._record_to_row(record),
            )
        return record

    def get_job(self, job_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_job_by_idempotency(self, idempotency_key):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def update_job(self, job_id, **updates):
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(f"Unknown job id {job_id}")
        updated = current.model_copy(update={"updated_at": utc_now(), **updates})
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET
                    status = ?, created_at = ?, updated_at = ?, callback_url = ?, correlation_id = ?,
                    input_blob_name = ?, output_blob_name = ?, error_code = ?, error_message = ?,
                    document_number = ?, order_document_number = ?, idempotency_key = ?,
                    callback_attempts = ?, callback_last_status_code = ?, callback_last_error = ?,
                    billing_summary = ?
                WHERE job_id = ?
                """,
                (
                    updated.status.value if isinstance(updated.status, JobStatus) else str(updated.status),
                    updated.created_at.isoformat(),
                    updated.updated_at.isoformat(),
                    updated.callback_url,
                    updated.correlation_id,
                    updated.input_blob_name,
                    updated.output_blob_name,
                    updated.error_code,
                    updated.error_message,
                    updated.document_number,
                    updated.order_document_number,
                    updated.idempotency_key,
                    updated.callback_attempts,
                    updated.callback_last_status_code,
                    updated.callback_last_error,
                    json.dumps(updated.billing_summary) if updated.billing_summary is not None else None,
                    job_id,
                ),
            )
        return updated

    def _connect(self):
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    callback_url TEXT NOT NULL,
                    correlation_id TEXT,
                    input_blob_name TEXT,
                    output_blob_name TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    document_number TEXT,
                    order_document_number TEXT,
                    idempotency_key TEXT UNIQUE,
                    callback_attempts INTEGER NOT NULL DEFAULT 0,
                    callback_last_status_code INTEGER,
                    callback_last_error TEXT,
                    billing_summary TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
            if "billing_summary" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN billing_summary TEXT")

    @staticmethod
    def _record_to_row(record):
        return (
            record.job_id,
            record.status.value if isinstance(record.status, JobStatus) else str(record.status),
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
            record.callback_url,
            record.correlation_id,
            record.input_blob_name,
            record.output_blob_name,
            record.error_code,
            record.error_message,
            record.document_number,
            record.order_document_number,
            record.idempotency_key,
            record.callback_attempts,
            record.callback_last_status_code,
            record.callback_last_error,
            json.dumps(record.billing_summary) if record.billing_summary is not None else None,
        )

    @staticmethod
    def _row_to_record(row):
        return JobRecord(
            job_id=row["job_id"],
            status=JobStatus(row["status"]),
            created_at=_iso_to_datetime(row["created_at"]),
            updated_at=_iso_to_datetime(row["updated_at"]),
            callback_url=row["callback_url"],
            correlation_id=row["correlation_id"],
            input_blob_name=row["input_blob_name"],
            output_blob_name=row["output_blob_name"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            document_number=row["document_number"],
            order_document_number=row["order_document_number"],
            idempotency_key=row["idempotency_key"],
            callback_attempts=row["callback_attempts"],
            callback_last_status_code=row["callback_last_status_code"],
            callback_last_error=row["callback_last_error"],
            billing_summary=json.loads(row["billing_summary"]) if row["billing_summary"] else None,
        )


class SqliteReceivedQueueMessage(ReceivedQueueMessage):
    def __init__(self, db_path, queue_id, message):
        super().__init__(message)
        self._db_path = db_path
        self._queue_id = queue_id

    def complete(self):
        with sqlite3.connect(self._db_path, timeout=30) as connection:
            connection.execute("DELETE FROM queue WHERE id = ?", (self._queue_id,))

    def abandon(self):
        with sqlite3.connect(self._db_path, timeout=30) as connection:
            connection.execute("UPDATE queue SET status = 'queued' WHERE id = ?", (self._queue_id,))


class SqliteJobQueue(JobQueue):
    def __init__(self, db_path):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._initialize()

    def enqueue(self, message):
        with sqlite3.connect(self._db_path, timeout=30) as connection:
            connection.execute(
                "INSERT INTO queue (payload, status) VALUES (?, 'queued')",
                (message.model_dump_json(),),
            )

    def dequeue(self, max_wait_time=5):
        with sqlite3.connect(self._db_path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, payload FROM queue WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute("UPDATE queue SET status = 'inflight' WHERE id = ?", (row["id"],))
            connection.commit()
        message = QueueMessage.model_validate_json(row["payload"])
        return SqliteReceivedQueueMessage(self._db_path, row["id"], message)

    def _initialize(self):
        with sqlite3.connect(self._db_path, timeout=30) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )


def build_service_container(settings=None):
    resolved_settings = settings or get_settings()
    use_local = (
        resolved_settings.local_dev_mode
        or not resolved_settings.azure_service_bus_connection_string
        or not resolved_settings.azure_storage_connection_string
    )
    if use_local:
        db_path = os.path.join(resolved_settings.local_data_dir, "document_jobs.sqlite3")
        return ServiceContainer(
            settings=resolved_settings,
            blob_storage=LocalFileBlobStorage(resolved_settings.local_data_dir),
            job_store=SqliteJobStore(db_path),
            job_queue=SqliteJobQueue(db_path),
            webhook_dispatcher=RequestsWebhookDispatcher(
                timeout_seconds=resolved_settings.webhook_timeout_seconds,
                max_attempts=resolved_settings.webhook_max_attempts,
                backoff_seconds=resolved_settings.webhook_retry_backoff_seconds,
            ),
        )
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
