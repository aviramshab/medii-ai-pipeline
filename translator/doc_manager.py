# translator/doc_manager.py
import os
import re
import zipfile
from typing import List
from lxml import etree as ET

from config.setting import settings

# Optional imports
try:
    from PyPDF2 import PdfReader
    _HAS_PYPDF2 = True
except Exception:
    _HAS_PYPDF2 = False


W_NS = settings.W_NS
NSMAP = settings.NSMAP

# -----------------------
# Simple readers
# -----------------------
def read_docx_text_simple(path: str) -> str:
    """Read text from DOCX by extracting word/document.xml and concatenating w:t nodes."""
    if not os.path.exists(path):
        return ""
    try:
        with zipfile.ZipFile(path, "r") as z:
            xml = z.read("word/document.xml")
            tree = ET.fromstring(xml)
        texts = [t.text for t in tree.findall(".//w:t", namespaces=NSMAP) if t.text]
        return "\n".join(texts)
    except Exception:
        return ""

def read_pdf_text_simple(path: str) -> str:
    """Read text from PDF using PyPDF2 when available; otherwise return empty string."""
    if not _HAS_PYPDF2 or not os.path.exists(path):
        return ""
    try:
        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(pages)
    except Exception:
        return ""

def read_reference_text(path: str) -> str:
    """
    Unified reader for reference file. Supports .docx, .pdf, .txt.
    Returns raw text.
    """
    if not path or not os.path.exists(path):
        return ""
    lower = path.lower()
    if lower.endswith(".docx"):
        return read_docx_text_simple(path)
    if lower.endswith(".pdf"):
        return read_pdf_text_simple(path)
    # fallback for plain text
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def read_docx_text(path: str) -> str:
    """
    Reads text from Template files (or any DOCX).chars for efficiency.
    Gracefully handles non-DOCX files.
    """
    if not os.path.exists(path):
        return ""
    try:
        with zipfile.ZipFile(path, 'r') as z:
            xml = z.read("word/document.xml")
            tree = ET.fromstring(xml)
        texts = [t.text for t in tree.findall(".//w:t", namespaces=NSMAP) if t.text]
        return "\n".join(texts)
    except Exception:
        return ""
# -----------------------
# Sentence extraction for reference examples
# -----------------------
def split_into_sentences_neutral(text: str) -> List[str]:
    """
    Neutral sentence splitter that avoids domain assumptions.
    Returns cleaned sentences between 8 and 250 chars.
    """
    if not text:
        return []

    s = re.sub(r'\r\n', '\n', text)
    s = re.sub(r'[\u200e\u200f]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    parts = re.split(r'(?<=[\.\?\!])\s+|\n+', s)

    cleaned = []
    seen = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if re.match(r'^(page\s*\d+)$', p.lower()):
            continue
        p = re.sub(r'^[\-\u2014\s]+', '', p)
        p = re.sub(r'[\-\u2014\s]+$', '', p)
        if 1 <= len(p) <= 500:
            if p not in seen:
                seen.add(p)
                cleaned.append(p)
    return cleaned


def extract_reference_sentences(reference_text: str) -> List[str]:
    """
    Extracts a list of neutral English sentences from the reference text.
    Returns up to max_sentences (de-duplicated, cleaned).
    No categories, no keywords — purely neutral extraction.
    """
    if not reference_text:
        return []
    sentences = split_into_sentences_neutral(reference_text)
    return sentences

# -----------------------
# Tag handling helpers
# -----------------------
def clean_style_tags(text: str) -> str:
    """
    Removes style tags but keeps content.
    IMPORTANT: Do NOT strip color hex codes from tags (we preserve tag content removal only).
    This function removes any style tags like {{B}}, {{/B}}, {{U}}, {{/U}}, {{I}}, {{/I}},
    {{C:XXXXXX}}, {{/C:XXXXXX}}, {{H:NAME}}, {{/H:NAME}} but leaves the inner text.
    """
    if not text:
        return text
    # Normalize single-brace tags to double-brace form
    text = re.sub(r'\{(/?[A-Za-z0-9:]+)\}', r'{{\1}}', text)

    # Remove all recognized tags but preserve the text
    # Match opening and closing tags like {{B}}, {{/B}}, {{C:FF0000}}, {{/C:FF0000}}, {{H:yellow}}
    text = re.sub(r'\{\{/?(?:B|U|I|C:[A-Za-z0-9]+|H:[A-Za-z0-9]+)\}\}', '', text)
    return text

def get_run_style_tags(r_elem: ET._Element) -> List[str]:
    """
    Detects Bold, Underline, Italic, Color, Highlight formatting.
    Returns list of style tags.
    """
    tags = []
    rPr = r_elem.find("w:rPr", namespaces=NSMAP)
    if rPr is None:
        return tags
    if rPr.find("w:b", namespaces=NSMAP) is not None:
        tags.append("{{B}}")
    if rPr.find("w:u", namespaces=NSMAP) is not None:
        tags.append("{{U}}")
    if rPr.find("w:i", namespaces=NSMAP) is not None:
        tags.append("{{I}}")
    color = rPr.find("w:color", namespaces=NSMAP)
    if color is not None:
          val = color.get(f"{{{W_NS}}}val")
          if val:
               pass
    return tags

def remove_adjacent_duplicate_urls(text: str) -> str:
    """
    Removes duplicate URLs/emails that appear adjacent to each other.
    Common in Hebrew documents where hyperlink text is followed by the same URL as plain text.
    Example: "https://example.comhttps://example.com" -> "https://example.com"
    Example: "email@test.comemail@test.com" -> "email@test.com"
    """
    if not text:
        return text
    
    # Pattern for URLs (http/https)
    url_pattern = r'(https?://[^\s<>\"\']+)'
    # Pattern for emails
    email_pattern = r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
    
    # Remove adjacent duplicate URLs (with or without space between)
    # Matches: URL immediately followed by same URL (no space or with space)
    text = re.sub(
        r'(https?://[^\s<>\"\']+)\s*\1',
        r'\1',
        text
    )
    
    # Remove adjacent duplicate emails (with or without space between)
    text = re.sub(
        r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\s*\1',
        r'\1',
        text,
        flags=re.IGNORECASE
    )
    
    return text


def extract_tagged_text(p_elem: ET._Element) -> str:
    """
    Converts XML paragraph to text with formatting tags.
    Example: {{B}}Text{{/B}}
    Also removes duplicate adjacent URLs/emails common in Hebrew source documents.
    """
    runs = []
    for r in p_elem.findall(".//w:r", namespaces=NSMAP):
        t = r.find("w:t", namespaces=NSMAP)
        if t is not None and t.text is not None:
            tags = get_run_style_tags(r)
            runs.append((t.text, tags))

    full_text = ""
    for text, current_tags in runs:
        if not current_tags:
            full_text += text
            continue
        prefix = "".join(current_tags)
        suffix = "".join([tag.replace("{{", "{{/") for tag in reversed(current_tags)])
        full_text += f"{prefix}{text}{suffix}"

    # Clean up fragmented tags
    full_text = full_text.replace("{{/B}}{{B}}", "")
    full_text = full_text.replace("{{/B}} {{B}}", " ")
    full_text = full_text.replace("{{B}} {{/B}}", " ")
    full_text = re.sub(r'(\{\{/[A-Z0-9:]+\}\})(\{\{[A-Z0-9:]+\}\})', r'\1', full_text)
    
    # Remove adjacent duplicate URLs/emails (common in Hebrew docs with hyperlinks)
    full_text = remove_adjacent_duplicate_urls(full_text)
    
    return full_text

def is_inside_table(p_elem: ET._Element) -> bool:
    """Check if paragraph is inside a table cell."""
    parent = p_elem.getparent()
    while parent is not None:
        if parent.tag == f"{{{W_NS}}}tc":
            return True
        parent = parent.getparent()
    return False

# -----------------------
# Table handling — preserve structure, set LTR
# -----------------------
def set_table_direction_ltr(tbl_elem: ET._Element):
    """
    Sets table to LTR but preserves existing alignment (center/left)
    so headers don't jump to the left edge.
    This removes bidiVisual (RTL table flag) and flips 'right' -> 'left' in jc.
    """
    tblPr = tbl_elem.find(f"{{{W_NS}}}tblPr")
    if tblPr is None:
        tblPr = ET.Element(f"{{{W_NS}}}tblPr")
        tbl_elem.insert(0, tblPr)

    # Remove bidiVisual (RTL table flag)
    bidi = tblPr.find(f"{{{W_NS}}}bidiVisual")
    if bidi is not None:
        tblPr.remove(bidi)

    # Smart alignment: Flip 'right' to 'left', preserve 'center'
    jc = tblPr.find(f"{{{W_NS}}}jc")
    if jc is not None:
        val = jc.get(f"{{{W_NS}}}val")
        if val == "right":
            jc.set(f"{{{W_NS}}}val", "left")
        # leave center alone

# -----------------------
# Rebuild paragraph (preserve runs and styles)
# -----------------------

def rebuild_paragraph(p_elem: ET._Element, translated_text: str):
    """
    Parses translated text with formatting tags and rebuilds paragraph XML.
    Preserves non-textual runs (images, drawings) and inserts them BEFORE text.
    Also preserves font-size, spacing, and other run-level styles.
    Forces translated text to use Arial font.
    """
    # Clean duplicate URLs/emails from translated text (in case LLM preserved them)
    translated_text = remove_adjacent_duplicate_urls(translated_text)
    
    original_rPr = None
    for child in list(p_elem):
        if child.tag == f"{{{W_NS}}}r":
            orig_rPr = child.find(f"{{{W_NS}}}rPr", namespaces=NSMAP)
            if orig_rPr is not None:
                original_rPr = orig_rPr
                break

    preserved_elements = []
    pPr_element = None
    children_to_remove = []
    for child in list(p_elem):
        is_run = child.tag == f"{{{W_NS}}}r"
        if child.tag == f"{{{W_NS}}}pPr":
            pPr_element = child
            continue

        if is_run and child.find(f"{{{W_NS}}}t", namespaces=NSMAP) is None:
            preserved_elements.append(child)
        elif not is_run:
            # Hyperlinks must be REMOVED (not preserved) because their text 
            # is already extracted and translated. Keeping them causes URL duplication.
            if child.tag == f"{{{W_NS}}}hyperlink":
                children_to_remove.append(child)
                continue
            preserved_elements.append(child)

        children_to_remove.append(child)

    for child in children_to_remove:
        p_elem.remove(child)

    new_text_runs = []
    parts = re.split(r'(\{\{/?[A-Za-z]+(:[A-Za-z0-9]+)?\}\})', translated_text)

    current_styles = {
        "bold": False,
        "underline": False,
        "italic": False,
        "color": None,
        "highlight": None
    }

    for part in parts:
        if not part:
            continue

        # Handle formatting tags
        if part.startswith("{{") and part.endswith("}}"):
            content = part.strip("{}")
            is_close = content.startswith("/")
            tag_type_full = content[1:] if is_close else content
            tag_type = tag_type_full.split(":")[0]

            if tag_type == "B":
                current_styles["bold"] = not is_close
            elif tag_type == "U":
                current_styles["underline"] = not is_close
            elif tag_type == "I":
                current_styles["italic"] = not is_close
            elif tag_type == "C":
                current_styles["color"] = None if is_close else tag_type_full.split(":")[1]
            elif tag_type == "H":
                current_styles["highlight"] = None if is_close else tag_type_full.split(":")[1]
            continue

        # Build run
        run = ET.Element(f"{{{W_NS}}}r")
        rPr = ET.SubElement(run, f"{{{W_NS}}}rPr")

        # FORCE ARIAL FONT FOR TRANSLATED TEXT
        rFonts = ET.SubElement(rPr, f"{{{W_NS}}}rFonts")
        rFonts.set(f"{{{W_NS}}}ascii", "Arial")
        rFonts.set(f"{{{W_NS}}}hAnsi", "Arial")
        rFonts.set(f"{{{W_NS}}}cs", "Arial")
        rFonts.set(f"{{{W_NS}}}eastAsia", "Arial")

        # Preserve size/spacing from original paragraph
        if original_rPr is not None:
            for elem in original_rPr:
                tag = elem.tag
                if tag in [
                    f"{{{W_NS}}}sz",
                    f"{{{W_NS}}}szCs",
                    f"{{{W_NS}}}spacing",
                    f"{{{W_NS}}}position",
                ]:
                    new_elem = ET.SubElement(rPr, tag)
                    for attrib, val in elem.attrib.items():
                        new_elem.set(attrib, val)

        # Apply active styles
        if current_styles["bold"]:
            ET.SubElement(rPr, f"{{{W_NS}}}b")

        if current_styles["underline"]:
            u = ET.SubElement(rPr, f"{{{W_NS}}}u")
            u.set(f"{{{W_NS}}}val", "single")

        if current_styles["italic"]:
            ET.SubElement(rPr, f"{{{W_NS}}}i")

        if current_styles["color"]:
            c = ET.SubElement(rPr, f"{{{W_NS}}}color")
            c.set(f"{{{W_NS}}}val", current_styles["color"])

        if current_styles["highlight"]:
            h = ET.SubElement(rPr, f"{{{W_NS}}}highlight")
            h.set(f"{{{W_NS}}}val", current_styles["highlight"])

        # Force English language
        lang = ET.SubElement(rPr, f"{{{W_NS}}}lang")
        lang.set(f"{{{W_NS}}}val", "en-US")
        lang.set(f"{{{W_NS}}}bidi", "en-US")

        # Add text node
        t = ET.SubElement(run, f"{{{W_NS}}}t")
        t.set(f"{{http://www.w3.org/XML/1998/namespace}}space", "preserve")
        t.text = clean_style_tags(part)
        new_text_runs.append(run)

    # Reinsert structure
    insert_index = 0
    if pPr_element is not None:
        p_elem.insert(0, pPr_element)
        insert_index = 1

    for child in preserved_elements:
        p_elem.insert(insert_index, child)
        insert_index += 1

    for run in new_text_runs:
        p_elem.insert(insert_index, run)
        insert_index += 1

# -----------------------
# Paragraph layout fixes    
#--------------------
def force_english_layout(p_elem: ET._Element):
    """
    Forces Left-to-Right layout for English text.
    Corrects indentation (start/end -> left/right).
    """
    pPr = p_elem.find("w:pPr", namespaces=NSMAP)
    if pPr is None:
        pPr = ET.Element(f"{{{W_NS}}}pPr")
        p_elem.insert(0, pPr)
    bidi = pPr.find("w:bidi", namespaces=NSMAP)
    if bidi is None:
        bidi = ET.SubElement(pPr, f"{{{W_NS}}}bidi")
    bidi.set(f"{{{W_NS}}}val", "0")
    jc = pPr.find("w:jc", namespaces=NSMAP)
    if jc is not None:
        val = jc.get(f"{{{W_NS}}}val")
        if val in ["right", "end"]:
            jc.set(f"{{{W_NS}}}val", "left")
    else:
        jc = ET.SubElement(pPr, f"{{{W_NS}}}jc")
        jc.set(f"{{{W_NS}}}val", "left")
    ind = pPr.find(f"{{{W_NS}}}ind", namespaces=NSMAP)
    if ind is not None:
        start_val = ind.get(f"{{{W_NS}}}start")
        if start_val:
            ind.set(f"{{{W_NS}}}left", start_val)
            if f"{{{W_NS}}}start" in ind.attrib:
                del ind.attrib[f"{{{W_NS}}}start"]
        end_val = ind.get(f"{{{W_NS}}}end")
        if end_val:
            ind.set(f"{{{W_NS}}}right", end_val)
            if f"{{{W_NS}}}end" in ind.attrib:
                del ind.attrib[f"{{{W_NS}}}end"]