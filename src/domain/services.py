"""Чистая доменная логика модерации: поиск ключевых слов в тексте.

Шаблоны ключевых слов:
    «слово»   — только целое слово (никаких срабатываний «бля» на «рубля»);
    «слово*»  — слово, начинающееся с «слово»; «*слово*» — содержащее его;
    «два слова» — фраза, слова через любые пробельные символы;
    «!шаблон» — исключение: подходящие под него слова не считаются нарушением.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_ZERO_WIDTH = dict.fromkeys(map(ord, "­​‌‍⁠﻿"))
# Латиница и цифры, которыми маскируют кириллицу: «xуй», «6ля», «пuзд…».
_CONFUSABLES = str.maketrans(
    {
        "a": "а",
        "c": "с",
        "e": "е",
        "h": "н",
        "k": "к",
        "m": "м",
        "n": "п",
        "o": "о",
        "p": "р",
        "t": "т",
        "x": "х",
        "y": "у",
        "0": "о",
        "3": "з",
        "4": "ч",
        "6": "б",
    }
)
_CONFUSABLE_CHARS = re.compile("[" + "".join(map(chr, _CONFUSABLES)) + "]")
_CYRILLIC = re.compile(r"[а-яё]")
_WORD = re.compile(r"\w+")
_REPEATS = re.compile(r"(\w)\1{2,}")


def normalize_text(text: str) -> str:
    """Приводит текст к виду, в котором ищутся ключевые слова.

    Регистр, «ё», невидимые символы, латинские двойники внутри кириллических слов
    и растянутые буквы («бляяять») не должны помогать обходить фильтр.
    """
    text = text.casefold().replace("ё", "е").translate(_ZERO_WIDTH)
    text = _WORD.sub(_fix_mixed_script, text)
    return _REPEATS.sub(r"\1", text)


def _fix_mixed_script(match: re.Match[str]) -> str:
    word = match.group(0)
    # Чисто латинские слова и числа не трогаем — только «смешанные» кириллические.
    if _CYRILLIC.search(word) and _CONFUSABLE_CHARS.search(word):
        return word.translate(_CONFUSABLES)
    return word


def compile_keyword(pattern: str) -> str:
    """Превращает шаблон вида «бля*» в тело регулярного выражения.

    Границы слова (`(?<!\\w)…(?!\\w)`) добавляет вызывающий код — так их можно
    вынести за скобки общей альтернативы и не проверять для каждого шаблона.
    """
    words = normalize_text(pattern).split()
    if not words or any(not _WORD.search(word.replace("*", "")) for word in words):
        raise ValueError(f"Некорректный шаблон ключевого слова: {pattern!r}")
    parts = [r"\w*".join(re.escape(chunk) for chunk in word.split("*")) for word in words]
    return r"\s+".join(parts)


def _whole_words(body: str) -> str:
    return rf"(?<!\w)(?:{body})(?!\w)"


@dataclass(frozen=True, slots=True)
class KeywordMatch:
    keyword: str  # шаблон из конфигурации, например «бля*»
    fragment: str  # что именно нашлось (в нормализованном виде)


class ModerationService:
    def __init__(self, keywords: Iterable[str]) -> None:
        blocked: list[str] = []
        allowed: list[str] = []
        for raw in keywords:
            rule = raw.strip()
            if not rule or rule.startswith("#"):
                continue
            if rule.startswith("!"):
                allowed.append(rule[1:].strip())
            else:
                blocked.append(rule)

        self._keywords = tuple(dict.fromkeys(blocked))
        # Один общий regex: finditer сразу даёт самое раннее совпадение в тексте,
        # а имя группы (k0, k1, …) говорит, какой шаблон сработал.
        alternatives = "|".join(
            f"(?P<k{index}>{compile_keyword(keyword)})"
            for index, keyword in enumerate(self._keywords)
        )
        self._matcher = re.compile(_whole_words(alternatives)) if self._keywords else None
        self._exceptions = tuple(
            re.compile(_whole_words(compile_keyword(rule))) for rule in allowed
        )

    @property
    def keywords(self) -> tuple[str, ...]:
        return self._keywords

    def find_violation(self, text: str) -> KeywordMatch | None:
        """Первое (по позиции в тексте) нарушение или None."""
        if self._matcher is None or not text:
            return None
        for match in self._matcher.finditer(normalize_text(text)):
            fragment = match.group(0)
            if any(exception.fullmatch(fragment) for exception in self._exceptions):
                continue
            assert match.lastgroup is not None
            return KeywordMatch(keyword=self._keywords[int(match.lastgroup[1:])], fragment=fragment)
        return None
