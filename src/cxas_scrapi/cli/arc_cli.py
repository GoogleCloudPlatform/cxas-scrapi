# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``cxas arc`` — talk to the CX Agent Studio AI assistant (Arc) from the CLI.

Handlers are intentionally thin: request construction, streaming, the
confirmation loop and LRO resumption live in ``cxas_scrapi.core.arc``. This
module owns the sticky session store, the pending-card sidecar, and the
markdown / JSON rendering that coding agents parse.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from cxas_scrapi.utils.arc_context import (
    ArcTarget,
    resolve_target,
    session_short_id,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cxas_scrapi.core.arc import Arc
    from cxas_scrapi.utils.arc_events import TurnStreamEvents

DEFAULT_SESSION_STORE = os.path.join("~", ".cxas", "arc_sessions.json")
SESSION_ID_PREFIX = "arc-cli-"
DEFAULT_MAX_CONFIRMATION_ROUNDS = 5
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_ATTACH_MAX_MB = 20
EXIT_EMPTY_TURN = 3


# ----------------------------------------------------------------- storage


def _expand(path: str) -> str:
    return os.path.expanduser(path) if path else path


def store_key(target: ArcTarget) -> str:
    """Key of the sticky session for an (endpoint, app) pair."""
    return f"{target.api_endpoint}:{target.app_name}"


def _load_json_dict(path: str) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        print(
            f"Warning: ignoring corrupted JSON state file {path}: {e}",
            file=sys.stderr,
        )
        return {}
    except OSError as e:
        print(
            f"Warning: could not read state file {path}: {e}",
            file=sys.stderr,
        )
        return {}
    if not isinstance(data, dict):
        print(
            f"Warning: ignoring non-object JSON state file {path}",
            file=sys.stderr,
        )
        return {}
    return data


def _save_json_dict(path: str, data: Mapping[str, Any]) -> None:
    if not path:
        return
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    if os.path.basename(os.path.normpath(parent)) == ".cxas":
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o700)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".arc_state_", suffix=".tmp", dir=parent
    )
    try:
        with contextlib.suppress(OSError):
            os.chmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def resolve_session_id(
    target: ArcTarget,
    explicit_session_id: str | None = None,
    new_session: bool = False,
    session_store_path: str = DEFAULT_SESSION_STORE,
    *,
    persist: bool = True,
) -> str:
    """Resolves or creates the sticky session id for the target app."""
    path = _expand(session_store_path)
    store = {str(k): str(v) for k, v in _load_json_dict(path).items()}
    key = store_key(target)

    candidate = explicit_session_id or target.session_id_from_url
    if candidate:
        short_id = session_short_id(candidate)
        if persist:
            store[key] = short_id
            _save_json_dict(path, store)
        return short_id

    if not new_session and store.get(key):
        return session_short_id(store[key])

    generated = f"{SESSION_ID_PREFIX}{uuid.uuid4().hex[:12]}"
    if persist:
        store[key] = generated
        _save_json_dict(path, store)
    return generated


def pending_store_path(session_store_path: str) -> str:
    """Sidecar file that tracks pending cards per sticky session."""
    if not session_store_path:
        return ""
    root, ext = os.path.splitext(_expand(session_store_path))
    return f"{root}.pending{ext or '.json'}"


def load_pending_ui_state(
    path: str, key: str, session_id: str
) -> dict[str, Any]:
    """Returns the pending cards/options saved for this session, if any."""
    entry = _load_json_dict(path).get(key)
    if not isinstance(entry, dict) or entry.get("session_id") != session_id:
        return {}
    return entry


def save_pending_ui_state(
    path: str, key: str, session_id: str, events: TurnStreamEvents
) -> None:
    """Persists the turn's unanswered cards and option panel for follow-ups."""
    if not path:
        return
    data = _load_json_dict(path)
    cards = events.pending_confirmation_requests
    options = [] if cards else list(events.pending_option_questions)
    if cards or options:
        data[key] = {
            "session_id": session_id,
            "confirmations": [dataclasses.asdict(c) for c in cards],
            "option_questions": [dataclasses.asdict(q) for q in options],
        }
    else:
        data.pop(key, None)
    _save_json_dict(path, data)


def _questions_from_dicts(raw: Sequence[Mapping[str, Any]]) -> list[Any]:
    from cxas_scrapi.utils.arc_events import parse_option_questions

    return parse_option_questions(raw)


def card_from_dict(data: Mapping[str, Any]) -> Any:
    """Rebuilds a ConfirmationRequestRecord saved by save_pending_ui_state."""
    from cxas_scrapi.utils.arc_events import ConfirmationRequestRecord

    return ConfirmationRequestRecord.from_dict(data)


def pending_cards_from_session(session: Mapping[str, Any]) -> list[Any]:
    """Recovers unanswered confirmation cards from a session resource.

    The session's model messages carry a ``confirmation_request`` typed
    payload whose ``status`` stays ``PENDING`` until it is answered.
    """
    from cxas_scrapi.utils.arc_events import parse_confirmation_request

    messages = session.get("messages", []) or []
    if not messages:
        return []
    # The server does not flip ``status`` once a card is answered, so only
    # trust cards in the *last* message: a later user or model message means
    # the turn moved on and the card was handled.
    cards = []
    for chunk in messages[-1].get("chunks", []) or []:
        payload = chunk.get("typedPayload") or {}
        if payload.get("type") != "confirmation_request":
            continue
        value = payload.get("value") or {}
        if str(value.get("status", "")).upper() != "PENDING":
            continue
        request = value.get("request") or {}
        if request.get("confirmationId"):
            cards.append(parse_confirmation_request(request))
    return cards


def _describe_typed_payload(payload: Mapping[str, Any]) -> str:
    kind = str(payload.get("type", "payload"))
    value = payload.get("value") or {}
    if kind == "tool_call":
        name = payload.get("displayName") or value.get("tool") or ""
        return f"[tool_call] {name}"
    if kind == "confirmation_request":
        request = value.get("request") or {}
        return (
            f"[confirmation_request] {request.get('confirmationId', '')} "
            f"tool={request.get('tool', '')} status={value.get('status', '')}"
        )
    return f"[{kind}] {json.dumps(payload)[:160]}"


# --------------------------------------------------------------- rendering


def format_choices_answer(
    questions: Sequence[Any], choices: Sequence[str]
) -> str:
    """Formats --choices the way the console's card / option panel submits."""
    from cxas_scrapi.utils.arc_confirmation_policy import (
        format_question_answer,
    )

    answers: list[str | None] = []
    for idx, question in enumerate(questions):
        raw = choices[idx].strip() if idx < len(choices) else ""
        if not raw:
            answers.append(None)
            continue
        opt_idx = None
        if len(raw) == 1 and raw.isalpha():
            opt_idx = ord(raw.upper()) - ord("A")
        elif raw.isdigit():
            opt_idx = int(raw) - 1
        if opt_idx is not None and 0 <= opt_idx < len(question.options):
            opt = question.options[opt_idx]
            answers.append(opt.submit_text or opt.title)
        else:
            answers.append(raw)
    return format_question_answer(questions, answers)


def _resolve_answer(
    questions: Sequence[Any],
    answer_text: str | None,
    choices: Sequence[str] | None,
    skip: str | None,
) -> str | None:
    """Turns --answer-text / --choices / --skip into console answer text."""
    from cxas_scrapi.utils import arc_confirmation_policy as policy_mod

    if answer_text is not None:
        return answer_text
    if skip:
        return (
            policy_mod.SKIP_ROUND_ANSWER
            if skip == "round"
            else policy_mod.SKIP_ALL_ANSWER
        )
    if choices is not None:
        if not questions:
            raise ValueError(
                "--choices needs a pending question; none is pending."
            )
        return format_choices_answer(questions, choices)
    return None


def cli_base_command(target: ArcTarget, session_id: str) -> str:
    """Copy-pasteable ``cxas arc`` prefix for this session."""
    parts = [
        "cxas arc",
        f"--app-name={target.app_name}",
        f"--session-id={session_id}",
    ]
    from cxas_scrapi.core.common import DEFAULT_API_ENDPOINT

    if target.api_endpoint != DEFAULT_API_ENDPOINT:
        parts.append(f"--api-endpoint={target.api_endpoint}")
    return " ".join(parts)


def suggested_followups(events: TurnStreamEvents, base_cmd: str) -> list[str]:
    """Ready-to-run commands that answer the turn's pending card or options."""
    cards = events.pending_confirmation_requests
    if cards:
        card = cards[-1]
        cid = f" --confirmation-id={card.confirmation_id}"
        cmd = base_cmd.replace("cxas arc ", "cxas arc confirm ", 1)
        chat = base_cmd.replace("cxas arc ", "cxas arc chat ", 1)
        if card.questions:
            letters = ",".join("A" for _ in card.questions)
            return [
                f"{cmd}{cid} --choices={letters}",
                f'{cmd}{cid} --answer-text="..."',
                f"{cmd}{cid} --skip=all",
                f'{chat} --message="..."  # typed reply, as in the console',
            ]
        return [
            f"{cmd}{cid} --confirmed",
            f"{cmd}{cid} --declined",
            f'{chat} --message="yes"  # typed reply, as in the console',
        ]
    options = events.pending_option_questions
    if options:
        letters = ",".join("A" for _ in options)
        cmd = base_cmd.replace("cxas arc ", "cxas arc answer ", 1)
        return [f"{cmd} --choices={letters}", f"{cmd} --skip=all"]
    return []


def render_turn(
    action: str,
    events: TurnStreamEvents,
    target: ArcTarget,
    client: Arc,
    session_id: str,
    *,
    include_thoughts: bool,
    include_tools: bool,
    response_format: str,
    notes: Sequence[str] = (),
    base_cmd: str = "",
    uploaded_attachments: Sequence[str] = (),
    downloaded_artifacts: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Renders a chat-like turn with its pending cards and follow-ups."""
    followups = suggested_followups(events, base_cmd) if base_cmd else []
    canonical = client.session_name(session_id)
    profile = getattr(client, "client_profile", "unknown")
    if response_format == "json":
        payload = {
            "action": action,
            "session_id": session_id,
            "canonical_session_id": canonical,
            "app_name": target.app_name,
            "api_endpoint": target.api_endpoint,
            "client_profile": profile,
            "events": events.to_dict(),
            "pending_confirmations": [
                dataclasses.asdict(c)
                for c in events.pending_confirmation_requests
            ],
            "suggested_commands": followups,
            "notes": list(notes),
            "uploaded_attachments": list(uploaded_attachments),
            "downloaded_artifacts": [dict(a) for a in downloaded_artifacts],
        }
        return json.dumps(payload, indent=2, sort_keys=True)
    header = (
        f"**Session**: `{canonical}` | **Endpoint**: `{target.api_endpoint}`"
        f" | **Profile**: `{profile}`"
    )
    sections = [header, *notes]
    body = events.format_markdown(
        include_thoughts=include_thoughts, include_tools=include_tools
    )
    if body:
        sections.append(body)
    if followups:
        sections.append(
            "### [Next Steps]\n```shell\n" + "\n".join(followups) + "\n```"
        )
    return "\n\n".join(sections)


def is_empty_turn(events: TurnStreamEvents) -> bool:
    """True when the server ended the turn without producing anything.

    A stream against a non-existent app, or one the caller cannot read,
    returns a lone ``turnCompleted`` with no content.
    """
    if events.turn_completed is None:
        return False
    return not (
        events.text.strip()
        or events.tool_calls
        or events.tool_responses
        or events.artifacts
        or events.option_questions
        or events.confirmation_requests
        or events.confirmation_actions
        or events.initiated_lros
        or events.thoughts
        or events.errors
    )


def _has_turn_activity(events: TurnStreamEvents) -> bool:
    """True if at least one streamed event or sequence number was observed."""
    return bool(
        events.text
        or events.thoughts
        or events.statuses
        or events.errors
        or events.tool_calls
        or events.tool_responses
        or events.artifacts
        or events.option_questions
        or events.confirmation_requests
        or events.confirmation_actions
        or events.initiated_lros
        or events.turn_completed is not None
        or events.last_sequence_number is not None
    )


def format_partial_turn(events: TurnStreamEvents | None) -> str:
    """Renders what streamed before a failure."""
    if events is None:
        return ""
    body = events.format_markdown(include_thoughts=False, include_tools=True)
    return f"### [Turn failed] partial output\n\n{body}" if body else ""


# --------------------------------------------------------------- plumbing


def _read_message(message: str | None, message_file: str | None) -> str:
    if message and message_file:
        raise ValueError(
            "Provide either --message or --message-file, not both."
        )
    if message_file:
        with open(message_file, encoding="utf-8") as f:
            return f.read().strip()
    return (message or "").strip()


def _progress_printer(args: argparse.Namespace) -> Any:
    if getattr(args, "quiet", False):
        return None
    return lambda msg: print(f"[arc] {msg}", file=sys.stderr, flush=True)


def _build_target(args: argparse.Namespace) -> ArcTarget:
    return resolve_target(
        url=getattr(args, "url", None),
        app_name=getattr(args, "app_name", None),
        project=getattr(args, "project", None),
        location=getattr(args, "location", None),
        api_endpoint=getattr(args, "api_endpoint", None),
    )


def _build_client(args: argparse.Namespace, target: ArcTarget) -> Arc:
    from cxas_scrapi.core.arc import Arc

    return Arc(
        app_name=target.app_name,
        api_endpoint=target.api_endpoint,
        client_profile=getattr(args, "client_profile", "console"),
        stream_idle_timeout=float(
            getattr(args, "stream_idle_timeout", 180.0) or 180.0
        ),
        progress=_progress_printer(args),
    )


def _build_policy(args: argparse.Namespace) -> Any:
    from cxas_scrapi.utils.arc_confirmation_policy import make_policy

    return make_policy(
        getattr(args, "confirmation_policy", "manual") or "manual",
        typed_reply_probability=getattr(args, "typed_reply_probability", 0.2),
        seed=getattr(args, "confirmation_seed", None),
        deny_tools=tuple(getattr(args, "deny_tools", None) or ()),
    )


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",")]


def _merge_choices(
    choices_csv: str | None, choice_list: Sequence[str] | None
) -> list[str] | None:
    from_csv = _split_csv(choices_csv)
    from_repeat = [c.strip() for c in (choice_list or [])]
    if from_csv is None and not from_repeat:
        return None
    return [*(from_csv or []), *from_repeat]


def _fail(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


@dataclasses.dataclass
class _TurnContext:
    target: ArcTarget
    client: Arc
    session_id: str
    store_key: str
    pending_path: str
    pending_state: dict[str, Any]
    base_cmd: str


def _turn_context(
    args: argparse.Namespace,
    *,
    new_session: bool = False,
    persist: bool = True,
) -> _TurnContext:
    target = _build_target(args)
    store_path = getattr(args, "session_store", DEFAULT_SESSION_STORE)
    session_id = resolve_session_id(
        target,
        explicit_session_id=getattr(args, "session_id", None),
        new_session=new_session or getattr(args, "new_session", False),
        session_store_path=store_path,
        persist=persist,
    )
    key = store_key(target)
    pending_path = pending_store_path(store_path)
    return _TurnContext(
        target=target,
        client=_build_client(args, target),
        session_id=session_id,
        store_key=key,
        pending_path=pending_path,
        pending_state=load_pending_ui_state(pending_path, key, session_id),
        base_cmd=cli_base_command(target, session_id),
    )


def _finish_turn(
    action: str,
    ctx: _TurnContext,
    events: TurnStreamEvents,
    args: argparse.Namespace,
    notes: Sequence[str] = (),
    extra_sections: Sequence[str] = (),
    uploaded_attachments: Sequence[str] = (),
    downloaded_artifacts: Sequence[Mapping[str, Any]] = (),
) -> None:
    save_pending_ui_state(
        ctx.pending_path, ctx.store_key, ctx.session_id, events
    )
    out = render_turn(
        action,
        events,
        ctx.target,
        ctx.client,
        ctx.session_id,
        include_thoughts=getattr(args, "include_thoughts", False),
        include_tools=not getattr(args, "no_tools", False),
        response_format=getattr(args, "format", "markdown"),
        notes=notes,
        base_cmd=ctx.base_cmd,
        uploaded_attachments=uploaded_attachments,
        downloaded_artifacts=downloaded_artifacts,
    )
    print(out)
    for section in extra_sections:
        if section:
            stream = sys.stderr if args.format == "json" else sys.stdout
            print("\n" + section, file=stream)


def _check_turn_outcome(ctx: _TurnContext, events: TurnStreamEvents) -> None:
    if events.errors:
        last_err = events.errors[-1]
        msg = last_err.message or "stream error"
        _fail(
            f"Arc ended the turn with error status {last_err.code}: {msg}",
            code=1,
        )
    if is_empty_turn(events):
        _fail(
            "Arc ended the turn without any output. Check that the app "
            "exists and that you can read it (cxas apps get "
            f"{ctx.target.app_name}).",
            code=EXIT_EMPTY_TURN,
        )


def _handle_turn_failure(
    ctx: _TurnContext, args: argparse.Namespace, error: Exception
) -> None:
    from cxas_scrapi.core.arc import ArcStreamInterrupted, ArcStreamTimeout

    partial = (
        error.partial
        if isinstance(error, (ArcStreamTimeout, ArcStreamInterrupted))
        else ctx.client.active_turn_events or ctx.client.last_stream_events
    )
    has_activity = partial is not None and _has_turn_activity(partial)
    if partial is not None and has_activity:
        save_pending_ui_state(
            ctx.pending_path, ctx.store_key, ctx.session_id, partial
        )
    if getattr(args, "format", "markdown") == "json":
        canonical = ctx.client.session_name(ctx.session_id)
        payload = {
            "action": "error",
            "error": str(error),
            "session_id": ctx.session_id,
            "canonical_session_id": canonical,
            "app_name": ctx.target.app_name,
            "api_endpoint": ctx.target.api_endpoint,
            "partial_events": (
                partial.to_dict()
                if partial is not None and has_activity
                else None
            ),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif partial is not None:
        rendered = format_partial_turn(partial)
        if rendered:
            print(rendered)
    _fail(str(error))


def _run_turn_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    timeout = getattr(args, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    return {
        "confirmation_policy": _build_policy(args),
        "max_confirmation_rounds": getattr(
            args, "max_confirmation_rounds", DEFAULT_MAX_CONFIRMATION_ROUNDS
        ),
        "timeout_seconds": timeout,
        "lro_timeout_seconds": timeout,
        "auto_resolve_lro": not getattr(args, "no_auto_resolve_lro", False),
    }


# ---------------------------------------------------------------- handlers


def arc_chat(args: argparse.Namespace) -> None:
    """Sends a message on the sticky session (or a new one)."""
    try:
        message = _read_message(args.message, args.message_file)
        attach_paths = list(args.attach or [])
        if not message and not attach_paths and not args.attached_gcs_uri:
            raise ValueError(
                "--message, --message-file or --attach is required."
            )
    except ValueError as e:
        _fail(str(e))
        return

    bundle = None
    if attach_paths:
        from cxas_scrapi.utils import arc_bundle

        limits = arc_bundle.BundleLimits(
            max_zip_bytes=int(args.attach_max_mb) * 1024 * 1024
        )
        allow_secrets = bool(getattr(args, "attach_allow_secrets", False))
        try:
            bundle = arc_bundle.build(
                attach_paths, limits=limits, allow_secrets=allow_secrets
            )
        except arc_bundle.BundleError as e:
            _fail(str(e))
            return
        if args.attach_dry_run:
            try:
                _build_target(args)
                if getattr(args, "session_id", None):
                    session_short_id(args.session_id)
            except ValueError as e:
                _fail(str(e))
                return
            if args.format == "json":
                print(
                    json.dumps(
                        {
                            "action": "chat.attach_dry_run",
                            "root": (
                                os.path.basename(os.path.normpath(bundle.root))
                                if bundle.root
                                else ""
                            ),
                            "files": [
                                {"path": f.rel_path, "size": f.size}
                                for f in bundle.files
                            ],
                            "file_count": len(bundle.files),
                            "excluded_count": bundle.excluded_count,
                            "secret_excluded_count": (
                                bundle.secret_excluded_count
                            ),
                            "uncompressed_bytes": bundle.uncompressed_bytes,
                            "zip_bytes": len(bundle.zip_bytes or b""),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(arc_bundle.format_report(bundle, max_listed=200))
            return

    try:
        ctx = _turn_context(args)
    except ValueError as e:
        _fail(str(e))
        return

    attached: list[str] = list(args.attached_gcs_uri or [])
    sections: list[str] = []
    if bundle is not None:
        from cxas_scrapi.utils import arc_bundle

        bucket = args.bucket
        if not bucket:
            try:
                project_id = arc_bundle.resolve_project_id(
                    ctx.target.project, credentials=ctx.client.creds
                )
            except RuntimeError as e:
                _fail(str(e))
                return
            bucket = arc_bundle.default_artifacts_bucket(project_id)
        if bundle.zip_bytes is None:
            _fail("Attachment bundle did not produce archive bytes.")
            return
        progress = _progress_printer(args)
        if progress is not None:
            preview = ", ".join(f.rel_path for f in bundle.files[:5])
            more = (
                f" (+{len(bundle.files) - 5} more)"
                if len(bundle.files) > 5
                else ""
            )
            secrets_note = (
                f", {bundle.secret_excluded_count} secret file(s) skipped"
                if bundle.secret_excluded_count
                else ""
            )
            clean_bucket = bucket.strip().removeprefix("gs://").strip("/")
            progress(
                f"uploading {len(bundle.files)} file(s)"
                f" ({len(bundle.zip_bytes):,} B zipped{secrets_note})"
                f" to gs://{clean_bucket}: {preview}{more}"
            )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                local = os.path.join(tmp, arc_bundle.bundle_file_name(bundle))
                with open(local, "wb") as f:
                    f.write(bundle.zip_bytes)
                gcs_uri = arc_bundle.upload_bundle(
                    local,
                    bucket,
                    credentials=ctx.client.creds,
                    project=ctx.target.project,
                )
        except Exception as e:  # noqa: BLE001
            _fail(f"Failed to upload attachment bundle: {e}")
            return
        attached.append(gcs_uri)
        sections.append(
            arc_bundle.format_report(
                bundle, max_listed=20, uploaded_uri=gcs_uri
            )
        )
        if not message:
            message = "Attached files for you to review."

    notes = []
    if ctx.pending_state.get("confirmations"):
        notes.append(
            "> Note: a confirmation card is pending; this message is sent as a "
            "typed reply (the console path), not as a click. Use "
            "`cxas arc confirm` to click."
        )
    try:
        events = ctx.client.chat(
            ctx.session_id,
            message,
            attached_gcs_uris=attached,
            **_run_turn_kwargs(args),
        )
    except Exception as e:  # noqa: BLE001 — surface any failure cleanly
        _handle_turn_failure(ctx, args, e)
        return
    downloaded_records: list[dict[str, Any]] = []
    if args.download_artifacts:
        dl_section, downloaded_records = (
            download_session_artifacts_with_records(
                ctx.client, ctx.session_id, args.download_artifacts
            )
        )
        sections.append(dl_section)
    _finish_turn(
        "chat",
        ctx,
        events,
        args,
        notes=notes,
        extra_sections=sections,
        uploaded_attachments=attached,
        downloaded_artifacts=downloaded_records,
    )
    _check_turn_outcome(ctx, events)


def arc_confirm(args: argparse.Namespace) -> None:
    """Answers the latest (or given) pending confirmation card."""
    from cxas_scrapi.utils.arc_events import ConfirmationRequestRecord

    try:
        ctx = _turn_context(args)
    except ValueError as e:
        _fail(str(e))
        return

    confirmed: bool | None = None
    if args.confirmed:
        confirmed = True
    elif args.declined:
        confirmed = False
    choices = _merge_choices(args.choices, getattr(args, "choice", None))
    given = [
        bool(args.confirmed),
        bool(args.declined),
        args.answer_text is not None,
        choices is not None,
        args.skip is not None,
    ].count(True)
    if given != 1:
        _fail(
            "cxas arc confirm needs exactly one of --confirmed, --declined, "
            "--answer-text, --choices/--choice or --skip."
        )
        return

    cards = [
        card_from_dict(c) for c in ctx.pending_state.get("confirmations") or []
    ]
    if not cards and not args.confirmation_id:
        # No local record (another machine, or a cleared store): recover the
        # pending card from the session itself.
        try:
            cards = pending_cards_from_session(
                ctx.client.get_session(ctx.session_id)
            )
        except Exception as e:  # noqa: BLE001
            _fail(str(e))
            return
    card = None
    if args.confirmation_id:
        card = next(
            (c for c in cards if c.confirmation_id == args.confirmation_id),
            ConfirmationRequestRecord(confirmation_id=args.confirmation_id),
        )
    elif cards:
        card = cards[-1]
    if card is None:
        _fail(
            "No pending confirmation card is recorded for this session; pass "
            "--confirmation-id (see the last chat output)."
        )
        return
    try:
        answer = _resolve_answer(
            card.questions, args.answer_text, choices, args.skip
        )
    except ValueError as e:
        _fail(str(e))
        return
    try:
        events = ctx.client.send_confirmation_response(
            ctx.session_id,
            card.confirmation_id,
            confirmed=confirmed if answer is None else None,
            answer_text=answer,
            tool=card.tool,
            **_run_turn_kwargs(args),
        )
    except Exception as e:  # noqa: BLE001
        _handle_turn_failure(ctx, args, e)
        return
    _finish_turn("confirm", ctx, events, args)
    _check_turn_outcome(ctx, events)


def arc_answer(args: argparse.Namespace) -> None:
    """Answers a clarification option panel like the console Submit button."""
    try:
        ctx = _turn_context(args)
    except ValueError as e:
        _fail(str(e))
        return
    options = _questions_from_dicts(
        ctx.pending_state.get("option_questions") or []
    )
    if not options:
        _fail(
            "No option panel is recorded for this session; use "
            "`cxas arc chat --message` to reply in your own words."
        )
        return
    choices = _merge_choices(args.choices, getattr(args, "choice", None))
    if [x is not None for x in (choices, args.skip)].count(True) != 1:
        _fail(
            "cxas arc answer needs exactly one of --choices/--choice or --skip."
        )
        return
    try:
        answer = _resolve_answer(options, None, choices, args.skip)
    except ValueError as e:
        _fail(str(e))
        return
    try:
        events = ctx.client.chat(
            ctx.session_id, answer or "", **_run_turn_kwargs(args)
        )
    except Exception as e:  # noqa: BLE001
        _handle_turn_failure(ctx, args, e)
        return
    _finish_turn("answer", ctx, events, args)
    _check_turn_outcome(ctx, events)


def arc_sessions_new(args: argparse.Namespace) -> None:
    """Starts a fresh sticky session (created lazily by the first turn)."""
    try:
        ctx = _turn_context(args, new_session=True)
    except ValueError as e:
        _fail(str(e))
        return
    save_pending_ui_state(
        ctx.pending_path, ctx.store_key, ctx.session_id, _empty_events()
    )
    canonical = ctx.client.session_name(ctx.session_id)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "action": "sessions.new",
                    "session_id": ctx.session_id,
                    "canonical_session_id": canonical,
                    "app_name": ctx.target.app_name,
                    "api_endpoint": ctx.target.api_endpoint,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(f"New sticky session: `{canonical}`")
    print(
        f'Next: {ctx.base_cmd.replace("cxas arc ", "cxas arc chat ", 1)} --message="..."'
    )


def arc_sessions_list(args: argparse.Namespace) -> None:
    """Lists assistant sessions on the app."""
    try:
        target = _build_target(args)
        client = _build_client(args, target)
        sessions = client.list_sessions(
            page_size=args.page_size, filter_=args.filter or ""
        )
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
        return
    if args.format == "json":
        print(json.dumps(sessions, indent=2))
        return
    if not sessions:
        print("(no assistant sessions)")
        return
    for s in sessions:
        name = s.get("name", "")
        short = name.rsplit("/", 1)[-1]
        msgs = len(s.get("messages", []) or [])
        print(
            f"- `{short}` state={s.get('state', '')} messages={msgs} "
            f"updated={s.get('updateTime', '')} "
            f"requestor={s.get('requestor', '')}"
        )


def arc_sessions_get(args: argparse.Namespace) -> None:
    """Shows a session's messages, artifacts and active turn."""
    try:
        ctx = _turn_context(args, persist=False)
        session = ctx.client.get_session(ctx.session_id)
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
        return
    if args.format == "json":
        print(json.dumps(session, indent=2))
        return
    print(f"**Session**: `{session.get('name', ctx.session_id)}`")
    print(
        f"state={session.get('state', '')} "
        f"messages={len(session.get('messages', []) or [])} "
        f"updated={session.get('updateTime', '')}"
    )
    active = session.get("activeTurn")
    if active:
        print(f"active turn: {json.dumps(active)}")
    for msg in session.get("messages", []) or []:
        role = msg.get("role", "")
        for chunk in msg.get("chunks", []) or []:
            if "text" in chunk:
                text = str(chunk["text"]).strip().replace("\n", "\n  ")
                print(f"- **{role}**: {text[:800]}")
            elif "typedPayload" in chunk:
                tp = chunk["typedPayload"] or {}
                print(f"- **{role}** {_describe_typed_payload(tp)}")
    artifacts = session.get("artifacts", []) or []
    if artifacts:
        print("### [Artifacts]")
        for art in artifacts:
            print(
                f"- `{art.get('displayName') or art.get('id')}` "
                f"{art.get('mimeType', '')} {art.get('gcsUri', '')}"
            )


def arc_sessions_delete(args: argparse.Namespace) -> None:
    """Deletes a session and forgets it locally."""
    try:
        ctx = _turn_context(args, persist=False)
        ctx.client.delete_session(ctx.session_id)
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
        return
    path = _expand(getattr(args, "session_store", DEFAULT_SESSION_STORE))
    store = _load_json_dict(path)
    if store.get(ctx.store_key) == ctx.session_id:
        store.pop(ctx.store_key, None)
        _save_json_dict(path, store)
    pending_entry = _load_json_dict(ctx.pending_path).get(ctx.store_key)
    if (
        isinstance(pending_entry, dict)
        and pending_entry.get("session_id") == ctx.session_id
    ):
        save_pending_ui_state(
            ctx.pending_path, ctx.store_key, ctx.session_id, _empty_events()
        )
    canonical = ctx.client.session_name(ctx.session_id)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "action": "sessions.delete",
                    "session_id": ctx.session_id,
                    "canonical_session_id": canonical,
                    "deleted": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(f"Deleted `{canonical}`")


def arc_cancel(args: argparse.Namespace) -> None:
    """Cancels the in-flight turn on the sticky session."""
    try:
        ctx = _turn_context(args, persist=False)
        result = ctx.client.cancel_turn(ctx.session_id, args.turn_id or "")
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
        return
    payload = {
        "action": "cancel",
        "session_id": ctx.session_id,
        "cancelled": bool(result.get("cancelled", False)),
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"cancelled: {payload['cancelled']}")


def _format_suggestion(s: Mapping[str, Any]) -> str:
    label = str(s.get("label") or s.get("title") or s.get("displayName") or "")
    load_session = s.get("loadSession")
    load_target = ""
    if isinstance(load_session, Mapping):
        sid = load_session.get("assistantSessionId", "")
        load_target = f"session {sid}".strip() if sid else ""
    action = str(
        s.get("seedPrompt")
        or s.get("sendMessage")
        or load_target
        or s.get("prompt")
        or s.get("message")
        or s.get("text")
        or ""
    )
    rationale = str(s.get("rationale") or "")
    if label and action:
        line = f"- {label}: {action}"
    elif label:
        line = f"- {label}"
    elif action:
        line = f"- {action}"
    else:
        line = f"- {json.dumps(s)}"
    if rationale:
        line = f"{line} ({rationale})"
    return line


def arc_suggestions(args: argparse.Namespace) -> None:
    """Prints the onboarding suggestions the console shows on a new session."""
    try:
        target = _build_target(args)
        client = _build_client(args, target)
        suggestions = client.generate_onboarding_suggestions()
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
        return
    if args.format == "json":
        print(json.dumps(suggestions, indent=2))
        return
    if not suggestions:
        print("(no suggestions)")
        return
    for s in suggestions:
        print(_format_suggestion(s))


def download_session_artifacts_with_records(
    client: Arc, session_id: str, dest_dir: str
) -> tuple[str, list[dict[str, Any]]]:
    """Downloads session artifacts and returns ``(markdown, records)``."""
    from cxas_scrapi.utils import arc_bundle

    artifacts = client.session_artifacts(session_id)
    if not artifacts:
        return "### [Downloaded Artifacts]\n(none)", []
    lines = ["### [Downloaded Artifacts]"]
    records: list[dict[str, Any]] = []
    project = getattr(client, "project_id", None)
    for art in artifacts:
        uri = art.get("gcsUri")
        if not uri:
            continue
        label = str(art.get("displayName") or art.get("id") or uri)
        if not uri.startswith("gs://"):
            lines.append(
                f"- `{label}` -> not downloadable here (managed artifact "
                f"`{uri}`); open it from the console"
            )
            records.append(
                {
                    "label": label,
                    "gcs_uri": uri,
                    "local_path": None,
                    "status": "skipped_managed",
                }
            )
            continue
        dl_kwargs: dict[str, Any] = {"credentials": client.creds}
        if isinstance(project, str) and project:
            dl_kwargs["project"] = project
        try:
            local = arc_bundle.download_gcs_uri(uri, dest_dir, **dl_kwargs)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- `{label}` -> FAILED: {e}")
            records.append(
                {
                    "label": label,
                    "gcs_uri": uri,
                    "local_path": None,
                    "status": "failed",
                    "error": str(e),
                }
            )
            continue
        lines.append(f"- `{label}` -> `{local}`")
        records.append(
            {
                "label": label,
                "gcs_uri": uri,
                "local_path": local,
                "status": "downloaded",
            }
        )
    return "\n".join(lines), records


def download_session_artifacts(
    client: Arc, session_id: str, dest_dir: str
) -> str:
    """Downloads every artifact on the session into ``dest_dir``."""
    markdown, _ = download_session_artifacts_with_records(
        client, session_id, dest_dir
    )
    return markdown


def _empty_events() -> TurnStreamEvents:
    from cxas_scrapi.utils.arc_events import TurnStreamEvents

    return TurnStreamEvents()


# ------------------------------------------------------------------ parser


def _add_target_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("target")
    app_group = g.add_mutually_exclusive_group()
    app_group.add_argument(
        "--app-name",
        help=(
            "Full app resource name (`projects/*/locations/*/apps/*`) or a "
            "short app id combined with `--project`/`--location`."
        ),
    )
    app_group.add_argument(
        "--url",
        help=(
            "CX Agent Studio console URL of the app (may include "
            "`/assistantSessions/<id>`); alternative to `--app-name`."
        ),
    )
    g.add_argument(
        "--project", help="Project id or number for a short `--app-name`."
    )
    g.add_argument(
        "--location",
        help="Location for a short `--app-name` (default: $CES_LOCATION or us).",
    )
    g.add_argument(
        "--api-endpoint",
        help="API host (default: $CES_API_ENDPOINT or ces.googleapis.com).",
    )
    sess_group = g.add_mutually_exclusive_group()
    sess_group.add_argument(
        "--session-id", help="Use this session (short id or full name)."
    )
    sess_group.add_argument(
        "--new-session",
        action="store_true",
        help="Start a fresh sticky session.",
    )
    g.add_argument(
        "--session-store",
        default=DEFAULT_SESSION_STORE,
        help=f"Sticky session store (default: `{DEFAULT_SESSION_STORE}`).",
    )
    g.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (`json` is stable for agents).",
    )
    g.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress `[arc]` progress on stderr.",
    )


def _add_turn_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("turn")
    g.add_argument(
        "--confirmation-policy",
        choices=["manual", "click", "mixed"],
        default="manual",
        help=(
            "manual: show cards and stop; click: confirm gate cards and answer "
            "questions with the first option; mixed: like click but types the "
            "answer with `--typed-reply-probability`."
        ),
    )
    g.add_argument(
        "--deny-tools",
        action="append",
        default=[],
        metavar="GLOB",
        help=(
            "Tool name glob that `click`/`mixed` must decline instead of "
            "confirming (repeatable, e.g. `*delete*` or `*deploy*`)."
        ),
    )
    g.add_argument(
        "--typed-reply-probability",
        type=float,
        default=0.2,
        help="Probability of typing instead of clicking under `mixed` policy.",
    )
    g.add_argument(
        "--confirmation-seed",
        type=int,
        default=None,
        help="RNG seed for reproducible `mixed` policy decisions.",
    )
    g.add_argument(
        "--max-confirmation-rounds",
        type=int,
        default=DEFAULT_MAX_CONFIRMATION_ROUNDS,
        help="Maximum confirmation rounds auto-answered in a single turn.",
    )
    g.add_argument(
        "--client-profile",
        choices=["console", "legacy"],
        default="console",
        help="`legacy` omits `CONFIRMATION_CARDS` (server auto-denies gated tools).",
    )
    g.add_argument(
        "--no-auto-resolve-lro",
        action="store_true",
        help="Do not poll evaluation runs / operations and resume the session.",
    )
    g.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Overall deadline in seconds for the turn and any polled LROs.",
    )
    g.add_argument(
        "--stream-idle-timeout",
        type=float,
        default=180.0,
        help="Seconds without streamed events before giving up.",
    )
    g.add_argument(
        "--include-thoughts",
        action="store_true",
        help="Include streamed model thoughts in markdown output.",
    )
    g.add_argument(
        "--no-tools", action="store_true", help="Hide tool activity."
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    """Registers the ``arc`` command group."""
    parser = subparsers.add_parser(
        "arc",
        help="Chat with the CX Agent Studio AI assistant (Arc) on an app.",
        description=(
            "Drive an Arc session like the console does: send messages, answer "
            "confirmation cards and clarification options, let evaluation runs "
            "resume the session, attach files, and download artifacts."
        ),
    )
    sub = parser.add_subparsers(dest="arc_command", required=True)

    chat = sub.add_parser("chat", help="Send a message on the sticky session.")
    _add_target_args(chat)
    _add_turn_args(chat)
    chat.add_argument("--message", "-m", help="User message.")
    chat.add_argument("--message-file", help="Read the message from a file.")
    chat.add_argument(
        "--attach",
        action="append",
        default=[],
        metavar="PATH",
        help="File or directory to zip and attach (repeatable).",
    )
    chat.add_argument(
        "--attach-dry-run", action="store_true", help="Only list the bundle."
    )
    chat.add_argument(
        "--attach-allow-secrets",
        action="store_true",
        help="Include `.env*`, key and credential files excluded by default.",
    )
    chat.add_argument(
        "--attach-max-mb",
        type=int,
        default=DEFAULT_ATTACH_MAX_MB,
        help="Zip size cap in MiB.",
    )
    chat.add_argument(
        "--bucket",
        help="GCS bucket for attachments (default: `<project>-agent-assist-artifacts`).",
    )
    chat.add_argument(
        "--attached-gcs-uri",
        action="append",
        default=[],
        metavar="GS_URI",
        help="Attach an existing `gs://` object (repeatable).",
    )
    chat.add_argument(
        "--download-artifacts",
        metavar="DIR",
        help="Download session artifacts after the turn.",
    )
    chat.set_defaults(func=arc_chat)

    confirm = sub.add_parser(
        "confirm", help="Answer the pending confirmation card."
    )
    _add_target_args(confirm)
    _add_turn_args(confirm)
    confirm.add_argument(
        "--confirmation-id", help="Card id (default: latest pending)."
    )
    confirm.add_argument(
        "--confirmed", action="store_true", help="Click the confirm button."
    )
    confirm.add_argument(
        "--declined", action="store_true", help="Click the decline button."
    )
    confirm.add_argument(
        "--choices",
        help="Per-question answers: letter, number or free text, comma-separated.",
    )
    confirm.add_argument(
        "--choice",
        action="append",
        default=[],
        metavar="CHOICE",
        help="Single question answer (repeatable; preserves commas).",
    )
    confirm.add_argument(
        "--answer-text", help="Raw answer text for a question card."
    )
    confirm.add_argument(
        "--skip",
        choices=["round", "all"],
        help="Skip this round or all questions.",
    )
    confirm.set_defaults(func=arc_confirm)

    answer = sub.add_parser(
        "answer", help="Answer a clarification option panel."
    )
    _add_target_args(answer)
    _add_turn_args(answer)
    answer.add_argument(
        "--choices", help="Per-question answers, comma-separated."
    )
    answer.add_argument(
        "--choice",
        action="append",
        default=[],
        metavar="CHOICE",
        help="Single question answer (repeatable; preserves commas).",
    )
    answer.add_argument(
        "--skip",
        choices=["round", "all"],
        help="Skip this round or all questions.",
    )
    answer.set_defaults(func=arc_answer)

    sessions = sub.add_parser(
        "sessions", help="List, inspect, create or delete sessions."
    )
    ssub = sessions.add_subparsers(dest="sessions_command", required=True)
    s_list = ssub.add_parser("list", help="List sessions on the app.")
    _add_target_args(s_list)
    s_list.add_argument(
        "--page-size",
        type=int,
        default=20,
        help="Maximum sessions returned per page.",
    )
    s_list.add_argument("--filter", default="", help="AIP-160 filter.")
    s_list.set_defaults(func=arc_sessions_list)
    s_get = ssub.add_parser("get", help="Show the sticky (or given) session.")
    _add_target_args(s_get)
    s_get.set_defaults(func=arc_sessions_get)
    s_new = ssub.add_parser("new", help="Start a fresh sticky session.")
    _add_target_args(s_new)
    s_new.set_defaults(func=arc_sessions_new)
    s_del = ssub.add_parser(
        "delete", help="Delete the sticky (or given) session."
    )
    _add_target_args(s_del)
    s_del.set_defaults(func=arc_sessions_delete)

    cancel = sub.add_parser("cancel", help="Cancel the in-flight turn.")
    _add_target_args(cancel)
    cancel.add_argument(
        "--turn-id",
        default="",
        help="Turn id to cancel (default: active turn).",
    )
    cancel.set_defaults(func=arc_cancel)

    suggestions = sub.add_parser(
        "suggestions", help="Show onboarding suggestions for the app."
    )
    _add_target_args(suggestions)
    suggestions.set_defaults(func=arc_suggestions)


def _gfm_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows), 3)
        for i in range(len(headers))
    ]

    def fmt_row(cells: Sequence[str]) -> str:
        return (
            "| "
            + " | ".join(cells[i].ljust(widths[i]) for i in range(len(headers)))
            + " |"
        )

    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    return "\n".join([fmt_row(headers), sep, *(fmt_row(r) for r in rows)])


def _action_row(action: argparse.Action) -> tuple[str, str, str]:
    opts = ", ".join(f"`{o}`" for o in action.option_strings)
    takes_value = action.nargs != 0
    if takes_value:
        if action.choices is not None:
            meta = "{" + ",".join(str(c) for c in action.choices) + "}"
        elif action.metavar is not None:
            meta = str(action.metavar)
        else:
            meta = action.dest.upper()
        flag_col = f"{opts} `{meta}`"
    else:
        flag_col = opts

    default = action.default
    if (
        default is None
        or default is False
        or default in ("", [])
        or default is argparse.SUPPRESS
    ):
        default_col = "—"
    else:
        default_col = f"`{default}`"
    desc_col = (action.help or "").strip()
    return (flag_col, default_col, desc_col)


def render_cli_reference_markdown() -> str:
    """Renders the markdown flag reference from ``register()``."""
    root = argparse.ArgumentParser(prog="cxas")
    root_sub = root.add_subparsers(dest="command", required=True)
    register(root_sub)
    arc_parser = root_sub.choices["arc"]
    arc_sub = next(
        a
        for a in arc_parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    help_by_name = {ca.dest: (ca.help or "") for ca in arc_sub._choices_actions}

    sessions_parser = arc_sub.choices["sessions"]
    sess_sub = next(
        a
        for a in sessions_parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    sess_help = {ca.dest: (ca.help or "") for ca in sess_sub._choices_actions}

    subcmd_rows: list[tuple[str, str]] = []
    for name in ("chat", "confirm", "answer"):
        subcmd_rows.append((f"`cxas arc {name}`", help_by_name[name]))
    for sname in ("list", "get", "new", "delete"):
        subcmd_rows.append((f"`cxas arc sessions {sname}`", sess_help[sname]))
    for name in ("cancel", "suggestions"):
        subcmd_rows.append((f"`cxas arc {name}`", help_by_name[name]))

    chat_parser = arc_sub.choices["chat"]
    groups = {g.title: g for g in chat_parser._action_groups}
    target_rows = [_action_row(a) for a in groups["target"]._group_actions]
    turn_rows = [_action_row(a) for a in groups["turn"]._group_actions]

    def own_rows(p: argparse.ArgumentParser) -> list[tuple[str, str, str]]:
        opt_group = next(g for g in p._action_groups if g.title == "options")
        return [
            _action_row(a)
            for a in opt_group._group_actions
            if "--help" not in a.option_strings
        ]

    sections = [
        "# `cxas arc` flag reference",
        (
            "Generated from the `argparse` definitions in "
            "`cxas_scrapi/cli/arc_cli.py`. Run `cxas arc <command> --help` "
            "for the live text."
        ),
        "## Sub-commands",
        _gfm_table(["Command", "Purpose"], subcmd_rows),
        "## Target and session flags (all sub-commands)",
        "Available on every `cxas arc` sub-command.",
        _gfm_table(["Flag", "Default", "Description"], target_rows),
        "## Turn flags (`chat`, `confirm`, `answer`)",
        "Available on the sub-commands that send a turn to Arc.",
        _gfm_table(["Flag", "Default", "Description"], turn_rows),
    ]
    per_cmd = [
        ("`cxas arc chat` flags", arc_sub.choices["chat"]),
        ("`cxas arc confirm` flags", arc_sub.choices["confirm"]),
        ("`cxas arc answer` flags", arc_sub.choices["answer"]),
        ("`cxas arc sessions list` flags", sess_sub.choices["list"]),
        ("`cxas arc cancel` flags", arc_sub.choices["cancel"]),
    ]
    for title, p in per_cmd:
        rows = own_rows(p)
        if rows:
            sections.append(f"## {title}")
            sections.append(
                _gfm_table(["Flag", "Default", "Description"], rows)
            )
    return "\n\n".join(sections) + "\n"
