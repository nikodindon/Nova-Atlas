# Secrets et administration — Sprint 01A

## Initialiser l'identité unique

L'administration est **fermée par défaut**. Aucun compte, mot de passe ou secret
n'est livré dans Git. Le web lit exclusivement ces variables dans son environnement :

| Variable | Fonction |
|---|---|
| ATLAS_ADMIN_USERNAME | Identifiant de l'administrateur unique |
| ATLAS_ADMIN_PASSWORD_HASH | Hash Werkzeug scrypt:32768:8:1, jamais mot de passe en clair |
| ATLAS_SESSION_SECRET | Secret de signature aléatoire d'au moins 32 caractères |
| ATLAS_COOKIE_SECURE | 1 par défaut ; HTTPS requis pour login et routes privées |
| ATLAS_TRUSTED_PROXIES | CIDR des pairs proxy immédiats autorisés à annoncer HTTPS ; vide par défaut |

Préparer le fichier local sans afficher de credentials :

```sh
.venv-baseline/bin/python -m modules.web.admin_setup --output .env.admin
```

Le programme demande identifiant et mot de passe avec confirmation (16 caractères
minimum), génère le hash et un secret aléatoire, écrit en mode 0600 et refuse
d'écraser un fichier existant. Le mot de passe n'est ni écrit ni affiché.
`.env.admin` est ignoré par Git. Le fichier contient des affectations compatibles
avec un shell POSIX, correctement citées (notamment les `$` du hash).
Ne jamais copier ces valeurs dans une commande versionnée, un ticket ou un log.

Le logiciel **ne charge pas automatiquement .env**. Dans le shell du service,
exporter le fichier privé avant le démarrage normal autorisé :

```sh
set -a
. ./.env.admin
set +a
```

Cette procédure ne démarre rien. Pour systemd/Compose ultérieurs, injecter les
variables avec leur mécanisme propre et vérifier les règles de citation ; le
fichier produit est d'abord destiné au shell. Aucune configuration d'une
installation existante n'a été modifiée par le sprint.

Une variable obligatoire absente, un secret trop court ou un hash non conforme
interdit la connexion. Pour changer le mot de passe ou renouveler la signature,
préparer un autre fichier privé, puis planifier le redémarrage normal du web :
les sessions sont invalidées au redémarrage. Ne pas modifier le hash via le web.

## Connexion, session et CSRF

Ouvrir `/admin/login` en HTTPS ; connexion via formulaire protégé contre le CSRF.
Le cookie signé ne contient que l'identifiant opaque de session, le jeton CSRF
et le marqueur de session permanente. L'autorisation est vérifiée dans un registre
serveur ; expiration absolue après une heure, sans prolongation par navigation.
Le POST `/admin/logout` supprime le registre et le cookie : rejouer l'ancien
cookie ne redonne aucun accès. Maximum 32 sessions actives.

Cookies HttpOnly et SameSite=Strict ; Secure par défaut, toujours Secure sur une
requête reconnue HTTPS même si l'exception HTTP locale est activée. Login et pages
privées ne sont pas mis en cache. Les API privées répondent 401 sans session,
403 si CSRF invalide ou transport HTTP interdit, 429 en cas de limite dépassée.
Les navigations HTML anonymes sont redirigées vers le login en HTTPS.

Dix échecs de connexion dans cinq minutes imposent une attente globale : changer
X-Forwarded-For ou l'identifiant n'évite pas la limite. Flash et reload sont bornés
à cinq demandes/minute chacun ; une seule génération flash active, 32 jobs conservés.
Cette protection globale peut aussi provoquer un refus temporaire pour l'admin
légitime sous attaque : filtrage/rate limit au proxy reste utile.

Le serveur exige `X-CSRF-Token` ou le champ de formulaire `csrf_token` pour **toutes**
les mutations privées par cookie, y compris déconnexion. Le HTML privé authentifié
injecte un jeton ; le helper statique enrichit les fetch de même origine et les
formulaires. Depuis une page publique sans token, il demande `/admin/csrf` seulement
au moment d’une action privilégiée. Aucun token ou formulaire admin dans le HTML
public, même authentifié. Le login possède son propre champ caché ;
le jeton est renouvelé à la connexion. Aucun jeton n'est envoyé à un hôte externe.
Pour un futur client API : établir la session par le formulaire, obtenir le jeton
via `GET /admin/csrf` authentifié, envoyer cookie et en-tête sur chaque mutation.
Il n'existe pas de bypass par Bearer, X-Admin, X-Forwarded-User ou adresse Tailscale.
Une authentification machine dédiée appartient à une évolution ultérieure.

## Réseau, reverse proxy et Tailscale

HTTPS est le chemin par défaut. Le backend n'accorde aucune confiance automatique
aux en-têtes forwarded. Définir ATLAS_TRUSTED_PROXIES avec le CIDR exact du reverse
proxy immédiat ; celui-ci doit écraser X-Forwarded-Proto et le port backend doit
être inaccessible aux autres pairs. Seul `https` provenant d'un pair autorisé
change le schéma ; host, IP client et identité forwarded restent ignorés.
Ne jamais déclarer `0.0.0.0/0`, `::/0` ou tout le réseau Tailscale comme proxy.

Tailscale contrôle l'accès au réseau ; il ne remplace pas l'identité applicative.
Utiliser une terminaison HTTPS ou, pour le développement HTTP explicitement privé,
ATLAS_COOKIE_SECURE=0. Cette exception autorise le transport de cookie en clair sur
HTTP : la réserver à loopback/tunnel privé maîtrisé, jamais à une exposition Internet.
Un en-tête HTTPS fourni par un client non autorisé ne suffit pas.
L'application web actuelle doit garder **un seul processus**, threads possibles :
registres de sessions, limites et jobs sont en mémoire, perdus au redémarrage et
non partagés entre workers. Avant plusieurs workers, ajouter un stockage commun
transactionnel ; les protections actuelles ne doivent pas être déployées en
multi-workers. Le debugger interactif Flask est désactivé, même avec --debug.

## Inventaire exhaustif des routes Flask

Toute route nouvelle est privée par défaut ; la liste publique est explicite.
Les routes statiques Flask implicites sont désactivées.

| Méthodes | Routes | Politique Sprint 01A |
|---|---|---|
| GET/HEAD | /, /archives, /index.html, /live, /live.html | Consultation publique |
| GET/HEAD | /editions/<filename>, /<filename> | Éditions matin/midi/soir et rapports datés ; assets racine limités ; confinement des chemins/symlinks |
| GET/HEAD | /stream/<mount> | Lecture radio publique, proxy existant ; Politique interne dédiée (voir HTTP_SECURITY.md) |
| GET/HEAD | /api/articles | Contrat Android conservé ; date YYYYMMDD valide requise |
| GET/HEAD | /api/subscription/status | Statut existant par device_id conservé ; ce paramètre ne constitue pas une identité sécurisée |
| GET/HEAD | /api/preferences | Lecture publique des seuls hidden_categories et updated_at, nécessaire au filtre existant |
| GET/POST | /admin/login | Formulaire public, HTTPS, CSRF, hash et limitation ; 503 si identité non configurée |
| GET/HEAD | /admin/csrf | Session admin obligatoire |
| POST | /admin/logout | Session admin et CSRF |
| GET/HEAD | /config, /config/simple, /config/yaml | Session admin, secrets non rendus |
| POST | /config/save, /config/yaml/save, /config/restart | Session admin, CSRF, validation ; reload limité |
| GET/HEAD | /config/feeds, /config/feeds/page | Session admin ; URLs privées masquées |
| POST | /config/feeds/add, /config/feeds/remove, /config/feeds/reset, /config/feeds/move, /config/feeds/toggle, /config/feeds/category | Session admin, CSRF et entrées bornées |
| GET/HEAD | /preferences | Session admin : les préférences sont globales à l'installation |
| POST | /api/preferences/toggle, /api/preferences/reset | Session admin et CSRF |
| POST | /api/flash | Session admin, CSRF et limites ; plus de génération anonyme |
| GET/HEAD | /api/flash/status/<job_id>, /audio/flash_<job_id>.mp3 | Session admin ; fichier audio dans la queue autorisée |

OPTIONS, lorsqu'automatique, ne produit aucune mutation et les endpoints privés
exigent toujours une session. Aucun GET ne déconnecte, écrit ou lance une génération.
Les GET publics lisent les fichiers locaux et le proxy stream contacte Icecast ;
ils ne déclenchent pas de collecte/génération.

## Configuration et incompatibilités volontaires

Le web continue de lire/écrire YAML et JSON dans les répertoires configurés, sans
fallback vers un autre checkout. La factory `create_app(config, root=...)` n'écrit
rien et ne démarre aucun service. Les chemins sont fournis par la configuration
locale de confiance et ne sont plus modifiables via la route YAML.

Éditions de configuration limitées aux champs de l'interface, un champ typé par
requête ; remplacement de section entière interdit. Nombres bornés, provider/voix/
URL/hôte/mount validés ; identifiants et credentials d'URL refusés dans les nouvelles
URL de backend. La validation des destinations RSS/articles est centralisée depuis 01B (voir HTTP_SECURITY.md).
Le champ Icecast est vide et de type password ; vide ou masque signifie « garder
le secret actuel ». Les autres clés de secrets et les URL contenant credentials
ne sont jamais rendues ; les URL de backend masquées laissées vides sont conservées.
Modifier les credentials historiques d'URL directement dans le fichier privé.
Les écritures YAML/JSON de configuration sont atomiques (temporaire 0600 + replace),
avec verrou de processus sur la mise à jour YAML pour les sauvegardes parallèles.
Les préférences et flux conservaient déjà une écriture atomique.

Les URL RSS historiques contenant userinfo ou un paramètre de type token/api_key
sont masquées dans la réponse admin et se gèrent localement. Depuis 01B, le client
externe refuse les URL avec userinfo ; les paramètres explicitement présents
dans les URL restent transmis à leur origine, sans propagation de headers sensibles. L'ajout de nouvelles URL contenant ces credentials par le web est refusé.
Les messages d'erreur ne renvoient pas les exceptions brutes ; les logs de parsing
des flux ne reproduisent plus les lignes malformées.

Les clients qui modifiaient préférences ou lançaient des flashs sans session doivent
être adaptés. Les lectures Android restent publiques ; aucun accès au dépôt Android
n'a été nécessaire. `mp3_path` du statut flash devient une URL protégée, au lieu d'un
chemin serveur. Les fichiers HTML arbitraires, YAML, JSON, JS et logs présents dans
site/ ne sont plus servis ; seuls rapports/éditions rendus et assets autorisés le sont.

## Risques résiduels et incident historique

Le Sprint 01B protège les connexions RSS/articles et limite le proxy Icecast :
voir [HTTP_SECURITY.md](HTTP_SECURITY.md), notamment les limites et refus conservateurs.
Les autres PR corrigeront scheduler/verrou/dates/reprises/audio. Les templates historiques et leur eval/innerHTML demandent
encore un audit XSS/CSP complet : CSRF n'est pas une défense contre un script déjà
exécuté dans la même origine. Le rendu des valeurs du formulaire YAML et les
littéraux JavaScript du gestionnaire de flux sont durcis ici, sans refonte globale.
L'accès système local, les configurations sur disque et la chaîne de collecte
restent de confiance ; ne pas exposer ce service comme produit multi-utilisateurs.
Le statut subscription reste un contrat historique, pas une validation Play Billing.
Pas de tests live du reverse proxy, TTS ou de continuité Icecast dans cette PR.

Le propriétaire et le blueprint signalent une clé OpenRouter exposée dans la PR
publique #1 du 14 avril 2026. Aucune valeur reproduite, aucun credential privé lu.
Le propriétaire doit révoquer la clé chez OpenRouter, renouveler les accès et examiner
l'usage du compte. Aucune révocation réalisée ici ; l'historique Git reste intact.
Les autres variables de .env.example (LLM, DB, Icecast) restent préparatoires V2,
non chargées par l'actuel loader YAML.

Références : [Flask — sécurité](https://flask.palletsprojects.com/en/stable/web-security/),
[Werkzeug — scrypt](https://werkzeug.palletsprojects.com/en/stable/utils/#werkzeug.security.generate_password_hash),
[Flask — proxy](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/).

Sprint 01G : scripts statiques et CSP sans unsafe-eval ; HSTS optionnel
`ATLAS_HSTS_MAX_AGE=0` par défaut. Voir WEB_SECURITY.md avant configuration du proxy
ou service direct des exports. Authentification et contrôles réseau restent distincts.
