# translator/counter.py

def count_prompt_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text)  # Approximation: 1 token ~ 4 characters