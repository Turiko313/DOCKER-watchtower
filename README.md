# DOCKER-watchtower

Image tout-en-un pour mettre à jour des conteneurs Docker avec :

- le fork maintenu **[nicholas-fedor/watchtower](https://github.com/nicholas-fedor/watchtower)**, figé en version `v1.22.0` ;
- un dashboard Flask/Gunicorn pour consulter les conteneurs, les métriques et déclencher une vérification ;
- une page de paramètres persistée dans un volume Docker ;
- des notifications Discord via Shoutrrr ;
- une authentification HTTP Basic et une protection CSRF, comme dans
  `Turiko313/DOCKER-image-jellyfin`.

L’ancien projet `containrrr/watchtower` est archivé. Cette image compile le fork
maintenu depuis son commit immuable
`a5bb3cf3ba7ce0d88f39f6017232765dc7c58f6b`.

## Installation

### 1. Préparer la configuration

```bash
git clone https://github.com/Turiko313/DOCKER-watchtower.git
cd DOCKER-watchtower
cp .env.example .env
```

Générez deux secrets distincts :

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Puis renseignez `.env` :

```env
TZ=Europe/Paris
WATCHTOWER_API_TOKEN=<premier_secret>
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=<mot_de_passe_d_au_moins_8_caracteres>
SECRET_KEY=<second_secret>
DASHBOARD_BIND_ADDRESS=127.0.0.1
DASHBOARD_PORT=8888
GHCR_USERNAME=
GHCR_TOKEN=
```

Protégez le fichier :

```bash
chmod 600 .env
```

Un dépôt GitHub privé peut publier un package GHCR public. Dans ce cas,
`GHCR_USERNAME` et `GHCR_TOKEN` doivent rester vides. Ces variables ne sont
nécessaires que si le **package GHCR lui-même** est privé.

### 2. Démarrer

Avec l’image publiée :

```bash
docker compose pull
docker compose up -d
```

Pour construire localement :

```bash
docker build --pull \
  -t ghcr.io/turiko313/watchtower-dashboard:latest .
docker compose up -d
```

Par défaut, le dashboard est disponible uniquement depuis l'hôte Docker :

```text
http://127.0.0.1:8888
```

Pour un accès distant, publiez-le derrière un reverse proxy HTTPS ou utilisez
un VPN. Une écoute directe sur le LAN peut être activée explicitement avec
`DASHBOARD_BIND_ADDRESS=0.0.0.0`, mais HTTP Basic ne chiffre pas les
identifiants.

Le navigateur affiche directement la boîte de dialogue HTTP Basic. Il n’existe
plus de page de connexion, de cookie persistant ou de bouton de déconnexion.

## Installation OMV7

Dans **Services → Compose → Fichiers** :

1. créez un stack utilisant `docker-compose.yml` ;
2. sélectionnez comme dossier de travail le dossier contenant `.env` ;
3. vérifiez que les variables obligatoires sont chargées par OMV ;
4. démarrez ou recréez le stack.

Après une mise à niveau depuis l’ancienne version :

```bash
docker compose pull
docker compose up -d --force-recreate
```

Une réponse `401` lors du premier accès au dashboard est normale : elle
déclenche la demande d’identifiants HTTP Basic du navigateur.

## Variables

| Variable | Requise | Description |
|---|---:|---|
| `WATCHTOWER_API_TOKEN` | oui | Secret d’au moins 32 caractères protégeant l’API Watchtower interne |
| `DASHBOARD_USERNAME` | oui | Utilisateur HTTP Basic |
| `DASHBOARD_PASSWORD` | oui | Mot de passe HTTP Basic d’au moins 8 caractères |
| `SECRET_KEY` | oui | Secret d’au moins 32 caractères utilisé pour Flask et le jeton CSRF |
| `DASHBOARD_BIND_ADDRESS` | non | Adresse d’écoute publiée (`127.0.0.1` par défaut) |
| `DASHBOARD_PORT` | non | Port du dashboard (`8888` par défaut) |
| `TZ` | non | Fuseau horaire (`Europe/Paris` par défaut) |
| `GHCR_USERNAME` | non | Compte GitHub pour un package GHCR privé |
| `GHCR_TOKEN` | non | PAT classic limité à `read:packages` pour un package GHCR privé |

## Paramètres Watchtower

La page **Settings** gère :

- la planification cron à six champs ou l’intervalle de vérification ;
- le nettoyage des anciennes images ;
- l’inclusion et la réactivation des conteneurs arrêtés ;
- le mode surveillance uniquement ;
- la sélection par label ;
- le niveau de journalisation et le timeout ;
- les notifications Discord ;
- les commandes OCC exécutées après une mise à jour de Nextcloud.

Le webhook Discord sauvegardé n’est jamais renvoyé au navigateur. Un champ vide
le conserve. Pour le remplacer, saisissez une nouvelle URL HTTPS Discord valide.

## Commandes post-mise-à-jour Nextcloud

La page **Settings** permet d'activer un worker dédié qui détecte un changement
réel de l'ID d'image du conteneur nommé exactement `nextcloud`. La première image
observée initialise seulement la référence. À chaque changement d'image, le worker
attend que le conteneur soit démarré (et sain si un healthcheck existe), puis vérifie
avec `occ status --output=json` que Nextcloud est installé, hors maintenance et
sans migration de base de données en attente. Il réessaie cette vérification pendant
environ dix minutes avant d'abandonner si Nextcloud reste indisponible.
Il exécute ensuite les lignes dans l'ordre, avec une seule tentative par changement
d'image détecté. Un redémarrage avec la même image ne relance pas les commandes.
Cela fonctionne aussi après un remplacement manuel de l'image, indépendamment
de Watchtower. Une mise à jour interne sans changement d'image n'est pas détectée.

Exemple recommandé pour l'image Nextcloud officielle :

```text
docker exec --user www-data nextcloud php occ db:add-missing-indices
docker exec --user www-data nextcloud php occ db:add-missing-columns
docker exec --user www-data nextcloud php occ maintenance:repair --include-expensive
```

Les formes courtes comme `docker exec -it nextcloud occ ...` sont aussi
acceptées; `-i` et `-t` sont ignorés car le worker n'utilise pas de terminal.
Pour éviter de transformer le dashboard en shell distant :

- la cible est verrouillée sur `nextcloud` ;
- seuls `occ`, `php occ` et les utilisateurs `www-data`, `33` ou `82` sont acceptés ;
- les pipes, redirections, substitutions shell et autres options Docker sont refusés ;
- l'exécution passe directement par l'API Docker, sans shell ;
- un maximum de 20 commandes et 8 192 caractères est accepté ;
- la suite s'arrête au premier code de sortie non nul.

Les statuts de réussite ou d'échec sont visibles dans les journaux de
`watchtower-dashboard` avec le préfixe `nextcloud-post-update`; la sortie détaillée
des commandes n'est pas conservée. En cas d'échec, corrigez puis exécutez si nécessaire
la commande manuellement en SSH. Une tentative échouée ou interrompue n'est pas
reprise automatiquement sur la même image, pour éviter les doubles exécutions.

Ces exemples effectuent des réparations de la base de données et de la maintenance
Nextcloud; ils n'installent pas des paquets système. Les commandes `apt`, `apk`,
les scripts shell et les autres conteneurs ne sont pas acceptés dans ce champ.

## Sécurité

Le déploiement applique notamment :

- HTTP Basic obligatoire sur toutes les pages et API du dashboard ;
- comparaison constante des identifiants et jeton CSRF sur toutes les requêtes d’écriture ;
- en-têtes CSP, anti-framing, `nosniff`, `no-referrer` et désactivation du cache ;
- secrets faibles ou absents refusés au démarrage ;
- dépendances Python verrouillées avec leurs empreintes SHA-256 ;
- source Watchtower figée sur une version et un commit précis ;
- GitHub Actions figées sur les SHA de leurs releases ;
- système de fichiers du conteneur en lecture seule, `/tmp` en `tmpfs` ;
- toutes les capabilities Linux supprimées et `no-new-privileges` activé ;
- fichier d’authentification GHCR créé atomiquement avec le mode `0600` ;
- tests et audit de dépendances avant la publication de l’image ;
- attestations de provenance et SBOM pour les images GHCR publiées.

Le socket Docker reste un accès très privilégié : tout processus pouvant
l’utiliser peut contrôler l’hôte Docker. Le montage `:ro` protège le fichier du
socket, mais ne rend pas l’API Docker en lecture seule. N’exposez donc pas le
dashboard directement sur Internet.

HTTP Basic n’assure pas le chiffrement. Le port est donc lié à `127.0.0.1` par
défaut. Pour tout accès distant, placez le dashboard derrière un reverse proxy
HTTPS ou un VPN.

La valeur sécurisée par défaut est :

```env
DASHBOARD_BIND_ADDRESS=127.0.0.1
```

## Commandes utiles

```bash
# État et version
docker compose ps
docker inspect watchtower-dashboard \
  --format '{{ index .Config.Labels "org.opencontainers.image.version" }}'

# Journaux
docker logs -f watchtower-dashboard

# Vérifier que les deux processus sont actifs
docker exec watchtower-dashboard \
  supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status

# Recréer après modification du .env
docker compose up -d --force-recreate

# Tests locaux
python -m unittest discover -s dashboard/tests -v
```

## Dépannage

| Problème | Vérification |
|---|---|
| Le stack refuse de démarrer | Vérifier les trois secrets obligatoires et leurs longueurs |
| Le navigateur affiche `401` | Saisir les identifiants HTTP Basic configurés dans `.env` |
| Une action retourne `403` | Recharger la page afin d’obtenir le jeton CSRF courant |
| Package GHCR public en `denied` | Tester `docker pull ghcr.io/namespace/image:tag` directement depuis le NAS |
| Package GHCR privé en `denied` | Configurer un PAT classic `read:packages` dans `GHCR_USERNAME`/`GHCR_TOKEN` |
| Dashboard inaccessible depuis le LAN | Vérifier `DASHBOARD_BIND_ADDRESS`, le port et le pare-feu |
| Paramètres non persistés | Vérifier le volume Docker `watchtower_config` |
