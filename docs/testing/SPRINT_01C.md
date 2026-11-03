# Validation Sprint 01C — 2026-11-03

La PR #4 a été vérifiée (deux contrôles CI réussis), puis fusionnée sur demande
explicite du propriétaire. Socle synchronisé : dev@48bf5b4accff46d11ab09c0f288cb193550aae52.
Branche : feature/v2-scheduler-fix. La nouvelle PR attend sa revue, sans fusion automatique.

## Reproductions avant correctif

Commande exécutée avant modification de main.py :
`.venv-baseline/bin/python -m pytest -q tests/test_scheduler_reproductions.py`.
Résultat : **5 échecs, aucun succès**, en 0.31 s, avec tous les services mockés.

| Reproduction | Attendu | Observé avant correction |
|---|---|---|
| Repos | Attente entre vérifications | Aucune attente |
| Démarrage dans un créneau | Un fetch | Deux fetchs |
| Même heure deux jours distincts | Startup + deux fetchs | Startup + un seul fetch |
| Posts longs | Une seule exécution active | Deux threads sur des créneaux distincts |
| Heure d'hiver | Deux demi-heures civiles uniques | Cinq fetchs, startup inclus |

Résumé pytest conservé dans sprint_01c_before.txt. Les mêmes cinq tests passent
après correction ; aucun xfail/skip utilisé pour les masquer.

## Résultat final

**614 tests réussis, 0 échec, 0 skip**, 12.95 s : 438 baseline + 176 cas ajoutés.
Sortie brute conservée dans sprint_01c.txt.

```sh
.venv-baseline/bin/python -m pytest -q
.venv-baseline/bin/python -m compileall -q main.py modules tests
node tests/test_csrf.cjs
git diff --check
```

Tous réussis sur Python 3.12, dépendances existantes. Aucun nouveau paquet.
La CI existante exécute la suite complète et le contrôle JS ; résultat distant à
consulter sur la PR. Le test SIGUSR1 est conditionnel à sa présence sur l'OS ;
il est exécuté et réussi sous Linux, sans skip.

Horaires fetch sur les 24 heures et fenêtres, éditions 6/12/19, rapport/rebuild,
posts configurés, minuit/mois/année, heure d'été/hiver Europe/Paris, startup, tâches
longues, slots sautés, erreurs de thread, historique borné, race réelle d'admission
avec barrière, arrêt et reload différé sont couverts. Radio : heures YAML et reload,
cibles :25/:55, changement d'année/DST, calendrier invalide et non-chevauchement.
Supervision : signaux simulés, drain, Event neuf, restart et reprise watchdog avec
configuration fraîche ; aucun véritable processus enfant lancé.

Tous les constructeurs métier des tests de main, collecte, LLM, TTS, génération et
streamers sont remplacés. Les quelques threads réels utilisés pour la race restent
en mémoire avec événements/barrière et délais de garde courts, sans sleep long.
La garde réseau/processus du Sprint 00 reste active. Aucun serveur, Icecast,
FFmpeg, génération payante, donnée réelle ou credential privé touché.

Pas de mesure CPU live ni de test audio/de supervision sur une installation réelle.
Les tests prouvent l'attente explicite et les décisions, pas une limite CPU mesurée.
Les politiques de reprise et les limites figurent dans SCHEDULER.md.
NOVA_ATLAS_V2_PLAN.md reste non suivi, intact, empreinte avant/après :
8cebdaee43c604ceb3d7056e27c52052b3aba2c07d84b148532abfd8af85a1e6.
