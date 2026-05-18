"""Slot-filling DAG configuration for the Bella Notte Host."""

from typing import Any


def _bella_notte_config() -> dict[str, Any]:
  """Reservation flow for the Bella Notte Host."""
  return {
      "slots": [
          {
              "name": "welcome",
              "source": "announce",
              "message": (
                  "Welcome to Bella Notte! I'd be happy"
                  " to help you with a reservation."
              ),
              "preempt": False,
              "response": [
                  {"type": "text", "text": (
                      "Welcome to Bella Notte! I'd be happy"
                      " to help you with a reservation."
                  )},
                  {"type": "payload", "data": {
                      "richContent": [[{
                          "type": "info",
                          "title": "Bella Notte",
                          "subtitle": (
                              "Welcome! I'd be happy to help"
                              " you with a reservation."
                          ),
                      }]],
                  }},
              ],
          },
          {
              "name": "party_size",
              "source": ["event", "user"],
              "event_key": "party_size",
              "setter": "set_reservation_basics",
              "setter_field": "party_size",
              "hint": "Party size / number of users",
              "ask": "How many guests will be dining?",
              "readback_fmt": {
                  "type": "plural",
                  "one": "guest",
                  "other": "guests",
              },
              "requires_readback": True,
              "response": [
                  {"type": "payload", "data": {
                      "richContent": [[
                          {"type": "chips", "options": [
                              {"text": "2"},
                              {"text": "3"},
                              {"text": "4"},
                              {"text": "5"},
                              {"text": "6"},
                          ]},
                      ]],
                  }},
              ],
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "out_of_range": (
                          "I'm sorry, we accept reservations"
                          " for parties of 1 to 8. For larger"
                          " parties, please contact our events"
                          " team at events@bellanotte.com."
                      ),
                      "parse_error": (
                          "I didn't catch the number of"
                          " guests. How many will be dining?"
                      ),
                  },
                  "error_responses": {
                      "out_of_range": [
                          {"type": "text", "text": (
                              "We accept reservations for"
                              " parties of 1 to 8."
                          )},
                          {"type": "payload", "data": {
                              "richContent": [[
                                  {"type": "chips", "options": [
                                      {"text": "2"},
                                      {"text": "3"},
                                      {"text": "4"},
                                      {"text": "5"},
                                      {"text": "6"},
                                  ]},
                              ]],
                          }},
                      ],
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the party"
                          " size. Please call us at 555-0100"
                          " and we'll help you directly."
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
          },
          {
              "name": "large_party_phone",
              "source": "user",
              "setter": "set_large_party_phone",
              "hint": (
                  "Contact phone number"
                  " (for parties of 5 or more)"
              ),
              "condition": (
                  "lambda filled:"
                  " int(filled.get('party_size', 0)) >= 5"
              ),
              "ask": (
                  "For parties of 5 or more, we require a"
                  " contact phone number in case we need to"
                  " reach you about your reservation."
                  " What's the best number?"
              ),
              "readback_fmt": {
                  "type": "prefix",
                  "text": "contact phone",
              },
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "invalid_phone": (
                          "I didn't catch a valid phone"
                          " number. Could you provide a"
                          " number with at least 7 digits?"
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the phone"
                          " number. Please call us at"
                          " 555-0100 and we'll help you"
                          " directly."
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
          },
          {
              "name": "preferred_date",
              "source": "user",
              "setter": "set_reservation_basics",
              "setter_field": "preferred_date",
              "hint": "Date",
              "ask": (
                  "What date would you like to come in?"
              ),
              "readback_fmt": "date",
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "invalid_format": (
                          "Could you provide the date?"
                          " For example, June 17th."
                      ),
                      "past_date": (
                          "That date is in the past."
                          " Could you provide a future"
                          " date?"
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the"
                          " date. Please call us at"
                          " 555-0100 and we'll help you"
                          " directly."
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
          },
          {
              "name": "available_times",
              "source": "task:FindAvailableTimes",
          },
          {
              "name": "selected_time",
              "source": "user",
              "setter": "set_selected_time",
              "hint": (
                  "Time (from the presented options)"
              ),
              "requires": ["available_times"],
              "validate_against": {
                  "response_field": "display_value",
                  "filled_slot": "available_times",
                  "error_code": "not_available",
              },
              "ask": (
                  "We have availability at"
                  " {available_times}. Which time"
                  " works best for you?"
              ),
              "response": [
                  {"type": "payload", "data": {
                      "richContent": [[{
                          "type": "description",
                          "title": (
                              "Available times for"
                              " {preferred_date}"
                          ),
                          "text": [
                              "{available_times}",
                          ],
                      }]],
                  }},
              ],
              "readback_fmt": "time",
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "prereq_not_met": (
                          "I'd love to get you that time!"
                          " I just need to check"
                          " availability first. How many"
                          " guests will be joining us, and"
                          " what date works best for you?"
                      ),
                      "not_available": (
                          "That time isn't available for"
                          " your party size. We do have"
                          " {available_times} — would"
                          " any of those work for you?"
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the"
                          " time selection. Please call"
                          " us at 555-0100 and we'll"
                          " help you directly."
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
          },
          {
              "name": "guest_name",
              "source": "user",
              "setter": "set_guest_info",
              "setter_field": "guest_name",
              "hint": (
                  "Name (any format, don't ask for"
                  " a specific format)"
              ),
              "ask": (
                  "What name should I put the"
                  " reservation under?"
              ),
              "readback_fmt": {
                  "type": "prefix",
                  "text": "under the name",
              },
              "requires_readback": True,
              "validation": {
                  "max_retries": 3,
                  "errors": {
                      "empty_name": (
                          "I didn't catch the name."
                          " What name should I put the"
                          " reservation under?"
                      ),
                  },
                  "on_exhaust": {
                      "say": (
                          "I'm having trouble with the"
                          " name. Please call us at"
                          " 555-0100 and we'll help you"
                          " directly."
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
          },
          {
              "name": "special_requests",
              "source": "user",
              "setter": "set_guest_info",
              "setter_field": "special_requests",
              "hint": 'Special requests or "none"',
              "ask": (
                  "Do you have any special requests"
                  " or dietary needs?"
              ),
              "readback_fmt": {
                  "type": "none_sub",
                  "default": "no special requests",
              },
              "requires_readback": True,
          },
          {
              "name": "confirmation_number",
              "source": "task:BookReservation",
          },
      ],
      "tasks": [
          {
              "name": "FindAvailableTimes",
              "tool": "find_available_times",
              "inputs": [
                  "party_size",
                  "preferred_date",
              ],
              "outputs": {
                  "available_times": "available_times",
              },
              "success_check": "success",
              "then_say": "Great choice!",
              "on_failure": {
                  "retry_say": (
                      "I'm sorry, we don't have"
                      " availability for that date and"
                      " party size. Could you try a"
                      " different date?"
                  ),
                  "max_retries": 1,
                  "clear_slots": ["preferred_date"],
                  "on_exhaust": {
                      "say": (
                          "I'm unable to find availability"
                          " for your request. Please call"
                          " us at 555-0100 to check for"
                          " openings."
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
          },
          {
              "name": "BookReservation",
              "tool": "book_reservation",
              "inputs": [
                  "party_size",
                  "large_party_phone",
                  "preferred_date",
                  "selected_time",
                  "guest_name",
                  "special_requests",
              ],
              "readback_inputs": True,
              "outputs": {
                  "confirmation_number":
                      "confirmation_number",
              },
              "success_check": "success",
              "terminal": True,
              "then_say": (
                  "Your reservation is confirmed."
                  " Your confirmation number is"
                  " {confirmation_number}. We look"
                  " forward to welcoming you to"
                  " Bella Notte!"
              ),
              "then_response": [
                  {"type": "payload", "data": {
                      "richContent": [[{
                          "type": "info",
                          "title": "Reservation Confirmed",
                          "subtitle": (
                              "Confirmation"
                              " #{confirmation_number}"
                          ),
                          "text": (
                              "{party_size} guests on"
                              " {preferred_date} at"
                              " {selected_time} under"
                              " {guest_name}"
                          ),
                      }]],
                  }},
              ],
              "on_failure": {
                  "retry_say": (
                      "I'm having a bit of trouble"
                      " completing your reservation."
                      " Let me try once more."
                  ),
                  "max_retries": 2,
                  "on_exhaust": {
                      "say": (
                          "I'm sorry, I wasn't able to"
                          " complete your reservation."
                          " Please call us directly at"
                          " 555-0100 and we'll get you"
                          " sorted."
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
          },
      ],
      "confirm_transition_prefix": [
          "Wonderful!", "Perfect!", "Great!",
          "Excellent!", "Lovely!",
      ],
      "steer_back": {
          "soft_after": 2,
          "hard_after": 4,
          "escalate_after": 6,
          "on_exhaust": {
              "say": (
                  "I'm having trouble completing"
                  " your reservation. Please call"
                  " us at 555-0100 and we'll help"
                  " you directly."
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


def bella_notte_dag() -> dict[str, Any]:
  """Return the DAG config for the Bella Notte Host."""
  return _bella_notte_config()
