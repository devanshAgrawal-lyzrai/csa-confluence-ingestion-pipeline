import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def parse_storage_format(xhtml: str, page_id: str, title: str, space_key: str) -> dict:
    soup = BeautifulSoup(xhtml, "lxml-xml")
    sections = _extract_sections(soup)
    return {
        "page_id": page_id,
        "title": title,
        "space_key": space_key,
        "sections": sections,
    }


def _extract_sections(soup: BeautifulSoup) -> list:
    sections = []
    current_heading = None
    current_level = 0
    current_content = []

    for tag in soup.find_all(True, recursive=False):
        _walk(tag, sections, current_heading, current_level, current_content)

    # delegate to recursive walk over entire doc
    sections = []
    _collect_sections(list(soup.children), sections)
    return sections


def _collect_sections(tags, sections: list) -> None:
    current_heading: Optional[str] = None
    current_level: int = 0
    current_content: list = []

    def flush():
        if current_heading is not None or current_content:
            sections.append({
                "heading": current_heading or "",
                "level": current_level,
                "content": current_content[:],
            })

    for tag in tags:
        if not isinstance(tag, Tag):
            continue
        tag_name = tag.name

        if tag_name in HEADING_TAGS:
            flush()
            current_heading = _clean_text(tag)
            current_level = int(tag_name[1])
            current_content = []
        else:
            block = _extract_content_block(tag)
            if block is not None:
                current_content.append(block)
            # recurse into divs / wrappers that don't produce a block themselves
            elif tag_name in ("div", "section", "article", "body", "root"):
                _collect_sections(list(tag.children), sections if not current_heading else [])

    flush()


def _extract_content_block(tag: Tag) -> Optional[dict]:
    name = tag.name
    if name in ("p",):
        text = _clean_text(tag)
        if text:
            return {"type": "paragraph", "text": text}
        return None
    if name in ("ul", "ol"):
        items = [_clean_text(li) for li in tag.find_all("li")]
        items = [i for i in items if i]
        if items:
            return {"type": "list", "items": items}
        return None
    if name == "table":
        return _parse_table(tag)
    if name == "ac:structured-macro":
        macro_name = tag.get("ac:name", "unknown")
        plain_text_body = tag.find("ac:plain-text-body")
        rich_text_body = tag.find("ac:rich-text-body")
        if plain_text_body:
            text = plain_text_body.get_text(separator=" ", strip=True)
        elif rich_text_body:
            text = rich_text_body.get_text(separator=" ", strip=True)
        else:
            text = tag.get_text(separator=" ", strip=True)
        if text:
            return {"type": "macro", "name": macro_name, "text": text}
        return None
    # skip images, attachments, layout containers
    return None


def _parse_table(tag: Tag) -> dict:
    rows = []
    for tr in tag.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            cells.append(_clean_text(cell))
        if cells:
            rows.append(cells)
    if rows:
        return {"type": "table", "rows": rows}
    return None


def to_plain_text(document: dict) -> str:
    """Convert the internal document model to readable plain text for the KB Parse API."""
    lines = []
    lines.append(f"Title: {document.get('title', '')}")
    lines.append("")

    for section in document.get("sections", []):
        heading = section.get("heading", "")
        if heading:
            prefix = "#" * max(1, section.get("level", 2))
            lines.append(f"{prefix} {heading}")
            lines.append("")

        for block in section.get("content", []):
            btype = block.get("type")
            if btype == "paragraph":
                lines.append(block.get("text", ""))
                lines.append("")
            elif btype == "list":
                for item in block.get("items", []):
                    lines.append(f"- {item}")
                lines.append("")
            elif btype == "table":
                rows = block.get("rows", [])
                if rows:
                    lines.append(" | ".join(str(c) for c in rows[0]))
                    lines.append(" | ".join(["---"] * len(rows[0])))
                    for row in rows[1:]:
                        lines.append(" | ".join(str(c) for c in row))
                    lines.append("")
            elif btype == "macro":
                text = block.get("text", "")
                if text:
                    lines.append(f"[{block.get('name', 'note')}]: {text}")
                    lines.append("")

    return "\n".join(lines).strip()


def _clean_text(tag: Tag) -> str:
    text = tag.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
