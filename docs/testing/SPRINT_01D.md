# Validation Sprint 01D — 2026-11-03

PR #5 vérifiée avec ses deux contrôles réussis, puis fusionnée sur demande
explicite. Socle dev : 597a7a77b9118f9f3cc4acd7f20d31dbfb131a20.
Branche : feature/v2-llm-lock. Aucun déploiement ni fusion automatique de la nouvelle PR.

Avant modification du client : six tests reproduisant les défauts actuels,
**6 échecs en 0.11 s**, attentes historiques simulées. Résumé conservé dans
sprint_01d_before.txt. Les mêmes six tests passent après correction.

Résultat final : **663 réussis, 0 échec, 0 skip**, 14.20 s.
614 cas baseline conservés + 49 cas de verrou/intégration ajoutés.
Sortie brute : sprint_01d.txt.

```sh
.venv-baseline/bin/python -m pytest -q
.venv-baseline/bin/python -m compileall -q main.py modules tests
node tests/test_csrf.cjs
git diff --check
```

Tous réussis sur Linux/Python 3.12 ; dépendances inchangées. La CI existante lance
ces contrôles sur la PR. Les tests spécifiques flock sont conditionnels à sa
présence ; sous Linux tous sont exécutés, sans skip.

Les tests ajoutés couvrent :

- Deux vrais processus locaux appelant OllamaClient avec génération simulée :
  compteur critique maximum un, attente du second et métadonnées cohérentes.
- Threads multiples d'un même client, refus de libération étrangère ou récursive.
- Acquisition atomique, erreurs open/flock/métadonnées, contexte refusant l'entrée
  sans bail et budgets invalides ; aucune génération après refus.
- Timeout monotone simulé, annulation d'attente, annulation ne tuant pas le travail actif.
- Exceptions et sorties normales, SIGKILL d'un détenteur dédié aux tests, récupération
  OS malgré un JSON périmé ; fork enfant ne libérant pas le parent.
- Inode conservé, non-héritage exec, symlinks/hardlinks refusés, JSON atomique sous
  lecture concurrente et données de diagnostic invalides sans autorité.
- Backends CLI et HTTP simulés sous bail, payload et nettoyage conservés, logs sans
  credentials synthétiques ; cache hit libre et cache miss revérifié après attente.
- Ressources indépendantes et lock_dir partagé, provider cloud non implémenté refusé.
- Intégration ThreadRunner du Sprint 01C : familles distinctes partageant bien le
  même verrou LLM, sans modifier le scheduler.

Seuls des processus **locaux dédiés aux tests**, des pipes/queues et des threads
mémoire sont réels. Les transports HTTP/CLI, modèles et sorties sont simulés. Les
processus tués appartiennent exclusivement à ces tests et sont joints/nettoyés.
La garde réseau du Sprint 00 reste active et héritée par fork. Aucun appel cloud,
collecte RSS, radio, TTS ou modèle réel démarré ; aucun fichier privé lu ni donnée
historique modifiée. Pas de test NFS/SMB/multi-hôte ni de charge sur un vrai modèle.

Les garanties d'un flock local supposent tous les participants sur la même ressource
et le même inode, sans processus de l'ancienne version. Limites et déploiement
coordonné documentés dans LLM_LOCKING.md ; pas de garantie d'arrêt serveur après
un timeout réseau ni de FIFO/non-famine. Aucun travail LiteLLM ajouté.

NOVA_ATLAS_V2_PLAN.md reste non suivi et intact, empreinte avant/après :
8cebdaee43c604ceb3d7056e27c52052b3aba2c07d84b148532abfd8af85a1e6.
