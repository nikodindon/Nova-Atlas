# ADR 0001 — Migration incrémentale

Date : 2026-11-03. Statut : retenu pour le plan V2 ; technologies cibles non livrées.

Contexte : moteur exploité avec stockage fichiers, radio et client Android ;
une réécriture simultanée augmenterait le risque de perte et de régression.

Décision : partir de dev, caractériser hors ligne puis corriger chaque P0 dans
une PR dédiée avec reproduction et non-régression. Préserver contrats, chemins,
exports et API Android avant remplacement par adaptateurs. Backend Python,
FastAPI/Pydantic, PostgreSQL/Alembic, SvelteKit/TypeScript, LiteLLM, TTS interchangeable,
FFmpeg/Icecast et Compose sont la cible décrite dans ARCHITECTURE_V2.md.
Aucun GPU requis ; services secondaires optionnels.

Alternatives : rewrite global rejeté pour risque opérationnel ; fusion de
l'ancienne branche OpenRouter rejetée comme socle obsolète avec secret exposé.
SQLite/WAL reste une option explicitement réévaluable pour prototype.

Conséquences : coexistence temporaire Flask/API et JSON/SQL, adaptateurs et tests
supplémentaires, petites PR plus faciles à vérifier. Chaque migration exige
sauvegarde, aller-retour et procédure de retour ; aucune fusion automatique.
Ce sprint livre docs/tests/CI/outillage seulement, sans déploiement.
