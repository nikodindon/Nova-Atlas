# Validation Sprint 01A — 2026-11-03

Socle : dev@a1db974f955904df65b1cd661828449180970b81, PR #2 vérifiée fusionnée
avant synchronisation et création de feature/v2-admin-security.

- Suite pytest complète : **286 réussis, 0 échec, 0 skip**, 11.84 s.
- Les 190 tests de baseline sont conservés, 96 cas de sécurité ajoutés.
- `python -m compileall -q main.py modules tests` : succès.
- `git diff --check` : succès.
- `node tests/test_csrf.cjs` : succès. VM locale, fetch/DOM simulés, aucun réseau.
  POST même origine enrichi, GET et origine externe sans jeton, formulaire enrichi.
  Ce contrôle ne remplace pas un test de navigateur complet.

Commande : `.venv-baseline/bin/python -m pytest -q` (Python 3.12, requirements-dev.lock).
Sortie brute : sprint_01a.txt. CI ajoute le contrôle JS avec Node 22, sans npm ni
nouvelle dépendance Python. Le résultat GitHub est à consulter sur la PR.

Couverture ajoutée : chaque route sensible anonyme, connexions valides/invalides,
refus si identité absente ou hash en clair, déconnexion avec rejeu de cookie,
expiration absolue, force brute malgré forwarded IP, HTTPS/proxy explicite,
401 API même avec Accept HTML, CSRF absent/invalide/valide sur chaque mutation,
routes nouvelles privées par défaut, inventaire complet, sauvegardes et CRUD,
conservation des secrets masqués/vides, absence de credentials dans les pages/flux/
erreurs/logs, sauvegardes YAML parallèles, fichiers privés/symlinks refusés,
contrats publics Android/news/radio, limites flash/reload et initialisation 0600.

Les tests créent leur installation complète dans tmp_path. La factory n'écrit
rien, aucun fallback vers le checkout réel. Les threads flash et rebuild sont
mockés dans les tests qui autorisent ces actions ; aucun LLM, RSS, TTS, FFmpeg,
Icecast ou serveur réel démarré. Le fichier NOVA_ATLAS_V2_PLAN.md du propriétaire
reste intact et non suivi. Aucun fichier privé de credentials consulté.

Limites : pas de déploiement, ni de navigateur complet, ni de test live derrière
un reverse proxy. Mono-processus web uniquement ; SSRF complet et XSS/CSP historiques
restent ouverts et documentés. Aucune correction du scheduler, verrou LLM ou audio.
Aucune fusion automatique ; 01B attend la revue de cette PR.
