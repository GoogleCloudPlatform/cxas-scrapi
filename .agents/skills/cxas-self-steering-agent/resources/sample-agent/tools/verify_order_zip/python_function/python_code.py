# pylint: disable=missing-module-docstring,undefined-variable
import re
from typing import Any

# Failed attempts are counted for the whole conversation, so switching orders
# doesn't start the count over.
MAX_ZIP_ATTEMPTS = 2


def verify_order_zip(postal_code: str) -> dict[str, Any]:
  """Verifies the 5-digit shipping ZIP code for the currently loaded order.

  Args:
    postal_code (str): The 5-digit shipping ZIP code the customer provided, as a
      string.

  Returns:
    dict[str, Any]: Verification status ('verified', 'invalid_zip',
      'zip_mismatch', 'verification_failed', or 'no_active_order'), verified
      flag, the order's details once verified, and remaining attempts on a
      mismatch.
  """
  order = context.state.get("shared") or {}
  if not order.get("order_id"):
    return {"status": "no_active_order", "verified": False}

  code = (postal_code or "").strip()
  if not re.fullmatch(r"\d{5}", code):
    return {"status": "invalid_zip", "verified": False}

  if code == order.get("on_file_zip"):
    context.state["shared"] = {**order, "zip_verified": True}
    return {
        "status": "verified",
        "verified": True,
        "order_number": order.get("order_id") or "",
        "item": order.get("order_item") or "",
        "order_status": order.get("order_status") or "",
        "delivery": order.get("order_delivery") or "",
    }

  # Numbers in OBJECT variables come back as floats.
  attempts = int(order.get("zip_attempts") or 0) + 1
  context.state["shared"] = {**order, "zip_attempts": attempts}
  if attempts >= MAX_ZIP_ATTEMPTS:
    context.state["pending_handoff_reason"] = "zip_verification_failed"
    return {
        "status": "verification_failed",
        "verified": False,
        "attempts_remaining": 0,
    }
  return {
      "status": "zip_mismatch",
      "verified": False,
      "attempts_remaining": MAX_ZIP_ATTEMPTS - attempts,
  }
