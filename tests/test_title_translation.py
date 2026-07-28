#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests pour la traduction de titre.

Politique : on traduit TOUJOURS le titre vers la langue cible (default_language
du config.yaml). C'est plus simple, plus coherent (tous les titres dans la
meme langue que les resumes), et le LLM est rapide (~1s/appel).

Couverture :
  - Titres en francais : traduits quand meme (coherence home)
  - Titres en anglais : traduits
  - Titres en CJK / cyrillique / arabe : traduits
  - Titre vide : retourne vide, pas d'appel LLM
  - LLM echec : fallback sur l'original
  - LLM guillemets : strip
  - LLM '[Timeout]' : fallback sur l'original
"""
import sys
from unittest.mock import patch, MagicMock
from pathlib import Path

# Mock modules.core.llm_client AVANT l'import de atlas_fetch pour eviter
# l'init Ollama reelle.
sys.modules['modules.core.llm_client'] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.fetch.atlas_fetch import ArticleFetcher


class TestAlwaysTranslate:
    """Tous les titres sont traduits (coherence de la home)."""

    def setup_method(self):
        self.fetcher = ArticleFetcher.__new__(ArticleFetcher)
        self.fetcher.log = MagicMock()

    def test_french_title_is_translated(self):
        """Meme un titre francais est passe au LLM pour coherence."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Macron rencontre Xi à Paris (traduit)"
            result = self.fetcher._translate_title("Macron rencontre Xi à Paris")
            assert mock_ollama.call_count == 1
            assert "traduit" in result

    def test_english_title_is_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Macron rencontre Xi à Paris"
            result = self.fetcher._translate_title("Macron meets Xi in Paris")
            assert mock_ollama.call_count == 1
            assert result == "Macron rencontre Xi à Paris"

    def test_spanish_title_is_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "L'économie espagnole grandit"
            result = self.fetcher._translate_title("La economía española crece")
            assert mock_ollama.call_count == 1
            assert "grandit" in result

    def test_japanese_title_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Le yen chute fortement"
            result = self.fetcher._translate_title("日本円が急落")
            assert mock_ollama.call_count == 1
            assert result == "Le yen chute fortement"

    def test_chinese_title_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Le gouvernement chinois publie une nouvelle politique"
            result = self.fetcher._translate_title("中国政府发布新政策")
            assert mock_ollama.call_count == 1
            assert "gouvernement chinois" in result

    def test_korean_title_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Croissance de l'économie coréenne"
            result = self.fetcher._translate_title("한국 경제 성장")
            assert mock_ollama.call_count == 1
            assert "coréenne" in result

    def test_cyrillic_title_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "Poutine a rencontré Xi"
            result = self.fetcher._translate_title("Путин встретился с Си")
            assert mock_ollama.call_count == 1
            assert "Poutine" in result

    def test_arabic_title_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "L'économie grandit en Egypte"
            result = self.fetcher._translate_title("الاقتصاد ينمو في مصر")
            assert mock_ollama.call_count == 1
            assert "Egypte" in result

    def test_hindi_title_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "L'économie indienne"
            result = self.fetcher._translate_title("भारत की अर्थव्यवस्था")
            assert mock_ollama.call_count == 1
            assert "indienne" in result

    def test_thai_title_translated(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "L'économie thaïlandaise grandit"
            result = self.fetcher._translate_title("เศรษฐกิจไทยเติบโต")
            assert mock_ollama.call_count == 1
            assert "thaïlandaise" in result


class TestEmptyAndFallback:
    """Cas limites : titre vide, LLM qui echoue."""

    def setup_method(self):
        self.fetcher = ArticleFetcher.__new__(ArticleFetcher)
        self.fetcher.log = MagicMock()

    def test_empty_string_returns_empty_no_llm(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            assert self.fetcher._translate_title("") == ""
            assert mock_ollama.call_count == 0

    def test_whitespace_returns_same_no_llm(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            assert self.fetcher._translate_title("   ") == "   "
            assert mock_ollama.call_count == 0

    def test_llm_returns_empty_falls_back_to_original(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = ""
            result = self.fetcher._translate_title("Macron meets Xi in Paris")
            assert result == "Macron meets Xi in Paris"

    def test_llm_returns_brackets_falls_back(self):
        """[Timeout], [Error], etc. → fallback sur l'original."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "[Timeout]"
            result = self.fetcher._translate_title("Macron meets Xi in Paris")
            assert result == "Macron meets Xi in Paris"

    def test_llm_returns_too_short_falls_back(self):
        """Une reponse de moins de 4 chars est consideree comme echec."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "ok"
            result = self.fetcher._translate_title("Macron meets Xi in Paris")
            assert result == "Macron meets Xi in Paris"

    def test_llm_strips_surrounding_quotes(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = '"Le yen chute"'
            result = self.fetcher._translate_title("日本円が急落")
            assert result == "Le yen chute"

    def test_llm_strips_single_quotes(self):
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama:
            mock_ollama.return_value = "'Le yen chute'"
            result = self.fetcher._translate_title("日本円が急落")
            assert result == "Le yen chute"

    def test_french_target_english_translation_falls_back(self):
        """Si la cible est francais mais que la traduction est en anglais,
        on garde l'original. C'est le bug rapporte par l'utilisateur
        (ex: 'To protest against the emergency agricultural law' sur un
        article du Figaro).
        Le mock llm_client doit retourner une langue cible francais.
        """
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama, \
             patch("modules.core.llm_client.get_language", return_value="francais"):
            mock_ollama.return_value = "The novelist returns her Legion of Honour in protest"
            result = self.fetcher._translate_title(
                "Pour protester contre la loi d'urgence agricole, la romanciere rend sa Legion d'honneur"
            )
            # Fallback sur l'original
            assert result == "Pour protester contre la loi d'urgence agricole, la romanciere rend sa Legion d'honneur"
            # Le titre garde bien le mot francais 'protester', pas 'protest' (anglais)
            assert "rend sa" in result.lower()
            # Pas de " the " dans la sortie (le marqueur anglais)
            assert " the " not in result.lower()

    def test_french_target_clean_translation_kept(self):
        """Si la cible est francais et que la traduction est en francais,
        on la garde. Pas de faux positif."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama, \
             patch("modules.core.llm_client.get_language", return_value="francais"):
            mock_ollama.return_value = "Le yen chute fortement face au dollar"
            result = self.fetcher._translate_title("日本円が急落")
            assert result == "Le yen chute fortement face au dollar"

    def test_english_target_not_checked(self):
        """Si la cible est l'anglais, on ne check pas la traduction
        (meme si elle contient des mots francais par hasard)."""
        with patch("modules.fetch.atlas_fetch.ollama_call") as mock_ollama, \
             patch("modules.core.llm_client.get_language", return_value="english"):
            mock_ollama.return_value = "The yen falls sharply against the dollar"
            result = self.fetcher._translate_title("日本円が急落")
            # En anglais, on garde la traduction telle quelle
            assert result == "The yen falls sharply against the dollar"
