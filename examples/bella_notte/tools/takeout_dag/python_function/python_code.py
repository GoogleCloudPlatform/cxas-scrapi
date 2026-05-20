"""Slot-filling DAG configuration for the Takeout Agent."""

from typing import Any


def _takeout_config() -> dict[str, Any]:
  """Takeout ordering flow for the Takeout Agent."""
  return {
      "bootstrap": {
          "tool": "set_active_flow",
          "slot": "active_flow",
          "reset_on_complete": True,
          "welcome_slot": "welcome",
      },
      "gate_slot": "active_flow",
      "slots": [
          {
              "name": "welcome",
              "source": "announce",
              "message": (
                  "Welcome to Bella Notte Takeout! I'd be happy"
                  " to help you with your takeout order."
              ),
              "preempt": False,
              "response": [
                  {"type": "text", "text": (
                      "Welcome to Bella Notte Takeout! I'd be happy"
                      " to help you with your takeout order."
                  )},
                  {"type": "payload", "data": {
                      "richContent": [[{
                          "type": "info",
                          "title": "Bella Notte Takeout",
                          "subtitle": (
                              "Welcome! I'd be happy to help"
                              " you with your takeout order."
                          ),
                      }]],
                  }},
              ],
          },
          {
              "name": "active_flow",
              "source": "user",
              "setter": "set_active_flow",
              "hint": "Flow type (takeout)",
              "requires_readback": False,
          },
          {
              "name": "takeout_items",
              "source": "user",
              "setter": "set_takeout_items",
              "condition": "lambda f: f.get('active_flow') == 'takeout'",
              "hint": "Items to order",
              "ask": "What dishes would you like to order for takeout?",
              "readback_fmt": {
                  "type": "prefix",
                  "text": "an order of",
              },
              "requires_readback": True,
          },
          {
              "name": "pickup_time",
              "source": "user",
              "setter": "set_pickup_time",
              "condition": "lambda f: f.get('active_flow') == 'takeout'",
              "hint": "Pickup time",
              "ask": "What time would you like to pick up your order?",
              "readback_fmt": "time",
              "requires_readback": True,
          },
          {
              "name": "guest_name",
              "source": "user",
              "setter": "set_takeout_guest_name",
              "condition": "lambda f: f.get('active_flow') == 'takeout'",
              "hint": "Name for the order",
              "ask": "Under what name should I put the order?",
              "readback_fmt": {
                  "type": "prefix",
                  "text": "under the name",
              },
              "requires_readback": True,
          },
          {
              "name": "contact_phone",
              "source": "user",
              "setter": "set_takeout_phone",
              "condition": "lambda f: f.get('active_flow') == 'takeout'",
              "hint": "Contact phone number",
              "ask": "And what is a good phone number to reach you if needed?",
              "readback_fmt": {
                  "type": "prefix",
                  "text": "contact phone",
              },
              "requires_readback": True,
          },
          {
              "name": "order_confirmation",
              "source": "task:PlaceTakeoutOrder",
          },
      ],
      "tasks": [
          {
              "name": "PlaceTakeoutOrder",
              "tool": "place_takeout_order",
              "inputs": [
                  "takeout_items",
                  "pickup_time",
                  "guest_name",
                  "contact_phone",
              ],
              "readback_inputs": True,
              "outputs": {
                  "confirmation_code": "order_confirmation",
              },
              "success_check": "success",
              "terminal": True,
              "then_say": (
                  "Perfect! Your takeout order is placed and confirmed. "
                  "Your confirmation number is {order_confirmation}. "
                  "We will have it hot and ready for you under the name "
                  "{guest_name} at {pickup_time}!"
              ),
          },
      ],
      "confirm_transition_prefix": [
          "Perfect!", "Great!", "Got it!", "Alright!", "Wonderful!",
      ],
      "readback_response": [
          {"type": "payload", "data": {
              "richContent": [[{
                  "type": "chips",
                  "options": [
                      {"text": "Yes, that's correct"},
                      {"text": "No, let me change something"},
                  ],
              }]],
          }},
      ],
      "steer_back": {
          "soft_after": 2,
          "hard_after": 4,
          "escalate_after": 6,
          "on_exhaust": {
              "say": (
                  "I'm having trouble completing your takeout order. "
                  "Please call us directly at 555-0100 and we'll get you "
                  "sorted."
              ),
              "then": {
                  "tool": "end_session",
                  "args": {
                      "reason": "retry_exhausted",
                      "session_escalated": True,
                  },
              },
          },
      },
  }


def takeout_dag() -> dict[str, Any]:
  """Return the DAG config for the Takeout Agent."""
  return _takeout_config()
