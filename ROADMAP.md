# ROADMAP — Nova-Atlas

> Document de référence pour les prochaines évolutions du projet.
> Mis à jour à chaque fin de sprint. Cible de lecture : toi (niko) quand tu reviens sur le projet.

## État actuel (au 6 juillet 2026)

- 5 sprints terminés : sprint 0 (quick wins), sprint 1 (stabilité),
  paywalls, feeds externalization, UI feeds niveau 1
- HEAD: `8912f8a` sur `origin/dev`
- 72/72 tests pytest
- 56 flux RSS actifs, 11 catégories
- Modèle distant `192.168.1.32:8080` lancé `--reasoning off` (~1.1s/résumé)
- Site web + radio Icecast opérationnels
- UI web pour gérer les flux sur `/config/feeds/page`

## Sprints terminés ✅

| Sprint | Contenu | Commit |
|---|---|---|
| Sprint 0 | Quick wins : cap articles, flux morts commentés, rename `llm_client` | `8735581` |
| Sprint 1 | Stabilité : cap global + cache LLM + retry cold-start + pytest 34/34 | `32ef1a2` |
| Paywalls | Désactivation 3 flux paywall (Lemonde × 2, NYTimes) | `59bfd0e` |
| Feeds | Externalisation `feeds.yaml` + 4 routes API CRUD | `59bfd0e` + `82c9a02` |
| UI Feeds L1 | Page web de gestion des flux | `9b6c57d` + `8912f8a` |

## Backlog

### UI Feeds niveau 2 — drag-and-drop natif (2-3h)

**Pourquoi** : meilleure UX, le drag est attendu pour ce genre d'UI moderne.

**Contenu** :
- Drag-and-drop HTML5 dans la même catégorie (réordonnancement)
- Drag-and-drop entre catégories (transfert d'URL)
- Indicateur visuel pendant le drag (ligne de drop, opacité réduite)
- Nouvelles routes API :
  - `POST /config/feeds/transfer` (déplacer vers autre catégorie)
  - `POST /config/feeds/reorder` (réordonner toute une catégorie d'un coup)
- Pas de lib JS externe (HTML5 Drag API suffit)

**Pré-requis** : UI Feeds L1 fait ✅

### UI Feeds niveau 3 — Premium (5h+)

**Pourquoi** : polish + fonctionnalités "pro" pour usage quotidien long terme.

**Contenu** :
- Import/export de `feeds.yaml` (partager ta config entre machines)
- Validation d'URL : test HTTP HEAD avant ajout (rejette si 4xx/5xx)
- Recherche/filtre dans la liste (par URL, par catégorie)
- Compteur d'articles par flux (stats sur 7 jours glissants depuis `data/articles/`)
- Indicateur "paywall probable" : si l'URL répond en 401/402/403, badge rouge
- Favicons des domaines
- Historique des modifications (qui a désactivé quoi quand)

**Pré-requis** : UI Feeds L2 fait

### Sprint 2 — Modularisation web (3-4h)

**Pourquoi** : `atlas_web.py` fait **2929 lignes** (juillet 2026). Illisible, impossible à tester en isolation.

**Contenu** :
- Découper en sous-modules par responsabilité :
  - `atlas_web_routes.py` (routes Flask)
  - `atlas_web_pages.py` (builders HTML)
  - `atlas_web_api.py` (routes API JSON)
  - `atlas_web_helpers.py` (fonctions utilitaires)
- Conserver `atlas_web.py` comme point d'entrée qui orchestre
- Garder 100% de la compatibilité (mêmes routes, mêmes pages)
- Ajouter des tests unitaires par sous-module

**Pré-requis** : aucun (autonome)

### Sprint 3 — Audio first (4-6h)

**Pourquoi** : la qualité audio est centrale pour une "radio IA". Aujourd'hui c'est basique (TTS seul, pas de mix).

**Contenu** :
- Cross-fade musique ↔ bulletins (intro/outro musique avant/après TTS)
- Ducking automatique : baisse la musique quand le speaker parle
- Rotation des voix intelligentes (alterne voix FR/EN selon langue du bulletin)
- Playlist structurée : jingle d'ouverture → résumé → musique → résumé → jingle fermeture
- Bruitages (transition, alerte breaking news)

**Pré-requis** : aucun (autonome)

### CI GitHub Actions (1-2h)

**Pourquoi** : actuellement, les tests tournent à la main sur ta machine. Un push sur `dev` devrait déclencher pytest automatiquement.

**Contenu** :
- Créer `.github/workflows/test.yml`
- Trigger : push sur `dev` et PR vers `main`
- Setup Python 3.12 + install requirements
- Run `pytest tests/ -v`
- Badge "tests passing" dans le README

**Pré-requis** : aucun (autonome, mais débloque la confiance pour merger `dev` → `main`)

### Polish mineur (low-hanging fruits)

- Remplacer les 3 flux avec redirects (HRW, Pitchfork, BBC Sport) par leur URL finale
- Ajouter une option "test avant ajout" dans le formulaire `/config/feeds/add`
- Logger le temps moyen de résumé par cycle (perf observability)
- Mode `--dry-run` pour fetch (télécharge mais ne stocke pas)
- **Bug streamer radio** : `Pipe cassé → relance ffmpeg` en boucle, le streamer ne tient pas la connexion Icecast. À investiguer (format audio ou config Icecast). Vu le 2026-07-06 ~18:45.

### Sprint "Pays sans source" — Google News par pays (1-2h, en cours 2026-07-27)

**Pourquoi** : audit 2026-07-27 a montré que ~80% des sites d'info asiatiques,
arabes, eurasie et européens ont des anti-bots agressifs (404/403/200-vide au 2e essai).
On a déjà 12 flux "vrais" livrés, mais des pays entiers (Belgique, Portugal,
Grèce, Scandinavie, Indonésie, etc.) restent sans source locale.

**Contenu** : ajouter des flux `news.google.com/rss/search?q=<Pays>&hl=<LANG>&gl=<CC>&ceid=<CC>:<LANG>`
pour combler les trous. Google n'a pas d'anti-bot et c'est immédiat.

**Pré-requis** : aucun. Premier patch livré dans le commit à venir.

### Sprint 4 — Self-hosted proxy (à planifier, options ci-dessous)

**Pourquoi** : même avec les Google News, on a 60+ URLs en backlog car
les sites ont des anti-bots qui rejettent les UA non-browser. Un proxy
self-hosted est la solution propre long terme.

**Choix d'implémentation à faire** (cf section "Décision proxy" plus bas) :

- **Option A : Caddy + forward_proxy** — le plus simple, ~50 lignes de config, 1h de setup
- **Option B : mitmproxy** — surdimensionné mais très flexible
- **Option C : Tinyproxy + script Python custom** — 200 lignes DIY, contrôle total
- **Option D : SmokeProxy / scraping-proxy dédié** — commercial mais self-hostable, 50-100$/an

**Pré-requis** : avoir identifié l'option (cf section dédiée plus bas).

### Décision proxy — 4 options à arbitrer (sprint 4)

**Contexte** : on a 60+ URLs en backlog à cause d'anti-bots. Le sprint 4 fix ça
en s'intercalant entre nova-atlas et les sites cibles avec un proxy qui gère
UA réaliste, headers Accept-Language par site, cookie jar, cache, retry.

| Option | Complexité | Maintenance | Coût | Contrôle | Recommandation |
|---|---|---|---|---|---|
| A. Caddy + forward_proxy | Faible (~1h) | Modérée | 0€ (VPS 5€/mois) | Moyen | Idéal pour démarrer |
| B. mitmproxy | Haute | Haute | 0€ | Total | Surdimensionné |
| C. Tinyproxy + Python | Moyenne (~2j) | Modérée | 0€ | Total | Bon compromis |
| D. SmokeProxy | Faible | Basse | 50-100€/an | Faible | Rapide mais payant |

**Mon vote actuel** : Option A (Caddy) pour prototyper, basculer vers C si
on a besoin de plus de contrôle après 1 mois. À décider avant le sprint 4.

## Idées à valider

- **Modèle dual** : un modèle dense rapide (Qwen2.5-7B-Instruct) pour le fetch, le modèle reasoning pour les éditions/rapports longs. Nécessite de charger 2 modèles sur le serveur distant.
- **Sauvegarde automatique** : `feeds.yaml` versionné via `git add -A && git commit` après chaque édition UI. Permet de rollback.
- **Métriques Prometheus** : exposer `/metrics` pour suivre le projet (cycles, latence LLM, articles/cycle, etc.)

## Quand tu reprends le projet

1. `cd nova-atlas && git pull origin dev`
2. Regarde les issues/PRs ouverts sur GitHub
3. Choisis un sprint du backlog (en haut : UI Feeds L2 est le plus naturel vu qu'on vient de finir L1)
4. Active le venv : `source .venv/bin/activate`
5. Lance le projet : `python main.py --all --debug`

## Notes de session

- Mémoire projet : voir `~/.hermes/profiles/dev/memories/` (auto-injecté)
- Skill dev : `nova-atlas-dev` (workflow dev de ce projet)
- Tests : `pytest tests/ -v` (72 tests, 2.2s)
- UI : http://localhost:5055/config/feeds/page
- Logs live : `tail -f nova.log`
