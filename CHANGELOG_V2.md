# Changements V2 — historique synthétique

Aucune entrée ci-dessous ne signifie mise en production ou qualification opérateur.
Les détails, limites et preuves se trouvent dans docs/ et RELEASE_CANDIDATE.md.

- **00** : baseline pytest/CI, vision, migration incrémentale, audit P0, secrets historiques signalés.
- **01A** : administration Flask fermée par défaut, sessions, scrypt, CSRF, rate limits et configuration masquée.
- **01B** : HTTP externe commun, DNS/IP pinning, TLS, redirects/tailles/timeouts et SSRF.
- **01C** : scheduler interruptible, créneaux civils datés, concurrence et arrêt coopératif.
- **01D** : verrou Linux flock exclusif, propriété, timeout et récupération OS.
- **01E** : dates calendaires, rétention, états d'articles, retries/checkpoints et JSON atomiques.
- **01F** : manifeste TTS complet, validations audio, publication MP3/reçu atomiques ; longueur éditoriale warn par défaut.
- **01G** : suppression d'eval, rendu inerte, initialisation JS explicite, CSP et radio persistante historique.
- **02** : Compose sélectif Web/News/Radio/Icecast, non-root, secrets, stockage POSIX et smokes.
- **03** : passerelle LiteLLM facultative, cinq alias, politiques locales/cloud, fallback explicite et faux providers.
- **04** : socle PostgreSQL/Alembic optionnel, import/export snapshots idempotents et pg_dump/restore ; legacy autoritaire.
- **05** : feedparser RSS/Atom, dates fiables/brutes, résultats typés, ETag/Last-Modified et état de santé.
- **06** : Source Manager YAML commun, CRUD versionné, import/export, conflits et surveillance.
- **07** : découverte par site, recherche SearXNG optionnelle, candidats vérifiés, ajout explicite.
- **08** : événements multi-sources conservateurs, index dérivé, provenance, overrides et catégories.
- **09** : images optionnelles, droits/approbation, décodage borné, miniatures locales, attribution et fallback.
- **10** : laboratoire TTS isolé, corpus français, métriques, adaptateurs expérimentaux ; aucun remplacement Edge.
- **11** : Radio Studio opt-in, jobs/outbox durables, réconciliation, supervision et bridge TTS facultatif.
- **12** : tranche SvelteKit SSR opt-in, lecteur racine, pages publiques, hub/consultations admin et coexistence Flask.
- **13 (candidature)** : export média sur volume writable, projections médias par page/cache texte borné,
  snapshots/restauration neufs, préflight sans lecture de secrets, archivage Studio conservateur,
  diagnostics radio distincts, corpus de charge/restauration et dossier Go/No-Go. Pas de déploiement.
