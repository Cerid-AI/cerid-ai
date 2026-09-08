"""Track and delete artifacts ingested by the beta harness.

The harness runs against the personal knowledge base, so every ingest it
performs must be undone: track the artifact_id from each ingest response,
then delete it via DELETE /admin/artifacts/{id} at teardown. No clear-domain,
purge, or reset endpoint is ever used.
"""
import time

import httpx

_RETRY_AFTER_S = 2.0


class KbCleanup:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self.ids: list[str] = []

    def track(self, payload: dict) -> str | None:
        if not isinstance(payload, dict):
            return None
        artifact_id = payload.get("artifact_id") or payload.get("id")
        if artifact_id:
            self.ids.append(artifact_id)
        return artifact_id

    def delete_all(self) -> list[str]:
        failed: list[str] = []
        for artifact_id in self.ids:
            for attempt in range(2):
                resp = self._client.delete(f"/admin/artifacts/{artifact_id}")
                if resp.status_code in (200, 404):
                    break
                if resp.status_code == 429 and attempt == 0:
                    time.sleep(_RETRY_AFTER_S)
                    continue
                failed.append(artifact_id)
                break
        return failed
