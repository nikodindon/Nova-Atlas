#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests pour la détection de langue et la traduction de titre.

Couvre :
  - _has_non_latin_chars : détection des caractères non-latins (CJK, cyrillique,
    arabe, hébreu, devanagari, thai)
  - _translate_title avec force=True/False
  - L'ancienne condition `if lang not in ("français","french","fr")` doit
    être remplacée par la détection de caractères
"""
import sys
from unittest.mock import patch, MagicMock
from pathlib import Path

# Mock modules.core.llm_client AVANT l'import de atlas_fetch pour eviter
# l'init Ollama reelle.
sys.modules['modules.core.llm_client'] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.fetch.atlas_fetch import ArticleFetcher


class TestNonLatinDetection:
    """Tests pour la regex de detection de caracteres non-latins."""

    def setup_method(self):
        # ArticleFetcher a besoin d'un config minimal
        self.fetcher = ArticleFetcher.__new__(ArticleFetcher)
        self.fetcher.log = MagicMock()

    def test_japanese_detected(self):
        assert self.fetcher._has_non_latin_chars("日本円が急落")

    def test_chinese_simplified_detected(self):
        assert self.fetcher._has_non_latin_chars("中国政府发布新政策")

    def test_chinese_traditional_detected(self):
        assert self.fetcher._has_non_latin_chars("日本經濟成長")  # aussi CJK unifie

    def test_korean_detected(self):
        assert self.fetcher._has_non_latin_chars("한국 경제 성장")

    def test_cyrillic_russian_detected(self):
        assert self.fetcher._has_non_latin_chars("Путин встретился с Си")

    def test_cyrillic_ukrainian_detected(self):
        assert self.fetcher._has_non_latin_chars("Це новина українською")

    def test_arabic_detected(self):
        assert self.fetcher._has_non_latin_chars("الاقتصاد ينمو في مصر")

    def test_hebrew_detected(self):
        assert self.fetcher._has_non_latin_chars("המשק הישראלי צומח")

    def test_hindi_detected(self):
        assert self.fetcher._has_non_latin_chars("भारत की अर्थव्यवस्था")

    def test_thai_detected(self):
        assert self.fetcher._has_non_latin_chars("เศรษฐกิจไทยเติบโต")


class TestLatinPassThrough:
    """Titres en caracteres latins : ne doivent PAS declencher la traduction."""

    def setup_method(self):
        self.fetcher = ArticleFetcher.__new__(ArticleFetcher)
        self.fetcher.log = MagicMock()

    def test_french_with_accents(self):
        assert not self.fetcher._has_non_latin_chars("Macron rencontre Xi à Paris")

    def test_french_typical(self):
        assert not self.fetcher._has_non_latin_chars("Les prix du gaz augmentent en Europe")

    def test_english_pure(self):
        assert not self.fetcher._has_non_latin_chars("The economy grows in 2025")

    def test_spanish_with_accents(self):
        assert not self.fetcher._has_non_latin_chars("La economía española crece")

    def test_japanese_name_romaji(self):
        # Nom propre japonais enromaji (latin) - doit PAS declencher
        assert not self.fetcher._has_non_latin_chars("Yamamoto wins gold in Paris")

    def test_french_with_japanese_name(self):
        # Francais avec nom propre japonais enromaji
        assert not self.fetcher._has_non_latin_chars("Japon : Kishida announces election")


class TestTranslateTitleSkipsLatin:
    """Le bug d'origine : titres latins (FR/EN) declenchaient la traduction
    si la langue cible etait differente. Avec le fix, ils sont skippes."""

    def setup_method(self):
        self.fetcher = ArticleFetcher.__new__(ArticleFetcher)
        self.fetcher.log = MagicMock()

    def test_latin_title_no_llm_call(self):
        """Un titre latin ne doit PAS appeler ollama_call."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            result = self.fetcher._translate_title("Macron meets Xi in Paris")
            assert result == "Macron meets Xi in Paris"
            assert mock_ollama.call_count == 0

    def test_empty_title_returns_empty(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            assert self.fetcher._translate_title("") == ""
            assert self.fetcher._translate_title("   ") == "   "
            assert mock_ollama.call_count == 0

    def test_force_true_overrides_heuristic(self):
        """force=True doit appeler ollama meme pour un titre latin."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Macron meets Xi in Paris (translated)"
            result = self.fetcher._translate_title("Macron meets Xi in Paris", force=True)
            assert mock_ollama.call_count == 1
            assert "translated" in result

    def test_non_latin_title_calls_llm(self):
        """Un titre CJK doit appeler ollama_call."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Le yen chute fortement"
            result = self.fetcher._translate_title("日本円が急落")
            assert mock_ollama.call_count == 1
            assert result == "Le yen chute fortement"

    def test_cyrillic_title_calls_llm(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Poutine a rencontré Xi"
            result = self.fetcher._translate_title("Путин встретился с Си")
            assert mock_ollama.call_count == 1
            assert result == "Poutine a rencontré Xi"

    def test_arabic_title_calls_llm(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "L'économie grandit en Egypte"
            result = self.fetcher._translate_title("الاقتصاد ينمو في مصر")
            assert mock_ollama.call_count == 1
            assert result == "L'économie grandit en Egypte"

    def test_llm_failure_falls_back_to_original(self):
        """Si ollama_call echoue, on garde le titre original."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = ""  # echec
            result = self.fetcher._translate_title("日本円が急落")
            assert result == "日本円が急落"

    def test_llm_brackets_falls_back(self):
        """Si ollama retourne '[Timeout]' on garde l'original."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "[Timeout]"
            result = self.fetcher._translate_title("한국 경제 성장")
            assert result == "한국 경제 성장"

    def test_llm_strips_quotes(self):
        """Les guillemets autour de la traduction sont strips."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = '"Le yen chute"'
            result = self.fetcher._translate_title("日本円が急落")
            assert result == "Le yen chute"
