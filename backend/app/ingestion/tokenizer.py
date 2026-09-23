"""Token counting for chunk sizing.

Chunk sizes must be measured with the *embedding model's own* tokenizer. Sizing
with a different tokenizer (tiktoken, or word counts) means a chunk the chunker
believes is 512 tokens can be 600 to the encoder, which silently truncates at
its 512-token limit -- the tail of the chunk is embedded as if it did not exist,
and no error is raised anywhere.

Loading the tokenizer pulls only the vocabulary files, not the model weights,
so this stays cheap. `SimpleTokenizer` exists for tests and for offline runs.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.core.logging import get_logger

log = get_logger(__name__)

# bge-small-en-v1.5 accepts 512 positions including [CLS] and [SEP].
MODEL_MAX_TOKENS = 512


class Tokenizer(Protocol):
    def encode(self, text: str) -> list[int]: ...
    def decode(self, tokens: list[int]) -> str: ...
    def count(self, text: str) -> int: ...


class SimpleTokenizer:
    """Whitespace tokenizer: deterministic, offline, no dependencies.

    Roughly 0.75 words per real subword token on English prose, so it
    *under*-counts. Only appropriate for tests.
    """

    _SPLIT = re.compile(r"\S+")

    def __init__(self) -> None:
        self._vocab: list[str] = []
        self._index: dict[str, int] = {}

    def encode(self, text: str) -> list[int]:
        tokens = []
        for word in self._SPLIT.findall(text):
            if word not in self._index:
                self._index[word] = len(self._vocab)
                self._vocab.append(word)
            tokens.append(self._index[word])
        return tokens

    def decode(self, tokens: list[int]) -> str:
        return " ".join(self._vocab[t] for t in tokens if 0 <= t < len(self._vocab))

    def count(self, text: str) -> int:
        return len(self._SPLIT.findall(text))


class HFTokenizer:
    """The embedding model's tokenizer, loaded lazily on first use."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._tokenizer: object | None = None

    def _load(self) -> object:
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            log.info("tokenizer.loading", model=self.model_name)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        return self._tokenizer

    def encode(self, text: str) -> list[int]:
        tok = self._load()
        # add_special_tokens=False: [CLS]/[SEP] are added at encode time by the
        # model, and counting them here would shrink every chunk by two tokens.
        return list(tok.encode(text, add_special_tokens=False))  # type: ignore[attr-defined]

    def decode(self, tokens: list[int]) -> str:
        tok = self._load()
        return str(tok.decode(tokens, skip_special_tokens=True))  # type: ignore[attr-defined]

    def count(self, text: str) -> int:
        return len(self.encode(text))


_cache: dict[str, Tokenizer] = {}


def get_tokenizer(model_name: str, *, offline: bool = False) -> Tokenizer:
    """Tokenizer for `model_name`, cached per process."""
    if offline:
        return SimpleTokenizer()
    if model_name not in _cache:
        _cache[model_name] = HFTokenizer(model_name)
    return _cache[model_name]
