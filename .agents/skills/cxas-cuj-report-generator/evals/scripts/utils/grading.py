import json
import os
import re
import yaml


def extract_and_parse_yaml(output):
    # Parse YAML output safely
    try:
        clean_yaml = ""
        if "```yaml" in output:
            match = re.search(r"```yaml\s*(.*?)\s*```", output, re.DOTALL)
            if match:
                clean_yaml = match.group(1).strip()
        elif "```" in output:
            match = re.search(r"```\s*(.*?)\s*```", output, re.DOTALL)
            if match:
                clean_yaml = match.group(1).strip()

        if not clean_yaml:
            cuj_idx = output.find("cujs:")
            list_idx = output.find("- subintent_id:")
            dict_idx = output.find("subintent_id:")
            if cuj_idx != -1:
                clean_yaml = output[cuj_idx:].strip()
            elif list_idx != -1:
                clean_yaml = output[list_idx:].strip()
            elif dict_idx != -1:
                clean_yaml = output[dict_idx:].strip()
            else:
                clean_yaml = output.strip()

        if "---" in clean_yaml:
            docs = yaml.safe_load_all(clean_yaml)
            data = [d for d in docs if d is not None]
        else:
            data = yaml.safe_load(clean_yaml)
        return data, None
    except Exception as e:
        return None, f"Output is not valid YAML: {e}"


def normalize_to_transcripts(data):
    # Normalize data to a list of transcripts
    transcripts = []
    if isinstance(data, list):
        transcripts = data
    elif isinstance(data, dict):
        if "cujs" in data:
            if isinstance(data["cujs"], list):
                transcripts = data["cujs"]
            elif isinstance(data["cujs"], dict):
                for cuj_name, cuj_data in data["cujs"].items():
                    if isinstance(cuj_data, dict):
                        for subintent_id, subintent_data in cuj_data.items():
                            if isinstance(subintent_data, dict):
                                t_copy = subintent_data.copy()
                                t_copy["subintent_id"] = subintent_id
                                t_copy["parent_cuj"] = cuj_name
                                transcripts.append(t_copy)
        else:
            transcripts = [data]
    else:
        return None, f"Parsed YAML has invalid root type: {type(data)}"

    return transcripts, None


def check_basic_turn_structure(turns, idx):
    for t in turns:
        speaker = t.get("speaker")
        text = t.get("text", "")
        if speaker not in ["Agent", "User"]:
            return (
                False,
                f"Transcript #{idx + 1}: Invalid speaker state: {speaker}",
            )
        if not text:
            return False, f"Transcript #{idx + 1}: Empty dialogue text in turn."
    return True, ""


def check_html_and_escaped_entities(turns, idx):
    for t in turns:
        text = t.get("text", "")
        if re.search(r"<[^>]+>", text):
            return (
                False,
                f"Transcript #{idx + 1}: Dialogue contains HTML tags: {text}",
            )
        if (
            "&lt;" in text
            or "&gt;" in text
            or "&quot;" in text
            or "&amp;" in text
        ):
            return (
                False,
                (
                    f"Transcript #{idx + 1}: Dialogue contains escaped HTML entities:"
                    f" {text}"
                ),
            )
    return True, ""


def check_first_turn_is_agent(turns, idx):
    first_turn = turns[0]
    if first_turn.get("speaker") != "Agent":
        return False, f"Transcript #{idx + 1}: Does not start with the Agent."
    return True, ""


def check_no_spoken_urls(turns, idx):
    for t in turns:
        if t.get("speaker") == "Agent":
            text = t.get("text", "")
            if (
                "http://" in text
                or "https://" in text
                or "www." in text
                or ".com" in text
            ):
                return (
                    False,
                    f"Transcript #{idx + 1}: Agent turn contains spoken URL: {text}",
                )
    return True, ""


def check_end_session(turns, idx):
    if len(turns) < 3:
        return False, f"Transcript #{idx + 1} has less than 3 turns."

    last_turn = turns[-1]
    if last_turn.get("speaker") != "Agent":
        return False, f"Transcript #{idx + 1}: Last turn is not the Agent."

    last_text = last_turn.get("text", "").lower()
    allowed_goodbyes = [
        "goodbye",
        "thank you for calling",
        "thanks for calling",
        "have a great day",
        "have a wonderful day",
    ]
    if not any(g in last_text for g in allowed_goodbyes):
        return (
            False,
            (
                f"Transcript #{idx + 1}: Last turn is not a standard goodbye:"
                f" {last_turn.get('text')}"
            ),
        )

    allowed_tools = ["end_session", "skill_completed"]
    tool_call = last_turn.get("tool_call")
    if not tool_call:
        return (
            False,
            (
                f"Transcript #{idx + 1}: Last Agent turn is missing the session"
                " closer tool call."
            ),
        )

    if isinstance(tool_call, str):
        if tool_call not in allowed_tools:
            return (
                False,
                f"Transcript #{idx + 1}: Incorrect tool call string value: {tool_call}",
            )
    elif isinstance(tool_call, dict):
        if tool_call.get("name") not in allowed_tools:
            return (
                False,
                (
                    f"Transcript #{idx + 1}: Incorrect system tool call name:"
                    f" {tool_call.get('name')}"
                ),
            )
    else:
        return (
            False,
            f"Transcript #{idx + 1}: Invalid tool_call type: {type(tool_call)}",
        )

    return True, ""


def validate_transcripts(transcripts, expectations):
    for idx, transcript in enumerate(transcripts):
        if not isinstance(transcript, dict):
            continue

        turns = transcript.get("turns", [])
        if not turns:
            return False, f"No turns found in transcript #{idx + 1}."

        passed, error = check_basic_turn_structure(turns, idx)
        if not passed:
            return passed, error

        passed, error = check_html_and_escaped_entities(turns, idx)
        if not passed:
            return passed, error

        passed, error = check_first_turn_is_agent(turns, idx)
        if not passed:
            return passed, error

        passed, error = check_no_spoken_urls(turns, idx)
        if not passed:
            return passed, error

        passed, error = check_end_session(turns, idx)
        if not passed:
            return passed, error

    return True, "All expectations satisfied."


def grade_transcript_compliance(output, expectations):
    data, error = extract_and_parse_yaml(output)
    if error:
        return False, error

    transcripts, error = normalize_to_transcripts(data)
    if error:
        return False, error

    return validate_transcripts(transcripts, expectations)


def check_unspelled_digits(text: str) -> tuple[bool, str]:
    """Rule 1: Checks for raw digits (unspelled numbers)."""
    if re.search(r"\d", text):
        return True, "Raw unspelled digits detected in spoken dialogue text."
    return False, ""


def check_code_contamination(text: str) -> tuple[bool, str]:
    """Rule 6: Generically checks for code, JSON, HTML, or unparsed metadata."""
    if re.search(r"[{}[\]]", text):
        return True, "JSON syntax braces or brackets '{ } [ ]' detected."
    if re.search(r'"\w+"\s*:', text) or re.search(r"'\w+'\s*:", text):
        return (
            True,
            "Key-value assignment pattern ('key': or \"key\":) detected.",
        )
    if re.search(r"<[^>]+>", text):
        return True, "XML or HTML markup tags '<...>' detected."
    if any(
        c in text
        for c in [
            "+Note+",
            "UCID",
            "InputParameters",
            "OutputParameters",
            "TimeoutMilliseconds",
        ]
    ):
        return True, "Robotic log parameter or flowchart marker detected."
    return False, ""


def check_breath_span(text: str) -> tuple[bool, str]:
    """Rule 2: Spoken breath span limit check (max 300 chars)."""
    if len(text) > 300:
        return (
            True,
            (
                "Spoken turn exceeds the 300-character breath-span cap (length:"
                f" {len(text)} characters)."
            ),
        )
    return False, ""


def check_vocabulary_monotony(text: str) -> tuple[bool, str]:
    """Rule 3: Checks for vocabulary monotony/word repetitions (> 4 times)."""
    words = [w.lower() for w in re.findall(r"\b\w{5,}\b", text)]
    for w in set(words):
        if words.count(w) > 4:
            return (
                True,
                (
                    f"Vocabulary monotony detected: the word '{w}' is repeated"
                    f" {words.count(w)} times in a single turn."
                ),
            )
    return False, ""


def check_politeness(text: str) -> tuple[bool, str]:
    """Rule 4: Checks if Agent turns contain at least one polite marker."""
    if not text:
        return False, ""
    polite_markers = [
        "please",
        "thank you",
        "thanks",
        "certainly",
        "happy to help",
        "welcome",
        "goodbye",
        "great day",
        "my pleasure",
        "certainly help",
        "help you with",
        "assist you",
        "alright",
        "no problem",
        "how can i",
        "thank you for calling",
        "thanks for calling",
        "of course",
        "sure",
    ]
    if not any(pm in text.lower() for pm in polite_markers):
        return (
            True,
            "Spoken Agent turn is missing a polite conversational marker.",
        )
    return False, ""


def check_scheduling_confirmation(text: str) -> tuple[bool, str]:
    """Rule 5: Checks if timeframe scheduling is confirmed with the caller."""
    if any(
        k in text.lower()
        for k in ["ready in", "minutes", "scheduled for", "booked for"]
    ):
        if not any(
            q in text.lower()
            for q in [
                "work for",
                "is that ok",
                "is that okay",
                "does that",
                "work for you",
            ]
        ):
            return (
                True,
                (
                    "Timeframe scheduling was presented without soliciting user"
                    " confirmation."
                ),
            )
    return False, ""


def calculate_transcript_naturalness(turns):
    if not turns:
        return 0, ["FAIL: Transcript contains no turns."]

    user_turns = sum(1 for t in turns if t.get("speaker") == "User")
    has_brief_loop = user_turns < 3


def check_empty_payloads(turn: dict) -> tuple[bool, str]:
    """Checks if a system/webhook call turn contains valid payloads/parameters."""
    input_required_tools = [
        "verify_order_id",
        "verify_reservation_id",
        "verify_guest_id",
        "verify_staff_id",
        "validate_coupon",
        "search_orders",
        "search_order_details",
        "set_order_id",
    ]
    for call_key in ["tool_call", "webhook_call"]:
        if call_key in turn:
            call_data = turn[call_key]
            if not isinstance(call_data, dict):
                return True, f"Invalid {call_key} type: expected dict."

            tool_name = call_data.get("name", "unknown")

            # 1. Enforce Non-Empty Request Payloads strictly for input-required tools
            if tool_name in input_required_tools:
                payload_keys = ["payload", "payload_patch", "parameters"]
                has_valid_payload = False
                for pk in payload_keys:
                    if pk in call_data:
                        p_dict = call_data[pk]
                        if p_dict is not None and (
                            isinstance(p_dict, dict) and len(p_dict) > 0
                        ):
                            has_valid_payload = True
                            break
                if not has_valid_payload:
                    return (
                        True,
                        (
                            f"Targeted tool '{tool_name}' requires request parameters,"
                            " but its payload/parameters block is empty or missing."
                        ),
                    )

            # 2. For all calls: if a response block is present, it must return a success
            if "response" in call_data:
                resp_data = call_data["response"]
                if (
                    not isinstance(resp_data, dict)
                    or resp_data.get("result") != "success"
                ):
                    return (
                        True,
                        (
                            f"{call_key} '{tool_name}' response block failed to return a"
                            " success."
                        ),
                    )
    return False, ""


def calculate_transcript_naturalness(turns):
    if not turns:
        return 0, ["FAIL: Transcript contains no turns."]

    user_turns = sum(1 for t in turns if t.get("speaker") == "User")
    has_brief_loop = user_turns < 3

    failures = []
    warnings = []

    if has_brief_loop:
        failures.append(
            "FAIL: Transcript is a brief loop containing fewer than 3 User turns."
        )

    agent_turns = 0
    agent_turns = 0
    polite_history = []
    monotonous_prefix_markers = [
        "certainly",
        "my pleasure",
        "happy to help",
        "alright",
        "no problem",
        "sure",
        "certainly help",
    ]

    for idx, t in enumerate(turns, 1):
        speaker = t.get("speaker")
        text = t.get("text", "")

        # Rule 7: Empty Payload Checker (Audits all turns generically!)
        payload_err, payload_msg = check_empty_payloads(t)
        if payload_err:
            failures.append(f"FAIL: Turn #{idx} ({speaker}) - {payload_msg}")

        if speaker == "Agent":
            agent_turns += 1

            # Rule 4 Monotony: Track and check targeted monotonous prefix markers only!
            matched_marker = None
            for pm in monotonous_prefix_markers:
                if pm in text.lower():
                    matched_marker = pm
                    break

            if matched_marker:
                if polite_history and polite_history[-1] == matched_marker:
                    failures.append(
                        f"FAIL: Turn #{idx} (Agent) - Polite marker monotony detected:"
                        " consecutive Agent turns repeated the exact same monotonous"
                        f" prefix '{matched_marker}'. Please contextually vary your"
                        " transitions."
                    )

            # Always append to history (even if None) to preserve chronological turn alignment
            polite_history.append(matched_marker)

            # Rule 1: Spelled out numbers
            digit_err, digit_msg = check_unspelled_digits(text)
            if digit_err:
                failures.append(f"FAIL: Turn #{idx} (Agent) - {digit_msg}")

            # Rule 6: Code contamination
            code_err, code_msg = check_code_contamination(text)
            if code_err:
                failures.append(f"FAIL: Turn #{idx} (Agent) - {code_msg}")

            # Rule 5: Unconfirmed scheduling
            sched_err, sched_msg = check_scheduling_confirmation(text)
            if sched_err:
                failures.append(f"FAIL: Turn #{idx} (Agent) - {sched_msg}")

            # Rule 2: Breath span limit
            breath_err, breath_msg = check_breath_span(text)
            if breath_err:
                warnings.append(f"WARNING: Turn #{idx} (Agent) - {breath_msg}")

            # Rule 3: Vocabulary repetition
            rep_err, rep_msg = check_vocabulary_monotony(text)
            if rep_err:
                warnings.append(f"WARNING: Turn #{idx} (Agent) - {rep_msg}")

            # Rule 4: Politeness presence check
            polite_err, polite_msg = check_politeness(text)
            if polite_err:
                warnings.append(f"WARNING: Turn #{idx} (Agent) - {polite_msg}")

    if agent_turns == 0:
        return 0, ["FAIL: Transcript contains no Agent turns."]

    # Rule 4 Monotony: Check for excessive usage of targeted monotonous markers only
    from collections import Counter

    marker_counts = Counter([m for m in polite_history if m is not None])
    for marker, count in marker_counts.items():
        if count > 3:
            failures.append(
                f"FAIL: Spoken politeness monotony: the repetitive prefix '{marker}'"
                f" was used {count} times across this transcript. You MUST limit any"
                " single prefix marker to at most 3 times, varying your voice"
                " transitions naturally."
            )

    if failures:
        return 1, failures
    elif warnings:
        return 2, warnings
    else:
        return 3, ["PASS: Pristine voice-natural proper-noun dialogue!"]


def is_generic_robotic_text(text: str, label_name: str) -> tuple[bool, str]:
    """Generically and domain-agnostically checks if a label is robotic/technical."""
    if not text:
        return True, f"Missing {label_name}."

    # 1. Check for underscores (always robotic connector!)
    if "_" in text:
        return (
            True,
            (
                f"Robotic {label_name} detected: '{text}' contains technical"
                " connector '_'."
            ),
        )

    # 2. Check for colons (staging/structural characters!)
    if ":" in text:
        return (
            True,
            (
                f"Robotic {label_name} detected: '{text}' contains structural"
                " character ':'."
            ),
        )

    # 3. Check for raw digits (staging row indexes or technical numbers!)
    if re.search(r"\d", text):
        return (
            True,
            f"Robotic {label_name} detected: '{text}' contains numeric digits.",
        )

    # 4. Check for alpha-numeric staging codes (e.g. tc01, cl2, de1208)
    # Regex matches: any boundary word containing both letters and numbers
    if re.search(r"\b(?=\w*\d)(?=\w*[a-zA-Z])\w+\b", text):
        return (
            True,
            (
                f"Robotic {label_name} detected: '{text}' contains alpha-numeric"
                " staging/developer variable."
            ),
        )

    return False, ""


def score_naturalness(output):
    data, error = extract_and_parse_yaml(output)
    if error:
        return []

    transcripts, error = normalize_to_transcripts(data)
    if error:
        return []

    scores_list = []

    for transcript in transcripts:
        if not isinstance(transcript, dict):
            continue

        parent_cuj = transcript.get("parent_cuj", "")
        subintent_name = transcript.get("subintent_name", "")

        failures = []

        # Audit Category and Scenario Title programmatically & generically!
        cuj_err, cuj_msg = is_generic_robotic_text(
            parent_cuj, "category header (parent_cuj)"
        )
        title_err, title_msg = is_generic_robotic_text(
            subintent_name, "scenario title (subintent_name)"
        )

        if cuj_err:
            failures.append(f"FAIL: {cuj_msg}")
        if title_err:
            failures.append(f"FAIL: {title_msg}")

        turns = transcript.get("turns", [])
        score, diagnostics = calculate_transcript_naturalness(turns)

        # If we had critical generic category/title failures, override score to 1 (FAIL)!
        if failures:
            score = 1
            if "PASS" in diagnostics[0]:
                diagnostics = failures
            else:
                diagnostics.extend(failures)

        scores_list.append((score, diagnostics))

    return scores_list
