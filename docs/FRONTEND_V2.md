# Frontend V2 — tranche verticale opt-in (Sprint 12)

SvelteKit 3.1.1 / Svelte 5.57.3 / TypeScript strict / adapter-node 6.0.1.
Flask reste l'unique backend métier et l'unique autorité de session. Aucun stockage
privé n'est monté dans Node ; aucune migration ni bascule automatique.
Les versions exactes et transitives sont dans `frontend/package-lock.json`.
Node 24 LTS est la cible de déploiement, Node >=22.17 pour le développement.
Kit 3 configure son plugin dans vite.config.ts ; voir les documentations officielles
[migration](https://svelte.dev/docs/kit/migrating-to-sveltekit-3) et
[adapter Node](https://svelte.dev/docs/kit/adapter-node).

## Périmètre réellement livré

| Pages SvelteKit | Données / opérations |
|---|---|
| `/` | Une, événements, catégories, derniers jours d'archives |
| `/direct`, `/categories/[id]` | Articles du jour, filtre et pagination |
| `/evenements`, `/evenements/[id]` | Index dérivé existant, couvertures et provenance |
| `/archives`, `/archives/[day]` | Jours connus et articles historiques |
| `/rapports/[day]`, `/editions-v2/[day]/[kind]` | Documents existants, sans génération |
| `/connexion` | Formulaire vers l'authentification Flask existante |
| `/administration` | Navigation privée vers les outils |
| `/administration/sources` | Consultation des 50 premières sources et observations |
| `/administration/radio` | Consultation des observations et métriques connues |
| `/healthz` | Processus HTTP disponible, pas une preuve de santé métier |

Les mutations Source Manager, découverte, illustrations, configuration, flashs et
contrôles Radio Studio restent **Flask**, accessibles par des liens explicites
« Ouvrir l'outil complet ». Aucun écran ne simule une opération absente.
La pagination administrative native et la migration complète des outils sont reportées.
Le passage vers un outil Flask recharge le document et quitte le lecteur Svelte ;
la persistance audio est garantie entre les pages natives, pas entre deux applications.
Les endpoints Android historiques restent inchangés.

## Inventaire et contrats API

| Contrat | Usage / confidentialité |
|---|---|
| `/api/articles`, `/api/events[/<id>]` | Historiques publics, inchangés (Android compris) |
| `/api/public/site` | Nouveau : catégories configurées et chemins `/stream/*`, sans URL RSS ni serveur Icecast |
| `/api/public/articles?date=YYYYMMDD&page=1&per_page=20&category=…` | Nouveau : projection textuelle explicite, image locale approuvée |
| `/api/public/events[/<id>]` | Nouveau : même index validé, avec projection d'image autorisée |
| `/api/public/archives?page=1&per_page=20` | Nouveau : dates réellement présentes, présence articles/rapport/éditions |
| `/api/public/documents/<day>/<kind>` | Nouveau : titre et nœuds de texte typés ; `report`, `matin`, `midi`, `soir` |
| `/api/auth/login` GET | Nouveau bootstrap préauthentification, token CSRF lié au cookie Flask, no-store |
| `/admin/login` POST, `/admin/logout` POST, `/admin/csrf` GET | Authentification/expiration/CSRF existants |
| `/api/admin/sources`, `/api/admin/radio` GET | Résumés privés natifs, pas de cache |
| `/api/admin/sources/*`, `/api/admin/discovery/*`, `/api/admin/media/*`, `/api/admin/radio*`, `/config/*`, `/api/flash*` | Outils Flask conservés ; opérations sensibles auth/CSRF |
| `/stream/*`, `/media/images/*`, `/assets/news-fallback.svg`, `/audio/*` | Proxy audio et médias historiques ; aucun téléchargement externe par Node |

Les nouvelles listes renvoient `status`, `observed_at`, `total`, `page`, `per_page`
et leur collection (`articles`, `events`, `days`). `per_page` 1–50, page 1–10000 ;
paramètres inconnus, répétés ou excessifs refusés. Articles : `id`, `hash`, `title`,
`original_title`, `summary`, `link`, `source`, `category`, dates brutes/fiables,
`language`, `image`. Les dates fiables sont affichées en Europe/Paris ; une date
naïve reste brute, sans fuseau inventé. Jour courant explicite Europe/Paris.

Lecture plafonnée : articles 4 MiB / 2000 enregistrements par jour, document 512 KiB,
4000 nœuds / profondeur 16, archives 10000 fichiers par répertoire. Fichiers spéciaux,
symlinks et données corrompues sont refusés ; aucune écriture ni réparation automatique.
Les documents passent par le sanitizer Markdown existant puis une projection typée,
pas un scraping de pages Flask. Svelte affiche les textes, jamais `{@html}`.

## Composants et identité

`src/lib/components` : cartes articles/événements, attribution, documents, pagination,
états et lecteur. `src/lib/server/api.ts` : client serveur à origine fixe, chemins
allowlistés. Types et liens sûrs sont séparés du rendu. Aucun framework UI/CDN.
Palette papier/ink/teal/cuivre, titres serif système, grille éditoriale responsive,
CSS commun et thème clair/sombre. Le registre existant fournit les catégories.
Français complet pour les parcours principaux, dictionnaire anglais et fallback
français pour quelques libellés historiques ; extension japonais préparée par locale.
Aucune traduction d'articles n'est lancée par la consultation.

## SSR, cache et erreurs

SSR sur les pages éditoriales, hydratation puis navigation SvelteKit. HTML `private,
no-store` (nonce par réponse, langue/thème cookies). Client serveur public : 16 entrées,
TTL 15 secondes ; dernier résultat signalé périmé jusqu'à 60 secondes si backend en
échec. Une révocation d'image peut donc attendre l'expiration de ce cache ; la route
média Flask reste l'autorité finale et renvoie son fallback lorsque nécessaire.
Pas de cache/stale pour l'administration. Quatre requêtes backend actives au maximum,
timeout 3 s, corps JSON 1 MiB, redirections interdites. Les erreurs ne répercutent pas
les messages internes. Pas de rafraîchissement automatique massif du direct.
Une absence d'index événementiel reste une absence de données, sans reconstruction.

## Lecteur persistant

Un seul `audio` dans le layout racine ; aucun autoplay. Lecture après action utilisateur,
pause, volume et deux canaux `/stream/nova` et `/stream/nova-android` (ou mounts
configurés sûrs). Le composant ne remonte pas entre les pages natives. Trois tentatives
de reconnexion au maximum, délais 1/3/8 secondes ; pause/démontage annulent le timer.
Le changement de canal est explicite. « Lecture » est l'état du navigateur, jamais
une preuve de disponibilité Icecast ni une garantie d'écoute sans interruption.

## Authentification, CSRF et sécurité

Flask conserve son cookie HttpOnly, sa durée de session, son rate limit, HTTPS et CSRF.
Node ne signe aucun cookie admin et ne reçoit aucun secret de session. Il ne transmet
que `atlas_admin` pour trois lectures privées allowlistées. Chaque requête de page
administrative revérifie `/admin/csrf` ; le navigateur revérifie aussi après navigation
et toutes les 30 secondes. Les mutations Flask revérifient toujours elles-mêmes
l'autorisation. Aucun token CSRF n'est inclus dans les pages publiques SSR.
Le bootstrap de connexion est volontairement public et no-store, lié à une session
préauthentification, comme le formulaire historique. Aucun CORS permissif.

CSP nonce de SvelteKit pour l'hydratation réelle : scripts self + nonce, aucun
unsafe-eval/unsafe-inline ; styles, images, médias et connexions self ; object none,
base self, frame-ancestors none, form-action self. L’unique style inline de l’annonceur
accessible de navigation Kit reçoit un hash exact (`style-src-attr unsafe-hashes`) ;
un test lie ce hash à la version épinglée, sans autoriser les autres styles inline.
Les listeners d’erreur d’image sont attachés après montage pour éviter les handlers
SSR que le compilateur Svelte génère autrement. En-têtes nosniff, DENY,
Referrer-Policy same-origin, Permissions-Policy caméra/micro/géolocalisation refusés.
Pas de HSTS global HTTP : terminer TLS et configurer HSTS sur le proxy HTTPS.
Images : chemins locaux contrôlés, attributions conservées, pas de hotlink. Liens
HTTP(S) sans credentials seulement. Les contenus RSS/LLM sont échappés.

## Développement et tests

```sh
cd frontend
npm ci --ignore-scripts
npm run check
npm run lint
npm test
npm run audit
npm run build
node tests/budget.mjs
npx playwright install --with-deps chromium --only-shell
npm run test:browser
```

Pour un serveur Python **de développement isolé**, définir explicitement
`FLASK_ORIGIN=http://127.0.0.1:5055 ATLAS_PUBLIC_SCHEME=http HOST=127.0.0.1 PORT=5173`
puis `npm start`. Les pages publiques SSR fonctionnent ; les chemins navigateur
`/api`, `/admin`, `/stream`, `/media`, `/assets`, `/config` nécessitent le proxy
same-origin décrit ci-dessous. Le mode dev ne lit pas automatiquement un `.env` privé.
Les tests navigateur utilisent `tests/start.mjs`, faux Flask synthétique et proxy local,
jamais un serveur utilisateur. La fixture JPEG est une géométrie générée pour ce sprint.
Les commandes de benchmark ne lancent aucun producteur.

## Compose et reverse proxy

```sh
# Installation neuve uniquement, selon DOCKER_DEPLOYMENT.md : initialisation interactive.
python -m deploy.init --admin-username votre-identifiant
# Pas de démarrage News/Radio pour cette prévisualisation.
docker compose --env-file deploy/local/compose.env -f docker-compose.yml \
  -f docker-compose.frontend.yml --profile web --profile frontend config --quiet
docker compose --env-file deploy/local/compose.env -f docker-compose.yml \
  -f docker-compose.frontend.yml --profile web --profile frontend up -d --build
```

Le gateway écoute **127.0.0.1:5173**, Node n'a aucun port publié. Réseau
`frontend_api` interne : gateway .2, Node .3, Flask .4 sur 172.30.42.0/28. Modifier
ensemble `ATLAS_FRONTEND_SUBNET`, `ATLAS_FRONTEND_GATEWAY_IP`, `ATLAS_FRONTEND_IP`,
`ATLAS_FRONTEND_BACKEND_IP` si conflit de sous-réseau. Seuls ces deux pairs exacts
sont déclarés proxies de confiance Flask dans l'overlay. Ne pas élargir à tous Docker.
Aucun volume/secret applicatif dans Node/nginx. Images Node24/nginx1.30 épinglées par
digest, non-root, read-only, capacités supprimées, logs bornés, SIGTERM 30 s.
Healthchecks HTTP prouvent seulement une disponibilité du processus.

`ATLAS_PUBLIC_SCHEME=https` par défaut : terminer TLS sur nginx hôte/Tailscale Serve,
transmettre vers le gateway loopback, conserver Host. Le gateway écrase les en-têtes
Forwarded/X-Forwarded-* clients ; le schéma est un enum **opérateur fixe**, jamais une
déduction d'un en-tête non fiable. Le proxy hôte doit filtrer les Host et ne pas exposer
les ports internes. Node ne contacte qu'un `FLASK_ORIGIN` serveur fixe, sans credentials.
Pas de proxy ouvert ni de `PUBLIC_` contenant une URL backend privée.
Pour une prévisualisation HTTP locale explicite uniquement, mettre
`ATLAS_PUBLIC_SCHEME=http` et `ATLAS_COOKIE_SECURE=0` ; sinon l'administration reste
fermée en HTTP. Ne jamais reprendre ce réglage sur un accès réseau non maîtrisé.

Routage : `/api/*`, `/admin/*`, `/assets/*`, `/media/*`, `/stream/*`, `/config*`,
`/preferences`, `/audio/*`, `/radio-studio`, `/editions/*`, `/live[.html]`, `/index.html`
restent Flask ; seules les routes natives listées ci-dessus et `/_app/*` sont
transmises à SvelteKit. Les autres chemins, dont `YYYYMMDD_*.html`, restent Flask
(pas réinterprétés comme API). Le profil Flask seul et son port historique ne changent pas.

## Validation, budgets et limites

Budget CI de **toutes** les routes client : JS gzip <=100 KiB et CSS gzip <=15 KiB,
mesuré par `tests/budget.mjs` (compression par fichier, pas un poids de première visite).
Mesures fixtures et résultats sont consignés dans `docs/measurements/frontend/`.
Pas de score Lighthouse annoncé. CPU/mémoire de production non mesurés.
Chromium desktop 1440×1000 et mobile 390×844 : navigation, audio simulé persistant,
canaux, erreurs, filtres, thème/langue, images, auth/session/CSRF et CSP. axe sur le direct
refuse les violations sérieuses/critiques ; il ne remplace pas une revue manuelle
clavier/lecteur d'écran/contraste sur tous les outils historiques. Audio et panne réseau
sont simulés : pas d'écoute Icecast réelle. CI Docker vérifie en plus Node/nginx avec
Flask réel et données synthétiques, sans News/Radio/LLM/TTS ni source externe.

`npm audit` est exécuté au seuil high. Le lockfile épingle temporairement `minimatch` 11.0.5 via override,
avec lint/tests vérifiés, pour retirer l’avis modéré de l’outillage. Quatre avis faibles
transitifs restent liés à `deepmerge` 4.3.1 (fusion de configuration de build) ; aucune
version corrigée compatible n’est disponible au moment du sprint. Ne pas appliquer
le downgrade Kit préhistorique proposé par l’audit automatique. Revoir le lockfile et les avis avant déploiement.

## Rollback

Arrêter uniquement frontend/frontend-gateway via la même composition, remettre le
reverse proxy vers Flask (port loopback historique), puis utiliser la composition de
base. Aucun export, migration ou restauration de données nécessaire : aucun producteur
ni format de stockage n'a changé. Conserver les volumes et secrets existants ; ne pas
utiliser `down -v` sur une installation réelle. Les nouvelles API sont additives et
peuvent rester présentes lorsque l'interface historique est utilisée.
