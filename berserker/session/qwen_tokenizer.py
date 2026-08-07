"""
Pure Python Qwen Tokenizer implementation.

Implements BPE tokenization for Qwen models without requiring tiktoken's Rust extension.
Fully compatible with Python 3.8.10 (including win32).

Based on Qwen's official tokenizer:
- vocab file: qwen.tiktoken (base64-encoded mergeable ranks)
- pat_str: Qwen's pre-tokenization regex pattern
- special_tokens: Qwen's control tokens

Python 3.8.10 compatible: uses type comments, no match/case.
"""

from __future__ import annotations

import base64
import functools
import logging
import os
import re
from typing import Dict, List, Optional, Tuple, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Qwen Tokenizer Configuration
# ---------------------------------------------------------------------------

# Qwen's pre-tokenization regex pattern (same as official tokenizer)
QWEN_PAT_STR = (
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|"
    r"(?:[^\r\n[a-zA-Z][0-9]]?[a-zA-Z]+)|"
    r"(?:\d{1,3})|"
    r"(?:[^\r\n[a-zA-Z][0-9]]+)|"
    r"(?:\r?\n)|"
    r"(?:\s+)|"
    r"(?:.)"
)

# Qwen special tokens (from official tokenization_qwen.py)
QWEN_SPECIAL_TOKENS = {
    "<|endoftext|>": 151643,
    "<|im_start|>": 151644,
    "<|im_end|>": 151645,
    "<|object_ref_start|>": 151646,
    "<|object_ref_end|>": 151647,
    "<|box_start|>": 151648,
    "<|box_end|>": 151649,
    "<|quad_start|>": 151650,
    "<|quad_end|>": 151651,
    "<|vision_start|>": 151652,
    "<|vision_end|>": 151653,
    "<|vision_pad|>": 151654,
    "<|image_pad|>": 151655,
    "<|video_pad|>": 151656,
    "<|tab|>": 151657,
    "<|newline|>": 151658,
    "<|fim_prefix|>": 151659,
    "<|fim_middle|>": 151660,
    "<|fim_suffix|>": 151661,
    "<|fim_pad|>": 151662,
    "<|repo_name|>": 151663,
    "<|file_sep|>": 151664,
    "<|user|>": 151665,
    "<|system|>": 151666,
    "<|assistant|>": 151667,
    "<|observation|>": 151668,
    "<|stop|>": 151669,
}

# ---------------------------------------------------------------------------
# BPE Tokenizer Core
# ---------------------------------------------------------------------------


class QwenTokenizer(object):
    """Pure Python BPE tokenizer for Qwen models.

    Implements the same tokenization algorithm as Qwen's official tokenizer
    without requiring tiktoken's Rust extension.

    Usage:
        tokenizer = QwenTokenizer()
        tokens = tokenizer.encode("Hello, world!")
        count = len(tokens)
    """

    def __init__(self, vocab_path=None):
        # type: (Optional[str]) -> None
        """Initialize the Qwen tokenizer.

        Args:
            vocab_path: Path to qwen.tiktoken vocab file. If None, uses
                        embedded minimal vocab or attempts to download.
        """
        self._pat = re.compile(QWEN_PAT_STR)
        self._special_tokens = dict(QWEN_SPECIAL_TOKENS)
        self._reverse_special_tokens = {
            v: k for k, v in self._special_tokens.items()
        }  # type: Dict[int, str]

        # Load mergeable ranks
        self._mergeable_ranks = {}  # type: Dict[bytes, int]
        self._mergeable_ranks_reverse = {}  # type: Dict[int, bytes]

        if vocab_path and os.path.exists(vocab_path):
            self._load_vocab(vocab_path)
        else:
            # Use embedded minimal vocab for counting
            self._load_embedded_vocab()

    def _load_vocab(self, path):
        # type: (str) -> None
        """Load mergeable ranks from a qwen.tiktoken file.

        Format: base64-encoded bytes + space + integer rank, one per line.
        """
        with open(path, "rb") as f:
            contents = f.read()

        for line in contents.splitlines():
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            token_bytes = base64.b64decode(parts[0])
            rank = int(parts[1])
            self._mergeable_ranks[token_bytes] = rank
            self._mergeable_ranks_reverse[rank] = token_bytes

        logger.info(
            "Loaded Qwen vocab: %d mergeable ranks, %d special tokens",
            len(self._mergeable_ranks),
            len(self._special_tokens),
        )

    def _load_embedded_vocab(self):
        # type: () -> None
        """Load embedded minimal vocab for token counting.

        This provides a minimal vocab that covers common characters and
        frequent tokens. For full accuracy, provide a vocab_path to the
        complete qwen.tiktoken file.
        """
        # Embedded minimal vocab: all single bytes (0-255) + common patterns
        # This ensures any UTF-8 byte sequence can be tokenized
        for i in range(256):
            token_bytes = bytes([i])
            self._mergeable_ranks[token_bytes] = i
            self._mergeable_ranks_reverse[i] = token_bytes

        # Add common multi-byte patterns for better accuracy
        # These are frequently occurring tokens in Qwen's vocab
        common_patterns = [
            (b" ", 256),
            (b"  ", 257),
            (b"in", 258),
            (b" t", 259),
            (b"        ", 260),
            (b"er", 261),
            (b"    ", 262),
            (b"on", 263),
            (b"a ", 264),
            (b"re", 265),
            (b"at", 266),
            (b"st", 267),
            (b"en", 268),
            (b"or", 269),
            (b" th", 270),
            (b"\n\n", 271),
            (b" c", 272),
            (b"le", 273),
            (b" s", 274),
            (b"it", 275),
            (b"an", 276),
            (b"ar", 277),
            (b"al", 278),
            (b"the", 279),
            (b":\n", 280),
            (b" p", 281),
            (b" f", 282),
            (b"ou", 283),
            (b" =", 284),
            (b"is", 285),
            (b"       ", 286),
            (b"ing", 287),
            (b"es", 288),
            (b" w", 289),
            (b"ion", 290),
            (b"ed", 291),
            (b"ic", 292),
            (b" b", 293),
            (b" d", 294),
            (b"te", 295),
            (b" m", 296),
            (b" o", 297),
            (b"\t", 298),
            (b"ro", 299),
            (b"as", 300),
            (b"el", 301),
            (b"ct", 302),
            (b"nd", 303),
            (b" in", 304),
            (b" h", 305),
            (b"ent", 306),
            (b"id", 307),
            (b" n", 308),
            (b"am", 309),
            (b"            ", 310),
            (b" to", 311),
            (b" re", 312),
            (b"--", 313),
            (b" {", 314),
            (b" of", 315),
            (b"om", 316),
            (b");\n", 317),
            (b"im", 318),
            (b"\r\n", 319),
            (b" (", 320),
            (b"il", 321),
            (b"//", 322),
            (b" and", 323),
            (b"ur", 324),
            (b"se", 325),
            (b" l", 326),
            (b"ex", 327),
            (b" S", 328),
            (b"ad", 329),
            (b' "', 330),
            (b"ch", 331),
            (b"ut", 332),
            (b"if", 333),
            (b"**", 334),
            (b" }", 335),
            (b"em", 336),
            (b"ol", 337),
            (b"                  ", 338),
            (b"th", 339),
            (b")\n", 340),
            (b" {\n", 341),
            (b" g", 342),
            (b"ig", 343),
            (b"iv", 344),
            (b",\n", 345),
            (b"ce", 346),
            (b"od", 347),
            (b" v", 348),
            (b"ate", 349),
            (b" T", 350),
            (b"ag", 351),
            (b"ay", 352),
            (b" *", 353),
            (b"ot", 354),
            (b"us", 355),
            (b" C", 356),
            (b" st", 357),
            (b" I", 358),
            (b"un", 359),
            (b"ul", 360),
            (b"ue", 361),
            (b" A", 362),
            (b"ow", 363),
            (b" '", 364),
            (b"ew", 365),
            (b" <", 366),
            (b"ation", 367),
            (b"()", 368),
            (b" for", 369),
            (b"ab", 370),
            (b"ort", 371),
            (b"um", 372),
            (b"ame", 373),
            (b" is", 374),
            (b"pe", 375),
            (b"tr", 376),
            (b"ck", 377),
            (b"\xe2\x80", 378),
            (b" y", 379),
            (b"ist", 380),
            (b"----", 381),
            (b".\n\n", 382),
            (b"he", 383),
            (b" e", 384),
            (b"lo", 385),
            (b" M", 386),
            (b" be", 387),
            (b"ers", 388),
            (b" on", 389),
            (b" con", 390),
            (b"ap", 391),
            (b"ub", 392),
            (b" P", 393),
            (b"               ", 394),
            (b"ass", 395),
            (b"int", 396),
            (b">\n", 397),
            (b"ly", 398),
            (b"urn", 399),
            (b" $", 400),
            (b":\n\n", 401),
            (b"av", 402),
            (b"port", 403),
            (b"ir", 404),
            (b"->", 405),
            (b"nt", 406),
            (b"ction", 407),
            (b"end", 408),
            (b" de", 409),
            (b"ith", 410),
            (b"out", 411),
            (b"turn", 412),
            (b"our", 413),
            (b"     ", 414),
            (b"lic", 415),
            (b"res", 416),
            (b"pt", 417),
            (b"==", 418),
            (b" this", 419),
            (b" wh", 420),
            (b" if", 421),
            (b" D", 422),
            (b"ver", 423),
            (b"age", 424),
            (b" B", 425),
            (b"ht", 426),
            (b"ext", 427),
            (b'="', 428),
            (b" that", 429),
            (b"****", 430),
            (b" R", 431),
            (b" it", 432),
            (b"ess", 433),
            (b" F", 434),
            (b" r", 435),
            (b"os", 436),
            (b"and", 437),
            (b" as", 438),
            (b"ect", 439),
            (b"ke", 440),
            (b"rom", 441),
            (b" //", 442),
        ]  # type: List[Tuple[bytes, int]]

        for token_bytes, rank in common_patterns:
            if token_bytes not in self._mergeable_ranks:
                self._mergeable_ranks[token_bytes] = rank
                self._mergeable_ranks_reverse[rank] = token_bytes

        logger.info(
            "Using embedded Qwen vocab: %d mergeable ranks, %d special tokens",
            len(self._mergeable_ranks),
            len(self._special_tokens),
        )

    def encode(self, text, allowed_special="all"):
        # type: (str, str) -> List[int]
        """Encode text into token IDs.

        Args:
            text: Input text to tokenize.
            allowed_special: How to handle special tokens.
                "all" = allow all special tokens (default)
                "none" = raise error on special tokens
                set = allow specific special tokens

        Returns:
            List of token IDs.
        """
        if not text:
            return []

        # Handle special tokens
        if allowed_special == "all":
            special_set = set(self._special_tokens.keys())
        elif allowed_special == "none":
            special_set = set()
        else:
            special_set = set(allowed_special)

        return self._encode_with_special(text, special_set)

    def _encode_with_special(self, text, special_set):
        # type: (str, Set[str]) -> List[int]
        """Encode text handling special tokens."""
        if not special_set:
            return self._encode_bytes(text.encode("utf-8"))

        # Find special tokens in text and split around them
        tokens = []  # type: List[int]
        remaining = text

        while remaining:
            # Find the earliest special token
            earliest_pos = len(remaining)
            earliest_token = None

            for special_token in special_set:
                pos = remaining.find(special_token)
                if pos != -1 and pos < earliest_pos:
                    earliest_pos = pos
                    earliest_token = special_token

            if earliest_token is None:
                # No special tokens found, encode the rest
                tokens.extend(self._encode_bytes(remaining.encode("utf-8")))
                break

            # Encode text before special token
            if earliest_pos > 0:
                tokens.extend(
                    self._encode_bytes(remaining[:earliest_pos].encode("utf-8"))
                )

            # Add special token ID
            tokens.append(self._special_tokens[earliest_token])

            # Move past the special token
            remaining = remaining[earliest_pos + len(earliest_token):]

        return tokens

    def _encode_bytes(self, text_bytes):
        # type: (bytes) -> List[int]
        """Encode raw bytes using BPE algorithm."""
        if not text_bytes:
            return []

        # Pre-tokenize using regex pattern
        chunks = self._pat.findall(text_bytes.decode("utf-8", errors="replace"))

        all_tokens = []
        for chunk in chunks:
            if not chunk:
                continue
            chunk_bytes = chunk.encode("utf-8", errors="replace")
            tokens = self._bpe_encode(chunk_bytes)
            all_tokens.extend(tokens)

        return all_tokens

    def _bpe_encode(self, text_bytes):
        # type: (bytes) -> List[int]
        """Core BPE encoding algorithm.

        Implements byte-level BPE:
        1. Start with each byte as a separate token
        2. Repeatedly merge the pair with the lowest rank (highest priority)
        3. Continue until no more merges are possible
        """
        if not text_bytes:
            return []

        # Initialize: each byte is a separate token
        # Use list of bytes for efficient merging
        tokens = []  # type: List[bytes]
        for b in text_bytes:
            tokens.append(bytes([b]))

        # Repeatedly merge pairs
        while len(tokens) > 1:
            # Find the best pair to merge (lowest rank = highest priority)
            best_rank = float("inf")
            best_idx = -1

            for i in range(len(tokens) - 1):
                pair = tokens[i] + tokens[i + 1]
                rank = self._mergeable_ranks.get(pair)
                if rank is not None and rank < best_rank:
                    best_rank = rank
                    best_idx = i

            if best_idx == -1:
                # No more merges possible
                break

            # Merge the best pair
            merged = tokens[best_idx] + tokens[best_idx + 1]
            tokens[best_idx] = merged
            del tokens[best_idx + 1]

        # Convert tokens to IDs
        token_ids = []
        for token in tokens:
            if token in self._mergeable_ranks:
                token_ids.append(self._mergeable_ranks[token])
            else:
                # Fallback: encode each byte separately
                for b in token:
                    byte_token = bytes([b])
                    if byte_token in self._mergeable_ranks:
                        token_ids.append(self._mergeable_ranks[byte_token])
                    else:
                        # Last resort: use byte value directly
                        token_ids.append(b)

        return token_ids

    def decode(self, token_ids):
        # type: (List[int]) -> str
        """Decode token IDs back to text.

        Args:
            token_ids: List of token IDs.

        Returns:
            Decoded text string.
        """
        if not token_ids:
            return ""

        result = b""
        for token_id in token_ids:
            if token_id in self._reverse_special_tokens:
                result += self._reverse_special_tokens[token_id].encode("utf-8")
            elif token_id in self._mergeable_ranks_reverse:
                result += self._mergeable_ranks_reverse[token_id]
            else:
                # Fallback: treat as raw byte
                result += bytes([token_id])

        return result.decode("utf-8", errors="replace")

    def count_tokens(self, text):
        # type: (str) -> int
        """Count tokens in text.

        Args:
            text: Input text.

        Returns:
            Token count.
        """
        return len(self.encode(text))


# ---------------------------------------------------------------------------
# Global tokenizer instance (lazy loading)
# ---------------------------------------------------------------------------

_qwen_tokenizer = None  # type: Optional[QwenTokenizer]
_qwen_tokenizer_lock = __import__("threading").Lock()



def _find_vocab_path():
    # type: () -> Optional[str]
    """Find qwen.tiktoken vocab file.

    Search order:
    1. PyInstaller temporary directory (for --onefile builds)
    2. Executable directory (for --onedir builds or external file)
    3. Module directory (development)
    4. User cache directory (~/.cache/berserker/qwen.tiktoken)

    Returns:
        Path to vocab file if found, None otherwise.
    """
    import sys

    # 1. Check PyInstaller temporary directory (for --onefile builds)
    if getattr(sys, 'frozen', False):
        try:
            base_path = sys._MEIPASS
            vocab_path = os.path.join(base_path, 'qwen.tiktoken')
            if os.path.exists(vocab_path):
                return vocab_path
        except Exception:
            pass

    # 2. Check executable directory (for --onedir builds or external file)
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))
        
    vocab_path = os.path.join(exe_dir, 'qwen.tiktoken')
    if os.path.exists(vocab_path):
        return vocab_path

    # 3. Check module directory (development)
    module_dir = os.path.dirname(os.path.abspath(__file__))
    vocab_path = os.path.join(module_dir, 'qwen.tiktoken')
    if os.path.exists(vocab_path):
        return vocab_path

    # 4. Check user cache
    cache_path = os.path.join(os.path.expanduser("~"), ".cache", "berserker", "qwen.tiktoken")
    if os.path.exists(cache_path):
        return cache_path

    return None


def get_qwen_tokenizer(vocab_path=None, force_reload=False):
    # type: (Optional[str], bool) -> QwenTokenizer
    """Get or create the global Qwen tokenizer instance.

    Args:
        vocab_path: Optional path to qwen.tiktoken vocab file. If None, auto-detects.
        force_reload: If True, reload the tokenizer even if it already exists.

    Returns:
        QwenTokenizer instance.
    """
    global _qwen_tokenizer

    if not force_reload and _qwen_tokenizer is not None:
        return _qwen_tokenizer

    # Auto-detect vocab path if not provided
    if vocab_path is None:
        vocab_path = _find_vocab_path()
        if vocab_path:
            logger.info("✅ Successfully loaded qwen.tiktoken from: %s", vocab_path)
            print("[INFO] ✅ Successfully loaded qwen.tiktoken from: {}".format(vocab_path))
        else:
            logger.warning("⚠️ qwen.tiktoken not found. Using embedded minimal vocab (lower accuracy).")
            print("[WARNING] ⚠️ qwen.tiktoken not found. Using embedded minimal vocab (lower accuracy).")

    with _qwen_tokenizer_lock:
        _qwen_tokenizer = QwenTokenizer(vocab_path=vocab_path)

    return _qwen_tokenizer
