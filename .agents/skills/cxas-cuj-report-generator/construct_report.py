"""Module to reconstruct a single HTML report from YAML transcripts."""

from __future__ import annotations

import collections
import html
import json
import pathlib
import re
import string
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, TypedDict

import yaml
from absl import app, flags, logging

_TRANSCRIPTS_DIR = flags.DEFINE_string(
    "transcripts_dir",
    None,
    "Directory containing YAML transcripts.",
    required=True,
)
_OUTPUT_FILE = flags.DEFINE_string(
    "output_file", None, "Path to output HTML report.", required=True
)
_CUJ_REPORT = flags.DEFINE_boolean(
    "cuj_report", True, "Whether to generate a CUJ report (max 3 per CUJ)."
)
_REPORT_HEADING = flags.DEFINE_string(
    "report_heading", None, "Report heading.", required=True
)
_PROJECT_NAME = flags.DEFINE_string(
    "project_name", None, "Project name.", required=True
)
_TITLE = flags.DEFINE_string("title", None, "HTML title.", required=True)
_INTRO_CONTEXT = flags.DEFINE_string(
    "intro_context", None, "Introduction context.", required=True
)
_INTRO_GOAL = flags.DEFINE_string(
    "intro_goal", None, "Introduction goal.", required=True
)
_INDEX_TITLE = flags.DEFINE_string(
    "index_title", None, "Index title.", required=True
)


class Turn(TypedDict):
    speaker: str
    text: str
    webhook_call: Mapping[str, Any] | None
    tool_call: Mapping[str, Any] | None


_NON_ALPHA_PREFIX_RE = re.compile(r"^[^A-Za-z]+")
_CAMEL_CASE_SPLIT_RE = re.compile(r"(?<!^)(?=[A-Z])")


BASE_DIR = pathlib.Path(__file__).parent
COMPONENTS_DIR = BASE_DIR / "resources/components"


def load_component(name: str) -> str:
    """Loads a component file from the resources directory.

    Args:
      name: The name of the component file.

    Returns:
      The content of the file as a string.
    """
    with open(COMPONENTS_DIR / name, "r") as f:
        return f.read()


def _clean_name(name: str) -> str:
    """Cleans a file name to be used as a CUJ title.

    Args:
      name: The original file name.

    Returns:
      A cleaned, title-cased string.
    """
    before, sep, after = name.partition(".drawio")
    base_name = after if sep and after else before

    with_spaces = base_name.replace("_", " ").replace("-", " ")
    alpha_only = _NON_ALPHA_PREFIX_RE.sub("", with_spaces)
    camel_split = _CAMEL_CASE_SPLIT_RE.sub(" ", alpha_only)
    return " ".join(camel_split.split()).title()


def _render_turn(
    *,
    turn: Turn,
    row_id: str,
    turn_tpl: str,
    call_item_tpl: str,
) -> Iterator[str]:
    """Renders a single turn of the transcript to HTML.

    Args:
      turn: A mapping containing turn data. Expected keys: - speaker: 'Agent' or
        'User'. - text: The literal string spoken. - webhook_call: Optional
        mapping for webhook calls. - tool_call: Optional mapping for tool calls.
      row_id: The ID for the HTML row.
      turn_tpl: The template for a turn.
      call_item_tpl: The template for a call item.

    Yields:
      An HTML string representing the rendered turn.
    """
    speaker = turn.get("speaker", "")
    speaker_class = (speaker or "").lower()

    yield string.Template(turn_tpl).substitute(
        ROW_ID=row_id,
        SPEAKER_CLASS=speaker_class,
        SPEAKER=speaker,
        TEXT=html.escape(turn.get("text", "")),
        EXTRA_CONTENT="",
    )

    if wh := turn.get("webhook_call"):
        if isinstance(wh, str):
            wh = {"endpoint": wh}
        yield string.Template(call_item_tpl).substitute(
            CALL_TYPE="webhook",
            CALL_LABEL="Webhook",
            NAME=wh.get("endpoint") or "",
            URL_INFO=(
                f"{wh.get('method', 'POST')} {wh['url']}\n"
                if wh.get("url")
                else ""
            ),
            PAYLOAD=html.escape(json.dumps(wh.get("payload") or {}, indent=2)),
            RESPONSE=html.escape(
                json.dumps(wh.get("response") or {}, indent=2)
            ),
        )
    if tl := turn.get("tool_call"):
        yield string.Template(call_item_tpl).substitute(
            CALL_TYPE="tool",
            CALL_LABEL="Tool",
            NAME=tl.get("name") or "",
            URL_INFO="",
            PAYLOAD=html.escape(json.dumps(tl.get("payload") or {}, indent=2)),
            RESPONSE=html.escape(
                json.dumps(tl.get("response") or {}, indent=2)
            ),
        )


def _render_card(
    *,
    item: Mapping[str, Any],
    cuj_card_tpl: str,
    card_id: str,
    turn_tpl: str,
    call_item_tpl: str,
    card_header_tpl: str,
    card_summary_tpl: str,
) -> str:
    """Renders a single transcript card to HTML.

    Args:
      item: A mapping containing transcript data.
      cuj_card_tpl: The template for a card.
      card_id: The unique ID for this card.
      turn_tpl: The template for a turn.
      call_item_tpl: The template for a call item.
      card_header_tpl: The template for a card header.
      card_summary_tpl: The template for a card summary.

    Returns:
      The rendered HTML string for the card.
    """
    turns_html = []
    for i, turn in enumerate(item.get("turns", []), 1):
        row_id = f"{card_id}-row-{i}"
        turns_html.extend(
            _render_turn(
                turn=turn,
                row_id=row_id,
                turn_tpl=turn_tpl,
                call_item_tpl=call_item_tpl,
            )
        )

    card_title = _clean_name(item.get("subintent_name", "Untitled"))

    card_header = string.Template(card_header_tpl).substitute(
        CARD_TITLE=card_title
    )
    summary_tag = string.Template(card_summary_tpl).substitute(
        CARD_TITLE=card_title
    )

    return string.Template(cuj_card_tpl).substitute(
        CARD_ID=item.get("subintent_id", card_id),
        TRANSCRIPT_TURNS="\n".join(turns_html),
        SUMMARY_TAG=summary_tag,
        CARD_HEADER=card_header,
    )


def assemble_report(
    *,
    transcripts_by_cuj_name: Mapping[str, Sequence[Mapping[str, Any]]],
    output_file: pathlib.Path,
    component_loader: Callable[[str], str],
    cuj_report: bool,
    report_heading: str,
    project_name: str,
    title: str,
) -> None:
    """Assembles the HTML report from grouped data.

    Args:
      transcripts_by_cuj_name: A mapping from CUJ name to a sequence of
        transcripts. Each transcript is a mapping with keys like 'subintent_id',
        'subintent_name', 'parent_cuj', and 'turns'.
      output_file: Path to the output HTML file.
      component_loader: A callable that loads a component by name.
      cuj_report: Whether to generate a CUJ report (limit examples).
      report_heading: Report heading.
      project_name: Project name.
      title: HTML title.
    """
    base_shell = component_loader("base/base_shell.html")
    header = component_loader("header/header.html")
    note_modal = component_loader("note_modal/note_modal.html")
    floating_actions = component_loader(
        "floating_actions/floating_actions.html"
    )
    interaction_engine = component_loader("base/interaction_engine.js")
    cuj_card_summary_tpl = component_loader("cuj_card/cuj_card_summary.html")
    cuj_card_detailed_tpl = component_loader("cuj_card/cuj_card_detailed.html")
    card_header_tpl = component_loader("card_header.html")
    card_summary_tpl = component_loader("card_summary.html")

    turn_tpl = component_loader("turn.html")
    call_item_tpl = component_loader("call_item.html")
    intro_tpl = component_loader("intro.html")
    index_tpl = component_loader("index.html")
    index_item_tpl = component_loader("index_item.html")
    cuj_section_header_tpl = component_loader("cuj_section_header.html")
    cuj_section_footer_tpl = component_loader("cuj_section_footer.html")
    toast_tpl = component_loader("toast.html")
    main_container_tpl = component_loader("main_container.html")
    early_script = component_loader("base/early_script.js")

    intro_context = _INTRO_CONTEXT.value
    intro_goal = _INTRO_GOAL.value
    index_title = _INDEX_TITLE.value

    intro_html = string.Template(intro_tpl).substitute(
        INTRO_CONTEXT=intro_context, INTRO_GOAL=intro_goal
    )

    index_template = string.Template(index_tpl)
    index_html = index_template.substitute(
        INDEX_TITLE=index_title,
        INDEX_ITEMS="\n".join(
            string.Template(index_item_tpl).substitute(
                CUJ_ID=f"cuj-{cuj_counter}",
                COUNTER=cuj_counter,
                CUJ_NAME=cuj_name,
            )
            for cuj_counter, cuj_name in enumerate(
                sorted(transcripts_by_cuj_name.keys()), 1
            )
        ),
    )

    all_sections_html = []

    for cuj_counter, (cuj_name, items) in enumerate(
        sorted(transcripts_by_cuj_name.items()), 1
    ):
        cuj_id = f"cuj-{cuj_counter}"

        all_cards_html = []
        items_to_show = items[:3] if cuj_report else items

        for item_counter, item in enumerate(items_to_show, 1):
            card_html = _render_card(
                item=item,
                cuj_card_tpl=cuj_card_summary_tpl
                if cuj_report
                else cuj_card_detailed_tpl,
                card_id=f"{cuj_id}-card-{item_counter}",
                turn_tpl=turn_tpl,
                call_item_tpl=call_item_tpl,
                card_header_tpl=card_header_tpl,
                card_summary_tpl=card_summary_tpl,
            )
            all_cards_html.append(card_html)

        section_header_template = string.Template(cuj_section_header_tpl)
        limit_text = "(Showing top 3 examples)" if cuj_report else ""

        all_sections_html.append(
            "".join(
                (
                    section_header_template.substitute(
                        CUJ_ID=cuj_id, CUJ_NAME=cuj_name, LIMIT_TEXT=limit_text
                    ),
                    "\n".join(all_cards_html),
                    cuj_section_footer_tpl,
                )
            )
        )

    header_template = string.Template(header)
    header_hydrated = header_template.substitute(
        REPORT_HEADING=report_heading, PROJECT_NAME=project_name
    )

    full_content = intro_html + index_html + "\n".join(all_sections_html)
    main_container_template = string.Template(main_container_tpl)
    container_hydrated = main_container_template.substitute(
        CONTENT=full_content
    )

    body_content = "".join(
        (
            header_hydrated,
            container_hydrated,
            note_modal,
            floating_actions,
            toast_tpl,
        )
    )

    css_content = "\n".join(
        (
            component_loader("base/base.css"),
            component_loader("header/header.css"),
            component_loader("cuj_card/cuj_card.css"),
            component_loader("note_modal/note_modal.css"),
            component_loader("floating_actions/floating_actions.css"),
        )
    )

    shell_template = string.Template(base_shell)
    final_html = shell_template.substitute(
        TITLE=title,
        EARLY_SCRIPT=early_script,
        CSS_CONTENT=css_content,
        BODY=body_content,
        JS_CONTENT=(
            "const CALL_ITEM_TEMPLATE ="
            f" `{component_loader('call_item.html')}`;\n{interaction_engine}"
        ),
    )

    with open(output_file, "w") as f:
        f.write(final_html)
    logging.info("Successfully assembled report: %s", output_file)


def main(argv: Sequence[str]) -> None:
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")

    transcripts_dir = pathlib.Path(_TRANSCRIPTS_DIR.value)
    files = list(transcripts_dir.glob("*.yaml")) + list(
        transcripts_dir.glob("*.yml")
    )

    grouped_data = collections.defaultdict(list)
    logging.info("Loading transcripts from %s", transcripts_dir)
    for f in sorted(files):
        with open(f, "r") as file:
            content = yaml.safe_load(file)
            if not content:
                continue

            items = content if isinstance(content, list) else [content]
            for item in items:
                parent_cuj = item.get("parent_cuj", "Uncategorized")
                grouped_data[parent_cuj].append(item)

    assemble_report(
        transcripts_by_cuj_name=grouped_data,
        output_file=pathlib.Path(_OUTPUT_FILE.value),
        component_loader=load_component,
        cuj_report=_CUJ_REPORT.value,
        report_heading=_REPORT_HEADING.value,
        project_name=_PROJECT_NAME.value,
        title=_TITLE.value,
    )


if __name__ == "__main__":
    app.run(main)
