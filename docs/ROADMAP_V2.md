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
| 03 | LiteLLM et profils Ollama Cloud/OpenRouter | PR #11 en revue : alias, tests offline/smoke, capacités et fallback ; budgets persistants différés |
| 04 | PostgreSQL/Alembic et import JSON/YAML | PR #12 en revue : snapshots vérifiés et dump/restore ; cutover métier en PR complémentaire |
| 05 | Collecte RSS/Atom, dates, statuts et retries | PR #13 fusionnée : cache conditionnel/santé legacy et concurrence bornée ; aucune bascule SQL |
| 06 | Source Manager API | PR #14 fusionnée : YAML autoritaire, mutations concurrentes, test RSS/Atom, santé et import/export YAML/OPML |
| 07 | Découverte de sources | PR #15 fusionnée : site statique, SearXNG public opt-in, preview et ajout volontaire ; RSSHub/LAN reportés |
| 08 | Éditorial | PR #16 fusionnée : index dérivé, regroupements conservateurs, provenance et corrections ; sélection radio/éditions opt-in |
| 09 | Images | PR #17 fusionnée ; candidats RSS/HTML, Commons manuel, CC BY 4.0 sous revue, worker borné et fallback ; documentaire/autres licences reportés |
| 10 | Laboratoire TTS | PR #18 fusionnée ; corpus fixe, worker/adaptateurs optionnels et écoute aveugle ; banc synthétique validé, mesures vocales Ryzen/i5/GPU et choix définitif non réalisés |
| 11 | Radio Studio | Journal/outbox privés opt-in, calendrier Paris, observations locales et reprise explicite ; bridge Edge facultatif, alternatives non qualifiées |
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

Sprint 04 : socle PostgreSQL/Alembic et import/export de snapshots proposé. La
bascule effective collecte/publication/lecteurs reste une tranche complémentaire,
avec transactions de traitement et protocole de cutover à tester. Les fichiers
restent autoritaires dans cette PR ; voir DATABASE_MIGRATION.md.

Sprint 05 : RSS/Atom, résultats typés, cache conditionnel et santé des flux legacy,
téléchargements bornés avec limites par hostname effectif. La consolidation
multi-sources, la découverte et l'interface Source Manager restent aux sprints
suivants ; voir RSS_INGESTION.md et AUDIT_P1.md. Aucun cutover PostgreSQL.

Sprint 06 : service SourceManager partagé, API privée versionnée et adaptateurs historiques,
UI Flask/CSP, preview signée et écritures POSIX atomiques. Aucun cutover SQL ni découverte.
Voir SOURCE_MANAGER.md ; tests hors ligne et smokes CI avant revue/fusion.

Sprint 07 : découverte par site puis recherche thématique facultative, vérification
RSS/Atom commune, budgets HTTP par hop, cache court privé et navigation radio persistante.
Sources autoritaires dans YAML, ajout exclusivement SourceManager explicite/versionné.
Voir SOURCE_DISCOVERY.md ; ni crawler généraliste ni réseau de production en test.

Sprint 08 : index événementiel explicite et reconstructible, API de lecture, corpus
annoté synthétique, catégories configurées/observées et provenance des consommateurs
opt-in. Les faits ne sont pas déclarés vérifiés par consensus de sources.
Voir EDITORIAL_EVENTS.md pour scores, contraintes, corrections, limites et rollback.

Sprint 12 : première tranche verticale SvelteKit opt-in livrée pour revue : pages
publiques SSR, événements, archives/documents, images autorisées et lecteur persistant.
Hub admin + sources/Radio Studio en consultation, authentification Flask inchangée.
Migration complète des écrans administratifs et revue accessibilité manuelle reportées ;
aucun cutover automatique. Voir FRONTEND_V2.md.

Sprint 13 : dossier RC-0 pour revue, pas une mise en production. Restauration
legacy vérifiée, préflight sans lecture des secrets, archivage manuel prudent,
export read-only corrigé et charge synthétique. **No-Go qualification publique** :
multi-machine, radio 24 h, licences/droits, revue opérateur et scan OS restent
à réaliser. Les limites de l'index (500) et de stockage ne sont pas levées.
Voir RELEASE_CANDIDATE.md et CHANGELOG_V2.md ; aucun cutover PostgreSQL.

## Sprint 16 — lots soumis séparément

16A : diagnostic LLM local, budgets par tâche, suppression du retry identique sur
réponse vide ; audit réel en lecture seule, voir SPRINT_16_AUDIT.md et
LLM_RELIABILITY.md. 16B (après fusion opérateur de 16A) : calendrier 24/7 et identité
radio déterministe. 16C : illustrations auto de qualification avec garde-fou des
droits. 16D : dates V1/V2 et corrections reproduites. Aucune fusion ni mise à jour
automatique de la configuration persistante.
## Sprint 15 — qualification préparée, NO-GO public

Registre de preuves, métriques et sondes radio bornées, instrumentation Android,
SBOM/CVE des candidats et corrections ciblées. A/B, trois smartphones, endurance,
TTS réel et droits restent des portes opérateur. Voir FIELD_QUALIFICATION.md.
