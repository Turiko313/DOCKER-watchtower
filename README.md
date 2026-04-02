# DOCKER-watchtower

Deploiement personnalise de **[Watchtower](https://containrrr.dev/watchtower/)** avec :

- Mise a jour automatique des conteneurs Docker selon un planning configurable
- Interface web locale protegee par mot de passe
- Notifications Discord (ou tout service shoutrrr) lors des mises a jour
- Tableau de bord : statuts, uptime, historique des mises a jour, metriques
- **Page Parametres** : configurez Watchtower entierement depuis le navigateur
- **Bouton Redemarrer** : appliquez les nouveaux parametres en un clic
- **Se souvenir de moi** : session persistante de 30 jours pour eviter de se reconnecter

---

## Structure du projet

```
DOCKER-watchtower/
├── docker-compose.yml          # Orchestration Watchtower + Dashboard
├── Dockerfile                  # Image Docker (multi-stage : Go + Python)
├── supervisord.conf            # Gestion des processus watchtower + dashboard
├── start_watchtower.py         # Script de demarrage Watchtower (lecture JSON → env vars)
├── .env.example                # Modele des variables d'environnement
├── .gitignore
├── dashboard/
│   ├── app.py                  # Application Flask (dashboard web)
│   ├── requirements.txt        # Dependances Python
│   └── templates/
│       ├── base.html
│       ├── login.html
│       ├── dashboard.html
│       └── settings.html       # Page de configuration Watchtower
└── README.md
```

---

## Installation

### Methode 1 — Ligne de commande (SSH)

#### 1. Cloner le depot

```bash
git clone https://github.com/Turiko313/DOCKER-watchtower.git
cd DOCKER-watchtower
```

#### 2. Creer le fichier `.env`

```bash
cp .env.example .env
```

Editez `.env` et remplissez toutes les valeurs (voir section [Configuration](#configuration)).

#### 3. Demarrer les services

```bash
docker compose up -d --build
```

Le tableau de bord sera accessible sur :
```
http://<IP_DE_VOTRE_NAS>:8888
```

---

### Methode 2 — Interface OMV7 (plugin Compose)

> Prerequis : le plugin **openmediavault-compose** doit etre installe dans OMV7.

#### 1. Creer le dossier et le fichier `.env`

```bash
mkdir -p /srv/watchtower
cd /srv/watchtower
nano .env
```

Contenu minimal du `.env` :

```env
TZ=Europe/Paris
WATCHTOWER_API_TOKEN=<token_aleatoire>
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=<mot_de_passe_fort>
SECRET_KEY=<cle_secrete_aleatoire>
DASHBOARD_PORT=8888
```

Pour generer les tokens :

```bash
openssl rand -hex 32
```

#### 2. Creer le stack dans OMV7

1. Allez dans **Services → Compose → Fichiers**, cliquez sur **+**.
2. Renseignez le **Nom** (`watchtower`) et le **Dossier de travail** (`/srv/watchtower`).
3. Collez le contenu du fichier `docker-compose.yml`, puis **Enregistrer**.

#### 3. Deployer le stack

1. Selectionnez le stack `watchtower` et cliquez sur **Demarrer**.
2. Verifiez que les conteneurs sont **running** dans **Services → Compose → Conteneurs**.

---

## Configuration

### Variables d'environnement (fichier `.env`)

Ces variables sont statiques et ne changent pas au cours de l'utilisation :

| Variable | Description | Exemple |
|---|---|---|
| `TZ` | Fuseau horaire | `Europe/Paris` |
| `WATCHTOWER_API_TOKEN` | Token pour l'API HTTP Watchtower | chaine aleatoire |
| `DASHBOARD_USERNAME` | Identifiant de connexion au dashboard | `admin` |
| `DASHBOARD_PASSWORD` | Mot de passe du dashboard | mot de passe fort |
| `SECRET_KEY` | Cle secrete Flask | chaine aleatoire |
| `DASHBOARD_PORT` | Port reseau du dashboard | `8888` |
| `GHCR_USERNAME` | *(optionnel)* Nom d'utilisateur GitHub pour images privees GHCR | `Turiko313` |
| `GHCR_TOKEN` | *(optionnel)* PAT GitHub avec scope `read:packages` | `ghp_xxxx…` |

> **Images privees GHCR** : si certains de vos conteneurs utilisent des images privees
> hebergees sur `ghcr.io`, renseignez `GHCR_USERNAME` et `GHCR_TOKEN` pour que
> Watchtower puisse verifier les mises a jour. Creez un PAT sur
> [github.com/settings/tokens](https://github.com/settings/tokens) avec le scope
> **read:packages**.

### Parametres geres par l'interface web

Toutes les autres options Watchtower sont configurables directement dans le dashboard
(**icone Parametres** dans la barre de navigation) et persistees dans le volume Docker
`watchtower_config` :

| Parametre | Description | Defaut |
|---|---|---|
| Planning (cron 6 champs) | Frequence des verifications | `0 0 4 * * *` (4h du matin) |
| Nettoyage des images | Supprime les anciennes images apres mise a jour | Actif |
| Redemarrage progressif | Redemarrage un conteneur a la fois | Inactif |
| Conteneurs arretes | Mise a jour des conteneurs stops | Inactif |
| Niveau de log | Verbosity des journaux | `info` |
| URL de notification | URL shoutrrr (Discord, Telegram, Slack…) | vide |
| Nom de l'hote | Affiche dans le titre des notifications | `NAS-Watchtower` |

> **Important** : apres avoir modifie et enregistre des parametres, cliquez sur
> le bouton rouge **Redemarrer Watchtower** pour que les changements prennent effet.
> Watchtower sera recree avec les nouveaux parametres (interruption ~5 secondes).

---

## Notifications Discord

1. Dans Discord : **Parametres du serveur → Integrations → Webhooks → Nouveau webhook**.
2. Copiez l'URL : `https://discord.com/api/webhooks/<WEBHOOK_ID>/<WEBHOOK_TOKEN>`
3. Convertissez au format shoutrrr : `discord://<WEBHOOK_TOKEN>@<WEBHOOK_ID>`
4. Collez cette valeur dans la page **Parametres** du dashboard.

---

## Interface web

Acces :
```
http://<IP_NAS>:8888
```

Fonctionnalites :
- **Statut Watchtower** : running / arrete, uptime
- **Metriques** : conteneurs analyses, mis a jour, en erreur
- **Liste des conteneurs** : nom, image, statut, uptime
- **Historique** : logs filtres de Watchtower
- **Verification immediate** : lancer une mise a jour via l'API Watchtower
- **Redemarrage** : recree le conteneur Watchtower avec les parametres enregistres
- **Parametres** : configurez et sauvegardez toutes les options Watchtower
- **Se souvenir de moi** : cochez la case a la connexion pour rester connecte 30 jours

---

## Commandes utiles

```bash
# Demarrer
docker compose up -d --build

# Arreter
docker compose down

# Voir les logs Watchtower
docker logs -f watchtower

# Voir les logs du dashboard
docker logs -f watchtower-dashboard

# Reconstruire uniquement le dashboard apres modification
docker compose up -d --build dashboard
```

---

## Securite

- L'interface est accessible **uniquement sur le reseau local**.
- Le socket Docker est monte avec acces complet dans le dashboard (necessaire pour
  recreer le conteneur Watchtower lors d'un changement de parametres).
- Watchtower et le dashboard sont **exclus de la surveillance automatique**
  via l'etiquette `com.centurylinklabs.watchtower.enable=false`.
- Les identifiants sont stockes **uniquement dans `.env`** qui ne doit jamais etre commite.
- L'option **Se souvenir de moi** utilise un cookie de session Flask signe,
  valable 30 jours. La session peut etre revoquee a tout moment via **Deconnexion**.

---

## Depannage

| Probleme | Solution |
|---|---|
| Dashboard inaccessible | Verifier le port `DASHBOARD_PORT` et le pare-feu du NAS |
| Pas de notifications | Verifier l'URL shoutrrr dans la page Parametres |
| Bouton "Verifier maintenant" en erreur | Verifier `WATCHTOWER_API_TOKEN` dans `.env` |
| Bouton "Redemarrer" en erreur | Verifier que le socket Docker n'est pas en `:ro` dans compose |
| Parametres non enregistres | Verifier les permissions du volume Docker `watchtower_config` |
| Conteneur `watchtower` non trouve | S'assurer que le conteneur se nomme bien `watchtower` |
| Image privee GHCR `unauthorized` | Renseigner `GHCR_USERNAME` et `GHCR_TOKEN` dans `.env` (PAT avec `read:packages`) |
