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
