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

### 1. Cloner le dépôt

```bash
git clone https://github.com/Turiko313/DOCKER-watchtower.git
cd DOCKER-watchtower
```

### 2. Créer le fichier `.env`

```bash
cp .env.example .env
```

Editez `.env` et remplissez toutes les valeurs (voir section [Configuration](#-configuration)).

### 3. Démarrer les services

```bash
docker compose up -d --build
```

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
