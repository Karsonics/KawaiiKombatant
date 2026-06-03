import unittest
import re

CHARACTER_PATTERNS = [
    re.compile(r"i (?:really )?like (\w+) from (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"i love (\w+) from (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"(\w+) from (.+?) is (?:my favorite|great|amazing|the best)", re.IGNORECASE),
    re.compile(r"favorite (?:character|person) is (\w+)(?: from (.+?))?(?:\.|$|,)", re.IGNORECASE),
]

MEDIA_PATTERNS = [
    re.compile(r"(?:watching|reading|love|like) (.+?)(?:anime|manga|series|show)", re.IGNORECASE),
    re.compile(r"have you (?:seen|read|watched) (.+?)\?", re.IGNORECASE),
]

FAVORITE_PATTERNS = [
    re.compile(r"(?:my )?favorite (?:color|colour) is (\w+)", re.IGNORECASE),
    re.compile(r"(?:my )?favorite food is (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"(?:my )?favorite (.+?) is (.+?)(?:\.|$|,)", re.IGNORECASE),
]

NAME_PATTERNS = [
    re.compile(r"(?:my )?name is (\w+)", re.IGNORECASE),
    re.compile(r"call me (\w+)", re.IGNORECASE),
]

HOBBY_PATTERNS = [
    re.compile(r"i (?:like to|love to|enjoy) (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"my hobby is (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"i'm into (.+?)(?:\.|$|,)", re.IGNORECASE),
]


class TestNamePatterns(unittest.TestCase):
    def test_my_name_is(self):
        match = NAME_PATTERNS[0].search("my name is Alex")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "Alex")

    def test_call_me(self):
        match = NAME_PATTERNS[1].search("call me Kuro")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "Kuro")

    def test_no_name(self):
        match = NAME_PATTERNS[0].search("what is your name?")
        self.assertIsNone(match)

    def test_name_in_sentence(self):
        match = NAME_PATTERNS[0].search("hi, my name is Sarah and I'm new here")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "Sarah")


class TestCharacterPatterns(unittest.TestCase):
    def test_i_like_character(self):
        match = CHARACTER_PATTERNS[0].search("i like Naruto from anime")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).capitalize(), "Naruto")
        self.assertEqual(match.group(2), "anime")

    def test_i_love_character(self):
        match = CHARACTER_PATTERNS[1].search("i love Pikachu from Pokemon")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).capitalize(), "Pikachu")
        self.assertEqual(match.group(2), "Pokemon")

    def test_favorite_character_is(self):
        match = CHARACTER_PATTERNS[3].search("my favorite character is Goku from Dragon Ball")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).capitalize(), "Goku")

    def test_character_from_is_best(self):
        match = CHARACTER_PATTERNS[2].search("Levi from Attack on Titan is the best")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "Levi")
        self.assertEqual(match.group(2), "Attack on Titan")

    def test_not_a_character(self):
        match = CHARACTER_PATTERNS[0].search("i like pizza from One Piece")
        self.assertIsNotNone(match)
        match = CHARACTER_PATTERNS[0].search("i like pizza without context")
        self.assertIsNone(match)


class TestMediaPatterns(unittest.TestCase):
    def test_watching_anime(self):
        match = MEDIA_PATTERNS[0].search("watching Demon Slayer anime")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "Demon Slayer")

    def test_have_you_seen(self):
        match = MEDIA_PATTERNS[1].search("have you seen One Piece?")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "One Piece")

    def test_no_media(self):
        match = MEDIA_PATTERNS[0].search("i like running")
        self.assertIsNone(match)


class TestFavoritePatterns(unittest.TestCase):
    def test_favorite_color(self):
        match = FAVORITE_PATTERNS[0].search("my favorite color is blue")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "blue")

    def test_favorite_food(self):
        match = FAVORITE_PATTERNS[1].search("my favorite food is pizza")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "pizza")

    def test_favorite_generic(self):
        match = FAVORITE_PATTERNS[2].search("my favorite game is Chess")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "game")
        self.assertEqual(match.group(2).strip(), "Chess")


class TestHobbyPatterns(unittest.TestCase):
    def test_i_like_to(self):
        match = HOBBY_PATTERNS[0].search("i like to play guitar")
        self.assertIsNotNone(match)
        self.assertIn("play guitar", match.group(1))

    def test_my_hobby_is(self):
        match = HOBBY_PATTERNS[1].search("my hobby is photography")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "photography")

    def test_im_into(self):
        match = HOBBY_PATTERNS[2].search("i'm into coding")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "coding")


class TestEdgeCases(unittest.TestCase):
    def test_empty_string(self):
        for patterns in [NAME_PATTERNS, CHARACTER_PATTERNS, MEDIA_PATTERNS,
                         FAVORITE_PATTERNS, HOBBY_PATTERNS]:
            for pattern in patterns:
                self.assertIsNone(pattern.search(""))

    def test_whitespace_only(self):
        for patterns in [NAME_PATTERNS, CHARACTER_PATTERNS, MEDIA_PATTERNS,
                         FAVORITE_PATTERNS, HOBBY_PATTERNS]:
            for pattern in patterns:
                self.assertIsNone(pattern.search("   "))

    def test_case_insensitive(self):
        match = NAME_PATTERNS[0].search("MY NAME IS ALEX")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "ALEX")


if __name__ == "__main__":
    unittest.main(verbosity=2)
