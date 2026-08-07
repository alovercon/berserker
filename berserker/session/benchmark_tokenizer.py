"""
Performance benchmark for Qwen tokenizer loading and token counting.

Measures:
1. Vocab file loading time
2. Tokenizer initialization time
3. Token counting time for various text sizes
"""

import time
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from berserker.session.qwen_tokenizer import QwenTokenizer, _find_vocab_path
from berserker.session.token_counter import TokenCounter

def benchmark_vocab_loading():
    """Benchmark vocab file loading time."""
    print("=" * 60)
    print("BENCHMARK: Vocab Loading")
    print("=" * 60)
    
    vocab_path = _find_vocab_path()
    if vocab_path:
        print(f"Vocab file found: {vocab_path}")
        file_size = os.path.getsize(vocab_path)
        print(f"File size: {file_size / 1024:.1f} KB")
    else:
        print("Vocab file not found, using embedded minimal vocab")
        vocab_path = None
    
    # Measure loading time
    start = time.perf_counter()
    tokenizer = QwenTokenizer(vocab_path=vocab_path)
    elapsed = time.perf_counter() - start
    
    print(f"Vocab loading time: {elapsed * 1000:.2f} ms")
    print(f"Mergeable ranks loaded: {len(tokenizer._mergeable_ranks)}")
    print(f"Special tokens: {len(tokenizer._special_tokens)}")
    print()
    
    return tokenizer, elapsed

def benchmark_token_counting(tokenizer):
    """Benchmark token counting for various text sizes."""
    print("=" * 60)
    print("BENCHMARK: Token Counting")
    print("=" * 60)
    
    # Test texts of different sizes
    test_texts = [
        ("Short text (10 chars)", "Hello world!"),
        ("Medium text (100 chars)", "The quick brown fox jumps over the lazy dog. " * 3),
        ("Long text (1K chars)", "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 20),
        ("Very long text (10K chars)", "Python is a programming language that lets you work quickly " * 200),
        ("Huge text (100K chars)", "Code is like humor. When you have to explain it, it's bad. " * 2000),
    ]
    
    for name, text in test_texts:
        # Measure token counting time
        start = time.perf_counter()
        token_count = tokenizer.count_tokens(text)
        elapsed = time.perf_counter() - start
        
        print(f"{name}:")
        print(f"  Text length: {len(text)} chars")
        print(f"  Token count: {token_count}")
        print(f"  Time: {elapsed * 1000:.2f} ms")
        print(f"  Speed: {len(text) / elapsed / 1000:.0f} chars/sec" if elapsed > 0 else "  Speed: N/A")
        print()

def benchmark_token_counter_class():
    """Benchmark TokenCounter class with Qwen model detection."""
    print("=" * 60)
    print("BENCHMARK: TokenCounter Class (Qwen model)")
    print("=" * 60)
    
    counter = TokenCounter()
    test_text = "The quick brown fox jumps over the lazy dog. " * 100
    
    # Measure token counting with model detection
    start = time.perf_counter()
    token_count = counter.count_text(test_text, model="qwen-turbo")
    elapsed = time.perf_counter() - start
    
    print(f"Text length: {len(test_text)} chars")
    print(f"Token count (qwen-turbo): {token_count}")
    print(f"Time: {elapsed * 1000:.2f} ms")
    print()

def benchmark_first_vs_subsequent_calls():
    """Benchmark first call (with tokenizer init) vs subsequent calls."""
    print("=" * 60)
    print("BENCHMARK: First vs Subsequent Calls")
    print("=" * 60)
    
    # Reset global tokenizer to simulate fresh start
    import berserker.session.qwen_tokenizer as qt
    qt._qwen_tokenizer = None
    
    test_text = "The quick brown fox jumps over the lazy dog. " * 50
    
    # First call (includes tokenizer initialization)
    start = time.perf_counter()
    counter1 = TokenCounter()
    count1 = counter1.count_text(test_text, model="qwen-turbo")
    first_elapsed = time.perf_counter() - start
    
    print(f"First call (with tokenizer init):")
    print(f"  Token count: {count1}")
    print(f"  Time: {first_elapsed * 1000:.2f} ms")
    print()
    
    # Subsequent calls (tokenizer already initialized)
    for i in range(3):
        start = time.perf_counter()
        counter2 = TokenCounter()
        count2 = counter2.count_text(test_text, model="qwen-turbo")
        elapsed = time.perf_counter() - start
        
        print(f"Subsequent call #{i+1}:")
        print(f"  Token count: {count2}")
        print(f"  Time: {elapsed * 1000:.2f} ms")
        print()

if __name__ == "__main__":
    print("Qwen Tokenizer Performance Benchmark")
    print("=" * 60)
    print()
    
    # Run benchmarks
    tokenizer, load_time = benchmark_vocab_loading()
    benchmark_token_counting(tokenizer)
    benchmark_token_counter_class()
    benchmark_first_vs_subsequent_calls()
    
    print("=" * 60)
    print("BENCHMARK COMPLETE")
    print("=" * 60)
