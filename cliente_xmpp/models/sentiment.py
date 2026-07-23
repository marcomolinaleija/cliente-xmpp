from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

WORD_PATTERN = re.compile(r"[a-záéíóúüñ]+", re.IGNORECASE)
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

POSITIVE_WEIGHTS = {
    "abracito": 2.0,
    "abrazo": 2.0,
    "abrazos": 2.0,
    "abrazote": 2.0,
    "adorar": 2.0,
    "adoro": 2.0,
    "alegre": 2.0,
    "alegria": 2.0,
    "amable": 1.5,
    "amar": 2.0,
    "amiguita": 1.5,
    "amiguito": 1.5,
    "amo": 2.0,
    "amor": 2.0,
    "amorcito": 2.0,
    "amorsito": 2.0,
    "apapacho": 2.0,
    "apapachos": 2.0,
    "bb": 1.5,
    "bebe": 1.5,
    "besito": 2.0,
    "besitos": 2.0,
    "beso": 2.0,
    "besos": 2.0,
    "bien": 1.0,
    "bonita": 1.5,
    "bonito": 1.5,
    "buena": 1.5,
    "buenas": 1.5,
    "bueno": 1.5,
    "buenos": 1.5,
    "celebrar": 1.5,
    "chida": 2.5,
    "chidas": 2.5,
    "chido": 2.5,
    "chidos": 2.5,
    "cielito": 2.0,
    "cielo": 1.5,
    "contento": 2.0,
    "corazon": 1.5,
    "corazoncito": 2.0,
    "deliciosa": 2.0,
    "delicioso": 2.0,
    "descansa": 1.5,
    "descansar": 1.5,
    "divertido": 1.5,
    "encanta": 2.0,
    "encantado": 2.0,
    "encantan": 2.0,
    "encantar": 2.0,
    "excelente": 3.0,
    "exito": 2.5,
    "extranar": 2.0,
    "extrano": 2.0,
    "felicidad": 2.5,
    "felicidades": 2.5,
    "felices": 2.0,
    "feliz": 2.0,
    "genial": 2.5,
    "gracias": 1.5,
    "gusta": 1.5,
    "gustan": 1.5,
    "gustar": 1.5,
    "gusto": 1.5,
    "hermosa": 2.0,
    "hermoso": 2.0,
    "increible": 3.0,
    "interesante": 1.5,
    "jaja": 1.0,
    "jajaj": 1.0,
    "jajaja": 1.0,
    "jajajaj": 1.0,
    "jajajaja": 1.0,
    "jajajajaja": 1.0,
    "jeje": 1.0,
    "jejeje": 1.0,
    "jiji": 1.0,
    "linda": 1.5,
    "lindo": 1.5,
    "listo": 1.0,
    "maravilla": 2.5,
    "maravilloso": 3.0,
    "mejor": 1.5,
    "muak": 2.0,
    "perfecto": 2.5,
    "preciosa": 2.0,
    "precioso": 2.0,
    "qmtm": 2.5,
    "querer": 1.5,
    "querida": 1.5,
    "querido": 1.5,
    "quiero": 1.5,
    "rica": 1.5,
    "rico": 1.5,
    "saludo": 1.0,
    "saludos": 1.0,
    "sonreir": 1.5,
    "suena": 1.0,
    "teamo": 2.5,
    "tkm": 2.5,
    "tkmm": 2.5,
    "tkmmm": 2.5,
    "tqm": 2.5,
    "tqmm": 2.5,
    "tqmmm": 2.5,
    "tqmmmm": 2.5,
    "tqmqm": 2.5,
    "tranquilidad": 1.5,
    "tranquilo": 1.0,
    "triunfo": 2.0,
    "vidita": 2.0,
    "wow": 1.5,
    "xd": 1.0,
}

NEGATIVE_WEIGHTS = {
    "agotada": 2.0,
    "agotado": 2.0,
    "aguitada": 2.0,
    "aguitado": 2.0,
    "alv": 2.0,
    "ansiedad": 2.0,
    "asco": 2.5,
    "asqueroso": 3.0,
    "bajon": 2.0,
    "cagada": 1.5,
    "cagado": 1.5,
    "cansada": 1.5,
    "cansado": 1.5,
    "chafa": 1.5,
    "chale": 1.5,
    "complicada": 1.0,
    "complicado": 1.0,
    "coraje": 2.0,
    "culera": 2.0,
    "culero": 2.0,
    "decepcionar": 2.5,
    "desanimo": 2.0,
    "desastre": 2.0,
    "desmadre": 1.5,
    "dificil": 1.0,
    "dolor": 2.0,
    "duele": 2.0,
    "encabronada": 2.0,
    "encabronado": 2.0,
    "enojada": 2.0,
    "enojado": 2.0,
    "enojo": 2.0,
    "error": 1.0,
    "errores": 1.0,
    "estres": 2.0,
    "estresada": 2.0,
    "estresado": 2.0,
    "fatal": 3.0,
    "feo": 1.5,
    "fracaso": 2.5,
    "horrible": 3.0,
    "hueva": 1.5,
    "ira": 2.0,
    "lamentable": 2.0,
    "lamento": 1.5,
    "lastimar": 2.0,
    "mal": 1.5,
    "mala": 1.5,
    "malas": 1.5,
    "malo": 1.5,
    "malos": 1.5,
    "miedo": 2.0,
    "mierda": 2.0,
    "molesta": 1.5,
    "molesto": 1.5,
    "odia": 3.0,
    "odias": 3.0,
    "odiar": 3.0,
    "odio": 3.0,
    "pendeja": 2.0,
    "pendejo": 2.0,
    "peor": 2.0,
    "pesima": 3.0,
    "pesimo": 3.0,
    "preocupa": 1.5,
    "preocupada": 1.5,
    "preocupado": 1.5,
    "preocupacion": 2.0,
    "problema": 1.0,
    "problemas": 1.0,
    "ptm": 2.0,
    "puta": 2.0,
    "puto": 2.0,
    "romper": 1.5,
    "sola": 1.0,
    "solo": 1.0,
    "terrible": 3.0,
    "triste": 2.0,
    "tristeza": 2.0,
    "tristes": 2.0,
    "valio": 1.5,
}

NEGATIONS = {"jamas", "nada", "ni", "ningun", "ninguna", "ninguno", "no", "nunca", "sin", "tampoco"}
INTENSIFIERS = {
    "bastante": 1.3,
    "demasiado": 1.5,
    "extremadamente": 1.8,
    "harta": 1.4,
    "harto": 1.4,
    "muchisima": 1.8,
    "muchisimo": 1.8,
    "mucho": 1.4,
    "muy": 1.5,
    "re": 1.3,
    "super": 1.5,
    "tan": 1.25,
}
POSITIVE_EMOJIS = (
    "😀", "😃", "😄", "😁", "😊", "😍", "🥰", "🙂", "❤", "❤️", "👍", "🎉",
    "🤗", "💛", "✨", "💖", "💕", "💓", "💗", "😻", "😘", "😚", "🤣", "😅", "😇", "🙌", "🥺", "🙈", "😌"
)
NEGATIVE_EMOJIS = ("😞", "😔", "😢", "😭", "😡", "😠", "🤬", "😟", "💔", "👎", "🤨", "🤦", "😤", "😒")


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
