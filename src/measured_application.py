"""Timing adapter; preserves the six implementation-fingerprinted modules."""
from src.case_application import CaseApplication


class MeasuredApplication(CaseApplication):
    def __init__(self, *args, timings, **kwargs):
        self.timings = timings
        super().__init__(*args, **kwargs)

    def submit(self, request, key):
        # Includes validation/hash, idempotency lookup, snapshot for new requests,
        # and persistence. Replays are samples too; not pure snapshot latency.
        with self.timings.stage('submission'):
            return super().submit(request, key)

    def tick(self):
        # A cycle may process zero or several cases, including recovery. This
        # cannot be interpreted as per-case analysis time or added to HTTP time.
        with self.timings.stage('worker_cycle'):
            return super().tick()

    def decide(self, case_id, payload):
        # Includes lock waiting, validation and durable decision/checkpoint writes.
        with self.timings.stage('decision'):
            return super().decide(case_id, payload)
