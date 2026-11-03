# Architecture V2 — cible et transition

## Existant

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
