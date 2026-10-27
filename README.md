# DOCKER-watchtower

Image tout-en-un pour mettre à jour des conteneurs Docker avec :

- le fork maintenu **[nicholas-fedor/watchtower](https://github.com/nicholas-fedor/watchtower)**, figé en version `v1.23.2` ;
- un dashboard Flask/Gunicorn pour consulter les conteneurs, les métriques et déclencher une vérification ;
- une page de paramètres persistée dans un volume Docker ;
- des notifications Discord via Shoutrrr ;
- une authentification HTTP Basic et une protection CSRF, comme dans
  `Turiko313/DOCKER-image-jellyfin`.

L’ancien projet `containrrr/watchtower` est archivé. Cette image compile le fork
maintenu depuis son commit immuable
`cd5946be205ea0a60dd31235a11f04ac9851ebf6`.
Un correctif local versionné dans `watchtower/` ajoute une action manuelle
indépendante de la sélection automatique, avec le même moteur et le même verrou
de mise à jour. Ses tests Go sont exécutés pendant la construction de l'image.

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

Dans le dashboard, le bouton **Mettre à jour** au début de chaque ligne lance
une vérification et applique la nouvelle image si elle est disponible pour ce
conteneur. Sur une ligne de groupe, il cible tous les conteneurs utilisant
cette image, y compris ceux exclus de la surveillance automatique. Dépliez le
groupe pour mettre à jour un seul de ses conteneurs.
Le bouton global **Vérifier maintenant** conserve la vérification générale.

Le bouton **Mettre à jour** est indépendant de la colonne **Watchtower** et
reste disponible pour chaque conteneur ou groupe. L'action manuelle ignore la
sélection automatique par labels et le mode surveillance uniquement, sans
modifier les labels ni la planification. Les conteneurs arrêtés peuvent être
mis à jour et restent arrêtés. La politique explicite de téléchargement local
(`WATCHTOWER_NO_PULL`) et les identifiants des registres restent applicables.
Le bilan affiche les nombres de conteneurs vérifiés, mis à jour, ignorés et en
échec. Le tableau s'actualise après la réponse.

Le bouton **Redémarrer**, placé à côté de **Mettre à jour**, redémarre le
conteneur via Docker, sans changer son image. Sur une ligne de groupe, il
redémarre tous les conteneurs de ce groupe, y compris ceux exclus des mises à
jour Watchtower. Comme `docker restart`, il peut aussi démarrer un conteneur
arrêté. Chaque résultat est vérifié : le conteneur doit être démarré avec une
nouvelle date de lancement. Cette vérification ne garantit pas que l'application
a terminé son initialisation ou que son healthcheck est déjà sain.

Les boutons d'action sont **bleus** au repos. Une action confirmée les passe en
**vert pendant deux minutes**, puis ils redeviennent bleus. Un échec ou un
résultat non confirmé les passe en **rouge jusqu'à une nouvelle tentative**.
Un symbole complète la couleur. Ces états sont conservés lors des actualisations
automatiques et des rechargements, dans la session de l'onglet. Pendant une action,
les boutons conservent leur couleur; les clics supplémentaires sont ignorés pour
éviter les doublons.

Lors du redémarrage du dashboard lui-même, la connexion peut être interrompue.
Le navigateur attend son retour et vérifie les dates de lancement des conteneurs
pendant environ une minute, sans renvoyer la demande de redémarrage. Si le
résultat ne peut pas être confirmé, le bouton reste rouge : vérifiez Docker
avant de relancer.

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
réel de l'ID d'image du conteneur nommé exactement `nextcloud`, même si son tag
reste identique. La première image observée initialise seulement la référence.
À chaque changement d'image, le worker enregistre la nouvelle référence dans le
volume de configuration et attend environ deux minutes, même après un redémarrage
du worker. Il attend ensuite que le conteneur soit démarré (et sain si un healthcheck
existe), puis vérifie avec `occ status --output=json` que Nextcloud est installé,
hors maintenance et sans migration de base de données en attente. Après environ
dix minutes d'indisponibilité, il signale la cause précise et conserve les commandes
en attente. Les vérifications reprennent automatiquement, y compris après un
redémarrage, jusqu'à ce que Nextcloud soit prêt ou que l'image soit remplacée.
Il exécute ensuite les lignes dans l'ordre, avec une seule tentative par changement
d'image détecté. Si le hook est désactivé, la nouvelle image reste en attente jusqu'à
son activation. Un redémarrage avec la même image ne relance pas les commandes déjà
tentées.
Cela fonctionne aussi après un remplacement manuel de l'image, indépendamment
de Watchtower. Une mise à jour interne sans changement d'image n'est pas détectée.

Exemple recommandé pour l'image Nextcloud officielle :

```text
docker exec --user www-data nextcloud php occ db:add-missing-columns
docker exec --user www-data nextcloud php occ db:add-missing-indices
docker exec --user www-data nextcloud php occ db:add-missing-primary-keys
```

Les formes courtes comme `docker exec -it nextcloud occ ...` sont aussi
acceptées; `-i` et `-t` sont ignorés car le worker n'utilise pas de terminal.
Le worker appelle directement `php /var/www/html/occ` pour l'image officielle :
le binaire `occ` n'a pas besoin d'être dans le `PATH`, ni le répertoire courant
de correspondre à celui de Nextcloud.

Pour les images LinuxServer (`linuxserver/nextcloud`, `docker.io/linuxserver/nextcloud`,
`lscr.io/linuxserver/nextcloud` ou `ghcr.io/linuxserver/nextcloud`), utilisez des
commandes sans `--user`, y compris pour `ghcr.io/linuxserver/nextcloud:latest` :

```text
docker exec -it nextcloud occ db:add-missing-columns
docker exec -it nextcloud occ db:add-missing-indices
docker exec -it nextcloud occ db:add-missing-primary-keys
docker exec -it nextcloud occ maintenance:repair --include-expensive
docker exec -it nextcloud occ app:update --all
```

Les deux dernières lignes sont facultatives : ajoutez-les si vous souhaitez aussi
exécuter les réparations coûteuses et mettre à jour toutes les applications.

Le worker détecte ces références d'image dans la configuration du conteneur et
appelle directement `php /app/www/public/occ` sous l'utilisateur `abc`, sans
utiliser le wrapper `occ`, `sudo` ou un shell. Sans utilisateur explicite, les
autres images utilisent `www-data`. Un `--user` ou un chemin OCC absolu explicite
reste prioritaire. Les images personnalisées ou miroirs LinuxServer ne sont pas
détectés automatiquement.

Pour éviter de transformer le dashboard en shell distant :

- la cible est verrouillée sur `nextcloud` ;
- seuls `occ`, `php occ` et les utilisateurs `www-data`, `33`, `82` ou `abc` sont acceptés ;
- les pipes, redirections, substitutions shell et autres options Docker sont refusés ;
- l'exécution passe directement par l'API Docker, sans shell ;
- un maximum de 20 commandes et 8 192 caractères est accepté ;
- la suite s'arrête au premier code de sortie non nul.

Les statuts de réussite ou d'échec sont visibles dans les journaux de
`watchtower-dashboard` avec le préfixe `nextcloud-post-update`; la sortie détaillée
des commandes n'est pas conservée. En cas d'échec, corrigez puis exécutez si nécessaire
la commande manuellement en SSH. Dès que l'exécution de la première commande a
été autorisée, une tentative échouée ou interrompue n'est pas reprise automatiquement
sur la même image, pour éviter les doubles exécutions. Un échec des seuls contrôles
de disponibilité conserve les commandes en attente. Leurs diagnostics distinguent
le healthcheck, la maintenance, une migration en attente, un JSON invalide et
un échec de `occ status`; les sorties brutes ne sont pas divulguées.

Si une ancienne version a déjà envoyé exactement « Nextcloud indisponible ou
image remplacée; aucune commande exécutée », elle a enregistré cette image comme
déjà tentée. Après déploiement du correctif et vérification des commandes dans
Settings, cette commande réarme le complément pour l'image courante :

```bash
docker exec watchtower-dashboard python -c "import nextcloud_post_update as n; s=n._load_state(); s.pop('hook_attempted_image_id', None); n._save_state(s)"
```

Le worker attend de nouveau environ deux minutes puis reprend ses vérifications.
Ce réarmement est destiné à cette annulation avant exécution; si des commandes
ont déjà été exécutées, vérifiez leur résultat avant de les relancer.

Si les notifications Discord sont activées et le webhook renseigné, le worker
envoie un **message complémentaire distinct du rapport Watchtower** à la fin de
la tentative : **Complément Nextcloud OK** si toutes les commandes réussissent,
ou **Complément Nextcloud NOK** en cas d'échec. Chaque commande est identifiée
par son numéro et son action OCC, avec `OK`, `NOK` (code de sortie si disponible)
ou `non exécutée` si une commande précédente a échoué. Les arguments et sorties
des commandes ne sont pas envoyés. Une configuration invalide ou un Nextcloud
indisponible produit également un NOK avec la raison de l'annulation.
Pour une indisponibilité persistante, le NOK n'est envoyé qu'une fois par
changement d'image; un nouveau bilan est envoyé lorsque les commandes finissent
par être exécutées.
Aucun complément n'est envoyé si le hook est désactivé, lors de l'initialisation
ou si l'image ne change pas. Un échec d'envoi Discord est journalisé, sans
relancer les commandes ni réessayer automatiquement l'envoi.

Ces commandes appliquent les changements de schéma recommandés par Nextcloud après
une mise à niveau. `maintenance:repair --include-expensive` peut durer longtemps :
vérifiez les alertes d'administration et lancez-la séparément si nécessaire.
Le hook n'installe pas de paquets système. Les commandes `apt`, `apk`,
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

# Audit des dépendances verrouillées (pip-audit 2.10.1)
python -m pip_audit --disable-pip -r dashboard/requirements.txt
```

Les interactions des boutons peuvent aussi être vérifiées avec le script
optionnel suivant, après installation de Playwright et de Chromium dans un
environnement de développement. Il utilise des API Docker et Watchtower simulées
et ne modifie aucun conteneur réel :

```bash
python dashboard/tests/browser_manual_actions.py
```

La CI utilise Python 3.15.1. Avant de publier une version, elle exige la réussite
des tests, de `pip check`, de l'audit et de la validation Compose. Elle construit
ensuite l'image et la teste sous Alpine : mêmes tests, démarrage des trois
processus, authentification du dashboard et accès aux métriques Watchtower.
La publication intervient après ces contrôles et est vérifiée par digest dans
GHCR. Un push Git ou un tag ne garantit donc pas à lui seul une image publiée :
le workflow correspondant doit être terminé avec succès.

Pour régénérer le verrouillage avec les versions stables compatibles :

```bash
uv pip compile dashboard/requirements.in --python-version 3.15 \
  --python-platform x86_64-unknown-linux-musl --generate-hashes --upgrade \
  --output-file dashboard/requirements.txt
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
