from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

WORD_PATTERN = re.compile(r"[a-záéíóúüñ]+", re.IGNORECASE)
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

POSITIVE_WEIGHTS = {
    "amar": 2.0,
    "amo": 2.0,
    "amor": 2.0,
    "amable": 1.5,
    "bien": 1.0,
    "bonita": 1.5,
    "bonito": 1.5,
    "buena": 1.5,
    "buenas": 1.5,
    "bueno": 1.5,
    "buenos": 1.5,
    "celebrar": 1.5,
    "contento": 2.0,
    "divertido": 1.5,
    "encanta": 2.0,
    "encantado": 2.0,
    "encantar": 2.0,
    "excelente": 3.0,
    "felicidad": 2.5,
    "felicidades": 2.5,
    "feliz": 2.0,
    "felices": 2.0,
    "genial": 2.5,
    "gracias": 1.5,
    "gusta": 1.5,
    "gusto": 1.5,
    "gustar": 1.5,
    "increible": 3.0,
    "jaja": 1.0,
    "jajaja": 1.0,
    "jeje": 1.0,
    "linda": 1.5,
    "lindo": 1.5,
    "maravilloso": 3.0,
    "mejor": 1.5,
    "perfecto": 2.5,
    "querer": 1.5,
    "querida": 1.5,
    "querido": 1.5,
    "quiero": 1.5,
    "sonreir": 1.5,
    "tranquilo": 1.0,
}

NEGATIVE_WEIGHTS = {
    "asco": 2.5,
    "ansiedad": 2.0,
    "asqueroso": 3.0,
    "decepcionar": 2.5,
    "dificil": 1.0,
    "duele": 2.0,
    "dolor": 2.0,
    "enojada": 2.0,
    "enojado": 2.0,
    "error": 1.0,
    "errores": 1.0,
    "fatal": 3.0,
    "feo": 1.5,
    "fracaso": 2.5,
    "horrible": 3.0,
    "ira": 2.0,
    "lastimar": 2.0,
    "mal": 1.5,
    "mala": 1.5,
    "malas": 1.5,
    "malo": 1.5,
    "malos": 1.5,
    "miedo": 2.0,
    "molesta": 1.5,
    "molesto": 1.5,
    "odia": 3.0,
    "odias": 3.0,
    "odiar": 3.0,
    "odio": 3.0,
    "peor": 2.0,
    "problema": 1.0,
    "problemas": 1.0,
    "preocupa": 1.5,
    "preocupado": 1.5,
    "romper": 1.5,
    "terrible": 3.0,
    "triste": 2.0,
    "tristes": 2.0,
}

NEGATIONS = {"jamas", "ni", "no", "nunca", "sin", "tampoco"}
INTENSIFIERS = {"demasiado": 1.5, "muy": 1.5, "super": 1.5, "tan": 1.25}
POSITIVE_EMOJIS = ("😀", "😃", "😄", "😁", "😊", "😍", "🥰", "🙂", "❤", "👍", "🎉")
NEGATIVE_EMOJIS = ("😞", "😔", "😢", "😭", "😡", "😠", "🤬", "😟", "💔", "👎")


@dataclass(frozen=True, slots=True)
class SentimentWeights:
    positive: float = 0.0
    negative: float = 0.0
    indicators: int = 0

    @property
    def total(self) -> float:
        return self.positive + self.negative

    @property
    def net(self) -> float:
        return self.positive - self.negative

    @property
    def balance(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.net / self.total) * 100


def sentiment_weights(text: str) -> SentimentWeights:
    text_without_urls = URL_PATTERN.sub(" ", text)
    tokens = [
        _normalize_word(match.group(0))
        for match in WORD_PATTERN.finditer(text_without_urls)
    ]
    positive = 0.0
    negative = 0.0
    indicators = 0

    for index, token in enumerate(tokens):
        base_weight = POSITIVE_WEIGHTS.get(token)
        direction = 1
        if base_weight is None:
            base_weight = NEGATIVE_WEIGHTS.get(token)
            direction = -1
        if base_weight is None:
            continue

        negated = _is_negated(tokens, index)
        multiplier = 1.0
        if index > 0:
            multiplier = INTENSIFIERS.get(tokens[index - 1], 1.0)
        weight = base_weight * multiplier
        effective_direction = -direction if negated else direction
        if effective_direction > 0:
            positive += weight
        else:
            negative += weight
        indicators += 1

    emoji_text = text_without_urls.replace("\ufe0f", "")
    for emoji in POSITIVE_EMOJIS:
        count = emoji_text.count(emoji)
        positive += count * 2.0
        indicators += count
    for emoji in NEGATIVE_EMOJIS:
        count = emoji_text.count(emoji)
        negative += count * 2.0
        indicators += count

    return SentimentWeights(
        positive=positive,
        negative=negative,
        indicators=indicators,
    )


def _normalize_word(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _is_negated(tokens: list[str], index: int) -> bool:
    for previous in reversed(tokens[max(0, index - 3) : index]):
        if previous in POSITIVE_WEIGHTS or previous in NEGATIVE_WEIGHTS:
            return False
        if previous in NEGATIONS:
            return True
    return False
