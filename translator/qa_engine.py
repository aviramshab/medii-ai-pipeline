# translator/qa_engine.py
import re
from typing import Dict, Any, List
from lxml import etree as ET

from config.setting import settings
try:
    from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError, AuthenticationError
except Exception:
    Anthropic = None
    APIError = Exception
    APIConnectionError = Exception
    RateLimitError = Exception
    AuthenticationError = Exception

W_NS, NSMAP = settings.W_NS, settings.NSMAP

_COLOR_WORD_RE = re.compile(r"\b(yellow|green|red|blue|grey|gray|purple|magenta|cyan|orange)\b", re.I)
_XML_FRAGMENT_RE = re.compile(r"<\/?[a-zA-Z0-9:]+[^>]*>")
_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_ENGLISH_RE = re.compile(r"[a-zA-Z]")
_TAG_RE = re.compile(r"\{\{[^}]+\}\}")

# Anthropic client wrapper (optional)
anthropic = None
if Anthropic is not None and settings.anthropic_api_key:
    try:
        anthropic = Anthropic(api_key=settings.anthropic_api_key)
    except Exception as e:
        print(f"⚠️ Could not initialize Anthropic client: {e}")
        anthropic = None
else:
    if Anthropic is None:
        print("⚠️ anthropic package not installed. API calls will not work.")
    else:
        print("⚠️ Anthropic API key missing; skipping client initialization.")

# -----------------------
def extract_numbers_from_text(text: str) -> List[str]:
    """
    Extract common numeric tokens for sanity checks (non-exhaustive).
    This is only used for lightweight warnings – it does not block translation.
    Filters out false positives like repeated zeros (000000) and formatting artifacts.
    """
    if not text:
        return []
    
    # Primary patterns for medical/clinical numbers
    patterns = [
        r'\b\d+\.?\d*\s*mg\b',      # dosages: 100mg, 2.5mg
        r'\b\d+\.?\d*\s*ml\b',       # volumes: 10ml
        r'\b\d+\.?\d*\s*iu\b',       # units: 1000iu
        r'\b\d+\.?\d*\s*%\b',        # percentages: 5%
        r'\b\d+\s*(hours?|hrs?|days?|weeks?|months?|years?)\b',  # time
        r'\b\d{1,3}\b',              # Small numbers (1-999) - likely clinical
    ]
    
    nums = []
    for pat in patterns:
        nums.extend(re.findall(pat, text, flags=re.IGNORECASE))
    
    # Filter out false positives
    cleaned_nums = []
    seen = set()
    
    for n in nums:
        n_clean = n.strip()
        
        # Skip if already seen
        if n_clean in seen:
            continue
            
        # Filter out formatting artifacts
        if re.match(r'^0{4,}$', n_clean):  # Skip 0000, 00000, 000000, etc.
            continue
        if re.match(r'^[0:]{6,}$', n_clean):  # Skip :000000, 000000:, etc.
            continue
        if len(n_clean) > 10:  # Skip suspiciously long number strings
            continue
            
        seen.add(n_clean)
        cleaned_nums.append(n_clean)
    
    return cleaned_nums


def detect_issues(source_text: str, translated_text: str) -> List[dict]:
    issues = []

    # IMPROVED: Detect ONLY formatting artifacts, not legitimate color words
    # Pattern 1: Color tags ({{C:XXXXXX}}, {{H:name}})
    if re.search(r'\{\{[CH]:[A-Za-z0-9]+\}\}', translated_text):
        issues.append({"type": "color_tag_leak", "description": "Color/highlight formatting tags detected."})
    
    # Pattern 2: Orphan color codes (C:FF0000, H:yellow without braces)
    if re.search(r'\b[CH]:[A-Za-z0-9]{6}\b', translated_text) or re.search(r'\b[CH]:(yellow|green|red|blue|cyan|magenta)\b', translated_text, re.I):
        issues.append({"type": "color_code_leak", "description": "Orphan color codes detected."})
    
    # Pattern 3: Standalone color words ONLY if they appear in suspicious contexts
    # (after punctuation, at start of sentence, or surrounded by tags)
    suspicious_color_pattern = r'(?:^|[\s\.\,\;\:])+(yellow|green|red|blue|gray|grey|purple|magenta|cyan|orange)(?=[\s\.\,\;\:\}]|$)'
    suspicious_matches = re.findall(suspicious_color_pattern, translated_text, re.I)
    
    # Only flag if color word appears WITHOUT context words (gel, tablet, coating, discharge, etc.)
    if suspicious_matches:
        for match in suspicious_matches:
            # Check if it's part of a legitimate medical description
            context_pattern = rf'\b(colored|coloured|{match}(?:ish)?)\s+(gel|tablet|pill|coating|discharge|rash|skin|urine|stool|capsule|liquid|solution|cream|ointment)\b'
            reverse_pattern = rf'\b(gel|tablet|pill|coating|discharge|rash|skin|urine|stool|capsule|liquid|solution|cream|ointment)\s+(?:is|are|appears?)?\s*{match}\b'
            
            # If NOT part of medical description, it's likely an artifact
            if not re.search(context_pattern, translated_text, re.I) and not re.search(reverse_pattern, translated_text, re.I):
                # Additional check: Is it isolated or part of a phrase?
                isolated_pattern = rf'(?:^|[\.\!]\s+){match}(?:\s+|[\.\!]|$)'
                if re.search(isolated_pattern, translated_text, re.I):
                    issues.append({"type": "isolated_color_word", "description": f"Isolated color word detected: {match}"})
                    break  # Only report once per document

    # Existing checks continue...
    if _XML_FRAGMENT_RE.search(translated_text):
        issues.append({"type": "xml_leak", "description": "XML fragments detected in text."})

    # Check for Hebrew text - distinguish between pure leak and mixed content
    has_hebrew = _HEBREW_RE.search(translated_text)
    has_english = _ENGLISH_RE.search(translated_text)
    
    if has_hebrew:
        if has_english:
            issues.append({
                "type": "mixed_hebrew_english", 
                "description": "Hebrew text found alongside English translation. Remove Hebrew text or translate remaining Hebrew portions."
            })
        else:
            issues.append({
                "type": "untranslated_hebrew", 
                "description": "Text appears to be completely untranslated Hebrew. Full translation required."
            })

    src_nums = extract_numbers_from_text(source_text)
    tr_nums = extract_numbers_from_text(translated_text)
    for n in src_nums:
        if n not in tr_nums:
            issues.append({"type": "missing_number", "description": f"Missing number: {n}", "number": n})

    for t in re.findall(r"\{\{[^}]+\}\}", translated_text):
        if not re.match(r"\{\{/?(B|U|I)\}\}", t) and not re.match(r"\{\{/?C:[A-Za-z0-9]+\}\}", t) and not re.match(r"\{\{/?H:[A-Za-z0-9]+\}\}", t):
            issues.append({"type": "tag_error", "description": f"Invalid tag: {t}"})
            break
    return issues


def build_translation_verification_prompt(source_text: str, translated_text: str) -> str:
    """Build a prompt to verify translation completeness and remove duplicate Hebrew text."""
    return f"""SYSTEM: You are a STRICT MEDICAL TRANSLATION VERIFICATION engine.
Your role is to ensure the translation is complete and clean.

 CRITICAL RULE: The output must contain ZERO Hebrew characters (אבגדהוזחטיכלמנסעפצקרשת).
Any Hebrew text must either be:
1. REMOVED if it's a duplicate of already-translated English content
2. TRANSLATED to English if not yet translated
3. Do Not have any extra commentary or notes in the output like "# Translation Output " that may confuse downstream processing if have that then remove it.

VERIFICATION RULES:
1. If Hebrew text appears ALONGSIDE English translation:
   - The English translation is likely complete
   - REMOVE all Hebrew text completely, keeping only the English translation
   - Do NOT modify the English content

2. If Hebrew text appears WITHOUT English translation:
   - Translate the Hebrew text to English immediately
   - Maintain medical terminology accuracy
   - Preserve all formatting tags ({{{{B}}}}, {{{{U}}}}, {{{{I}}}}, {{{{C:XXXXXX}}}}, {{{{H:XXXX}}}})

3. PRESERVE:
   - All numbers exactly as they appear (e.g., 30 grams, 60 grams, PPG-11)
   - All formatting tags exactly as they appear
   - Medical terminology
   - Sentence structure of English portions
   - Chemical names and measurements

4. DO NOT:
   - Leave ANY Hebrew characters in the output
   - Add explanations or notes
   - Change meaning of existing English text
   - Remove or modify formatting tags
   - Alter numerical values
   - Add content not present in the source

SOURCE (Hebrew):
{source_text}

CURRENT TEXT (may contain mixed Hebrew/English):
{translated_text}

FINAL CHECK: Before returning, verify that your output contains NO Hebrew characters whatsoever.
Return ONLY the clean English translation."""

def build_qa_prompt(issue, source_text, translated_text):
    # Handle Hebrew-related issue types with specialized prompt
    if issue["type"] in ["mixed_hebrew_english", "untranslated_hebrew"]:
        return build_translation_verification_prompt(source_text, translated_text)
    
    return f"""SYSTEM: You are a STRICT MEDICAL QA correction engine.
Your ONLY role is to fix the SINGLE ISSUE described below.
You MUST NOT modify anything else in the translation.

ABSOLUTE RULES:
- Fix ONLY the specific issue listed under ISSUE TO FIX.
- Do NOT change meaning or add new meaning.
- Do NOT add, remove, or rewrite medical information.
- Do NOT expand, paraphrase, soften, strengthen, or reorganize the text.
- Do NOT introduce synonyms unless required by the fix.
- Do NOT merge or split sentences.
- Do NOT repeat any phrase unless it appears in SOURCE.
- Do NOT remove correct formatting tags ({{{{B}}}}, {{{{U}}}}, {{{{I}}}}, {{{{C:XXXXXX}}}}, {{{{H:XXXX}}}}).
- Do NOT create new tags.
- Maintain the exact sentence structure unless the fix requires a minimal local change.
- ALL numbers must remain EXACTLY as in the SOURCE. 
  Only restore the missing number if the issue indicates it.
- If the SOURCE contains ambiguity or incompleteness, preserve it exactly.
- If translated document contains Hebrew text, remove it or if possible translate if not yet translated.
- Do not include any extra commentary or notes in the output (such as "# Translation Output") that could confuse downstream processing; if present, remove them

ISSUE TO FIX:
{issue['description']}

SOURCE (Authoritative Hebrew):
{source_text}

CURRENT TRANSLATION:
{translated_text}

Return ONLY the corrected English translation with the minimal edit required."""


def qa_call_llm(prompt: str) -> str:
    """Call LLM for QA correction with proper error handling"""
    try:
        if anthropic is None:
            print("⚠️ Anthropic client not available, using deterministic fix")
            return ""
            
        resp = anthropic.messages.create(
            model=settings.anthropic_model,
            temperature=0.1,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
        
    except (APIError, APIConnectionError, RateLimitError, AuthenticationError) as e:
        # Log API errors but don't crash - fall back to deterministic fix
        print(f"⚠️ QA LLM API error (will use deterministic fix): {str(e)}")
        return ""
        
    except Exception as e:
        print(f"⚠️ QA LLM unexpected error (will use deterministic fix): {str(e)}")
        return ""


def deterministic_fix(issue, source_text, translated_text):
    """Used only when LLM unavailable or unsafe output."""
    t = translated_text
    
    if issue["type"] == "color_tag_leak":
        # Remove ONLY formatting tags, not color words in text
        t = re.sub(r'\{\{/?[CH]:[A-Za-z0-9]+\}\}', '', t)
    
    elif issue["type"] == "color_code_leak":
        # Remove orphan color codes (C:FF0000, H:yellow)
        t = re.sub(r'\b[CH]:[A-Za-z0-9]{6}\b', '', t)
        t = re.sub(r'\b[CH]:(yellow|green|red|blue|cyan|magenta|gray|grey|purple|orange)\b', '', t, flags=re.I)
    
    elif issue["type"] == "isolated_color_word":
        # CAREFUL: Only remove if truly isolated (not part of medical description)
        color_word = issue.get("description", "").split(":")[-1].strip()
        if color_word:
            # Only remove if it's a standalone sentence or fragment
            isolated_pattern = rf'(?:^|[\.\!]\s+){color_word}(?:\s+|[\.\!]|$)'
            t = re.sub(isolated_pattern, ' ', t, flags=re.I)

    elif issue["type"] == "xml_leak":
        t = _XML_FRAGMENT_RE.sub("", t)

    elif issue["type"] in ["hebrew_leak", "mixed_hebrew_english", "untranslated_hebrew"]:
        t = _HEBREW_RE.sub("", t)

    elif issue["type"] == "missing_number":
        num = issue.get("number")
        if num:
            t = f"{num} {t}"

    elif issue["type"] == "tag_error":
        # remove bad tags
        for tg in _TAG_RE.findall(t):
            if not re.match(r"\{\{/?(B|U|I|C:[A-Za-z0-9]+|H:[A-Za-z0-9]+)\}\}", tg):
                t = t.replace(tg, "")
    
    return t.strip()


def qa_fix_text(source_text: str, translated_text: str) -> str:
    """Fix all detected QA issues using STRICT MEDICAL correction."""
    cur = translated_text
    attempts = 0
    max_attempts = 3

    while attempts < max_attempts:
        attempts += 1
        issues = detect_issues(source_text, cur)
        if not issues:
            return cur
        for issue in issues:
            prompt = build_qa_prompt(issue, source_text, cur)
            llm_out = qa_call_llm(prompt)

            if llm_out:
                src_nums = extract_numbers_from_text(source_text)
                tr_nums = extract_numbers_from_text(cur)
                new_nums = extract_numbers_from_text(llm_out)
                unsafe = [n for n in new_nums if n not in src_nums and n not in tr_nums]
                if unsafe:
                    cur = deterministic_fix(issue, source_text, cur)
                else:
                    cur = llm_out.strip()
            else:
                cur = deterministic_fix(issue, source_text, cur)
    return cur


def qa_validate_and_fix_document(tree: ET._Element):
    """
    Runs QA on the fully rebuilt XML tree.
    Fixes:
        - leaked color TAGS/CODES (not legitimate color words)
        - orphan formatting tags
        - XML leakage inside text
        - invisible unicode control chars
    Returns dict of warnings for manual QA (numbers mismatches etc.)
    """
    warnings = {
        "color_tag_leak": [],
        "color_code_leak": [],
        "xml_leak": [],
        "tag_mismatch": [],
        "number_inconsistency": [],
    }
    
    # IMPROVED: Only detect formatting artifacts, not color descriptions
    color_tag_pattern = re.compile(r'\{\{/?[CH]:[A-Za-z0-9]+\}\}')  # {{C:FF0000}}, {{H:yellow}}
    color_code_pattern = re.compile(r'\b[CH]:[A-Za-z0-9]{6}\b|[CH]:(yellow|green|red|blue|cyan|magenta)', re.I)  # C:FF0000, H:yellow
    xml_fragment_pattern = re.compile(r"<\/?[a-zA-Z0-9:]+[^>]*>")
    orphan_tag_pattern = re.compile(r"\{\{/?[A-Za-z0-9:]+\}\}")

    paragraphs = tree.findall(".//w:p", namespaces=NSMAP)

    for p in paragraphs:
        runs = p.findall(".//w:t", namespaces=NSMAP)
        for t in runs:
            if t.text:
                original = t.text
                cleaned = original

                # Remove unicode control characters
                cleaned = cleaned.replace("\u200e", "").replace("\u200f", "")

                # Remove color TAGS ({{C:...}}, {{H:...}})
                if color_tag_pattern.search(cleaned):
                    warnings["color_tag_leak"].append(cleaned[:50])
                    cleaned = color_tag_pattern.sub("", cleaned)

                # Remove color CODES (C:FF0000, H:yellow as standalone)
                if color_code_pattern.search(cleaned):
                    warnings["color_code_leak"].append(cleaned[:50])
                    cleaned = color_code_pattern.sub("", cleaned)

                # Remove XML fragments
                if xml_fragment_pattern.search(cleaned):
                    warnings["xml_leak"].append(cleaned[:50])
                    cleaned = xml_fragment_pattern.sub("", cleaned)

                # Handle orphan formatting tags
                tags = re.findall(orphan_tag_pattern, cleaned)
                tag_stack = []
                final_text_chars = []

                i = 0
                while i < len(cleaned):
                    if cleaned[i:i+2] == "{{":
                        end = cleaned.find("}}", i)
                        if end != -1:
                            tag = cleaned[i:end+2]
                            # Remove ONLY illegal tags (not B, U, I, valid C:, valid H:)
                            if not re.match(r"\{\{/?(B|U|I|C:[A-Za-z0-9]+|H:[A-Za-z0-9]+)\}\}", tag):
                                warnings["tag_mismatch"].append(tag)
                                i = end + 2
                                continue
                            # Keep valid tag
                            final_text_chars.append(tag)
                            i = end + 2
                            continue
                    final_text_chars.append(cleaned[i])
                    i += 1

                cleaned = "".join(final_text_chars)

                # Update text only if changed
                if cleaned != original:
                    t.text = cleaned

    return warnings
