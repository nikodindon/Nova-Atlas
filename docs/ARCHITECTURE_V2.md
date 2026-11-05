# Architecture V2 — cible et transition

## Socle historique au Sprint 00

`main.py` orchestre processus et threads ; Python/Flask/Jinja, requests/BeautifulSoup,
LLM Ollama CLI ou llama-server HTTP, Edge TTS, FFmpeg et Icecast. Les échanges
passent par JSON/YAML et fichiers audio. Compose couvre uniquement Icecast.
Le Sprint 00 ne modifie ni les chemins ni les contrats métier.

## Cible proposée

SvelteKit/TypeScript → FastAPI/Pydantic `/api/v1` → services Python réutilisés.
PostgreSQL et Alembic pour transactions/migrations ; scheduler unique et jobs
persistants, workers séparés. Pas de broker/Redis avant besoin démontré.
SQLite/WAL reste une alternative de prototype si PostgreSQL bloque l'adoption.
Maintenir `/api/articles` pour Android pendant la transition.

LiteLLM Proxy interne expose les alias `atlas-fast`, `atlas-translate`,
`atlas-editorial`, `atlas-radio-script`, `atlas-verify`. Ollama Cloud/OpenRouter
seront configurables ; llama.cpp local facultatif. Aucun appel cloud ajouté ici.
Quotas, budgets, timeout, Retry-After, cooldown, retries bornés et fallback explicite
font partie du contrat ; plusieurs clés d'un compte ne multiplient pas le quota.

Chaîne : source → entrée → extraction → normalisation/déduplication → événement
multi-sources → résumé vérifiable → édition/rapport/post/bulletin.
Tables envisagées : sources, feed_entries, articles, events/event_articles,
summaries, jobs, outputs, audio_segments/radio_programs, media_assets.
Conserver URL/titre originaux, modèles/prompts versionnés, licence/provenance,
statuts et tentatives. Séparer published_at, first_seen_at, fetched_at et
processed_at en UTC ; timezone d'affichage explicite.

Source Manager : CRUD, import/export sans perte, historique, test borné HTTP +
parsing + fraîcheur + extraction + images. feedparser pour RSS/Atom ; découverte
site→flux puis recherche→sites ; RSSHub/SearXNG facultatifs. Pas de crawl illimité.
Protection SSRF sur ajout, test, extraction, images et chaque redirection : schémas,
DNS/IPv4/IPv6, loopback, privés, link-local, métadonnées, CGNAT/Tailscale et rebinding.

TTSProvider.synthesize(segment, voice, language, style) → AudioArtifact.
Edge, Piper CPU, Qwen3-TTS/speech.cpp Vulkan et Kokoro à évaluer sur corpus français.
Mesurer latence, RTF, RAM/VRAM, erreurs et qualité d'écoute sur les deux Linux.
Numérotation déterministe ; segment incomplet → retry/fallback ou refus de publier.
FFmpeg/Icecast conservés, loudness/ducking, continuité et programme de secours.

Compose futur : API, frontend, worker, scheduler, PostgreSQL, LiteLLM, Icecast ;
TTS local/RSSHub optionnels. Volumes sauvegardables, healthchecks, versions épinglées,
DB/proxy internes. TTS hôte possible avant GPU en conteneur. Admin privée/auth,
CSRF navigateur, validation et limites de débit avant exposition publique.

Migration par adaptateurs et tranches verticales, export/import et rollback testés.
Aucune donnée réelle n'est migrée dans le Sprint 00.

## Livraison incrémentale Sprint 01B

Les URL non fiables RSS/articles passent maintenant par `modules.http.external.get` :
DNS validé intégralement et IP fixée à la socket, redirects et ressources bornés.
Ce même point d'entrée devra servir aux futurs tests/découverte Source Manager.
Le proxy Icecast utilise une politique interne distincte à origine/mounts configurés.
Les clients LLM de confiance restent séparés. Voir [HTTP_SECURITY.md](HTTP_SECURITY.md).

## Livraison incrémentale Sprint 01C

Le scheduler reste dans les processus existants. Décisions isolées dans
modules/core/scheduler.py, attente Event, créneaux civils datés, admissions par
famille et arrêt coopératif. L'historique est borné et en mémoire ; les jobs durables
et l'idempotence inter-instances restent au sprint SQL. Voir SCHEDULER.md.

## Livraison incrémentale Sprint 01D

Les ressources LLM locales exclusives utilisent un flock partagé par ressource,
avec un bail par processus/thread et une attente bornée sans préemption. Le cache
peut répondre sans ce verrou ; il est revérifié après attente. La capacité des
futures API cloud sera gérée séparément par l'adaptateur/LiteLLM, jamais par le
verrou global d'un modèle local. Aucun backend cloud ajouté. Voir LLM_LOCKING.md.

## Livraison incrémentale Sprint 01E

Les JSON quotidiens et le format historique seen restent lisibles. Un journal
versionné `article_processing.json` sépare découverte, tentative, échec temporaire,
succès et échec terminal. Checkpoint du résumé avant publication, écritures
atomiques et verrou de collecte Linux rendent la reprise idempotente après
checkpoint. Sept dates civiles pour la déduplication, horloge injectable et
backoff sans attente bloquante ; aucun nouveau service. Les transactions et
la normalisation UTC globale restent au sprint SQL. Voir ARTICLE_INTEGRITY.md.

## Livraison incrémentale Sprint 01F

Le pipeline radio conserve Edge TTS et son mix FFmpeg. Un manifeste ordonné impose
la réussite et le décodage de chaque segment obligatoire ; les retries restent
locaux à la génération. MP3 et reçu d'intégrité sont publiés depuis une zone privée
du même filesystem. Les admissions principale/Android et les flashs vérifient le
reçu ; Android reçoit une copie atomique avant consommation principale. Aucun
changement des mounts/API Android ni nouveau moteur. Les jobs durables et la
reprise distribuée restent au sprint SQL. Voir RADIO_INTEGRITY.md.

## Livraison incrémentale Sprint 01G

Flask conserve les templates avec autoescape ; les corps Markdown sont sanitisés
par nh3 et les liens filtrés avant HTML. Des assets JS connus initialisent les
composants sans interpréter le HTML reçu. Navigation publique et radio persistent,
CSP self sans unsafe-eval ni scripts inline ; styles historiques inline conservés.
Aucune API Android modifiée. Voir WEB_SECURITY.md pour audit et limites.

## Livraison incrémentale Sprint 02

Compose sépare les modes existants --web/--news/--radio et Icecast, sans dupliquer
leurs schedulers. Racine /app commune, mêmes binds POSIX locaux et UID/GID pour
les verrous, journaux et MP3 privés ; contrôles d'identité de stockage avant exec.
Images non-root, secrets en fichiers, backend interne et egress explicite, health
processus/stockage distincte de la santé métier. Tini et arrêt coopératif incluent
les travaux web en cours. L'adaptateur Ollama de déploiement conserve l'appel run
existant sans installer de modèle ; aucun backend cloud. Voir DOCKER_DEPLOYMENT.md.

## Livraison incrémentale Sprint 03

Backend LiteLLM optionnel, alias métier stables et Chat Completions. Proxy sans
DB/Redis ni port public, relais de sortie à destinations configurées : flock local
commun aux anciens clients et slots cloud séparés, secrets par rôle, retries/fallback
uniquement dans l'adaptateur Nova. Cache gateway versionné sans migration des
anciens fichiers. Pas de plafond financier strict sans comptabilité persistante ;
voir LLM_GATEWAY.md pour limites multi-hôtes, budgets et rollback.

## Livraison incrémentale Sprint 04

PostgreSQL optionnel, Alembic et snapshots historiques transactionnels importables/
exportables. Sources, articles, tentatives/checkpoints, sorties, seen et documents
complets sont conservés avec manifeste et empreintes. Les outils seuls lisent SQL ;
les producteurs/lecteurs métier gardent legacy comme autorité. Aucune migration au
startup ni cutover fictif : storage.mode postgres est refusé par main tant que le
parcours métier n'est pas branché. Voir DATABASE_MIGRATION.md pour droits, dates,
round-trip, sauvegarde et limites. Audio et verrous POSIX inchangés.

## Livraison incrémentale Sprint 05

HTTP externe sécurisé, XML strict et feedparser sur bytes produisent des entrées
RSS/Atom typées, sans téléchargement interne au parser. Téléchargements bornés,
cache conditionnel/état de santé legacy privé et replay 304 après interruption ;
le journal d'articles et les publications restent séquentiels et autoritaires.
Pas de canonicalisation destructive, changement des prompts/API ou cutover SQL.
Voir RSS_INGESTION.md et AUDIT_P1.md pour formats, budgets et limites DNS/dates.

## Sprint 06 — Source Manager legacy

SourceManager centralise le modèle, la grammaire YAML bornée, validation et mutations
sous flock stable + version SHA-256. L'API administrative et les anciennes routes
utilisent ce service ; la page Flask utilise uniquement JS statique et rendu textuel.
L'état de santé du collecteur reste indépendant et lu seul par Web. Le journal des
tests ponctuels est séparé ; ses embargos hôte contraignent le prochain cycle News.
Sources autoritaires dans YAML ; snapshots SQL inchangés, aucune écriture métier SQL.
Voir SOURCE_MANAGER.md pour contrats, concurrence, formats et limites.
