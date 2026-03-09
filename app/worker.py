import time

from app.config import get_settings
from app.infrastructure import build_service_container
from app.processor import DocumentJobProcessor


class DocumentWorker:
    def __init__(self, services=None):
        self._services = services or build_service_container()
        self._processor = DocumentJobProcessor(self._services)

    def process_next_message(self, max_wait_time=None):
        wait_time = max_wait_time or self._services.settings.worker_poll_seconds
        received = self._services.job_queue.dequeue(max_wait_time=wait_time)
        if received is None:
            return False
        try:
            self._processor.process_job(received.message.job_id)
            received.complete()
            return True
        except Exception:
            received.abandon()
            raise

    def run_forever(self):
        while True:
            processed = self.process_next_message(max_wait_time=self._services.settings.worker_poll_seconds)
            if not processed:
                time.sleep(self._services.settings.worker_poll_seconds)


def main():
    worker = DocumentWorker()
    worker.run_forever()


if __name__ == "__main__":
    main()
