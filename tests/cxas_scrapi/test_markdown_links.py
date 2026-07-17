import pathlib
import re

import pytest
import requests

ROOT_DIR = pathlib.Path(__file__).parent.parent.parent
DOCS_DIR = ROOT_DIR / "docs"
README_PATH = ROOT_DIR / "README.md"


def extract_links(text):
    # Extract markdown links: [text](url)
    markdown_links = re.findall(r"\[.*?\]\((.*?)\)", text)
    # Extract HTML links: <a href="url">...</a>
    html_links = re.findall(r'<a\s+(?:[^>]*?\s+)?href="([^"]*)"', text)
    return set(markdown_links + html_links)


def is_external(url):
    return url.startswith("http://") or url.startswith("https://")


def is_ignored(url):
    ignored_patterns = ["mailto:", "#", "127.0.0.1", "localhost"]
    return any(pattern in url for pattern in ignored_patterns)


def get_markdown_files():
    files = []
    if README_PATH.exists():
        files.append(README_PATH)
    if DOCS_DIR.exists():
        files.extend(DOCS_DIR.glob("**/*.md"))
    return files


@pytest.mark.parametrize(
    "md_path", get_markdown_files(), ids=lambda p: str(p.relative_to(ROOT_DIR))
)
def test_markdown_links(md_path):
    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    links = extract_links(content)
    broken_links = []

    for link in links:
        if not link or is_ignored(link):
            continue

        if is_external(link):
            if md_path != README_PATH:
                continue

            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                response = requests.get(link, headers=headers, timeout=5)
                if response.status_code >= 400:
                    broken_links.append(
                        f"External: {link} (Status: {response.status_code})"
                    )
            except requests.RequestException as e:
                broken_links.append(f"External: {link} (Error: {e})")
        else:
            # Relative link
            # Remove query params or anchors if any
            clean_link = link.split("?")[0].split("#")[0]

            # Path relative to the containing markdown file
            target_path = (md_path.parent / clean_link).resolve()

            if not target_path.exists():
                broken_links.append(
                    f"Internal: {link} (Path not found: {target_path})"
                )

    msg = (
        f"Found broken links in {md_path.relative_to(ROOT_DIR)}:\n"
        + "\n".join(broken_links)
    )
    assert not broken_links, msg
