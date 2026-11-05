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

## Sprint 01E — intégrité des articles

Socle `dev@99bc9e4` (PR #6 fusionnée, contrôles réussis) : 663 tests.
Sept reproductions exécutées avant correction : **7 échecs** (trois frontières
calendaires, extraction vide marquée vue, résumé vide publié, fichier corrompu
écrasable, suivi expiré non purgé). La caractérisation de l'article inaccessible
vérifie maintenant sa reprise plutôt que le défaut historique.

Suite finale locale Python 3.12 : **736 réussis, 0 échec, 0 ignoré**. Exécution dans
`.venv-baseline`, fichiers temporaires uniquement, DNS/transport/LLM simulés ; aucun
serveur Flask réel ni service audio. Syntaxe `compileall`, test JavaScript
`node tests/test_csrf.cjs` et `git diff --check` réussis. Voir ARTICLE_INTEGRITY.md
pour les cas de reprise et les limites ; les contrôles CI sont requis avant revue.

## Sprint 01F — intégrité radio

Socle `dev@2db598d` : 736 tests. Six reproductions exécutées avant correction :
**6 échecs** (segment manquant au début/milieu/fin, structure invalide acceptée,
mix visible avant validation, collision de noms à la seconde).

Validation finale locale Python 3.12 : **808 réussis, 0 échec, 0 ignoré** en 18,58 s,
soit 72 cas supplémentaires. Les caractérisations de segment sauté, de segments
vides et d'obligation de deux voix vérifient désormais la politique corrigée.
Le pipeline historique sans assertion vérifie une publication complète sur disque
temporaire. Les tests de flash utilisent le client Flask et des jobs/générateurs
simulés ; aucun serveur ou streamer démarré. Syntaxe compileall, contrôle JavaScript
CSRF et git diff --check réussis.

Essais complémentaires autorisés hors pytest : FFmpeg local produit trois segments
sinusoïdaux de 0,4 seconde à 24 kHz mono, injectés comme synthétiseur. Build complet,
probe/décodage et reçu réussis avec et sans fond musical ; MP3 final 1,312 seconde
dans les deux cas. Fichiers dans un TemporaryDirectory sous /tmp, supprimés après
l'essai. Aucun Edge TTS réseau, Icecast ou modèle LLM. La CI n'exige pas FFmpeg :
les contrôles de codec/transport y sont simulés. Voir RADIO_INTEGRITY.md.

## Sprint 01G — XSS et CSP

Socle `dev@a631590` : 815 tests. Avant correction, cinq reproductions en échec :
HTML de données RSS actif, URL JavaScript, HTML Markdown actif, eval et CSP absente.
Après correction : **866 réussis, 0 échec, 0 ignoré** en 26,09 s sous Python 3.12.
Les 51 cas supplémentaires comprennent cinq tests Chromium locaux ; aucune
navigation vers un service réel. Les requêtes du navigateur sont interceptées et
remplies par Flask.test_client, médias simulés, proxy fermé pour les autres URL.
La fixture autorise uniquement le driver et le navigateur local ; les autres tests
conservent les garde-fous réseau/processus. Voir WEB_SECURITY.md pour l'inventaire,
les interactions vérifiées, l'exception CSS et les limites du transport simulé.

Compilation Python, contrôle JavaScript CSRF, syntaxe de tous les assets JS et
git diff --check réussis. La CI installe Chromium headless et ses bibliothèques,
puis exécute ces mêmes tests pour les deux contrôles push/PR. Aucun serveur,
Edge TTS, Icecast, modèle ou source RSS réel démarré.

## Sprint 02 — Compose et persistance

Socle dev@f70ff2c : 866 tests. Quatre reproductions en échec avant correction :
services manquants, tag latest, credentials d'exemple et port hôte wildcard.
Suite locale finale Python 3.12 : **907 réussis, 0 échec, 0 ignoré** en 29,63 s,
dont Chromium conservé et processus News synthétique avec SIGTERM au démarrage.
Compose est résolu avec un env temporaire explicite, sans daemon et sans .env privé.
Les tests de précontrôles ne lisent que leurs secrets synthétiques temporaires.
Compilation Python, syntaxe JS/shell, contrôle CSRF et git diff --check réussis.

Deux images de contrôle ont été construites localement, sans pile ni service
applicatif démarré. La CI Docker construit également les images, puis teste sur un
projet jetable Web/Icecast uniquement : exécutables, santé, singleton/ressource
partagée, audio sinusoïdal et reçu, permissions, persistance et arrêt. Les probes
News/Radio remplacent leur entrypoint ; aucun moteur RSS, TTS ou LLM réel lancé.
Voir DOCKER_DEPLOYMENT.md pour la procédure, les limites et les conditions du FS.

## Sprint 03 — passerelle LLM optionnelle

Socle dev@ca1f64c : 907 tests. Trois reproductions en échec avant correction :
adaptateur d'alias absent, Compose sans passerelle optionnelle et cache sans
identité de modèle. **988 réussis, 0 échec, 0 ignoré** en 31,42 s sous Python 3.12,
soit 81 cas ajoutés, Chromium conservé. Aucune connexion fournisseur réelle.
Les tests couvrent erreurs 401/402/403/404/429/5xx, Retry-After, deadlines,
annulation, capacités/fallback autorisé, secrets, concurrence/cache et Compose.
Syntaxes Python/JS, contrôle CSRF et git diff --check réussis.

Le smoke Compose historique reste exécuté. Un job CI distinct construit le dérivé
LiteLLM officiel puis lance seulement proxy, relais et fournisseur HTTP synthétique
sur un projet jetable. Les probes applicatives remplacent l'entrypoint News :
aucun scheduler, RSS, LLM, Edge TTS, Icecast ou streamer réel n'est démarré par ce
nouveau job. Vérifications de bout en bout : cinq alias/langue, auth, absence de
secrets dans les logs, santé, SIGTERM/restart ; scénarios 429/500 et comptage des
appels ajoutés pour vérifier l'unique niveau de retry. Voir LLM_GATEWAY.md.

## Sprint 04 — PostgreSQL et archives réversibles

Socle dev@0d7f787 : 988 tests. Trois reproductions en échec avant correction :
planificateur d'import absent, overlay DB absent et aucune révision Alembic.
Suite locale finale Python 3.12 : **1 034 réussis, 0 échec, 0 ignoré** en 33,75 s,
soit 46 cas supplémentaires. Chromium et smokes Docker/LiteLLM conservés.
Syntaxes Python/JavaScript, CSRF et git diff --check réussis.

Les unités utilisent des copies synthétiques et SQLite pour le rollback élémentaire ;
le test du journal exporté invoque le véritable chargeur legacy sans init de moteur.
Les documents sources complets permettent de vérifier précision numérique, champs
inconnus, commentaires et Unicode sans dépendre de la seule projection JSONB.

La CI database-smoke utilise PostgreSQL 18.6 réel jetable : Alembic + comparaison de
schéma, droits app/migrateur, dry-run, imports répétés/concurrents, processus tué
avant commit, checkpoint/tentatives préservés, export sémantique, déconnexion et
redémarrage PGDATA, pg_dump/pg_restore dans une DB neuve puis downgrade/upgrade.
Aucun producteur métier ni import réel n'est démarré. Les huit contrôles push/PR
incluent ce nouveau job ; la bascule métier SQL reste explicitement hors livraison.

## Sprint 05 — ingestion RSS/Atom

Socle dev@1cf5352 : 1 034 tests. Six reproductions exécutées avant correction :
contrat RSS, Atom ignoré, 304, réseau confondu avec vide, XML malformé et date avec
offset différent. Toutes échouaient au nouveau contrat typé sur l'ancien parser.
Suite locale finale Python 3.12 : **1 118 réussis, 0 échec, 0 ignoré** en 34,21 s,
soit 84 nouveaux cas ; la caractérisation Atom vérifie désormais son ingestion.

Fixtures RSS 0.90/0.91/0.92/0.93/0.94/1.0/2.0, Atom 0.3/1.0, CDATA/namespaces,
dates fiables/ambiguës, HTML hostile, requêtes conditionnelles, persistance,
backoff/rétention, DNS/redirect, concurrence et arrêt. Intégration réelle du journal
legacy avec extraction/LLM simulés : crash avant découverte puis 304, quota catégorie,
déduplication inter-flux, contrat JSON public identique et budget de tentatives.
Syntaxes Python/JS, CSRF et git diff --check réussis.

Le job Docker historique ajoute un probe RSS dans un conteneur network=none :
faux serveur loopback et transport explicitement injecté, vraie politique URL/DNS
avant chaque requête/redirect. 200/304, reprise cache après restart, déduplication,
absence de nouvelles générations et rejet du redirect privé. Échéance 30 secondes
et nettoyage du faux serveur. Aucun transport dérogatoire sélectionnable en prod.
Smokes LiteLLM/PostgreSQL et Chromium conservés ; aucune source RSS Internet réelle.
