

from __future__ import annotations

import asyncio
import sys


async def _main() -> int:
    from infrastructure.config import get_settings
    from infrastructure.expense_submit_mail import send_expense_smtp_test

    try:
        recipients = await send_expense_smtp_test(get_settings())
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"SMTP error: {e}", file=sys.stderr)
        return 2
    print("OK: test email sent to:", ", ".join(recipients))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
