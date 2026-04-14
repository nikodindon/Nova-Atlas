#!/usr/bin/env python3
"""Test unitaire pour valider le fallback LLM (OpenRouter -> Ollama)."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import patch, MagicMock
from modules.core.config import load_config
from modules.core.llm_provider import LLMProvider

def test_fallback_on_openrouter_429():
    config = load_config('config/config.yaml')
    
    # Mock OpenRouterBackend.generate pour lever une erreur 429
    mock_openrouter_generate = MagicMock(side_effect=Exception('429 Rate limit mocked'))
    mock_ollama_generate = MagicMock(return_value='Mocked summary from Ollama')
    
    with patch('modules.core.llm_provider.OpenRouterBackend.generate', mock_openrouter_generate), \
         patch('modules.core.llm_provider.OllamaBackend.generate', mock_ollama_generate):
        
        provider = LLMProvider(config)
        # Forcer le provider à openrouter (config déjà en openrouter)
        result = provider.generate('test prompt', caller='test')
        
        # Vérifier que l'erreur a été catchée
        assert mock_openrouter_generate.called, "OpenRouter n'a pas été appelé"
        # Vérifier que le fallback a été déclenché
        assert mock_ollama_generate.called, "Ollama n'a pas été appelé en fallback"
        # Vérifier que le résultat vient d'Ollama
        assert result == 'Mocked summary from Ollama', f"Résultat inattendu: {result}"
        print("✅ Test fallback OpenRouter -> Ollama: OK")

def test_no_fallback_on_success():
    config = load_config('config/config.yaml')
    
    mock_openrouter_generate = MagicMock(return_value='Direct OpenRouter summary')
    
    with patch('modules.core.llm_provider.OpenRouterBackend.generate', mock_openrouter_generate), \
         patch('modules.core.llm_provider.OllamaBackend.generate') as mock_ollama:
        
        provider = LLMProvider(config)
        result = provider.generate('test prompt', caller='test')
        
        assert mock_openrouter_generate.called
        assert not mock_ollama.called, "Ollama ne doit pas être appelé quand OpenRouter réussit"
        assert result == 'Direct OpenRouter summary'
        print("✅ Test pas de fallback quand OpenRouter réussit: OK")

if __name__ == '__main__':
    test_fallback_on_openrouter_429()
    test_no_fallback_on_success()
    print("\nTous les tests de fallback sont passés.")
