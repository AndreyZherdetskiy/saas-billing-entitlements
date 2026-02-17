"""CLI: ``python -m billing_platform.bootstrap`` — run deterministic demo seed."""

from __future__ import annotations

import asyncio
import json
import sys

from billing_platform.bootstrap.demo_seed import ensure_demo_seed
from billing_platform.db import get_session_factory


async def _main() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await ensure_demo_seed(session)
        await session.commit()
    payload = {
        "platform_admin_key_created": result.key_created,
        "organization_public_id": str(result.organization_public_id),
        "subscription_public_id": str(result.subscription_public_id),
        "plan_id": str(result.plan_id),
        "external_subscription_id": result.external_subscription_id,
        "feature_keys": list(result.feature_keys),
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
    print("Demo seed ready (deterministic local keys).", file=sys.stderr)
