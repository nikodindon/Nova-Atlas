# Validation Sprint 12

- Prérequis : PR19 fusionnée dans dev (8d5e2b5), huit contrôles précédents réussis.
- Suite Python complète : **1563 passed**, aucun échec/skip, 71,59 secondes localement.
  Inclut les scénarios Chromium historiques et les tests de protections antérieures.
- Frontend : **31 tests unitaires/composants** ; **10 scénarios Chromium** desktop/mobile.
- TypeScript/Svelte : zéro erreur et zéro avertissement ; ESLint réussi.
- Build de production, budget client, compilation Python, syntaxe JS historique,
  helper CSRF, configuration Compose et diff Git vérifiés.
- npm : quatre avis faibles transitifs deepmerge, zéro avis modéré/high/critical après
  override minimatch ; aucun downgrade automatique dangereux.
- Smokes Docker/RSS/images/événements/TTS lab/Radio Studio/LiteLLM/PostgreSQL conservés
  en CI. Nouveau smoke Node/nginx/Flask réel à données synthétiques ; statut final à
  consulter dans les checks GitHub de la PR.

Reproductions conservées : API documents/archives manquantes, refus des paramètres,
symlinks/données corrompues, exposition de champs privés évitée par projection,
bootstrap session/CSRF, texte et liens hostiles inertes, attributs img SSR incompatibles
avec la CSP, style d'annonceur Kit hashé, reconnexions bornées et identité audio
préservée en navigation. Aucun service producteur démarré localement.

Les tests audio navigateur simulent HTMLMediaElement ; aucun résultat ne constitue
une mesure de qualité ou disponibilité Icecast. Administration native limitée à la
consultation, mutations dans Flask. Pas de revue manuelle complète avec lecteur d'écran.
