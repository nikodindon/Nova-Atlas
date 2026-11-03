# Roadmap V2

Le ROADMAP.md racine est historique ; cette séquence remplace son ordre de travail.
Les PR peuvent être subdivisées. Tests et revue avant toute fusion ; rollback et
sauvegarde requis lorsqu'une étape touche aux données ou au déploiement.

| Étape | Livraison | Critère de sortie |
|---|---|---|
| Sprint 00 | Baseline, fixtures, CI, audit et docs | Résultats pytest enregistrés, aucun changement métier |
| Sprint 01A | Auth admin Flask, sessions/CSRF, configuration sûre | Tests offline, revue et CI avant fusion ; pas de déploiement automatique |
| Sprint 01B et PR séparées | P0 scheduler, verrou, dates, reprises, routes/auth/CSRF/SSRF | Reproduction hors ligne et non-régression pour chaque correction |
| 02 | Compose pour l'existant, secrets et healthchecks | Installation et redémarrage sans perte sur Linux |
| 03 | LiteLLM et profils Ollama Cloud/OpenRouter | Alias métier, tests 429/timeout/auth, budgets/fallback |
| 04 | PostgreSQL/Alembic et import JSON/YAML | Sauvegarde/restauration et aller-retour sans perte |
| 05 | Collecte RSS/Atom, dates, statuts et retries | Erreur temporaire récupérable, HTTP simulé |
| 06 | Source Manager API | CRUD, test, santé, SSRF et import/export des flux historiques |
| 07 | Découverte de sources | Recherche/prévisualisation/ajout bornés, RSSHub facultatif |
| 08 | Éditorial | Catégories complètes, regroupement événement, provenance |
| 09 | Images | Métadonnées, licence/attribution et fallback vérifiés |
| 10 | Laboratoire TTS | Corpus français identique, benchmark Ryzen/i5 et choix motivé |
| 11 | Radio Studio | Segments complets, calendrier unique, continuité après panne |
| 12 | SvelteKit | Parcours public/studio/admin, auth, lecteur persistant, accessibilité |
| 13 | Release candidate | Installation seconde machine, charge, sécurité, droits, restauration |
| 14+ | Android | Compatibilité API puis tests multi-smartphones |

Hors V2 initiale : atlas géographique, multi-utilisateurs, journalistes,
personnalisation radio par personne. Aucun travail dans nova-atlas-android au Sprint 00.

Avant exposition : propriétaire révoque la clé OpenRouter de la PR publique #1,
renouvelle les accès concernés et vérifie leur usage. La révocation n'est pas
réalisée par ce sprint. Ne pas réécrire l'historique ici.

Sprint 01A : implémentation proposée sur feature/v2-admin-security, revue requise.
Le Sprint 01B (SSRF approfondi) ne commence qu'après validation de cette PR.
