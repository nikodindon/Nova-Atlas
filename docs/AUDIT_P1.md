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
