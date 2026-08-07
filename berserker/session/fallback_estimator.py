"""
Fallback token estimator with language-aware estimation.

Provides multi-language token estimation when tiktoken is unavailable.
Uses character distribution analysis to select appropriate chars/token ratios
for different text types (Chinese, English, code, mixed).

Python 3.8.10 compatible: uses type comments, Optional, no match/case.
"""

from __future__ import annotations

import re
from typing import Dict, Tuple


# ---------------------------------------------------------------------------
# Language-Specific Token Ratios
# ---------------------------------------------------------------------------

# Characters per token ratios for different text types.
# These are empirically derived from common tokenizer behavior:
# - Chinese: ~1.5-2.0 chars/token (each CJK character is typically 1-2 tokens)
# - English: ~3.5-4.5 chars/token (common words are 1 token)
# - Code: ~3.0-4.0 chars/token (identifiers, keywords, symbols)
# - Mixed: ~2.5-3.0 chars/token (blend of CJK and Latin)

_LANGUAGE_RATIOS = {
    "chinese": 1.8,  # CJK-heavy text
    "english": 4.0,  # Latin alphabet text
    "code": 3.5,  # Programming code
    "mixed": 2.8,  # Mixed CJK + Latin
}  # type: Dict[str, float]


# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------

# Regex patterns for language detection
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002ebef]")
_HIRAGANA_PATTERN = re.compile(r"[\u3040-\u309f]")
_KATAKANA_PATTERN = re.compile(r"[\u30a0-\u30ff]")
_KOREAN_PATTERN = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")

# Code indicators: common programming keywords and patterns
_CODE_KEYWORDS = re.compile(
    r"\b(def|class|function|const|let|var|import|from|return|if|else|for|while|"
    r"try|catch|async|await|yield|lambda|struct|enum|interface|type|impl|fn|pub|"
    r"use|mod|crate|self|super|this|new|delete|throw|throws|extends|implements|"
    r"package|namespace|using|include|define|typedef|template|typename|"
    r"public|private|protected|static|final|abstract|virtual|override|"
    r"switch|case|break|continue|default|do|goto|sizeof|typeof|"
    r"println|printf|scanf|cout|cin|console|print|input|"
    r"std::|std::string|std::vector|std::map|std::set|"
    r"__init__|__main__|__name__|__file__|__class__|__self__|"
    r"=>|->|::|\.\.\.|\?\?|&&|\|\||!=|===|!==|<=|>=|<<|>>)\b",
    re.IGNORECASE,
)

# Code structure indicators
_CODE_STRUCTURE = re.compile(r"[{}()\[\];,]")


def _detect_language(text):
    # type: (str) -> str
    """Detect the dominant language/type of the given text.

    Analyzes character distribution and patterns to classify text into:
    - 'chinese': CJK characters dominate (>30%)
    - 'japanese': Hiragana/Katakana present
    - 'korean': Hangul characters present
    - 'code': Programming keywords/structure detected
    - 'mixed': Significant CJK + Latin mix (10-30% CJK)
    - 'english': Primarily Latin alphabet

    Args:
        text: The text to analyze.

    Returns:
        Language type string matching _LANGUAGE_RATIOS keys.
    """
    if not text:
        return "english"

    total_chars = len(text)
    if total_chars == 0:
        return "english"

    # Count CJK characters
    cjk_count = len(_CJK_PATTERN.findall(text))
    cjk_ratio = cjk_count / total_chars

    # Count Japanese characters
    jp_count = len(_HIRAGANA_PATTERN.findall(text)) + len(_KATAKANA_PATTERN.findall(text))
    if jp_count > 10:
        return "chinese"  # Treat Japanese similarly for token estimation

    # Count Korean characters
    kr_count = len(_KOREAN_PATTERN.findall(text))
    if kr_count > 10:
        return "chinese"  # Treat Korean similarly for token estimation

    # Check for code indicators
    code_keyword_matches = len(_CODE_KEYWORDS.findall(text))
    code_structure_matches = len(_CODE_STRUCTURE.findall(text))

    # Code detection: significant keywords or structure patterns
    # At least 2 code keywords OR 5+ structure chars in a reasonable sample
    sample_size = min(total_chars, 500)
    code_density = (code_keyword_matches * 3 + code_structure_matches) / max(sample_size / 50, 1)

    if code_density > 3.0 or (code_keyword_matches >= 3 and code_structure_matches >= 5):
        return "code"

    # Language classification based on CJK ratio
    if cjk_ratio > 0.3:
        return "chinese"
    elif cjk_ratio > 0.1:
        return "mixed"
    else:
        return "english"


# ---------------------------------------------------------------------------
# FallbackTokenEstimator
# ---------------------------------------------------------------------------


class FallbackTokenEstimator(object):
    """Multi-language aware token estimator.

    Provides more accurate token estimation than simple len(text) // 4
    by detecting the language/type of text and using appropriate
    characters-per-token ratios.

    Usage:
        estimator = FallbackTokenEstimator()
        tokens = estimator.estimate("Hello world")        # ~2-3 tokens
        tokens = estimator.estimate("你好世界")            # ~4-5 tokens
        tokens = estimator.estimate("def foo(): pass")    # ~5-6 tokens
    """

    def __init__(self, default_ratio=None):
        # type: (Optional[float]) -> None
        """Initialize the estimator.

        Args:
            default_ratio: Default chars/token ratio for unknown languages.
                          Defaults to 4.0 (English-like).
        """
        self.default_ratio = default_ratio if default_ratio is not None else 4.0

    def estimate(self, text):
        # type: (str) -> int
        """Estimate token count for the given text.

        Detects the language/type of text and applies the appropriate
        characters-per-token ratio.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Estimated token count (minimum 1 for non-empty text).
        """
        if not text:
            return 0

        lang = _detect_language(text)
        ratio = _LANGUAGE_RATIOS.get(lang, self.default_ratio)

        # Calculate estimate, ensuring at least 1 token for non-empty text
        return max(1, int(len(text) / ratio))

    def estimate_with_language(self, text):
        # type: (str) -> Tuple[int, str]
        """Estimate token count and return detected language.

        Useful for debugging and logging.

        Args:
            text: The text to estimate tokens for.

        Returns:
            Tuple of (estimated_tokens, detected_language).
        """
        if not text:
            return (0, "english")

        lang = _detect_language(text)
        ratio = _LANGUAGE_RATIOS.get(lang, self.default_ratio)
        tokens = max(1, int(len(text) / ratio))

        return (tokens, lang)


# ---------------------------------------------------------------------------
# Module-Level Convenience Function
# ---------------------------------------------------------------------------

# Singleton instance for module-level access
_default_estimator = FallbackTokenEstimator()


def estimate_tokens(text):
    # type: (str) -> int
    """Estimate token count using language-aware fallback estimation.

    Convenience function that uses the default FallbackTokenEstimator.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    return _default_estimator.estimate(text)
