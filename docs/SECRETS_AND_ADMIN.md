# Secrets, configuration et surface administrative

## Incident historique

Le propriétaire et le blueprint signalent une clé OpenRouter en clair dans le diff
public de la PR #1 du 14 avril 2026, branche feature/openrouter-integration.
Le diff n'est pas consulté pour éviter de reproduire le secret. Le propriétaire
doit révoquer cette clé côté OpenRouter, renouveler les accès nécessaires et examiner
l'usage du compte. Aucune révocation effectuée ni revendiquée ici. Effacer un fichier
ou fermer une PR ne révoque pas une clé. Pas de réécriture d'historique dans ce sprint.
Aucun fichier privé de secrets n'a été lu ; ceci n'est pas une certification de
l'absence d'autres secrets historiques.

## Variables proposées

`.env.example` est un contrat préparatoire V2, **non chargé par le code actuel**.
Ne pas y coller des secrets. La configuration courante reste YAML et JSON ;
`.env` ne remplacera pas automatiquement les valeurs de config/config.yaml.

| Variable | Usage futur |
|---|---|
| LLM_BASE_URL | URL du proxy interne |
| LITELLM_API_KEY | Clé virtuelle par service, budgets/modèles bornés |
| OPENROUTER_API_KEY | Credential fournisseur côté proxy uniquement |
| OLLAMA_API_KEY | Credential Ollama Cloud côté proxy uniquement |
| DATABASE_URL | Connexion SQL, potentiellement secrète |
| ICECAST_SOURCE_PASSWORD | Authentification du diffuseur |
| ICECAST_ADMIN_PASSWORD | Administration Icecast |
| ATLAS_ADMIN_SECRET | Secret serveur d'administration/session |

Secrets locaux hors Git, jamais navigateur/logs/fixtures. `.gitignore` protège les
.env et le YAML privé mais ne retire pas un secret déjà suivi. Les exemples
Icecast historiques et défauts du code nécessitent une configuration privée avant
usage ; aucun serveur n'a été démarré et leurs valeurs ne sont pas reproduites ici.

## Routes à protéger au Sprint 01

Inventaire statique de modules/web/atlas_web.py ; aucune protection ajoutée ici.

| Méthode | Routes | Protection prévue |
|---|---|---|
| GET | /config, /config/simple, /config/yaml | Admin, masquage des secrets |
| POST | /config/save, /config/yaml/save, /config/restart | Admin, CSRF, validation, audit |
| GET | /config/feeds, /config/feeds/page | Admin |
| POST | /config/feeds/add, /config/feeds/remove, /config/feeds/reset, /config/feeds/move, /config/feeds/toggle, /config/feeds/category | Admin, CSRF, validation ; SSRF sur URL |
| GET | /preferences, /api/preferences | Propriétaire des préférences (globales actuellement) |
| POST | /api/preferences/toggle, /api/preferences/reset | Auth, CSRF, validation |
| POST | /api/flash | Auth, CSRF, quotas/concurrence/coût |
| GET | /api/flash/status/<job_id>, /audio/flash_<job_id>.mp3 | Propriété des jobs et validation identifiant |
| GET | /api/subscription/status | Contrat d'identité et limites de débit à revoir |

À auditer aussi : /api/articles (date/limit/pagination bornées), /stream/<path:mount>
(proxy interne et destination strictement bornés), routes fichiers/archives
(confinement des chemins), confiance ProxyFix limitée au vrai reverse proxy.
Tailscale seul n'authentifie pas un utilisateur de l'application. Restreindre
l'exposition en attendant auth effective, CSRF et validation. Les tests de routes
futurs devront utiliser une app factory et des chemins temporaires : run_server
écrit de la configuration et ne doit pas être appelé sur l'installation réelle.
