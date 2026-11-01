# Vérification du code — 1er novembre 2026

La revue couvre le code Python du dashboard et du lanceur, les trois templates,
le worker Nextcloud et la validation OCC, l'extension Go des mises à jour manuelles,
Dockerfile, Compose, Supervisor, les tests et le workflow de publication.
Elle ne constitue pas une validation de l'installation Docker du NAS.

## Défauts corrigés

| Défaut | Correction |
|---|---|
| La colonne Watchtower assimilait l'absence du label `enable=false` à une surveillance active. | Lecture de la sélection du moteur actif, qui applique ses filtres et options. Les pannes de l'API donnent un état inconnu explicite. |
| La colonne était vide pour les groupes et ne distinguait pas les modes de surveillance. | Nombre de membres surveillés, répartition des modes et indication des images locales. |
| Un conteneur exclu masquait son résultat de mise à jour manuelle. | Résultat des journaux affiché indépendamment de sa sélection automatique. |
| Les journaux JSON n'étaient pas lus. | Prise en charge de JSON et logfmt, ainsi que du message explicite `Updated container`. Les bilans ambigus restent sans succès inventé. |
| Les jauges des mises à jour et échecs étaient traitées comme des compteurs cumulés. | Affichage du dernier scan, migration de l'ancienne valeur observée et conservation en cas de panne d'API. |
| La carte des erreurs additionnait les échecs des journaux aux métriques, avec doublons possibles. | Affichage séparé : carte du dernier scan et résultats par conteneur sur 24 heures. |
| Plusieurs requêtes pouvaient écraser un historique plus récent. | Transaction protégée par un verrou de threads et un verrou de fichier entre workers Linux, y compris lors d'une remise à zéro. |
| Six champs suffisaient à accepter une expression cron invalide, et toutes les macros étaient acceptées. | Validation des bornes, listes, plages, pas et macros ; repli au démarrage pour les anciennes configurations invalides. |
| Des identifiants accentués ou un CSRF non ASCII pouvaient provoquer une erreur serveur. | Comparaison des octets UTF-8 à temps constant et rejet propre des jetons invalides. |

## Vérifications réalisées

- 108 tests Python réussis sous Python 3.13.16, dont 23 ajouts depuis les 85 tests initiaux.
- Parcours Chrome réussi avec API simulées : ciblage, exclusions, couleurs, expiration,
  erreurs persistantes, nouvelle tentative, actualisation, rechargement, reconnexion
  sans doublon, modes Watchtower, groupes, panne et retour d'API, affichage mobile.
- Inspection de la page dans Chrome avec `agent-browser`, dont les groupes dépliés
  et l'état indisponible.
- Audit des dépendances verrouillées : aucune vulnérabilité connue signalée.
- `docker compose config --quiet` réussi avec des secrets factices.
- `git apply --check` réussi pour le patch Go sur le commit Watchtower épinglé.
- `git diff --check` réussi.

## Limites et mise en service

Le moteur Docker local est inactif et Go n'est pas installé. La construction de
l'image Alpine, les tests Go et les actions Docker réelles n'ont pas été exécutés.
La CI existante conserve ces contrôles avant publication ; son test de l'image
vérifie désormais aussi la sélection exposée par l'API `containers`.

Les corrections sont regroupées dans la version `v1.5.2`. Après réussite du
workflow de publication, récupérer l'image puis recréer le service avec
`docker compose pull` et `docker compose up -d --force-recreate`.
Un simple redémarrage du conteneur existant ne recharge pas le code embarqué
dans une nouvelle image.

Le nombre de scans cumulés reste observé lors des consultations du dashboard :
des événements survenus entièrement entre deux consultations avec redémarrage du
moteur ne peuvent pas être reconstruits exactement. Les résultats par conteneur
dépendent des journaux disponibles sur les dernières 24 heures et de leur niveau
de détail ; l'absence de badge ne prouve pas l'absence de mise à jour.

## Sources techniques vérifiées

- [API des détails de conteneurs du commit utilisé](https://github.com/nicholas-fedor/watchtower/blob/cd5946be205ea0a60dd31235a11f04ac9851ebf6/internal/api/handlers/containers/details/handler.go)
- [Types des métriques Watchtower](https://github.com/nicholas-fedor/watchtower/blob/cd5946be205ea0a60dd31235a11f04ac9851ebf6/internal/metrics/metrics.go)
- [Parseur cron robfig](https://github.com/robfig/cron/blob/v3.0.1/parser.go)
