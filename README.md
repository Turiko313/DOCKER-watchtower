# DOCKER-watchtower

Image tout-en-un pour mettre à jour des conteneurs Docker avec :

- le fork maintenu **[nicholas-fedor/watchtower](https://github.com/nicholas-fedor/watchtower)**, figé en version `v1.20.1` ;
- un dashboard Flask/Gunicorn pour consulter les conteneurs, les métriques et déclencher une vérification ;
- une page de paramètres persistée dans un volume Docker ;
- des notifications Discord via Shoutrrr ;
- une authentification HTTP Basic et une protection CSRF, comme dans
  `Turiko313/DOCKER-image-jellyfin`.

L’ancien projet `containrrr/watchtower` est archivé. Cette image compile le fork
maintenu depuis son commit immuable
`56afbbefa18f8c1fa215079588f9e487b3e2e746`.

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
DASHBOARD_BIND_ADDRESS=0.0.0.0
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

Le dashboard est disponible sur :

```text
http://<IP_DU_NAS>:8888
```

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
| `DASHBOARD_BIND_ADDRESS` | non | Adresse d’écoute publiée (`0.0.0.0` par défaut) |
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
- le redémarrage progressif ;
- le niveau de journalisation et le timeout ;
- les notifications Discord.

Le webhook Discord sauvegardé n’est jamais renvoyé au navigateur. Un champ vide
le conserve. Pour le remplacer, saisissez une nouvelle URL HTTPS Discord valide.

## Sécurité

Le déploiement applique notamment :

- HTTP Basic obligatoire sur toutes les pages et API du dashboard ;
- comparaison constante des identifiants et jeton CSRF sur toutes les requêtes d’écriture ;
- en-têtes CSP, anti-framing, `nosniff`, `no-referrer` et désactivation du cache ;
- secrets faibles ou absents refusés au démarrage ;
- dépendances Python verrouillées avec leurs empreintes SHA-256 ;
- source Watchtower figée sur une version et un commit précis ;
- système de fichiers du conteneur en lecture seule, `/tmp` en `tmpfs` ;
- toutes les capabilities Linux supprimées et `no-new-privileges` activé ;
- fichier d’authentification GHCR créé atomiquement avec le mode `0600` ;
- tests et audit de dépendances avant la publication de l’image ;
- attestations de provenance et SBOM pour les images GHCR publiées.

Le socket Docker reste un accès très privilégié : tout processus pouvant
l’utiliser peut contrôler l’hôte Docker. Le montage `:ro` protège le fichier du
socket, mais ne rend pas l’API Docker en lecture seule. N’exposez donc pas le
dashboard directement sur Internet.

HTTP Basic n’assure pas le chiffrement. Pour un accès hors d’un LAN de confiance,
placez le dashboard derrière un reverse proxy HTTPS ou un VPN.

Pour limiter l’exposition au seul hôte :

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
