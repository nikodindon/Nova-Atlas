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
