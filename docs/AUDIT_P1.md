# Audit P1 — ingestion RSS/Atom, Sprint 05

| Surface | Défaut reproduit | Correction / preuve | Limite restante |
|---|---|---|---|
| atlas_fetch._fetch_rss | Atom sans item ignoré | feed_parser + fixtures Atom 0.3/1.0, RSS RDF/0.9x/2.0 | DTD et XML malformé refusés |
| HTTP 304 | assimilé à échec/liste vide | type FeedResult et replay d'entrées validées après restart | cache perdu : requête fraîche différée |
| récupération | timeout/erreur confondus avec flux vide | statuts, HTTP code, motifs et backoff persistants | pas de dashboard Source Manager |
| dates source | chaîne sans information fiable normalisée implicitement | raw + UTC seulement avec timezone, publication/updated distinctes | dates multilingues non standard conservées brutes |
| téléchargements | séquentiels, sans budget par site | pool 4, budget par hostname/redirect, état des 429 | DNS libc hérité non annulable ; pas de coordination multi-hôtes |
| configuration feeds | cache mtime global sans chemin | cache chemin + mtime_ns et reload | parser YAML historique conservé, hors découverte Source Manager |
| identité | risque de changer les hashes/canonicals | MD5 URL et priorité YAML conservés ; GUID en provenance privée | pas de consolidation multi-sources/mises à jour éditoriales |

Les six reproductions initiales et tests de sécurité sont conservés. Les données
réelles ne sont pas inspectées, ni corrigées automatiquement. Pas de cutover SQL,
de découverte de flux ou de changement de prompts. Voir RSS_INGESTION.md.

## Sprint 06 — Source Manager

| Surface | Reproduction / risque | Correction / preuve |
|---|---|---|
| feeds_loader.save_feeds | YAML malformé partiellement chargé puis écrasé | SourceManager revalide tout sous verrou, refuse et préserve original ; test_source_reproductions |
| Catégories / URL des mutations | Catégorie multiligne injectait une nouvelle structure YAML | Validation commune sur tous les parcours et import, pas de tokens connus/URL privées ; test_source_manager |
| reorder_feeds | Une URL répétée passait la comparaison par set | Vérification longueur et ensemble complet ; reproduction conservée |
| Routes /config/feeds/* | read/edit/save concurrents perdaient des mises à jour | flock fichier stable, version, sauvegarde/fsync/rename ; vrais processus et threads synthétiques |
| Cache News | Invalidation mémoire Web non propagée | Vérification inode/device/taille/mtime au prochain cycle ; test mtime identique |
| Santé | Comptage de commentaires approximatif, désactivés absents de l'UI | Modèle actif/inactif + état FeedState observé avec date, âge et ancienneté |
| Test d'URL | Nouveau point de sortie potentiellement SSRF/coûteux | Même client épinglé/parser, admission 1 et 5/min persistée, embargos croisés ; tests API et smoke |
| Interface | Titres, noms et erreurs non fiables | textContent/DOM contrôlé, CSP conservée, parcours Chromium hostile |
| Import | XML externe, fichier local ou écrasement implicite | OPML sans DTD/réseau, limites, preview signée expirante, ajout seul et version |

Limites : édition manuelle hors flock non coordonnée, quotas locaux et sessions Web
mono-processus, fenêtre d'observation (pas de sonde automatique), un backup seulement,
refus complet plutôt que diagnostic ligne par ligne d'un import invalide. Grammaire
YAML volontairement restreinte ; tokens opaques non identifiables universellement.
La découverte et le modèle opérationnel SQL restent à livrer ; voir SOURCE_MANAGER.md.

## Sprint 07 — Découverte et nouvelles sorties HTTP

| Surface | Risque / reproduction | Mesure et preuve |
|---|---|---|
| Test RSS sur une URL HTML | Rejet correct mais aucun lien de flux détecté | Deux reproductions initialement rouges pour alternate relatif/HTTP Link ; HTML seul jamais validé |
| Liens et chemins conventionnels | Faux positifs ou crawl incontrôlé | Candidats typés puis parser commun, liens bornés, cinq chemins au plus, pas de pages internes ordinaires |
| DNS/redirect d'un candidat | SSRF et rebinding | Client existant épinglé ; mêmes refus IPv4/IPv6/privés ; tests transport réel simulé |
| Budget multiplié par redirect | Fan-out et dépassement temporel | RequestBudget par opération décompte chaque hop avant DNS/connexion, deadline partagée |
| HTML/metadata hostile | XSS dans preview et navigation | Texte nettoyé/DOM textContent, scripts statiques connus, CSP inchangée, Chromium |
| Découverte comme abonnement | Écriture implicite/perte de version | Aucun mutate dans le moteur ; POST sources explicite/versionné, 409 couvert |
| Fournisseur de recherche | Proxy arbitraire/LAN ou fuite de clés | Endpoint SearXNG public HTTPS fixe opérateur, aucun credential ni paramètre client endpoint, pas de fallback |
| Monitoring/quotas concurrents | Écrasement du journal News ou 429 ignoré | Journal propre et verrou partagé des tests, embargos lus entre producteurs, admission durable avant réseau |
| Réponse tardive après navigation | Mise à jour du mauvais écran/perte radio | Références de composant isolées, init idempotente, même objet Audio ; test Chromium tardif |

Limites : SearXNG privé et RSSHub reportés, détection non exhaustive des sites
nécessitant JS/auth, metadonnées éditeur non certifiées, quotas POSIX locaux et
parseur borné plutôt qu'interruption CPU à la milliseconde. DNS libc conserve les
limites héritées. Pas de fusion canonique automatique, découverte sociale ou cutover SQL.

## Sprint 08 — Rapprochements éditoriaux et catégories

| Surface | Risque reproduit | Mesure / validation |
|---|---|---|
| get_breaking_news | Deux mots communs associaient deux faits distincts sur une personnalité | Index dérivé, comparaison conservatrice avec dates/nombres/actions, fallback borné ; reproduction rouge puis verte |
| dedup_articles_by_similarity | Préfixe de résumé commun supprimait une actualité différente | Égalité exacte des records seulement ; aucun fait supprimé sur une ressemblance |
| Regroupement transitif | A–B et B–C fusionnent A–C à tort | Tous les membres doivent être compatibles ; ambiguïté séparée ; test de chaîne |
| Dates/sources | Confusion date d'indexation/publication/événement et consensus | Dates aware séparées, inconnues NULL, independent_confirmation=false systématique |
| Corrections | Rebuild effaçant une décision humaine | Overrides séparés, version et raison, contraintes transitives validées, IDs hors fenêtre signalés |
| Fichiers dérivés | Index partiel/corrompu ou concurrence | Stable flock + verrou producteur, revalidation input, fsync/replace, tests crash/deux processus, aucun JSON source modifié |
| Catégories | CAT_ORDER/CATEGORY_ORDER excluaient les catégories ajoutées | Registre configuré + observé, ordre de repli historique, rapports/éditions et flashs custom |
| Radio/éditions | Nouvelle sélection contournant l'intégrité ou la provenance | Opt-in seulement, manifestes privés, overrides relus ; publication synthétique et segment manquant refusé |
| API/UI | Fuite de journaux, HTML hostile ou arrêt radio | Projection explicite, Jinja échappé, API lecture seule, huit scénarios Chromium incluant événements |

Corpus : 24 paires synthétiques annotées, 6 vrais groupes proposés/0 fausse fusion,
2 groupes manqués et 4 ambiguïtés non regroupées. Ce jeu n'établit aucune qualité
sur la presse réelle. Benchmark local 500 couvertures ~2,91 s ; groupes ≤50.
Limites : lexical FR/EN, dates fiables requises, index snapshot explicite (pas de
synchronisation instantanée), sous-dossiers historiques signalés comme non encore
adaptés, revue humaine et corpus représentatif à prévoir. Aucune activation cloud.

## Sprint 09 — Illustrations et droits

| Surface | Risque / reproduction | Correction et limite |
|---|---|---|
| FeedEntry | media:thumbnail perdu | Candidats facultatifs bornés, vieux caches lisibles ; JSON articles inchangé |
| OG/HTML/RSS | Confusion accessibilité/licence | Droits inconnus, jamais de publication automatique |
| Commons | Métadonnées HTML, licence ambiguë, ressource mutable | Endpoint fixe, CC BY 4.0 uniquement, révision/SHA1/taille, attribution et revue ; droits de tiers non garantis |
| Images externes | SSRF, fichiers actifs et bombes | Client épinglé existant, worker Pillow borné, raster JPEG local sans EXIF |
| Approbation | Changement de licence/contexte | Binding exact, expiration 24 h, invalide après changement ; pas de vérification réseau permanente |
| Stockage/export | Partiel, corruption et copies révoquées | Manifest atomique/POSIX, binaires immuables, export d'arbre atomique ; copies déjà distribuées hors contrôle |
| UI | Légendes hostiles et hotlinks | textContent/autoescape, CSP img-src self, revue auth/CSRF, radio persistante Chromium |

Voir NEWS_IMAGES.md pour les plafonds et exclusions. Aucun jeu de données réel lu.

## Constat RC — Sprint 13

| Surface | Constat / preuve | Décision |
|---|---|---|
| media/export.py | mkdtemp dans site.parent hors bind writable ; reproduction avant fix | Stage et fsync sous site/media ; échange refusé préserve l'ancien arbre |
| web/public_api.py et media/store.py | Validation manifeste répétée par carte ; 2,8 s/page et 44 s/12 lectures sur corpus initial | Snapshot droits par page et cache texte SHA deux versions ; droits/octets recontrôlés, mesures dans measurements/rc |
| radio/studio.py | Quota 1024 exigeait archive sans outil | CLI dry-run/version/leases, archives puis tombstones puis compactage ; fichiers/cas ambigus protégés |
| Déploiement | Restauration seulement décrite | Outil refusant écrasement/liens/état actif ; round-trip et redémarrage neuf CI |
| Capacité éditoriale | >500 articles dans fenêtre = refus explicite | Limite conservée, pas d'index incomplet ; No-Go promesse de charge supérieure |
| Stockage audio/logs | Croissance sans purge globale automatique | Maintenance opérateur après backup ; pas de disponibilité 24/7 revendiquée |
| Qualification/droits | B, 24 h, écoute, Edge/musique, licence projet et scan OS manquent | No-Go exploitation publique ; checklist dans RELEASE_CANDIDATE.md |
