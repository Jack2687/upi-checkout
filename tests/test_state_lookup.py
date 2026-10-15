"""Suy bang khi API geo KHÔNG trả `region` (34.908 ca '?' trong log)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geobilling  # noqa: E402


class CanonicalStateTests(unittest.TestCase):
    def test_aliases_and_old_names(self):
        self.assertEqual(geobilling.canonical_state("Orissa"), "Odisha")
        self.assertEqual(geobilling.canonical_state("Pondicherry"), "Puducherry")
        self.assertEqual(geobilling.canonical_state("Uttaranchal"), "Uttarakhand")
        self.assertEqual(geobilling.canonical_state("NCT of Delhi"), "Delhi")
        self.assertEqual(geobilling.canonical_state("Telengana"), "Telangana")

    def test_decorated_names(self):
        self.assertEqual(geobilling.canonical_state("State of Kerala"), "Kerala")
        self.assertEqual(geobilling.canonical_state("Kerala state"), "Kerala")
        self.assertEqual(geobilling.canonical_state("Bihar, India"), "Bihar")

    def test_iso_codes(self):
        self.assertEqual(geobilling.canonical_state("BR"), "Bihar")
        self.assertEqual(geobilling.canonical_state("ts"), "Telangana")

    def test_unknown_stays_empty(self):
        self.assertEqual(geobilling.canonical_state("? "), "")
        self.assertEqual(geobilling.canonical_state(""), "")


class StateFromCityTests(unittest.TestCase):
    def test_common_cities(self):
        for city, state in (("Mumbai", "Maharashtra"), ("Patna", "Bihar"),
                            ("Bengaluru", "Karnataka"), ("Bengaluru Urban", "Karnataka"),
                            ("Kochi", "Kerala"), ("Navi Mumbai", "Maharashtra"),
                            ("Gurugram", "Haryana"), ("Hyderabad", "Telangana"),
                            ("Port Blair", "Andaman & Nicobar Islands")):
            with self.subTest(city=city):
                self.assertEqual(geobilling.state_from_city(city), state)

    def test_suffixes_are_trimmed(self):
        for value, state in (("mumbai suburban district", "Maharashtra"),
                             ("Thane city", "Maharashtra"),
                             ("Pune Municipal Corporation", "Maharashtra")):
            with self.subTest(value=value):
                self.assertEqual(geobilling.state_from_city(value), state)

    def test_unknown_city_is_empty(self):
        self.assertEqual(geobilling.state_from_city("Nowhereville"), "")
        self.assertEqual(geobilling.state_from_city(""), "")


if __name__ == "__main__":
    unittest.main()
