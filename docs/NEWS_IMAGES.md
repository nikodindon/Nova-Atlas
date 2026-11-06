# Illustrations locales — Sprint 09

Le pipeline est optionnel et désactivé par défaut. Les articles JSON, leurs hashes
et les contrats Android restent inchangés. Un globe SVG local neutre sert de
fallback, même hors ligne. Aucun appel LLM ni image générée par IA.

## Architecture et sources

`modules/media/candidates.py` extrait au plus cinq candidats par document :
MRSS thumbnail/content, enclosure image, itunes:image, liens Atom image ; Open
Graph, Twitter, image principale explicitement balisée et JSON-LD Article.
Les URL relatives utilisent l'URL finale contrôlée. Aucun script n'est exécuté.
Les métadonnées RSS optionnelles restent dans FeedEntry et son cache privé ; les
anciens caches sans ce champ restent lisibles. Un 304 ne force pas un téléchargement.

Lorsque le mode est activé, `_fetch_content` capture les métadonnées du HTML déjà
récupéré, sans second téléchargement ni téléchargement d'image. L'administration
peut rattacher ces candidats à l'article publié via « Candidats RSS en cache ».
L'inspection HTML manuelle est une opération réseau distincte et explicite.
Les candidats inconnus ne sont pas préparés ni publiés.

`commons.py` utilise exclusivement l'[API Imageinfo officielle](https://www.mediawiki.org/wiki/API:Imageinfo),
`prop=info|imageinfo`, URL/SHA1/timestamp/size/mime/extmetadata, version de métadonnées
latest et langue en. Recherche namespace File, cinq résultats maximum, originaux
JPEG/PNG/WebP de 4 MiB maximum. Les pages, révisions et empreintes identifient la
ressource examinée. Aucun endpoint arbitraire configurable, aucune clé nécessaire.
Les textes HTML d'extmetadata sont rendus inertes. Ce contrat est testé sur des
réponses synthétiques ; aucune recherche Commons réelle effectuée pendant le sprint.

## Droits et politique effectivement livrée

Une URL, un og:image ou une déclaration RSS **ne confère aucun droit**.
La politique initiale accepte seulement CC BY 4.0, avec son URL officielle,
auteur, attribution obligatoire, ressource, taille, SHA1 et révision Commons.
L'API expose une déclaration documentaire, pas une garantie juridique universelle.
Voir [Commons : réutilisation](https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia)
et [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

L'opérateur doit examiner les droits de tiers (personnes, marques, œuvres, etc.)
et reconnaître cette revue avant d'approuver. Auteur/crédit, page source, lien de
licence et transformations sont affichés. Une légende ne peut supprimer le libellé
« Illustration générique ». La licence ne garantit pas la pertinence documentaire.

CC BY-SA, NC, ND, CC0 et domaine public ne sont **pas pris en charge** par cette
première politique. Ils sont refusés pour publication, sans prétendre que ces
licences interdisent tous les usages. Les variantes ont des obligations distinctes ;
les affirmations de domaine public exigent une analyse juridictionnelle reportée.
Aucune attribution automatique de licence à un domaine de presse.

États : `rights_unknown`, `rights_verified`, `rights_restricted`,
`rights_conflicting`, `verification_failed`, `approved`, `rejected`.
`rights_verified` signifie « satisfait la politique documentaire implémentée ».
L'approbation porte sur la ressource, ses preuves, les dérivés, la légende et le
contexte article/événement précis. Changer les métadonnées ou la légende invalide
l'approbation ; changer une association événementielle invalide son contexte.
Une image d'article n'est jamais automatiquement propagée à un événement.

Seules les **illustrations génériques approuvées** sont publiables. Photos
prétendant représenter l'événement et mode automatique `trusted_illustrations`
reportés : absence de preuve suffisante de lieu/date/événement. Aucun transfert
implicite après fusion ou séparation d'événements.

## Configuration et revue

Ajouter explicitement au config.yaml privé, puis recharger les processus selon
la procédure existante (la configuration du service web est lue à son démarrage) :

```yaml
media:
  mode: disabled       # disabled | suggest | approved_only
  commons_enabled: false
```

Exemple complet sans credentials : `config/media.example.yaml`.
`disabled` : aucune recherche, préparation ou publication, fallback uniquement.
`suggest` : découverte/préparation manuelles possibles, aucune publication publique.
`approved_only` : mêmes opérations, seules les ressources approuvées sont affichées.
`commons_enabled: true` autorise une recherche explicite, jamais un appel automatique.
Aucun mode implicite payant, aucun téléchargement au rendu public.

Dans `/config/feeds/page`, section Illustrations : choisir un article/événement,
chercher Commons ou consulter les candidats RSS/HTML, lire droits et provenance,
préparer, vérifier la miniature et sa légende, cocher la revue, approuver.
Refuser/désactiver retire immédiatement la projection dynamique. La preuve doit
avoir moins d'une heure pour préparer et moins de 24 h pour publier. Après
expiration, fallback jusqu'à une revérification explicite ; pas de vérificateur
réseau automatique. Une révocation externe non encore observée demeure un risque.

## API

Toutes les routes `/api/admin/media*` exigent session admin ; les POST exigent
CSRF (`X-CSRF-Token`, mécanisme existant). JSON strict, 8 KiB maximum.

| Route | Contrat |
|---|---|
| GET `/api/admin/media?page=1` | 20 candidats/page, version SHA256, état et binding d'approbation |
| GET `/api/admin/media/targets` | Contextes d'articles récents/index événementiel, sans modification |
| POST `/api/admin/media/search` | query, target `{kind,id,fingerprint}` ; Commons configuré |
| POST `/api/admin/media/discover` | target, page=false : cache ; page=true : inspection HTML explicite |
| POST `/api/admin/media/cancel` | Annulation coopérative dans ce processus |
| POST `/api/admin/media/<id>/refresh` | Relecture des preuves Commons |
| POST `/api/admin/media/<id>/prepare` | version courante requise |
| POST `/api/admin/media/<id>/approve` | version, binding exact, ack=true |
| POST `/api/admin/media/<id>/caption` | version, caption ; annule l'approbation |
| POST `/api/admin/media/<id>/reject` ou `/disable` | version ; conserve les fichiers et l'audit |
| GET `/api/admin/media/<id>/preview/320.jpg` | Miniature privée déjà préparée |
| GET `/api/media?kind=article&id=…&fingerprint=…` | Projection publique optionnelle ou fallback, aucun téléchargement |
| GET `/media/images/<approval>/320.jpg` | JPEG local validé ou fallback, tailles 320/768 seulement |

Conflit de version/contexte : HTTP 409, recharger. Un succès de recherche n'approuve
rien. Les anciennes API articles/événements restent inchangées. Pas d'URL publique
permettant de télécharger une destination arbitraire. Réponses media `no-store`.

## Réseau et ressources

`MediaService` réutilise ExternalHTTPClient (DNS validé, connexion épinglée,
TLS, chaque redirection revalidée). Ports/schémas/credentials/réseaux internes,
CGNAT/Tailscale et métadonnées restent refusés. Aucun hotlink navigateur.
Une opération à la fois par stockage POSIX partagé, trois opérations/minute,
quatre requêtes maximum, 25 secondes de budget réseau, huit secondes/requête,
deux redirections, corps 4 MiB. Ces plafonds sont conservateurs et fixes dans cette
tranche ; les agrandir demande une revue. Le journal HTTP media participe aux
embargos par hôte des autres moteurs ; Retry-After/429 persistant, sans bypass.
DNS libc et annulation inter-processus conservent leurs limites historiques.

Pillow 13.0.0 est épinglé. Décodage dans un processus dédié : mémoire virtuelle
256 MiB, CPU cinq secondes, timeout parent dix secondes, environnement réduit
sans credentials. Entrées JPEG/PNG/WebP uniquement, 8 millions de pixels,
8192 pixels par axe, animation/SVG/format inconnu/troncature refusés. Le MIME ne
fait pas preuve : décodage complet et vérification du format. Orientation corrigée,
EXIF supprimés par nouveau raster RGB, re-encodage JPEG, tailles bornées
320×180 et 768×432, sans agrandissement. Le SVG fallback est un asset local écrit
pour ce projet, jamais un SVG externe. Limites OS Linux, pas une sandbox universelle.

## Stockage, intégrité et Docker

`data/news_media/manifest.json`, version 1, privé ; verrou `media.lock` stable,
transactions lecture/version/écriture, flush/fsync/rename. `network.lock` coordonne
les opérations réseau ; ne supprimer aucun verrou en fonctionnement. Les services
web/news/radio partagent déjà le même volume POSIX data et UID/GID Compose ; aucun
nouveau port ou montage requis. Le worker est dans l'image, Pillow dans son lock.
Les originaux ne sont jamais servis. `originals/<sha256>.bin` et
`variants/<artifact-id>/{320,768}.jpg` sont privés, immuables et dédupliqués.
Les binaires sont publiés par renommage d'un répertoire préparé avant le commit
du manifeste. Crash avant manifeste : fichiers orphelins, aucune publication.
Un manifeste corrompu est conservé et bloque les mutations, fallback public.
Une empreinte manquante/incorrecte bloque l'affichage.

Plafonds : 128 candidats, huit versions historiques/candidat, 1000 décisions,
manifest 2 MiB, 256 MiB de stockage et 4096 fichiers. À la limite : refus explicite,
pas d'effacement implicite d'une approbation. Pas de politique universelle NFS/SMB.
Une vérification à l'affichage évite les binaires corrompus mais a un coût disque ;
ce prototype ne prétend pas servir des millions d'images sans cache dédié.

## Maintenance, export et rollback

Commandes **hors ligne**, avec chemin explicite, dry-run par défaut :

```sh
python -m modules.media cleanup --data /chemin/test/data
python -m modules.media cleanup --data /chemin/test/data --apply --expected SHA256_DU_MANIFEST
python -m modules.media rebuild --data /chemin/test/data --id ID_CANDIDAT
python -m modules.media rebuild --data /chemin/test/data --id ID_CANDIDAT --apply --expected SHA256_DU_MANIFEST
python -m modules.media.benchmark
```

Cleanup ne touche que les orphelins non référencés de plus de sept jours et stages
abandonnés de plus d'un jour ; versions/approbations conservées protégées. Rebuild
vérifie les originaux locaux et ne télécharge rien. Une variante corrompue n'est
pas écrasée silencieusement : diagnostic/restauration opérateur nécessaire.

L'export statique copie les dérivés encore autorisés, sans originaux ni manifeste.
L'arbre `site/media/images` est remplacé atomiquement (renameat2 Linux, même FS),
y compris après révocation. Échec : ancien export conservé et erreur explicite.
Les URLs restent sur la même origine ; l'hébergement doit conserver le site à la
racine et la CSP. Un export déjà distribué reste une copie hors ligne : régénérer
et redéployer après désactivation, aucune révocation à distance garantie.

Sauvegarder data/news_media et config séparément, à producteurs arrêtés ou via
snapshot POSIX cohérent. Pour rollback, `mode: disabled`, redémarrage contrôlé du
web et régénération des exports. Les fichiers historiques/articles restent inchangés ;
aucune migration SQL ni suppression nécessaire. Revenir à l'image précédente est
possible en conservant ce répertoire à part.

## Validation et limites

Deux reproductions initiales échouaient : perte des métadonnées RSS et absence
de distinction explicite entre candidat et droit. Tests offline : formats,
preuves/licences, invalidation, corruption, crash, quotas, CSRF/auth, SSRF,
429, projection et exports. Chromium vérifie revue, JPEG local, CSP et radio
persistante. Le smoke RSS Docker teste aussi le vrai worker isolé et la révocation.

Mesures reproductibles : `docs/measurements/news-images-synthetic.json`, trois
aplats 1280×720, un processus par format. Ces mesures ne représentent ni la
couverture de presse ni un taux de conformité juridique. `peak_rss_kib` mesure
le maximum du worker Linux, pas la consommation totale de la stack.

Limites assumées : pas de licence universelle, pas de photo documentaire automatique,
pas de trusted_illustrations, pas de revue juridique automatisée, pas de recherche
Commons en arrière-plan, pas de refresh réseau public, pas de job persistant après
crash. Les candidats/artefacts réussis persistent ; relance manuelle sûre.
