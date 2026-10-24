"""Keep substantive text and tables; never paraphrase financing/legal terms."""

import re
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import unquote, urljoin, urlsplit

from lxml import etree, html


@dataclass(frozen=True)
class Document:
    url: str
    name: str
    kind: str
    description: str


def archive_url(value, base):
    url = urljoin(base, value)
    parsed = urlsplit(url)
    # EDGAR's index sometimes points at its inline-XBRL viewer, not the file.
    if parsed.path in {"/ixviewer/doc/action", "/ix", "/ix.xhtml"}:
        from urllib.parse import parse_qs

        doc = parse_qs(parsed.query).get("doc", [""])[0]
        url = urljoin("https://www.sec.gov", doc)
        parsed = urlsplit(url)
    relative = parsed.path.removeprefix(urlsplit(base).path)
    if re.fullmatch(r"xslSCHEDULE_13D_X\d{2}/primary_doc\.xml", relative):
        url = base + "primary_doc.xml"
        parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.sec.gov"
        or parsed.query
        or ".." in unquote(parsed.path).split("/")
        or not parsed.path.startswith(urlsplit(base).path)
    ):
        raise ValueError("sec_document_outside_filing")
    return url


def document_manifest(raw, base, primary, form):
    root = html.fromstring(raw)
    rows = root.xpath('//table[@summary="Document Format Files"]//tr')
    if not rows:
        raise ValueError("sec_document_index_missing")
    included, skipped, seen = [], [], set()
    for row in rows:
        cells = row.xpath("./td")
        if len(cells) < 4:
            continue
        links = cells[2].xpath(".//a/@href")
        if not links:
            continue
        kind = cells[3].text_content().strip().upper()
        url = archive_url(links[0], base)
        name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        desc = " ".join(cells[1].text_content().split())
        is_main = name == primary or kind == form
        exhibit = kind.startswith("EX-")
        pure_legal = re.match(r"EX-(?:5|23|24|31|32)(?:\.|$)", kind)
        technical = kind.startswith(("EX-101", "EX-104"))
        if not is_main and (not exhibit or pure_legal or technical):
            skipped.append({"name": name, "type": kind, "reason": "non_economic_document"})
            continue
        if url not in seen:
            seen.add(url)
            included.append(Document(url, name, kind, desc))
    if not any(d.name == primary for d in included):
        raise ValueError("sec_primary_document_missing_from_index")
    included.sort(key=lambda d: d.name != primary)
    if len(included) > 40:
        raise ValueError("sec_material_document_count_limit")
    return included, skipped


BLOCKS = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "pre"}


def _tag(node):
    return str(node.tag).lower().split("}")[-1].split(":")[-1]


def _visible(node):
    parts = []

    def visit(element):
        if not isinstance(element.tag, str):
            return
        block = _tag(element) in BLOCKS | {"tr", "table", "br"}
        if block:
            parts.append(" ")
        if element.text:
            parts.append(element.text)
        for child in element:
            visit(child)
            if child.tail:
                parts.append(child.tail)
        if block:
            parts.append(" ")

    visit(node)
    return " ".join("".join(parts).replace("\xa0", " ").split())


def extract_document(raw, name):
    if name.lower().endswith(".xml"):
        return extract_schedule_13d(raw)
    if name.lower().endswith(".pdf") or raw.startswith(b"%PDF-"):
        from pypdf import PdfReader

        pdf = PdfReader(BytesIO(raw))
        if len(pdf.pages) > 500:
            raise ValueError("sec_pdf_page_limit")
        pages = []
        for page in pdf.pages:
            text = (
                (page.extract_text(extraction_mode="layout") or "") if page.get_contents() else ""
            )
            if not text.strip() and len(page.images):
                raise ValueError("sec_pdf_requires_ocr")
            pages.append(text)
        text = "\n\n".join(pages)
        if len(text.strip()) < 40:
            raise ValueError("sec_pdf_requires_ocr")
        return text.strip()
    if not name.lower().endswith((".htm", ".html", ".xhtml", ".txt")):
        raise ValueError("sec_material_document_unsupported_format")
    # Decode UTF-8 explicitly; lxml otherwise guesses Latin-1 on XML-less filings.
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Older EDGAR exhibit producers still emit Windows-1252 (including
        # currency and smart quotes) without a reliable HTML charset header.
        decoded = raw.decode("cp1252", errors="replace")
    if re.search(
        r"Your Request Originates from an Undeclared Automated Tool|"
        r"Request Rate Threshold Exceeded|SEC\.gov\s*\|\s*Access Denied",
        decoded[:10000],
        re.I,
    ):
        raise ValueError("sec_access_block_page")
    if "<" not in decoded:
        return decoded.strip()
    root = html.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", decoded))
    for image in root.xpath("//img"):

        def dimension(name, image=image):
            match = re.match(r"\d+", image.get(name, ""))
            return int(match[0]) if match else 0

        style = image.get("style", "")
        width = re.search(r"width\s*:\s*(\d+)", style)
        height = re.search(r"height\s*:\s*(\d+)", style)
        w = int(width[1]) if width else dimension("width")
        h = int(height[1]) if height else dimension("height")
        if w >= 400 and h >= 200 and len(image.get("alt", "")) < 80:
            raise ValueError("sec_material_images_require_ocr")
        if image.get("alt"):
            image.text = " " + image.get("alt") + " "
    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            continue
        tag = _tag(node)
        if (
            tag in {"script", "style", "noscript", "hidden", "header", "resources"}
            or node.get("hidden") is not None
            or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", node.get("style", ""), re.I)
        ):
            if node.getparent() is not None:
                node.drop_tree()
    parts = []

    def visit(node):
        if not isinstance(node.tag, str):
            return
        tag = _tag(node)
        if tag == "table":
            parts.append("\n\n")
            for row in node.xpath(".//tr"):
                if row.xpath("ancestor::table[1]")[0] is not node:
                    continue
                cells = [_visible(c) for c in row.xpath("./td|./th")]
                if any(cells):
                    parts.append(" | ".join(cells) + "\n")
            parts.append("\n\n")
            return
        if tag in BLOCKS or tag == "br":
            parts.append("\n")
        if node.text:
            parts.append(re.sub(r"\s+", " ", node.text))
        for child in node:
            visit(child)
            if child.tail:
                parts.append(re.sub(r"\s+", " ", child.tail))
        if tag in BLOCKS:
            parts.append("\n")

    visit(root)
    lines = [
        re.sub(r"[^\S\n]+", " ", line.replace("\xa0", " ")).strip()
        for line in "".join(parts).splitlines()
    ]
    return "\n".join(
        line
        for line in lines
        if line
        and not re.fullmatch(
            r"(?:Field:.*|Document created using Wdesk|Copyright \d{4} Workiva)", line
        )
    )


def extract_schedule_13d(raw, *, expected_cik=None):
    """Keep labelled ownership facts and all narrative items, excluding transport headers."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(raw, parser=parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("sec_13d_external_document_type")
    if root.tag != "{http://www.sec.gov/edgar/schedule13D}edgarSubmission":
        raise ValueError("sec_unsupported_xml_document")
    form = root.find("{*}formData")
    if form is None:
        raise ValueError("sec_13d_form_data_missing")
    issuer = form.find(".//{*}issuerCIK")
    if expected_cik and (issuer is None or int(issuer.text) != int(expected_cik)):
        raise ValueError("sec_13d_issuer_cik_mismatch")
    lines = []

    def visit(node, path):
        if not isinstance(node.tag, str):
            return
        label = etree.QName(node).localname
        children = [c for c in node if isinstance(c.tag, str)]
        here = path + [label]
        if children:
            for index, child in enumerate(children, 1):
                visit(child, here + [str(index)])
        elif node.text and node.text.strip():
            text = node.text.strip()
            if re.search(r"<(?:p|div|table|br)\b", text, re.I):
                text = extract_document(text.encode(), "narrative.htm")
            label_path = " / ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", p) for p in here)
            lines.append(f"{label_path}: {text}")

    visit(form, [])
    if not lines:
        raise ValueError("sec_13d_empty_form")
    return "\n".join(lines)


def clean_main(text, form):
    if form.startswith("8-K"):
        first = re.search(r"(?im)^\s*Item\s+[1-9]\.\d{2}\b", text)
        if first:
            text = text[first.start() :]
    if form.startswith(("8-K", "6-K")):
        signature = re.search(r"(?im)^\s*SIGNATURES?\s*$", text)
        if signature:
            text = text[: signature.start()]
    return text.strip()


def merge_documents(parts):
    seen, merged, removed = set(), [], 0
    for document, text in parts:
        lines = []
        for line in text.splitlines():
            normalized = " ".join(line.split())
            # Exact prose repeats only. Never discard numeric table rows or a
            # similar clause whose amount, maturity or exception has changed.
            if len(normalized) >= 180 and " | " not in line:
                if normalized in seen:
                    removed += 1
                    continue
                seen.add(normalized)
            lines.append(line)
        merged.append(
            f"[{document.kind} — {document.description or document.name}]\n"
            f"Source: {document.url}\n" + "\n".join(lines)
        )
    return "\n\n".join(merged), removed
