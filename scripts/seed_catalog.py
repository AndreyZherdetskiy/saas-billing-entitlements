#!/usr/bin/env python3
"""CLI for ``billing_platform.bootstrap.demo_seed`` (also billing-api entrypoint)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from billing_platform.bootstrap.demo_seed import ensure_demo_seed
from billing_platform.db import get_session_factory

SEED_OUTPUT_PATH = Path(".local/seed-output.json")


def _write_output(payload: dict[str, object]) -> None:
    SEED_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


async def main() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await ensure_demo_seed(session)
        await session.commit()

    output: dict[str, object] = {
        "platform_admin_key": result.platform_admin_key,
        "platform_admin_key_created": result.key_created,
        "organization_public_id": str(result.organization_public_id),
        "subscription_public_id": str(result.subscription_public_id),
        "plan_id": str(result.plan_id),
        "external_subscription_id": result.external_subscription_id,
        "feature_keys": list(result.feature_keys),
        "seed_output_path": str(SEED_OUTPUT_PATH),
    }
    _write_output(output)
    print(json.dumps(output, indent=2))
    print(
        "\nCatalog seeded. DEMO_UI_* defaults match bootstrap constants "
        "(see .env.example). Demo UI: http://localhost:8080",
        file=sys.stderr,
    )


if __name__ == "__main__":
    asyncio.run(main())
