# Changelog

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
