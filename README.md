# 🐳 DOCKER-watchtower

Déploiement personnalisé de **[Watchtower](https://containrrr.dev/watchtower/)** avec :

- 🔄 Mise à jour automatique des conteneurs Docker selon un planning configurable
- 🖥️ Interface web locale protégée par mot de passe
- 🔔 Notifications Discord lors des mises à jour
- 📊 Tableau de bord : statuts, uptime, historique des mises à jour, métriques

---

## 📁 Structure du projet

```
DOCKER-watchtower/
├── docker-compose.yml          # Orchestration Watchtower + Dashboard
├── .env.example                # Modèle des variables d'environnement
├── .gitignore
├── dashboard/
│   ├── Dockerfile              # Image Docker du tableau de bord
│   ├── requirements.txt        # Dépendances Python
│   ├── app.py                  # Application Flask
│   └── templates/
│       ├── base.html
│       ├── login.html
│       └── dashboard.html
└── README.md
```

---

## 🚀 Installation

### Méthode 1 — Ligne de commande (SSH)

#### 1. Cloner le dépôt

```bash
git clone https://github.com/Turiko313/DOCKER-watchtower.git
cd DOCKER-watchtower
```

#### 2. Créer le fichier `.env`

```bash
cp .env.example .env
```

Editez `.env` et remplissez toutes les valeurs (voir section [Configuration](#-configuration)).

#### 3. Démarrer les services

```bash
docker compose up -d --build
```

Le tableau de bord sera accessible sur :
```
http://<IP_DE_VOTRE_NAS>:8888
```

---

### Méthode 2 — Interface OMV7 (plugin Compose)

> ⚠️ Prérequis : le plugin **openmediavault-compose** doit être installé dans OMV7 (via **Système → Plugins**).

#### 1. Créer le dossier et le fichier `.env`

Connectez-vous en SSH à votre NAS et préparez le dossier de travail :

```bash
mkdir -p /srv/watchtower
cd /srv/watchtower
```

Créez le fichier `.env` en vous basant sur le modèle du dépôt :

```bash
# Copiez le contenu de .env.example et adaptez les valeurs
nano .env
```

Contenu minimal du `.env` :

```env
TZ=Europe/Paris
WATCHTOWER_SCHEDULE=0 0 4 * * *
WATCHTOWER_HOSTNAME=NAS-Watchtower
WATCHTOWER_API_TOKEN=<token_aleatoire>
DISCORD_NOTIFICATION_URL=discord://<TOKEN>@<WEBHOOK_ID>
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=<mot_de_passe_fort>
SECRET_KEY=<cle_secrete_aleatoire>
DASHBOARD_PORT=8888
```

Pour générer les tokens :

```bash
openssl rand -hex 32
```

#### 2. Créer le stack dans OMV7

1. Ouvrez l'interface web OMV7 et allez dans **Services → Compose → Fichiers**.
2. Cliquez sur le bouton **+** (Ajouter).
3. Renseignez les champs :
   - **Nom** : `watchtower`
   - **Dossier de travail** : `/srv/watchtower`
4. Dans l'éditeur **Compose**, collez le contenu du fichier `docker-compose.yml` de ce dépôt.
5. Cliquez sur **Enregistrer**.

#### 3. Déployer le stack

1. Sélectionnez le stack `watchtower` dans la liste.
2. Cliquez sur **Démarrer** (bouton ▶).
   - OMV7 exécutera automatiquement `docker compose up -d --build` avec le `.env` présent dans le dossier de travail.
3. Vérifiez que les conteneurs sont bien **running** dans **Services → Compose → Conteneurs**.

Le tableau de bord sera accessible sur :
```
http://<IP_DE_VOTRE_NAS>:8888
```

---

## ⚙️ Configuration

Toutes les options se configurent dans le fichier `.env` :

| Variable | Description | Exemple |
|---|---|---|
| `TZ` | Fuseau horaire | `Europe/Paris` |
| `WATCHTOWER_SCHEDULE` | Planning cron (6 champs) | `0 0 4 * * *` (tous les jours à 4h) |
| `WATCHTOWER_HOSTNAME` | Nom affiché dans les notifications | `NAS-Watchtower` |
| `WATCHTOWER_API_TOKEN` | Token pour l'API HTTP Watchtower | chaîne aléatoire |
| `DISCORD_NOTIFICATION_URL` | URL Discord au format shoutrrr | voir ci-dessous |
| `DASHBOARD_USERNAME` | Identifiant de connexion au dashboard | `admin` |
| `DASHBOARD_PASSWORD` | Mot de passe du dashboard | mot de passe fort |
| `SECRET_KEY` | Clé secrète Flask | chaîne aléatoire |
| `DASHBOARD_PORT` | Port réseau du dashboard | `8888` |

### Générer des tokens sécurisés

```bash
openssl rand -hex 32   # Pour WATCHTOWER_API_TOKEN et SECRET_KEY
```

---

## 🔔 Configurer les notifications Discord

1. Dans Discord, accédez aux **Paramètres du serveur → Intégrations → Webhooks**.
2. Créez un nouveau webhook et copiez l'URL :
   ```
   https://discord.com/api/webhooks/<WEBHOOK_ID>/<WEBHOOK_TOKEN>
   ```
3. Convertissez au format **shoutrrr** :
   ```
   discord://<WEBHOOK_TOKEN>@<WEBHOOK_ID>
   ```
4. Collez cette valeur dans `.env` :
   ```
   DISCORD_NOTIFICATION_URL=discord://VotreToken@VotreWebhookID
   ```

---

## 🖥️ Interface web

Accédez au tableau de bord à l'adresse :
```
http://<IP_NAS>:8888
```

Fonctionnalités :
- **Statut Watchtower** : running / arrêté, uptime
- **Métriques** : conteneurs analysés, mis à jour, en erreur
- **Liste des conteneurs** : nom, image, statut, uptime
- **Historique** : logs filtrés de Watchtower
- **Déclenchement manuel** : lancer une vérification immédiate via l'API Watchtower

---

## 📋 Commandes utiles

```bash
# Démarrer
docker compose up -d --build

# Arrêter
docker compose down

# Voir les logs Watchtower
docker logs -f watchtower

# Voir les logs du dashboard
docker logs -f watchtower-dashboard

# Forcer une mise à jour immédiate (si le token est configuré)
curl -H "Authorization: Bearer <WATCHTOWER_API_TOKEN>" http://localhost:8080/v1/update

# Reconstruire uniquement le dashboard après modification
docker compose up -d --build dashboard
```

---

## 🔒 Sécurité

- L'interface est accessible **uniquement sur le réseau local** (pas d'exposition internet prévue).
- Le socket Docker est monté **en lecture seule** (`ro`) dans le dashboard.
- Watchtower et le dashboard sont **exclus de la surveillance automatique** via l'étiquette `com.centurylinklabs.watchtower.enable=false`.
- Les identifiants sont stockés **uniquement dans `.env`** qui ne doit jamais être commité.

---

## 🛠️ Dépannage

| Problème | Solution |
|---|---|
| Dashboard inaccessible | Vérifier le port `DASHBOARD_PORT` et le pare-feu du NAS |
| Pas de notifications Discord | Vérifier `DISCORD_NOTIFICATION_URL` au format shoutrrr |
| Bouton "Vérifier maintenant" en erreur | Vérifier `WATCHTOWER_API_TOKEN` dans `.env` |
| Conteneur `watchtower` non trouvé | S'assurer que le conteneur se nomme bien `watchtower` |
