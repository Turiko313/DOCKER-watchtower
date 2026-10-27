# Extension des actions manuelles

`manual-update.patch` s'applique uniquement au commit Watchtower vérifié par le
Dockerfile. `overlay/` ajoute les fichiers Go de l'endpoint `/v1/manual-update`
et ses tests. La construction vérifie le patch et exécute les tests des packages
routes, container et types avant de compiler le binaire.

L'endpoint manuel réutilise le pipeline de remplacement Docker, les notifications
et le verrou partagé du moteur. Une cible conteneur est obligatoire et le dashboard
transmet des noms échappés et ancrés. La politique de sélection est propre à la
requête : aucun label, filtre global ni réglage du client partagé n'est modifié.
Les requêtes normales `/v1/update` et les passages planifiés conservent leurs règles.

Les conteneurs arrêtés sont inclus et ne sont pas réactivés. Les labels de
surveillance seule, de non-téléchargement et de délai d'attente d'image n'empêchent
pas une action manuelle; un `WATCHTOWER_NO_PULL=true` explicitement configuré au
niveau du processus reste une politique de mise à jour depuis le cache local.
