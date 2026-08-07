"""Download Qwen vocab file from HuggingFace."""
import os
import urllib.request

# Qwen vocab file URL (from Qwen/Qwen-7B-Chat on HuggingFace)
VOCAB_URL = "https://huggingface.co/Qwen/Qwen-7B-Chat/raw/main/qwen.tiktoken"

# Default save location
DEFAULT_SAVE_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "berserker", "qwen.tiktoken"
)


def download_qwen_vocab(save_path=None):
    # type: (str) -> str
    """Download Qwen vocab file from HuggingFace.

    Args:
        save_path: Path to save the vocab file. Defaults to ~/.cache/berserker/qwen.tiktoken.

    Returns:
        Path to the downloaded vocab file.
    """
    if save_path is None:
        save_path = DEFAULT_SAVE_PATH

    # Create directory if it doesn't exist
    save_dir = os.path.dirname(save_path)
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    # Check if file already exists
    if os.path.exists(save_path):
        print("Vocab file already exists at: {}".format(save_path))
        return save_path

    # Download the file
    print("Downloading Qwen vocab from: {}".format(VOCAB_URL))
    print("This may take a while (~2.5 MB)...")

    try:
        urllib.request.urlretrieve(VOCAB_URL, save_path)
        print("Downloaded to: {}".format(save_path))
        return save_path
    except Exception as e:
        print("Download failed: {}".format(e))
        raise


if __name__ == "__main__":
    import sys

    save_path = sys.argv[1] if len(sys.argv) > 1 else None
    download_qwen_vocab(save_path)
