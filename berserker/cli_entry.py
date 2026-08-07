"""berserker - The open source AI coding agent (Python port)."""

from __future__ import annotations

from berserker.cli.cli import main


def _verify_qwen_vocab():
    # type: () -> None
    """Verify qwen.tiktoken vocab file is available at startup."""
    try:
        from berserker.session.qwen_tokenizer import _find_vocab_path

        vocab_path = _find_vocab_path()
        if vocab_path:
            print("[INFO] qwen.tiktoken found: {}".format(vocab_path))
        else:
            print("[WARNING] qwen.tiktoken not found. Using embedded minimal vocab.")
    except Exception:
        pass


_verify_qwen_vocab()
main()
