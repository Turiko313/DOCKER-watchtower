# Changelog

## 1.2.0 — 2026-09-21

- Ajout d'un message Discord complémentaire « Complément Nextcloud OK/NOK » après la tentative de commandes OCC, avec le résultat de chaque commande et les commandes non exécutées.
- Signalement des annulations et erreurs Docker; un échec d'envoi Discord ne relance pas les commandes.
- Vérification des versions au 20 septembre 2026 : Watchtower 1.22.0 → 1.22.2, avec commit immuable et correction de la réutilisation des jetons anonymes GHCR.
- Actualisation des digests des images Go 1.27.1 Alpine et Python 3.12 Alpine.
- Dépendances Python transitives : charset-normalizer 3.5.1, click 8.5.0, idna 3.20 et urllib3 2.8.0; fichier verrouillé régénéré avec ses hashes sous Python 3.12.
- Actions GitHub Docker : setup-buildx-action 4.4.1 et build-push-action 7.4.0, épinglées par SHA.

Validation locale : construction Docker réussie, 41 tests réussis dans l'image Python 3.12 Alpine, `pip check` et configuration Compose valides, aucune vulnérabilité Python connue signalée par pip-audit. Notifications testées avec un webhook simulé; aucune commande exécutée sur une instance Nextcloud réelle.

## 1.1.0 — 2026-09-06

- Ajout dans Settings d'une fonction optionnelle exécutant des commandes OCC après un changement d'image du conteneur `nextcloud`.
- Vérification de l'état Nextcloud avant exécution, ordre des commandes conservé, arrêt au premier échec et état persistant pour éviter une répétition après redémarrage.
- Exécution sans shell, cible et utilisateurs limités, sorties traitées en flux avec mémoire bornée.
- Correction de la falsification des statuts de mise à jour via les access logs Gunicorn.
- Écoute du dashboard limitée à `127.0.0.1` par défaut. Pour un accès distant, prévoir HTTPS ou VPN; une adresse explicite dans `.env` reste prioritaire.
- Watchtower mis à jour vers 1.22.0, Go vers 1.27.1, Docker SDK vers 7.2.0 et Gunicorn vers 26.2.0; images de base rafraîchies et actions CI épinglées par SHA.
- Publication d'un tag Docker de version depuis les tags Git `v*`.

Validation locale : 31 tests réussis, Compose valide et aucune vulnérabilité Python connue signalée par pip-audit. Build et publication de l'image confiés à la CI; aucun test sur une instance Nextcloud réelle effectué localement.

La version 1.1.0 désigne ce dashboard; 1.22.0 désigne le moteur Watchtower embarqué.
