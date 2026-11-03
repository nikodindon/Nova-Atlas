# Baseline Sprint 00 — 3 novembre 2026

## Socle et résultats exécutés

Workspace initial : branche dev, HEAD `71937360d4e8d776a3cc88ad2ff7b6f58e51bff0`,
seul fichier non suivi NOVA_ATLAS_V2_PLAN.md fourni par le propriétaire.
Branche créée : feature/v2-foundation. Pas de changement production ni Android.

Python 3.12, venv isolé `.venv-baseline`, dépendances requirements.txt résolues
puis figées dans requirements-dev.lock (directes + transitives). Pas de paquet
ajouté à l'installation globale ; pas de moteur/collecte/radio démarré.

| Exécution | Réussis | Échoués | Skippés | Durée |
|---|---:|---:|---:|---:|
| Huit fichiers originaux, garde hors ligne temporaire | 185 | 0 | 0 | 3.09 s |
| Suite avec garde permanente, fixtures et mock scoped | 190 | 0 | 0 | 3.06 s |

Commande baseline : `PYTHONPATH=/tmp:. .venv-baseline/bin/python -m pytest -q -p nova_baseline_guard`
(plugin temporaire identique à tests/conftest.py, avant ajout de celui-ci).
Commande finale : `.venv-baseline/bin/python -m pytest -q`.
Sorties brutes utiles : baseline.txt et final.txt dans ce dossier.

Inventaire : test_config (defaults/merge), test_llm_client (HTTP/retry/cache),
test_llm_cache (TTL/fichiers), test_feeds_loader (CRUD/cache), test_preferences
(persistance), test_title_translation (langues/fallback), test_tts_clean
(nettoyage/script), test_bulletin_generator (fenêtres/prompts/voix/pipeline mocké).

Défaut d'isolation réparé dans les tests seulement : test_title_translation
remplaçait sys.modules['modules.core.llm_client'] par un MagicMock à la collecte.
Il utilise maintenant un patch de get_language limité à chaque test, sans
remplacer le module réel. Aucun xfail/skip pour masquer un défaut.

## Reproduire sans services

```sh
python3.12 -m venv .venv-baseline
.venv-baseline/bin/python -m pip install -r requirements-dev.lock
.venv-baseline/bin/python -m pytest -q
```

Le téléchargement initial nécessite accès au registre ; l'exécution des tests
n'en nécessite aucun. Pour installation déconnectée, préparer un wheelhouse sur
machine compatible : `pip download -r requirements-dev.lock -d wheelhouse`, puis
`pip install --no-index --find-links=wheelhouse -r requirements-dev.lock`.
Le lock est un instantané pip freeze de l'environnement Python 3.12 Linux testé,
sans hashes : pas un lock universel multi-plateformes ni un audit de vulnérabilités.
Régénérer dans un venv neuf, revoir le diff et relancer la suite lors des upgrades.
requirements.txt de production reste inchangé. CI utilise ce lock et Python 3.12.

La fixture autouse change le CWD vers tmp_path, bloque socket connect/connect_ex,
DNS getaddrinfo et subprocess.Popen. Les dépendances HTTP/LLM/TTS sont simulées.
Ce garde-fou n'est pas un sandbox OS général ; tout nouveau chemin absolu ou appel
natif doit être revu. Les imports actuels ne chargent pas le YAML privé ; la
collecte lit éventuellement les flux publics versionnés, sans requête HTTP.
Les fixtures sont fictives (example.invalid) ; audio synthétique non décodable,
créé uniquement dans tmp_path. Pas de test acoustique/FFmpeg/Icecast réel.

CI : PR vers dev/main et push dev/feature/v2-*, permissions lecture seule,
compileall (syntaxe), pytest, rapport JUnit en artifact. Aucun secret nécessaire,
aucun lancement de main.py. La réussite distante devra être vérifiée sur GitHub.

Vérification complémentaire d'ordre de collecte : tests traduction → client LLM →
caractérisations : 66 réussis en 0.37 s. Vérification syntaxique compileall réussie.
Le HEAD dev distant vérifié avant publication est identique au socle local.
