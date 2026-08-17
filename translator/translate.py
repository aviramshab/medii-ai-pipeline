import os
import re
import shutil
import zipfile
import time
from typing import List, Dict, Any,Tuple
from lxml import etree as ET
from tqdm import tqdm

from config.setting import settings
try:
    from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError, AuthenticationError
except Exception:
    Anthropic = None
    APIError = Exception
    APIConnectionError = Exception
    RateLimitError = Exception
    AuthenticationError = Exception

from translator.doc_manager import (
    read_docx_text,
    read_reference_text,
    extract_reference_sentences,
    extract_tagged_text,
    is_inside_table,
    rebuild_paragraph,
    force_english_layout,
    set_table_direction_ltr,
)

from translator.qa_engine import (
    qa_fix_text,
    qa_validate_and_fix_document,
    extract_numbers_from_text,
)

# Custom exception for translation errors
class TranslationAPIError(Exception):
    """Custom exception for API-related translation errors"""
    def __init__(self, message: str, error_type: str = "api_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(self.message)

# Anthropic client wrapper (optional)
anthropic = None
if Anthropic is not None and settings.anthropic_api_key:
    try:
        anthropic = Anthropic(api_key=settings.anthropic_api_key)
    except Exception as e:
        print(f" Could not initialize Anthropic client: {e}")
        anthropic = None
else:
    if Anthropic is None:
        print(" anthropic package not installed. API calls will not work.")
    else:
        print("Anthropic API key missing; skipping client initialization.")


W_NS, NSMAP = settings.W_NS, settings.NSMAP
MODEL = settings.anthropic_model
MAX_CHARS_PER_CALL = settings.max_chars_per_call
MAX_OUTPUT_TOKENS = settings.max_output_tokens


# Add this after the imports section, around line 45
def final_hebrew_cleanup(tree: ET._Element) -> int:
    """
    Final safety net: Remove any remaining Hebrew characters from the document.
    This runs AFTER all translation and QA steps.
    Returns count of text nodes cleaned.
    """
    cleaned_count = 0
    hebrew_pattern = re.compile(r'[\u0590-\u05FF]+')
    
    for t_elem in tree.findall(".//w:t", namespaces=NSMAP):
        if t_elem.text and hebrew_pattern.search(t_elem.text):
            original_text = t_elem.text
            cleaned_text = hebrew_pattern.sub('', t_elem.text)
            
            # Only modify if there's still content after cleaning
            if cleaned_text.strip():
                t_elem.text = cleaned_text
                cleaned_count += 1

                print(f"   🧹 Cleaned Hebrew from text: '{original_text[:50]}...'")
            else:
                # If nothing remains, just remove Hebrew but keep empty string
                t_elem.text = cleaned_text
                cleaned_count += 1
    
    return cleaned_count

def format_reference_block_as_examples(ref_sentences: List[str]) -> str:
    """
    Create a compact block of reference English sentences that will be supplied
    to the model as phrasing examples. The model is explicitly instructed to
    select only matching sentences and NOT to copy or add new clinical content.
    """
    if not ref_sentences:
        return ""
    chosen = ref_sentences
    lines = ["\nREFERENCE SENTENCES (ENGLISH - TERMINOLOGY AUTHORITY):"]
    lines.append("NOTE: These are authoritative English sentences from a trusted reference document.")
    lines.append("*** ABSOLUTE REQUIREMENT ***")
    lines.append("You MUST copy the EXACT English words AND word order from this reference when translating equivalent concepts.")
    lines.append("")
    lines.append("FORBIDDEN SUBSTITUTIONS:")
    lines.append("- 'residual' in reference → NEVER write 'trace', 'remaining', 'minimal'")
    lines.append("- 'medicine' in reference → NEVER write 'drug', 'medication', 'pharmaceutical'")
    lines.append("- 'injection' in reference → NEVER write 'shot'")
    lines.append("- ANY term in reference → Use that EXACT term, no synonyms allowed")
    lines.append("")
    lines.append("")
    lines.append("CRITICAL TERMINOLOGY RULE:")
    lines.append("- When the SOURCE meaning matches a reference sentence, you MUST use the EXACT English terminology from the reference.")
    lines.append("- Do NOT substitute synonyms for terms that appear in the reference (e.g., if reference says 'trace', do NOT use 'residual'; if reference says 'mild', do NOT use 'slight').")
    lines.append("- The reference terminology takes PRIORITY over your own word choices.")
    lines.append("")
    lines.append("FORBIDDEN SUBSTITUTIONS:")
    lines.append("- 'medicine' in reference → NEVER write 'drug', 'medication', 'pharmaceutical'")
    lines.append("- 'residual' in reference → NEVER write 'trace', 'remaining', 'minimal'")
    lines.append("RESTRICTIONS:")
    lines.append("- You MUST NOT import or add clinical content from these sentences that is NOT present in the SOURCE.")
    lines.append("- Do NOT add missing side effects, warnings, dosages, or any new clinical statements.")
    lines.append("- Use reference ONLY for terminology and phrasing when SOURCE meaning matches exactly.")
    lines.append("FORBIDDEN REORDERING:")
    lines.append("- 'reversible non-infectious inflammation' → NEVER reorder to 'non-infectious and reversible inflammation'")
    lines.append("- Do NOT add 'and' between adjectives if reference doesn't have it")
    lines.append("- Do NOT change the order of medical descriptors")
    lines.append("- Copy the EXACT phrase structure including word order")
    lines.append("HOW TO USE THIS REFERENCE:")
    lines.append("1. Find the matching concept in reference sentences below")
    lines.append("2. Copy the EXACT English terminology AND word order from reference")
    lines.append("3. Apply that terminology consistently throughout the ENTIRE document")
    lines.append("")
    lines.append("REFERENCE SENTENCES:\n")
    for s in chosen:
        one = " ".join(s.split())
        lines.append(f"- {one}")
    return "\n".join(lines)


def build_prompt(batch_texts: List[str], template_content: str, is_table: bool = False, reference_sentences: List[str] = None) -> str:
    """
    Replacement build_prompt that includes neutral reference sentences as examples.
    The model is instructed to pick from these only when the meaning matches.
    """
    text_block = "\n[[[BLOCK-SEPARATOR]]]\n".join(batch_texts)
    table_instruction = ""
    if is_table:
       table_instruction = """
TABLE / STRUCTURE RULES (VERY IMPORTANT):
- This content is from a table. Do NOT add or remove rows or columns.
- Do NOT rearrange, reorder, or swap the position of any cell. Preserve the exact cell order.
- Before writing, check that this space is black and white (no added color text).
- If the source cell is empty, the translated cell must also be empty.
- Do NOT modify, overwrite, or interfere with the table structure (cell, row, or column). 
  Only translate the text inside each cell.
- Do NOT merge or split cells.
- Translate only the text inside each cell exactly as it appears.
- Preserve all formatting tags ({{B}}, {{U}}, {{I}}, {{H:XXXX}}, {{C:XXXXXX}}) inside cells.
- Keep the number of lines per cell EXACTLY the same as the input (do NOT join or break lines).
- Keep bullet points, numbering, and internal order inside each cell EXACTLY as in the SOURCE.
"""


    # Full system block (reconstructed and syntactically safe)

    system_block =settings.system_prompt

    template_block = f"\nTEMPLATE CONTEXT (for headings/style):\n{template_content}\n" if template_content else ""
    ref_block = format_reference_block_as_examples(reference_sentences) if reference_sentences else ""
    prompt = f"""{system_block}
{table_instruction}
{template_block}
{ref_block}
SOURCE BLOCKS TO TRANSLATE:
{{
{text_block}
}}

OUTPUT INSTRUCTIONS:
- Output exactly the same number of blocks, in the same order, separated only by lines containing exactly: [[[BLOCK-SEPARATOR]]]
- NEVER add clinical statements not present in the SOURCE.
- CRITICAL: When reference terminology exists for a concept, use the EXACT words from reference (no synonyms).
- Use reference sentences ONLY as phrasing examples for text that matches the SOURCE meaning EXACTLY.
- Do NOT copy or import numbers, dosages, or items that are not present in the SOURCE.
- Preserve tags and paragraph/block structure.
- Keep translations concise, clinical, and patient-friendly.
- Never Add any output explanations or comments like "# Translation Output " that type
- Now produce the English translations, blocks separated by [[[BLOCK-SEPARATOR]]]."""
    return prompt


def translate_batch(texts: List[str], template_content: str, is_table: bool = False, reference_sentences: List[str] = None) -> List[str]:
    """Translates a batch of text using the AI API and provides reference sentences as examples."""
    prompt = build_prompt(texts, template_content, is_table, reference_sentences)
    try:
        if anthropic is None:
            raise TranslationAPIError(
                "Anthropic client not available (anthropic package missing or API_KEY not set).",
                error_type="client_unavailable"
            )
        
        resp = anthropic.messages.create(
            model=MODEL,
            temperature=0.0,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        translated_blocks = [x.strip() for x in raw.split("\n[[[BLOCK-SEPARATOR]]]\n")]
        if len(translated_blocks) != len(texts):
            print(f" Mismatch: Expected {len(texts)} translations, got {len(translated_blocks)}. Padding/truncating.")
            while len(translated_blocks) < len(texts):
                translated_blocks.append(texts[len(translated_blocks)])
            translated_blocks = translated_blocks[:len(texts)]
        return translated_blocks
    
    except AuthenticationError as e:
        error_msg = f"Authentication failed: {str(e)}"
        print(f" API Error: {error_msg}")
        raise TranslationAPIError(error_msg, error_type="authentication_error")
    
    except RateLimitError as e:
        error_msg = f"Rate limit exceeded: {str(e)}"
        print(f" API Error: {error_msg}")
        raise TranslationAPIError(error_msg, error_type="rate_limit_error")
    
    except APIConnectionError as e:
        error_msg = f"Connection error: {str(e)}"
        print(f" API Error: {error_msg}")
        raise TranslationAPIError(error_msg, error_type="connection_error")
    
    except APIError as e:
        # Check for credit balance error
        error_str = str(e)
        if "credit balance" in error_str.lower():
            error_msg = "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."
            print(f" API Error: {error_msg}")
            raise TranslationAPIError(error_msg, error_type="insufficient_credits")
        
        # Check for token limit error
        if "tokens" in error_str.lower() and "limit" in error_str.lower():
            error_msg = f"Token limit exceeded: {error_str}"
            print(f" API Error: {error_msg}")
            raise TranslationAPIError(error_msg, error_type="token_limit_error")
        
        # Generic API error
        error_msg = f"API request failed: {str(e)}"
        print(f" API Error: {error_msg}")
        raise TranslationAPIError(error_msg, error_type="api_error")
    
    except Exception as e:
        error_msg = f"Unexpected error during translation: {str(e)}"
        print(f" Error: {error_msg}")
        raise TranslationAPIError(error_msg, error_type="unknown_error")

def remove_header_table(tree: ET._Element) -> bool:
    """Identifies and removes the specific Document History / metadata table
    based on its content (e.g., 'File Name', 'Job No.', 'Product'). Returns True if a table was removed.
    """
    body = tree.find(f"{{{W_NS}}}body", namespaces=NSMAP)
    if body is None:
        return False
    tables_removed = False
    for table in body.findall(f"{{{W_NS}}}tbl", namespaces=NSMAP):
        table_text = ET.tostring(table, encoding='unicode')
        if "File Name" in table_text or "Job No." in table_text or "Product" in table_text:
            print(" Found and removing Document History/Metadata table.")
            parent = table.getparent()
            if parent is not None:
                parent.remove(table)
                tables_removed = True
                break

    return tables_removed



def validate_number_fidelity(source_text: str, translated_text: str) -> Tuple[bool, List[str]]:
    """
    Ensure clinically-relevant numbers in source are preserved in translation (best-effort).
    Returns (is_ok, list_of_discrepancies)
    Ignores formatting artifacts and non-clinical number patterns.
    """
    src_nums = extract_numbers_from_text(source_text)
    tr_nums = extract_numbers_from_text(translated_text)
    
    discrepancies = []
    
    for n in src_nums:
        n_stripped = n.strip()
        
        # Skip validation for numbers that are likely formatting artifacts
        if n_stripped in ['0', '00', '000']:  # Common padding
            continue
            
        if n not in tr_nums:
            # Try normalized match (e.g., "hours" vs "hrs", "2.5mg" vs "2.5 mg")
            n_norm = n.lower().replace("hours", "hrs").replace("hour", "hr").replace(" ", "")
            found = False
            
            for tn in tr_nums:
                tn_norm = tn.lower().replace("hours", "hrs").replace("hour", "hr").replace(" ", "")
                if tn_norm == n_norm:
                    found = True
                    break
            
            # Also check if the core number exists (e.g., "100" in "100mg")
            if not found:
                core_num = re.search(r'\d+\.?\d*', n)
                if core_num:
                    core = core_num.group()
                    for tn in tr_nums:
                        if core in tn:
                            found = True
                            break
            
            if not found:
                discrepancies.append(f"Missing: {n}")
    
    return (len(discrepancies) == 0, discrepancies)


def postprocess_remove_blank_pages_and_fix_numbering(docx_path: str):
    """
    Remove ONLY truly blank pages.
    A page is blank if, between page boundaries, it contains:
      - no visible text
      - no tables
      - no drawings/images
    Whitespace-only paragraphs are ignored.
    """

    print("\n🧹 Running final blank-page cleanup + page-number fix...")

    # -------------------------------
    # STEP 1 – Load document.xml
    # -------------------------------
    with zipfile.ZipFile(docx_path, "r") as zin:
        xml = zin.read("word/document.xml")
        other_files = [
            (item.filename, zin.read(item.filename))
            for item in zin.infolist()
            if item.filename != "word/document.xml"
        ]

    tree = ET.fromstring(xml)
    body = tree.find(f"{{{W_NS}}}body")
    if body is None:
        print(" No <w:body> found.")
        return

    paragraphs = list(body.findall("w:p", namespaces=NSMAP))

    # -------------------------------
    # STEP 2 – Build TRUE page blocks
    # Page ends on:
    #   - page break
    #   - section break (<w:sectPr>)
    # -------------------------------
    pages = []
    current = []

    def ends_page(p):
        # explicit page break
        for el in p.iter():
            if el.tag.endswith("br") and el.get(f"{{{W_NS}}}type") == "page":
                return True
        # section break also ends page
        if p.find("w:pPr/w:sectPr", namespaces=NSMAP) is not None:
            return True
        return False

    for p in paragraphs:
        current.append(p)
        if ends_page(p):
            pages.append(current)
            current = []

    if current:
        pages.append(current)

    # -------------------------------
    # STEP 3 – Detect REAL content
    # -------------------------------
    def page_is_truly_blank(page):
        for p in page:
            # tables
            if p.find(".//w:tbl", namespaces=NSMAP) is not None:
                return False

            # images/drawings
            if (
                p.find(".//w:drawing", namespaces=NSMAP) is not None or
                p.find(".//w:pict", namespaces=NSMAP) is not None
            ):
                return False

            # visible text
            for t in p.findall(".//w:t", namespaces=NSMAP):
                if t.text and t.text.strip():
                    return False

        return True

    pages_to_remove = [p for p in pages if page_is_truly_blank(p)]

    # -------------------------------
    # STEP 4 – Remove blank pages
    # -------------------------------
    removed = 0
    for page in pages_to_remove:
        for p in page:
            parent = p.getparent()
            if parent is not None:
                parent.remove(p)
        removed += 1

    print(f"   → Removed {removed} completely blank page(s)")

    # -------------------------------
    # STEP 5 – Normalize page numbering
    # -------------------------------
    for fld in tree.findall(".//w:fldSimple", namespaces=NSMAP):
        instr = fld.get(f"{{{W_NS}}}instr")
        if instr and "PAGE" in instr.upper():
            fld.set(f"{{{W_NS}}}instr", " PAGE ")

    for instr in tree.findall(".//w:instrText", namespaces=NSMAP):
        if instr.text and "PAGE" in instr.text.upper():
            instr.text = " PAGE "

    print("   → Page numbering fields normalized")

    # -------------------------------
    # STEP 6 – Write DOCX safely
    # -------------------------------
    temp_path = docx_path + ".tmp"

    ET.register_namespace("w", W_NS)
    xml_bytes = ET.tostring(tree, encoding="UTF-8", xml_declaration=True)

    with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for fname, data in other_files:
            zout.writestr(fname, data)
        zout.writestr("word/document.xml", xml_bytes)

    os.replace(temp_path, docx_path)

    print(f" Final cleanup completed. Saved: {docx_path}")


def process_document_final(input_path: str, template_path: str, output_path: str, reference_path: str = None, progress_callback=None) -> Dict[str, Any]:

    print(f" Processing: {os.path.basename(input_path)}")

    template_content = read_docx_text(template_path)
    if reference_path:
     ref_text  = extract_reference_sentences(read_reference_text(reference_path))
    else:
     ref_text  = []

    shutil.copy(input_path, output_path)

    # read and parse document.xml from copied output file
    with zipfile.ZipFile(output_path, 'r') as z:
        xml = z.read("word/document.xml")
        tree = ET.fromstring(xml)

    removed = remove_header_table(tree)
    if removed:
        print("✓ Header table removed.")

    print("🔄 Converting RTL tables to LTR...")
    tables = tree.findall(".//w:tbl", namespaces=NSMAP)
    for table in tables:
        # Use set_table_direction_ltr from Script A to avoid column merging/reversals
        
        set_table_direction_ltr(table)

    paragraphs = tree.findall(".//w:p", namespaces=NSMAP)
    batch_mapping = []
    current_batch = []
    current_len = 0
    current_is_table = False
    
    print(" Building translation batches...")
    for i, p in enumerate(paragraphs):
        tagged = extract_tagged_text(p)
        clean = re.sub(r'[\u200e\u200f]', '', tagged)
        is_table = is_inside_table(p)
        if clean.strip():
            if current_batch and (is_table != current_is_table or current_len + len(clean) > MAX_CHARS_PER_CALL):
                batch_mapping.append((current_batch, current_is_table))
                current_batch = []
                current_len = 0
            current_batch.append((i, clean))
            current_len += len(clean)
            current_is_table = is_table
    if current_batch:
        batch_mapping.append((current_batch, current_is_table))
    print(f" Translating {len(batch_mapping)} batches...")

    start_time = time.time()
    
    # Track if any API error occurred
    api_error_occurred = False
    error_message = ""
    
    try:
        for batch_idx, (batch, is_table) in enumerate(tqdm(batch_mapping)):
            indices = [b[0] for b in batch]
            texts = [b[1] for b in batch]

            if progress_callback:
                progress_callback(min(int((batch_idx + 1) / len(batch_mapping) * 100), 99))

            # This will raise TranslationAPIError if API fails
            results = translate_batch(texts, template_content, is_table, ref_text)
            
            for j, trans in enumerate(results):
                p_node = paragraphs[indices[j]]
                src = texts[j]
                corrected = qa_fix_text(src, trans)
                valid, mismatches = validate_number_fidelity(src, corrected)
                if not valid:
                    print(f" After-QA numeric mismatch in block {batch_idx}-{j}: {mismatches}")
                try:
                    # Rebuild paragraph with preserved runs and styles
                    rebuild_paragraph(p_node, corrected)
                    # Force English paragraph layout
                    force_english_layout(p_node)
                except Exception as e:
                    print(f" Error rebuilding paragraph: {e}")
    
    except TranslationAPIError as e:
        # API error occurred - mark it and clean up
        api_error_occurred = True
        error_message = e.message
        print(f" Translation failed due to API error: {error_message}")
        
        # Clean up the output file if it was created
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
                print(f" Removed incomplete output file: {output_path}")
            except Exception as cleanup_error:
                print(f" Could not remove output file: {cleanup_error}")
        
        # Re-raise the error so it can be handled by the FastAPI endpoint
        raise

    print("🔍 Running document-level QA cleanups...")
    warnings = qa_validate_and_fix_document(tree)
    for k, v in warnings.items():
        if v:
            print(f" QA warning [{k}]: {len(v)} occurrences (sample): {v[:2]}")
    
    #  NEW: Final Hebrew cleanup (safety net)
    print("🧹 Running final Hebrew cleanup...")
    hebrew_cleaned = final_hebrew_cleanup(tree)
    if hebrew_cleaned > 0:
        print(f"    Removed Hebrew text from {hebrew_cleaned} location(s)")
    else:
        print(f"    No Hebrew text detected")

    # Register all common Office XML namespaces
    ET.register_namespace('w', W_NS)
    ET.register_namespace('r', "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
    ET.register_namespace('wp', "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing")

    print("💾 Saving output...")
    # Serialize XML properly with UTF-8 encoding and declaration
    xml_bytes = ET.tostring(
    tree,
    encoding='UTF-8',
    xml_declaration=True,
    method='xml'
    )

     # Write updated document.xml back into new docx
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
     with zipfile.ZipFile(input_path, 'r') as zin:
          for item in zin.infolist():
               if item.filename != "word/document.xml":
                    zout.writestr(item, zin.read(item.filename))
          zout.writestr("word/document.xml", xml_bytes)

    total_time = time.time() - start_time

    total_input_tokens = 0
    total_output_tokens = 0

    print(f" Finished! Saved to: {output_path}")
    postprocess_remove_blank_pages_and_fix_numbering(output_path)
    return {
        "total_time": total_time,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
    }