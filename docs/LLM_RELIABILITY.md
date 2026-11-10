# Fiabilité LLM — Sprint 16A

Ce lot traite le backend historique **llama-server**, sans modifier les prompts,
les backends Ollama/LiteLLM, les fichiers d'articles ou l'instance opérateur.
Voir [SPRINT_16_AUDIT.md](SPRINT_16_AUDIT.md) pour les observations réelles.

## Réponses et logs

Une seule tentative HTTP applicative par appel, avec le timeout existant. Une
réponse vide n'entraîne plus cinq secondes d'attente et une seconde génération
identique. Les reprises persistantes d'articles restent gérées par leur journal.
Un échec radio laisse la musique et le dernier bulletin Android aux streamers
existants ; ce lot ne modifie pas leur fonctionnement.

Le journal `nova.ollama` comporte task, duration_ms, max_tokens, finish_reason,
prompt_tokens, completion_tokens, reasoning_tokens, reasoning_present, http_status
et kind. Les valeurs absentes restent inconnues. Aucun prompt, texte de sortie,
raisonnement, corps d'erreur fournisseur, URL ou nom de modèle/chemin n'est ajouté.
Les durées de transport et d'admission sont séparées. Les logs restent soumis à
la rétention opérateur ; aucun nouveau journal de contenu n'est créé.

- `success` : texte non vide, finish stop (ou absent pour compatibilité).
- `output_limit` : finish length, même avec texte partiel. **Pas de cache ni
  publication de ce texte tronqué** ; augmenter le budget ou réduire la tâche
  après diagnostic, sans retry automatique de même taille.
- `reasoning_only` : texte vide avec champ de raisonnement présent ; indicateur,
  pas preuve que le raisonnement explique seul la panne.
- `empty_response`, `invalid_response`, `reasoning_in_content`,
  `unsupported_finish`, `response_too_large` : refus explicites. Les balises
  `<think>` ne sont jamais republiées comme texte éditorial.
- `timeout`, `transport_error`, `authentication`, `payment`, `forbidden`,
  `rate_limited`, `provider_unavailable`, `http_error` : causes distinctes.

Les métriques usage sont celles déclarées par le serveur, pas une mesure
indépendante ni une facture. Réponse limitée à 4 MiB. L'interface historique garde
texte ou chaîne vide : aucune exception fournisseur brute exposée aux consommateurs.

## Budgets configurables

Ajouter les clés souhaitées sous le `llm` **déjà présent** dans la configuration
persistante, après sauvegarde et revue opérateur ; ne pas ajouter un second bloc
YAML et ne pas remplacer les chemins/provider existants :

```yaml
llm:
  max_tokens_by_task:
    fetch: 1024
    translate: 512
    posts: 1024
    flash: 1536
    bulletin: 4096
    report: 4096
    editions: 4096
    atlas: 4096
  # Facultatif. Sans ces clés : attente historique timeout + 60 secondes.
  lock_wait_seconds:
    fetch: 60
    bulletin: 180
```

Les plafonds par défaut restent **4096 pour chaque tâche**, afin de ne pas
réduire sans qualification le budget d'Ornith en mode raisonnement. Les plafonds
plus courts et délais d'attente de cet exemple sont des choix expérimentaux
explicites, **pas de nouveaux défauts imposés**. Tokens :
entier 64…32768 ; attente : entier 0…3600 secondes. Configuration invalide refusée.
`translate` est sélectionné via operation translate/translation/title_translation,
les autres via caller. Un appel privé de transport sans contexte utilise atlas.
4096 reste le budget bulletin compatible, pas une promesse de 1500 mots. Les
budgets courts limitent le coût maximal possible ; les performances Ornith
avec ces valeurs restent à mesurer avant généralisation.

## Concurrence et limites

Le même flock stable protège News et Radio ; aucun vol, unlink, priorité
préemptive ou expiration de bail. Un cache hit évite la génération ; le cache est
revérifié après l'attente. La configuration d'attente borne une admission sans
changer l'exclusivité. Le log wait_ms montre si le temps est passé à attendre ou
à générer. Aucun FIFO ni absence de famine garantie ; une file de priorités durable
n'est pas introduite ici. Une génération en cours n'est jamais tuée par un caller.

Le timeout urllib reste un délai d'opération socket, **pas une échéance absolue
contre un serveur qui livre lentement des octets**. Une déconnexion ne prouve pas
l'arrêt du calcul distant. Annulation vérifiée avant admission et lancement,
mais pas interruption forcée de la requête déjà active. Les futurs travaux sur
l'annulation distante doivent tenir compte de la capacité réelle du serveur.
Pas de retry HTTP 429 ici, donc aucun Retry-After raccourci par ce client ; la
transmission de ce délai au journal de reprise métier reste à étendre. Pas de
modification de `--reasoning off`, ni d'attribution causale sans mesure.

## Essai opérateur après fusion et autorisation distincte

1. Sauvegarder `~/atlas-lab/state/config/config.yaml`. La PR ne le modifie pas.
2. Choisir les budgets, conserver provider et ressource de verrou commune.
3. Lors d'une fenêtre de déploiement autorisée séparément, appliquer l'image
   construite depuis le commit fusionné. Rien n'est redéployé par ce sprint.
4. Observer les cycles habituels, sans lancer de charge supplémentaire : logs
   `LLM admission` et `LLM response`, métriques usage et erreurs classées.
5. Comparer séparément résumés et bulletins : temps d'attente, temps HTTP,
   complétude, output_limit, réponses vides, nombre de reprises. Ne pas exporter
   les logs historiques bruts sans masquage.
6. Si fetch plafonne souvent, augmenter son budget explicitement ou revenir à
   l'image précédente. Les données et anciens caches sont préservés ; aucune
   migration/effacement n'est nécessaire. Un ancien cache valide reste lisible.

Tests : transports simulés, limites, logs sans contenu, erreurs, cache, annulation
et flock ; aucune mesure de qualité, de débit réel ou d'amélioration du modèle
n'est déduite de ces tests. Les changements de prompt/assemblage identitaire et
radio nocturne appartiennent au lot 16B, après revue de 16A.

Commandes d'observation en lecture seule, après déploiement décidé par l'opérateur :

```bash
cd ~/atlas-lab/Nova-Atlas
export ATLAS_STATE="$HOME/atlas-lab/state"
docker compose -p atlas-lab logs --since 30m --tail=200 news radio
```

Ne pas remplacer les fichiers sous `state/config` par ceux du checkout. Aucun
redémarrage n'est nécessaire pour lire les logs. Un changement de configuration
LLM n'est pas annoncé comme rechargé à chaud : l'initialisation du client intervient
au lancement du processus et le cycle de déploiement reste soumis à autorisation.
