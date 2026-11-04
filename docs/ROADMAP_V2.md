# Roadmap V2

Le ROADMAP.md racine est historique ; cette séquence remplace son ordre de travail.
Les PR peuvent être subdivisées. Tests et revue avant toute fusion ; rollback et
sauvegarde requis lorsqu'une étape touche aux données ou au déploiement.

| Étape | Livraison | Critère de sortie |
|---|---|---|
| Sprint 00 | Baseline, fixtures, CI, audit et docs | Résultats pytest enregistrés, aucun changement métier |
| Sprint 01A | Auth admin Flask, sessions/CSRF, configuration sûre | Tests offline, revue et CI avant fusion ; pas de déploiement automatique |
| Sprint 01B | HTTP externe/SSRF | PR #4 fusionnée après CI réussie |
| Sprint 01C | Scheduler et concurrence | PR #5 fusionnée après CI réussie |
| Sprint 01D | Verrou LLM local | PR #6 fusionnée après CI réussie |
| Sprint 01E | Intégrité des articles JSON | PR #7 fusionnée après CI réussie |
| Sprint 01F | Intégrité des bulletins audio | PR #8 fusionnée après CI réussie ; longueur warn par défaut |
| Sprint 01G | XSS, JavaScript et CSP | PR #9 fusionnée après CI réussie |
| Autres PR P0 | Anomalies résiduelles, revue de déploiement et robustesse | Une reproduction et une non-régression par correction |
| 02 | Compose pour l'existant, secrets et healthchecks | Images et smoke CI isolé ; revue avant déploiement Linux réel |
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

Sprint 01A livré par la PR #3, fusionnée dans dev avant le Sprint 01B.
Sprint 01B livré par la PR #4, fusionnée avant le Sprint 01C. Voir HTTP_SECURITY.md.
Sprint 01C livré par la PR #5 ; voir SCHEDULER.md pour les garanties locales et les limites de reprise.
Sprint 01D livré par la PR #6, fusionnée avant le Sprint 01E ; voir LLM_LOCKING.md.
Sprint 01E livré par la PR #7, fusionnée avant le Sprint 01F ; voir ARTICLE_INTEGRITY.md.
Le sprint 05 conserve le parser RSS/Atom et la normalisation globale, en réutilisant
les garanties de reprise. Sprint 01F livré par la PR #8 ; voir
RADIO_INTEGRITY.md pour manifeste, reçus, publication, compatibilité et limites.
Aucun déploiement ni modification de données réelles ; aucune fusion automatique
de la PR du sprint. Le sprint Radio Studio conserve le calendrier global, la
continuité avancée et les futurs moteurs TTS.

Sprint 01G proposé sur feature/v2-xss-csp ; voir WEB_SECURITY.md pour inventaire,
contrats, politique CSP, tests et limites. Aucune fusion ni exposition automatique
de cette PR ; les exports externes nécessitent un déploiement/rebuild approuvé.

Sprint 02 proposé sur feature/v2-docker-foundation ; voir DOCKER_DEPLOYMENT.md.
Aucun déploiement/migration réel ni fusion automatique. La santé métier, les lots
longs, les sauvegardes et le réseau hôte restent à vérifier dans une installation
autorisée avant exposition ; PostgreSQL/LiteLLM restent aux étapes suivantes.

Sprint 03 : passerelle LiteLLM opt-in, cinq alias, backend historique conservé,
cloud désactivé par défaut, relais de capacité local/cloud et cache par modèle.
Tests hors ligne et smoke complet avec fournisseur simulé ; détails LLM_GATEWAY.md.
Les quotas financiers persistants, équité distribuée et jobs durables restent à
livrer avec l'architecture de stockage ; aucune base ajoutée par ce sprint.
