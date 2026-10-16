"""Load the main tokenizer, including Strata's exported Qwen byte-BPE descriptor."""

import json
from pathlib import Path


def load_tokenizer(path):
    from tokenizers import Regex, Tokenizer, models, pre_tokenizers
    path = Path(path)
    descriptor = json.loads(path.read_text())
    if not isinstance(descriptor.get("model"), str):
        return Tokenizer.from_file(str(path))
    if descriptor.get("model") != "gpt2" or not descriptor.get("pre_pattern"):
        raise ValueError("Unsupported main tokenizer descriptor; no estimated-token fallback")
    root = path.parent
    tokenizer = Tokenizer(models.BPE.from_file(str(root / "vocab.json"), str(root / "merges.txt")))
    tokenizer.pre_tokenizer = pre_tokenizers.Sequence([
        pre_tokenizers.Split(Regex(descriptor["pre_pattern"]), behavior="isolated"),
        pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)])
    vocab = json.loads((root / "vocab.json").read_text())
    token_types = json.loads((root / "token_type.json").read_text())
    specials = [token for token, index in vocab.items() if token_types[index] == 3]
    tokenizer.add_special_tokens(specials)
    return tokenizer
