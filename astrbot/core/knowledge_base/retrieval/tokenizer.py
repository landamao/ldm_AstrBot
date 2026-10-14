"""Tokenization helpers shared by sparse retrieval indexes."""

import re
from pathlib import Path
from re import Pattern

_TERM_PATTERN: Pattern[str] = re.compile(r"\w", re.UNICODE)


def load_stopwords(path: Path | str) -> set[str]:
    with Path(path).open(encoding="utf-8") as f:
        return {word.strip() for word in set(f.read().splitlines()) if word.strip()}


def tokenize_text(text: str, stopwords: set[str]) -> list[str]:
    # 延后到真正分词时才导入 jieba：它会在首次使用时构建词典，
    # 启动期加载纯属浪费（未建知识库的实例用不到）。
    import jieba

    tokens = []
    for token in jieba.cut(text or ""):
        token = token.strip()
        if not token or token in stopwords:
            continue
        if not _TERM_PATTERN.search(token):
            continue
        tokens.append(token)
    return tokens


def to_fts5_search_text(text: str, stopwords: set[str]) -> str:
    return " ".join(tokenize_text(text, stopwords))


def quote_fts5_token(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def build_fts5_or_query(tokens: list[str]) -> str:
    quoted_tokens = [quote_fts5_token(token) for token in tokens if token]
    return " OR ".join(quoted_tokens)
