from __future__ import annotations

import unittest

from cliente_xmpp.models.sentiment import sentiment_weights


class SentimentWeightsTests(unittest.TestCase):
    def test_words_intensifiers_and_emojis_have_weights(self) -> None:
        positive = sentiment_weights("Excelente, gracias 😊")
        negative = sentiment_weights("Terrible y muy malo 😡")

        self.assertEqual(positive.positive, 6.5)
        self.assertEqual(positive.negative, 0)
        self.assertEqual(positive.indicators, 3)
        self.assertEqual(negative.positive, 0)
        self.assertEqual(negative.negative, 7.25)
        self.assertEqual(negative.indicators, 3)

    def test_negation_reverses_nearby_emotional_word(self) -> None:
        positive = sentiment_weights("No es malo")
        negative = sentiment_weights("Nunca excelente")
        reset_after_indicator = sentiment_weights("No es problema, excelente")

        self.assertEqual(positive.positive, 1.5)
        self.assertEqual(positive.negative, 0)
        self.assertEqual(negative.positive, 0)
        self.assertEqual(negative.negative, 3.0)
        self.assertEqual(reset_after_indicator.positive, 4.0)
        self.assertEqual(reset_after_indicator.negative, 0)

    def test_text_without_indicators_is_neutral(self) -> None:
        result = sentiment_weights("La reunión comienza a las ocho")
        url = sentiment_weights("Consulta https://error.example/malo")

        self.assertEqual(result.total, 0)
        self.assertEqual(result.balance, 0)
        self.assertEqual(result.indicators, 0)
        self.assertEqual(url.total, 0)


if __name__ == "__main__":
    unittest.main()
