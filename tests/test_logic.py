#!/usr/bin/env python3
"""Pruebas rápidas sin servidor ni pantalla: config y filtros de audio.

    python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "pet", os.path.join(os.path.dirname(HERE), "pet.py"))
pet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pet)


class TestAudioFx(unittest.TestCase):
    def setUp(self):
        self._ffmpeg = pet.FFMPEG
        pet.FFMPEG = "/usr/bin/ffmpeg"      # la prueba no depende del sistema

    def tearDown(self):
        pet.FFMPEG = self._ffmpeg

    def test_neutro(self):
        self.assertEqual(pet.audio_fx({"audio_gain": 0,
                                       "audio_bass": 0,
                                       "audio_treble": 0}), "")

    def test_solo_volumen(self):
        self.assertEqual(pet.audio_fx({"audio_gain": 6}),
                         "volume=6dB,alimiter=limit=0.95")

    def test_todo(self):
        fx = pet.audio_fx({"audio_gain": 6, "audio_bass": 3,
                           "audio_treble": -2})
        self.assertIn("volume=6dB", fx)
        self.assertIn("bass=g=3:f=120:w=0.7", fx)
        self.assertIn("treble=g=-2:f=3500:w=0.7", fx)
        self.assertIn("alimiter=limit=0.95", fx)

    def test_topes(self):
        fx = pet.audio_fx({"audio_gain": 99, "audio_bass": 99,
                           "audio_treble": -99})
        self.assertIn("volume=20dB", fx)
        self.assertIn("bass=g=15:", fx)
        self.assertIn("treble=g=-15:", fx)


class TestSpeakable(unittest.TestCase):
    def test_quita_emojis_y_marcas(self):
        self.assertEqual(pet._speakable("**Hola** 😀 mundo"),
                         "Hola mundo")

    def test_raya_es_pausa(self):
        self.assertEqual(pet._speakable("uno — dos"), "uno. dos")


class TestConfig(unittest.TestCase):
    def test_voice_parcial_conserva_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "config.json")
            with open(p, "w") as f:
                json.dump({"voice": {"engine": "piper"}}, f)
            old = pet.CFG_PATH
            pet.CFG_PATH = p
            try:
                cfg = pet.load_cfg()
            finally:
                pet.CFG_PATH = old
        self.assertEqual(cfg["voice"]["engine"], "piper")
        self.assertEqual(cfg["voice"]["audio_gain"], 0)
        self.assertEqual(cfg["voice"]["kokoro_voice"], "ef_dora")
        self.assertEqual(cfg["mascot"], "terminal")


if __name__ == "__main__":
    unittest.main()
