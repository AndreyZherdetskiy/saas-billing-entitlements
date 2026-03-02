"""Locust HttpUser scenarios: evaluate, usage ingest, admin usage read.

Official docs: https://docs.locust.io/en/stable/quickstart.html
               https://docs.locust.io/en/stable/writing-a-locustfile.html
               https://docs.locust.io/en/stable/running-without-web-ui.html
Run: `make load-locust` or `uv run --group load locust -f loadtests/locustfile.py`
"""

from __future__ import annotations

import uuid
from typing import Any

from locust import HttpUser, between, constant, events, task

from loadtests.config import load_api_key, load_feature_key, load_org_id, load_wait_bounds
from loadtests.preflight import MIN_SMOKE_REQUESTS, PreflightError, assert_minimum_requests


def _resolve_wait_time():
    min_wait, max_wait = load_wait_bounds()
    if min_wait <= 0 and max_wait <= 0:
        return constant(0)
    return between(min_wait, max_wait)


class BillingAuthMixin:
    """Shared auth + wait. Not a User subclass — Locust only spawns HttpUser classes."""

    wait_time = _resolve_wait_time()

    def on_start(self) -> None:
        self.org_id = load_org_id()
        self.feature_key = load_feature_key()
        self._auth_headers = {
            "Authorization": f"Bearer {load_api_key()}",
            "Content-Type": "application/json",
        }


class EvaluateUser(BillingAuthMixin, HttpUser):
    weight = 9

    @task
    def evaluate_entitlement(self) -> None:
        with self.client.post(
            "/v1/entitlements/evaluate",
            json={
                "organization_public_id": self.org_id,
                "checks": [{"feature_key": self.feature_key, "quantity": 1}],
            },
            headers=self._auth_headers,
            name="/v1/entitlements/evaluate",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}: {response.text}")


class UsageIngestUser(BillingAuthMixin, HttpUser):
    weight = 4

    @task
    def ingest_usage_batch(self) -> None:
        with self.client.post(
            "/v1/usage/events/batch",
            json={
                "organization_public_id": self.org_id,
                "events": [
                    {
                        "feature_key": self.feature_key,
                        "quantity": 1,
                        "idempotency_key": f"locust-{uuid.uuid4()}",
                    }
                ],
            },
            headers=self._auth_headers,
            name="/v1/usage/events/batch",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}: {response.text}")


class AdminReadUser(BillingAuthMixin, HttpUser):
    weight = 2

    @task
    def read_org_usage(self) -> None:
        headers = {"Authorization": self._auth_headers["Authorization"]}
        with self.client.get(
            f"/v1/organizations/{self.org_id}/usage",
            headers=headers,
            name="/v1/organizations/{org}/usage",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"expected 200, got {response.status_code}: {response.text}")


@events.quitting.add_listener
def _fail_on_zero_requests(environment: Any, **_: object) -> None:
    total = environment.stats.total
    try:
        assert_minimum_requests(request_count=total.num_requests, minimum=MIN_SMOKE_REQUESTS)
    except PreflightError:
        environment.process_exit_code = 1
