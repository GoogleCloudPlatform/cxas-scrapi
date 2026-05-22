import pathlib
import re

import requests

README_PATH = pathlib.Path(__file__).parent.parent.parent / "README.md"


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


def test_readme_links():
    assert README_PATH.exists(), f"README.md not found at {README_PATH}"

    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    links = extract_links(content)

    broken_links = []

    for link in links:
        if not link or is_ignored(link):
            continue

        if is_external(link):
            try:
                # Using a browser-like User-Agent might help avoid some blocks
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

            # Path relative to README
            target_path = README_PATH.parent / clean_link

            if not target_path.exists():
                broken_links.append(
                    f"Internal: {link} (Path not found: {target_path})"
                )

    msg = "Found broken links in README.md:\n" + "\n".join(broken_links)
    assert not broken_links, msg
