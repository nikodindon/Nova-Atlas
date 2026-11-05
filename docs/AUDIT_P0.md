# Audit priorisé — Sprint 00

Référence des lignes : dev@71937360d4e8d776a3cc88ad2ff7b6f58e51bff0 (production inchangée).
Preuves statiques et caractérisations locales, pas un pentest ni une validation
d'exploitation distante. P0 : sécurité/intégrité/disponibilité urgente ; P1 :
fiabilité/contrat ; P2 : dette documentaire. Chaque correctif aura sa PR dédiée.

| Fichier/ligne | Gravité | Symptôme | Preuve statique | Reproduction possible hors ligne | Correctif recommandé | Test à écrire |
|---|---|---|---|---|---|---|
| main.py:237–295 | P0 | Scheduler consomme un cœur au repos | while True sans attente en fin de boucle news | Horloge et services simulés, arrêter après N tours ; pas de moteur réel | Attente interruptible bornée, slots datés et garde concurrence | Avance simulée, une tâche par slot, attente au repos |
| modules/core/llm_client.py:161–223 | P0 | Appels concurrents malgré verrou ; suppression du verrou d'un autre | exists puis open w ; priorité force après 30s ; exception renvoie True ; unlink sans propriétaire | Deux clients temporaires/barrière + erreur d'ouverture simulée | Verrou OS/exclusif, propriétaire, fail closed, aucune préemption d'appel actif | Deux processus en compétition, crash, timeout et libération étrangère |
| modules/fetch/atlas_fetch.py:292–298 | P0 | Dédup perd des entrées au changement de mois/année | Soustraction d'entiers YYYYMMDD | 20261101 - 20261031 = 70 pour un jour | Dates réelles et timedelta, horloge injectable | 31→1, année, bissextile, dates invalides |
| modules/web/atlas_web.py:1456–1607,1642–1813,1970–2082 | P0 | Configuration modifiable et génération coûteuse sans contrôle applicatif visible | Flask sans décorateur auth/CSRF ; routes écrivent YAML, flag et lancent thread | Futur client Flask avec factory et chemins temporaires, jamais run_server réel | Auth admin, autorisation par ressource, CSRF navigateur, limites débit/taille/concurrence | Anonyme refusé, CSRF absent refusé, admin valide autorisé, surcharge bornée |
| modules/web/atlas_web.py:1642–1660 ; modules/fetch/atlas_fetch.py:351–378 | P0 | Source arbitraire peut faire contacter le réseau privé (SSRF différé) | Seul préfixe http(s) vérifié ; requests suit redirections | DNS/transport simulés pour loopback, IPv6, privés, metadata, CGNAT et rebinding | Validation commune avant chaque connexion/redirection, filtrage réseau | Matrice IPv4/IPv6 et DNS changeant ; aucune connexion interdite |
| modules/fetch/atlas_fetch.py:611–641 | P0 | Erreur temporaire d'extraction devient « vue » et ne sera pas retentée | content vide → seen.add + existing_hashes.add | Test test_inaccessible_article_is_marked_seen ajouté | Statuts retryable/permanent, backoff et tentatives bornées | Timeout/429/5xx récupérés au cycle suivant, erreurs permanentes distinguées |
| modules/radio/bulletin_generator.py:538–555,860–866 | P0 | Bulletin tronqué publié après perte d'un segment | Échec TTS → continue ; seule liste vide est fatale | Test test_failed_audio_segment_is_currently_skipped ajouté | Manifest complet, retry ciblé/fallback, publication atomique | Échec au milieu interdit publication, ordre conservé après reprise |
| modules/web/atlas_web.py:1930–1967 | P1 | Pagination absente, limites négatives/non bornées | int(limit) puis articles[:limit], sans offset/curseur | Fixture de 3 articles, limit=-1 renvoie 2 en slicing | Bornes positives, taille max, curseur stable, date validée | limit négatif/énorme/invalide ; pages sans trou/doublon |
| modules/fetch/atlas_fetch.py:351–371,757–769 | P1 | Atom ignoré, RSS séquentiel ; échec confondu avec flux vide | find_all(item), boucle requests synchrone, exception → [] | Fixtures rss.xml/atom.xml : 1/0 entrée caractérisée | feedparser, résultat typé, concurrence bornée, ETag | RSS/Atom/namespaces/malformé/timeout/redirection simulés |
| main.py:340–371 ; modules/radio/bulletin_generator.py:58,237–243,839–847 | P1 | Calendriers multiples ; ancienne dépêche perçue fraîche ; validation permissive | post_hours forcé 24h ; sélection timestamp ; script invalide accepté | Horloge + vieux pub_date et timestamp récent ; YAML temporaire | Calendrier unique validé ; published/fetched distincts ; refuser script incomplet | Heures configurées, passage minuit/timezones, script trop court/balises absentes |
| modules/report/atlas_report.py:36,178–195 ; modules/web/atlas_web.py:1978 | P1 | Catégories récentes exclues de sections/flash | Boucles CATEGORY_ORDER et validation CATEGORY_ICONS statiques | Article dans catégorie absente des constantes | Registre de catégories commun avec fallback explicite | Chaque catégorie de feeds représentée dans sorties/flash |
| modules/web/atlas_web.py:2569–2588 | P0 à qualifier (XSS) | Exécution JS dynamique et injection HTML | innerHTML puis eval(s.textContent) ; exploitabilité à établir | DOM synthétique et contenu malveillant, sans serveur réel | Supprimer eval via modules explicites ; encodage/sanitization et CSP | Texte hostile reste inerte, scripts attendus fonctionnent sans eval |
| README.md:3 ; ROADMAP.md:6–12 | P2 | État/compteurs/architecture historiques présentés comme actuels | Site dit statique, 72 tests et ancien HEAD ; baseline réelle 185 | Comparaison sources + pytest local | Marquer historique et pointer docs V2/baseline | Revue documentaire ; pas de test miroir des textes |
| PR publique #1, config/config.yaml historique (ligne non consultée) | P0 incident externe | Credential OpenRouter exposé selon propriétaire/blueprint | Signalement fourni ; diff volontairement non affiché | Aucune utilisation de credential ; reproduction inutile | Révocation par propriétaire, rotation, revue usage ; historique séparé | Futur contrôle de secrets sans affichage de valeurs |

## Limites et ordre de correction

Traiter exposition/auth/SSRF, boucle/verrou puis intégrité dates/retries/audio dans
des PR distinctes ; préciser l'ordre selon exposition réelle du déploiement.
Aucune correction de production dans cet audit. Les caractérisations des défauts
réussissent aujourd'hui : elles documentent l'existant, elles ne prouvent pas sa
sécurité. Elles devront changer lors de la correction accompagnée de sa régression.
Le test pipeline bulletin existant n'affirme pas une sortie audio valide ; la
couverture actuelle ne valide ni continuité Icecast ni qualité TTS/LLM.

## Mise à jour Sprint 01A

Le socle de cette PR est dev@a1db974 (PR #2 fusionnée). Les numéros de ligne du
tableau précédent restent ceux de l'audit original ; les fonctions ci-dessous
identifient les correctifs malgré le déplacement des lignes.

| Constat | Statut 01A | Preuve / non-régression |
|---|---|---|
| Accès administratif sans auth/CSRF | Corrigé dans le périmètre Flask mono-processus | admin_security.install_admin_security : refus par défaut, sessions révoquées/expirées, CSRF ; tests sur chaque route sensible |
| Confiance automatique ProxyFix | Retirée | TrustedProxy ne reconnaît HTTPS que depuis CIDR explicites ; en-têtes d'identité ignorés, tests de spoofing |
| Mot de passe Icecast rendu, erreurs brutes et chemins serveur | Corrigé sur les surfaces traitées | safe_view, champ password vide, erreurs génériques, statut flash avec URL ; tests de non-divulgation |
| Écriture arbitraire de section YAML/chemins | Corrigée côté web | Allowlist typée, écriture atomique/verrou, secrets vides/masques conservés ; tests de rejet et de concurrence |
| Fallback de config vers checkout réel / fichiers arbitraires | Corrigé | create_app root explicite, allowlist assets, contrôle des symlinks ; tests temporaires sans service |
| Génération flash et restart sans limites | Bornés | Auth/CSRF, 5/minute, un flash actif ; threads simulés dans les tests |
| SSRF complet | Ouvert — 01B après revue | Auth ne valide pas DNS/destination ; proxy et extraction restent à auditer |
| XSS/HTML/JS eval | Partiellement durci, toujours ouvert | Valeurs config échappées et données feeds hors littéraux JS ; audit global/CSP encore requis |
| Scheduler, verrou LLM, dates, reprises, audio | Inchangés | Caractérisations Sprint 00 conservées ; correctifs dédiés à venir |
| Clé OpenRouter historique | Action propriétaire en attente de confirmation | Aucune révocation effectuée par l'agent |

Limite explicite : sessions/limites en mémoire dans un seul processus web. Pas de
protection multi-workers tant qu'un stockage partagé n'est pas implémenté.
Voir SECRETS_AND_ADMIN.md pour configuration, inventaire complet et incompatibilités.

## Mise à jour Sprint 01B

Socle : dev@3ccbb8d après fusion autorisée de la PR #3 ; aucune fusion automatique
de la PR SSRF. Le plan local non suivi est conservé.

| Surface | Statut 01B | Preuve / non-régression |
|---|---|---|
| SSRF flux RSS et extraction d'articles | Corrigé dans le client externe commun | modules/http/external.py : validation URL/IP, refus de tout RRset mixte, socket sur IP validée ; tests DNS rebinding et socket simulée |
| Redirections externes | Bornées et revalidées | Chaque saut repasse URL/DNS/pinning ; 5 max, boucles et downgrade refusés |
| TLS, taille, compression et délais | Bornés | Vérification CA/hostname/SNI, watchdog total, limites headers/corps/gzip ; parseur HTTP réel exercé sur socket fictive |
| XML comme voie réseau secondaire | Neutralisé | DTD, entités et réseau XML explicitement désactivés ; resolver de test prouve l'absence de chargement |
| Proxy Icecast | Politique interne distincte | Origine configurée, mounts exacts, aucun redirect/proxy ambiant ; tests d'origines internes et du contrat Flask |
| Auth admin/CSRF | Conservés | Suite Sprint 01A maintenue, ajout des refus d'URL littérales interdites à l'ajout de flux |
| Sources privées futures | Refusées par défaut | Pas d'exception globale ; proposition d'exception par source/origine/IP documentée, non implémentée |

La disponibilité réseau des flux n'est pas vérifiée ; compatibilité statique des
120 entrées et fixtures uniquement. Voir HTTP_SECURITY.md pour budgets, refus
conservateurs IPv6/ports, DNS libc non annulable, confiance envers le routage hôte
et configuration des services internes. XSS/CSP et autres P0 restent ouverts.

## Mise à jour Sprint 01C

Socle : dev@48bf5b4 après fusion explicitement autorisée de la PR #4, CI réussie.
Les cinq reproductions exécutées **avant** modification de production échouaient :
attente absente, fetch initial doublé, même heure confondue entre dates, posts
chevauchants et doublons à l'heure d'hiver. Elles passent après le correctif.

| Constat | Statut 01C | Preuve / non-régression |
|---|---|---|
| Boucle news active au repos | Corrigée | run_loop attend Event.wait à chaque tour, horloge/attente injectables ; tests idle et arrêt |
| Slots HH:MM et dernier post_hour sans date | Corrigés | Clés famille + date/heure civile, tests minuit/mois/année et posts le lendemain |
| Fetch initial doublé dans une fenêtre | Corrigé | Réservation du créneau initial et maintien de l'ordre initial fetch→autres tâches |
| Chevauchements et références de threads | Corrigés dans chaque scheduler | Admission atomique par famille, nettoyage et historique borné, tests de race avec barrière |
| Reload pendant traitement actif | Différé | Flag conservé jusqu'au repos, nouveau config transmis aux callbacks, historique conservé |
| Radio h24 forcée et bulletins simultanés | Corrigés | Heures cibles YAML, fenêtres :25/:55 conservées ; une génération à la fois, streamers simulés |
| Arrêt brutal des travaux planifiés | Arrêt normal coopératif | Event partagé, drain des tâches, contrôles superviseur simulés ; arrêt forcé/panne toujours hors garantie |
| Doublons après restart ou entre instances | Non garantis, politique historique explicite | Historique mémoire 1 024 tentatives, aucune persistance/catchup ; test de redémarrage simulé |

Les défauts du verrou LLM, dates de déduplication, reprises, segments audio et XSS/CSP
restent ouverts. Les protections 01A/01B sont conservées. Détails et limites :
SCHEDULER.md et testing/SPRINT_01C.md ; aucune mesure CPU ou radio live exécutée.

## Mise à jour Sprint 01D

Socle : dev@597a7a7 après fusion autorisée de la PR #5 et vérification de ses CI.
Six reproductions exécutées avant modification échouaient : création impossible
annoncée réussie, suppression par non-détenteur, vol par priorité, vol par ancienneté,
génération malgré échec et deux acquisitions lors d'une course exists/open.

| Constat | Statut 01D | Preuve / non-régression |
|---|---|---|
| Verrou témoin non atomique | Corrigé | LocalLLMLock, flock sur inode stable ; deux processus avec générations simulées, plusieurs threads même client |
| Libération étrangère | Refusée | Descripteur et bail PID/thread privés ; tests client/thread étrangers et enfant forké |
| Erreur d'acquisition autorisant un appel | Corrigée | Échec fermé open/flock/métadonnées/plateforme/budget ; transport jamais appelé |
| Préemption par priorité ou TTL | Supprimée | Attente monotone bornée, âge JSON ignoré, aucun retrait du fichier ni signal au détenteur |
| Fuite du bail sur exception/mort | Corrigée dans le périmètre OS | finally/context manager, SIGKILL d'un enfant de test, récupération du verrou ; handles fork/exec traités |
| Métadonnées partielles | Écriture atomique séparée | Lecteurs concurrents, JSON invalide sans autorité, erreurs d'écriture testées |
| Cache manqué simultanément | Revérifié après attente | Une génération pour deux appels concurrents de même clé ; hits toujours sans attente de ressource |
| Sérialisation future cloud | Frontière explicite | Seuls ollama/llama-server exclusifs ; provider inconnu refusé, ressources indépendantes possibles |

Pas de FIFO ni garantie de non-famine ; pas de coordination multi-hôtes, entre
répertoires distincts ou avec l'ancien protocole. Un timeout client ne certifie pas
l'arrêt effectif du calcul serveur. Le cache txt/meta reste non transactionnel.
Voir LLM_LOCKING.md. Dates de déduplication, reprises, segments audio et XSS/CSP
restent ouverts. Aucun modèle/fournisseur réel ni service de production lancé.

## Mise à jour Sprint 01E

Socle `dev@99bc9e4`, PR #6 fusionnée sur demande explicite, CI réussie.
Sept reproductions échouaient avant correction ; elles vérifient maintenant le
comportement corrigé (dont la caractérisation de l'article inaccessible).

| Constat | Statut 01E | Preuve / non-régression |
|---|---|---|
| Soustraction des entiers YYYYMMDD | Corrigée, sept dates J−6…J | `_recent`, `_load_seen`, `_save_seen` : dates calendaires ; mois, année, février/bissextile, dates futures/invalides et purge testés |
| Extraction vide définitivement marquée vue | Corrigée | `_process_item` : états persistants, seen uniquement après publication ; reprise même hors du flux et après redémarrage |
| Résumé vide publié | Corrigé pour les nouvelles publications | Validation avant checkpoint/publication, erreurs LLM/extraction récupérables ; réparation historique opt-in sauvegardée |
| Échecs HTTP indistincts | Corrigés pour les tentatives d'articles | `HTTPStatusError` sans URL/body, 429/503 Retry-After, 404/410 terminaux et SSRF ignoré ; transport simulé |
| JSON corrompu traité comme vide, fallback copie non atomique | Corrigé | `article_state.read_json/atomic_json` : sauvegarde + arrêt conservateur, fsync/replace ; interruptions et conservation de l'original testées |
| Reprise entre génération et sauvegarde / doublon URL | Corrigée avec checkpoint | `prepared` + jour cible, réconciliation idempotente, verrou collecteur ; pas de nouveau LLM après checkpoint durable |
| Auth/CSRF, SSRF et verrou LLM | Préservés | Suites précédentes exécutées sans réseau/modèle/service réel |

Voir [ARTICLE_INTEGRITY.md](ARTICLE_INTEGRITY.md) : bornes, états, schéma, configuration,
rollback et limites. Pas de transaction multi-fichiers/distribuée, pas de garantie
exactly-once avant checkpoint ni d'ACK durable du build public. Parser RSS/Atom,
statuts de santé des flux, normalisation temporelle globale, audio et XSS/CSP restent
hors de cette correction. L'exposition historique OpenRouter reste une action du
propriétaire ; aucune révocation n'est revendiquée.

## Mise à jour Sprint 01F

Socle `dev@2db598d`, PR #7 fusionnée sur demande explicite après CI réussie.
Six reproductions exécutées avant correction échouaient : segment obligatoire
manquant au début/milieu/fin, script invalide accepté, mix directement public et
collision à la seconde. Elles passent après correction.

| Constat | Statut 01F | Preuve / non-régression |
|---|---|---|
| `synthesize_all_segments` saute un segment échoué | Corrigé | Manifeste ordonné, validation individuelle, retry borné ; premier/centre/dernier, reprise et budget épuisé testés |
| `build` accepte un sous-ensemble non vide | Corrigé | Nombre attendu = nombre validé, toutes les voix contrôlées ; aucun assemblage sur manifeste incomplet |
| Script invalide accepté, troncature silencieuse | Corrigé | Structure bloquante, longueur strict/warn, profil flash conservé ; tests sans suppression de fin de texte |
| FFmpeg considéré réussi sur simple présence du fichier | Corrigé | Codes retour, taille, décodage MP3 complet, probe optionnel, durées/formats ; tests erreurs/timeout/fichier vide/invalide |
| Mix partiel visible et noms à la seconde | Corrigé | Zone privée même filesystem, UUID, reçu d'intégrité puis déplacement atomique ; interruption et collision concurrente testées |
| Admissions radio/Android non validées ou dupliquées | Corrigé dans l'historique local borné | Reçu/empreinte et admission verrouillée, cache Android avant lecture principale, copie atomique ; refus et reprises testés |
| Flash manuel potentiellement partiel | Corrigé | Validation avant `done` et contrôle du reçu lors du service audio ; auth/CSRF conservées |

Les anciens MP3 non attestés restent intacts mais ne sont plus admis. La génération
et les files ne disposent pas de jobs durables ni d'exactly-once après crash ; les
reçus ne sont pas une signature contre un écrivain local malveillant. Voir
[RADIO_INTEGRITY.md](RADIO_INTEGRITY.md) pour limites, nettoyage et rollback. Aucun
TTS réseau ni streamer/Icecast/LLM réel démarré. XSS/CSP et autres P0 hors périmètre
restent ouverts ; révocation historique OpenRouter toujours à confirmer par son
propriétaire, aucune révocation effectuée ici.

## Mise à jour Sprint 01G

Socle `dev@a631590`, PR #8 fusionnée sur demande explicite, deux CI réussies.
Cinq reproductions échouaient avant correction : rendu actif de données d'article,
URL JavaScript, HTML brut Markdown, eval et absence de CSP. Toutes sont corrigées.

| Surface | Statut 01G | Preuve / non-régression |
|---|---|---|
| Templates sans autoescape et attributs de flux | Corrigé sur les renderers inventoriés | Environnement Jinja autoescape, breaking échappé, allowlist URL ; HTML/guillemets/Unicode testés |
| Rapports/éditions Markdown bruts | Corrigé | nh3 HTML5 avec allowlist, pas de style/formulaire/script/namespace actif ; formatage légitime préservé |
| Exécution des scripts du HTML reçu | Supprimée | JS statique, initialisation par composants, navigation publique par DOM contrôlé ; eval/Function absents |
| Handlers inline et données interpolées dans du JS | Retirés | data-action + événements, DOM texte pour feeds/préférences ; tests navigateur admin et RSS |
| CSP et en-têtes | Appliqués après vérification navigateur | Scripts self, aucun unsafe-eval/inline script, objets/frames interdits ; exception CSS documentée, HSTS opt-in |
| Radio web et refresh | Préservés | Proxy existant de même origine, même Audio après navigation/history/refresh, filtres et thème testés |
| Sessions/CSRF et confidentialité | Préservés et cloisonnés | Meta/logout privés seulement, acquisition à la demande depuis public, tests cookies/401/403 et absence de tokens publics |
| Contrats Android / autres P0 | Inchangés | JSON public conservé, suites précédentes complètes ; aucun moteur/service réel lancé |

Voir [WEB_SECURITY.md](WEB_SECURITY.md) pour inventaire détaillé, politique HTML/CSP,
nginx/Tailscale, déploiement des exports et limites. Les styles inline historiques
restent permis ; la compromission du serveur/assets et les anciens exports non
régénérés ne sont pas couverts. Aucun secret historique reproduit ou révoqué ici.

## Mise à jour Sprint 02

Socle dev@f70ff2c après fusion explicitement demandée de la PR #9, CI réussie.
Quatre reproductions de déploiement en échec avant correction : services manquants,
latest, identifiants d'exemple opérationnels et écoute hôte wildcard. Compose les
corrige avec profils, secrets privés et port web loopback. Les digests Python et
Icecast ont été vérifiés publiquement, sans utiliser de credentials privés.

Les processus partagent les mêmes binds/UID et identifiants LLM ; leases de rôle
stables contre les doubles instances. SIGTERM est installé avant les enfants et
le web draine requêtes/flashs. Le log Android ne révèle plus l'URL Icecast contenant
le mot de passe. Les réglages conteneur gérés ne peuvent pas être écrasés par l'UI.
Voir DOCKER_DEPLOYMENT.md : healthchecks non métier, arrêt forcé après expiration,
limites POSIX/local et tests CI. Aucun déploiement, migration ou accès aux secrets
historiques. Leur rotation reste à effectuer par le propriétaire.

## Extension de capacité LLM — Sprint 03

Le backend optionnel litellm utilise un relais interne qui acquiert le même flock
local que les clients historiques ; cloud séparé par fournisseur et slots, sans
verrou global local. Namespace de cache gateway-v1 par alias/modèle/langue/prompt
et tâche, anciens fichiers préservés. L'auth upstream sans DB a été reproduite
avec un mauvais token (400 No connected db) : hook explicite 401/403, inférence
seule et allowlist des paramètres. Les modes DB/gestion de clés/budget SQL restent
désactivés. Limites résiduelles : métriques/RPM/cooldowns non persistants, appels
directs d'autres machines hors flock, timeout ne prouvant pas l'arrêt du calcul
serveur ; pas de plafond financier strict. Voir LLM_GATEWAY.md et tests gateway.

## Fondation PostgreSQL — Sprint 04

Imports explicites de snapshots, une transaction et advisory lock par manifeste ;
aucun remplacement silencieux des originaux ou des anciens snapshots. Alembic
versionné, droits limités, dates naïves non converties arbitrairement. Le bootstrap
interrompu est refusé au redémarrage plutôt que considéré comme complet ; aucune
suppression/réinitialisation automatique de PGDATA. Tests PostgreSQL jetable.

Limite à ne pas masquer : les producteurs et consommateurs opérationnels restent
legacy, avec les garanties des Sprints 01E/01F et leurs verrous POSIX. Aucune
transition de job/checkpoint/publication en SQL n'est annoncée ici. Le cutover,
la concurrence de collecteurs SQL et les nouveaux événements/jobs nécessitent une
PR complémentaire avec tests métier. Voir DATABASE_MIGRATION.md.
