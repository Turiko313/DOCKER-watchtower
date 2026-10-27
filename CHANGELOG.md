# Changelog

## 1.5.1 — 2026-10-27

- Le bouton **Mettre à jour** reste disponible et ne dépend plus de l'indicateur Watchtower, y compris pour les groupes entièrement exclus de la surveillance automatique. Les boutons ne sont plus grisés pendant une action; les clics supplémentaires restent bloqués.
- L'action manuelle cible tous les conteneurs sélectionnés et ignore les exclusions automatiques, les labels de surveillance seule et le mode surveillance global. Les labels et la configuration automatique ne sont pas modifiés. Les conteneurs arrêtés peuvent être mis à jour en conservant leur état arrêté.
- Extension locale du moteur Watchtower épinglé : endpoint manuel authentifié et limité à une cible, politique isolée par requête et verrou partagé avec les mises à jour planifiées. Tests Go exécutés pendant la construction.
- Contrôle CI d'une vraie mise à jour d'image sur un conteneur jetable avec `enable=false` et `monitor-only=true`, puis vérification que ses labels, son exclusion automatique et le conteneur voisin sont conservés.

Validation locale : 85 tests Python et tests navigateur réussis, avec API simulées. Validation du moteur Go et du remplacement Docker réel confiée à la CI.

## 1.5.0 — 2026-10-27

- Bouton **Redémarrer** à côté de **Mettre à jour**, pour un conteneur ou un groupe complet. Le redémarrage utilise directement l'API Docker et reste disponible pour les conteneurs exclus des mises à jour Watchtower.
- Vérification du redémarrage par l'état `running` et une nouvelle date de lancement. Gestion des échecs partiels et vérification après reconnexion si le dashboard redémarre lui-même, sans répétition automatique de l'action.
- Boutons bleus avec icônes; vert pendant deux minutes après une action confirmée, rouge jusqu'à une nouvelle tentative en cas d'échec. Conservation des états après rafraîchissement ou rechargement dans le même onglet, indicateurs accessibles et disposition adaptée aux petits écrans.
- Renforcement de la mise à jour ciblée : bilan Watchtower complet et valide exigé, signalement des cibles non vérifiées et des délais de réponse dépassés. Regroupement par la référence d'image configurée dans le conteneur plutôt que par le premier alias d'image.
- Conservation des groupes ouverts par leur référence d'image lors d'un rafraîchissement et rejet des réponses de rafraîchissement antérieures à une action.
- Contrôle de l'image en CI étendu à une vérification Watchtower ciblée sans téléchargement et au redémarrage Docker d'un conteneur jetable, avec vérification qu'un conteneur voisin reste inchangé.

Validation locale : 84 tests réussis. Tests Chromium avec API simulées : ciblage, exclusions, résultats, durée de deux minutes avec horloge contrôlée, erreurs persistantes, nouvelles tentatives, actualisation, rechargement, reconnexion sans doublon et affichage mobile. Docker n'étant pas démarré localement, aucun conteneur réel n'a été redémarré; validation de l'image confiée à la CI.

## 1.4.0 — 2026-10-27

- Bouton **Mettre à jour** au début de chaque ligne d'image ou de groupe, avec une action individuelle dans les groupes dépliés.
- Ciblage des conteneurs par leurs noms exacts, protection contre les expressions régulières et refus des cibles vides ou inconnues. Les actions respectent les paramètres et exclusions Watchtower.
- Affichage du bilan de vérification, des mises à jour, des conteneurs ignorés et des échecs; indicateur d'activité et boutons désactivés pendant la requête.
- Rafraîchissement du tableau après l'action et conservation du fonctionnement des boutons après les actualisations automatiques; défilement horizontal du tableau sur les petits écrans.

Validation locale : 66 tests réussis. Interactions vérifiées dans Chromium avec Docker et l'API Watchtower simulés : groupes, conteneurs individuels, exclusions, indicateur d'activité, rafraîchissement et largeur mobile. Aucune mise à jour de conteneur réel effectuée localement.

## 1.3.0 — 2026-10-23

- Watchtower 1.23.2, épinglé au commit `cd5946be205ea0a60dd31235a11f04ac9851ebf6`; images de base Go 1.27.3 et Python 3.15.1 Alpine, avec digests vérifiés.
- Dépendances Python actualisées : certifi 2026.10.23, charset-normalizer 3.5.2, MarkupSafe 3.0.4 et Werkzeug 3.1.9. Les mises à jour certifi et Werkzeug corrigent les deux vulnérabilités qui bloquaient l'audit de la version 1.2.2.
- Verrouillage et hashes des dépendances régénérés pour Python 3.15 sous Linux avec uv. Les autres dépendances Python et l'outil pip-audit sont déjà aux dernières versions stables disponibles.
- Action setup-python 7.1.0 épinglée par SHA; versions et SHA des autres actions GitHub vérifiés.
- La CI contrôle les dépendances installées, exécute les 54 tests dans l'image Alpine construite et vérifie le démarrage des trois processus, l'authentification, les pages du dashboard et les métriques Watchtower avant publication. Après publication, elle vérifie que l'image est accessible dans GHCR par son digest.

## 1.2.2 — 2026-10-23

- Exécution des formes courtes OCC via PHP et un chemin absolu; détection des images LinuxServer pour utiliser le chemin `/app/www/public/occ` et l'utilisateur `abc` en l'absence de `--user`, sans wrapper ni shell.
- Un contrôle de disponibilité échoué ne consomme plus la mise à jour : les commandes restent en attente, y compris après un redémarrage. La tentative définitive est enregistrée juste avant la première commande.
- Le NOK indique désormais la cause du blocage (healthcheck, maintenance, migration, contrôle OCC ou Docker). Les avertissements PHP entourant un statut JSON valide sont tolérés; les sorties brutes restent privées.
- Un seul NOK est envoyé pendant l'attente, puis le bilan des commandes lors de leur exécution. Documentation du réarmement d'une annulation produite par une ancienne version.

Validation locale : 54 tests réussis, dont la séquence des cinq commandes fournie pour `ghcr.io/linuxserver/nextcloud:latest`. Contrôles Docker et notifications simulés; aucune commande exécutée sur une instance Nextcloud réelle.

## 1.2.1 — 2026-10-04

- Le complément Nextcloud attend deux minutes après la détection d'un nouvel ID d'image avant de vérifier l'état du conteneur et d'exécuter les commandes OCC.
- L'image en attente et l'heure de détection sont conservées dans le volume de configuration. Un hook désactivé ne consomme plus le changement d'image; l'attente reprend après un redémarrage du worker.
- Le modèle de commandes Nextcloud inclut les colonnes, indices et clés primaires manquants. Les réparations coûteuses restent à lancer au cas par cas.
- Le bilan Discord est envoyé après la tentative OCC lorsque les notifications sont configurées.

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
