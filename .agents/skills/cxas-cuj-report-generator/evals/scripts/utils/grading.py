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

    tool_call = last_turn.get("tool_call")
    if not tool_call:
        return (
            False,
            (
                f"Transcript #{idx + 1}: Last Agent turn is missing the end_session"
                " tool call."
            ),
        )

    if isinstance(tool_call, str):
        if tool_call != "end_session":
            return (
                False,
                f"Transcript #{idx + 1}: Incorrect tool call string value: {tool_call}",
            )
    elif isinstance(tool_call, dict):
        if tool_call.get("name") != "end_session":
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


def calculate_transcript_naturalness(turns):
    has_turns = len(turns) > 0
    has_unspelled_digits = False
    has_brief_loop = False
    has_unconfirmed_scheduling = False
    has_breath_infraction = False
    has_vocabulary_repetition = False
    has_politeness_omission = False

    user_turns = sum(1 for t in turns if t.get("speaker") == "User")
    if user_turns < 3:
        has_brief_loop = True

    turns_evaluated = 0
    for t in turns:
        speaker = t.get("speaker")
        text = t.get("text", "")

        if speaker == "Agent":
            turns_evaluated += 1

            # Rule 1: Raw digits / unspelled numbers
            if re.search(r"\d", text):
                has_unspelled_digits = True

            # Rule 2: Spoken breath span Cap
            if len(text) > 300:
                has_breath_infraction = True

            # Rule 3: Repetitive vocabulary monotony
            words = [w.lower() for w in re.findall(r"\b\w{5,}\b", text)]
            for w in set(words):
                if words.count(w) > 4:
                    has_vocabulary_repetition = True

            # Rule 4: Politeness marker omissions
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
            ]
            if not any(pm in text.lower() for pm in polite_markers):
                has_politeness_omission = True

            # Rule 5: Unconfirmed timeframe scheduling
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
                    has_unconfirmed_scheduling = True

    if not has_turns or turns_evaluated == 0:
        return 0

    # Directly resolve mutually-exclusive Rubric Levels:
    if has_unspelled_digits or has_brief_loop or has_unconfirmed_scheduling:
        return 1
    elif (
        has_breath_infraction
        or has_vocabulary_repetition
        or has_politeness_omission
    ):
        return 2
    else:
        return 3


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

        turns = transcript.get("turns", [])
        scores_list.append(calculate_transcript_naturalness(turns))

    return scores_list
