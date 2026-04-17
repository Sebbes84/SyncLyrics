# SyncLyrics - Home Assistant Add-on

[![Home Assistant Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-blue.svg)](https://www.home-assistant.io/)
[![Maintainability](https://img.shields.io/badge/maintainability-A-brightgreen.svg)](https://github.com/Sebbes84/SyncLyrics)

**SyncLyrics** est un add-on pour Home Assistant qui permet de récupérer, stocker localement et afficher les paroles synchronisées de votre lecture Spotify dans une interface premium style Karaoké.

## 🚀 Fonctionnalités

- **Synchronisation temps réel** : Les paroles défilent et se surlignent en suivant précisément votre musique.
- **🌍 Traduction Automatique** : Traduction intelligente des paroles étrangères vers le français (activable en option).
    - Support des chansons multilingues.
    - Affichage bilingue (Original / Traduction en italique).
- **Multi-sources** : Récupération intelligente sur **LRCLIB**, **Musixmatch** et **Genius**.
- **💾 Gestion du Cache local** :
    - Stockage automatique des paroles (Originales et Traduites) pour un accès instantané hors-ligne.
    - **Nettoyage automatique** : Limitation de l'espace disque configurable (ex: 100 Mo) avec suppression des fichiers les plus anciens.
- **Interface Premium** :
    - Fond transparent pour intégration dans vos dashboards.
    - Animations fluides.
    - Affichage optionnel de la pochette d'album en fond flouté.
    - Barre de progression et métadonnées (Artiste/Titre).
- **Mode Jeu** : Un mode "Trouve les paroles" pour masquer certains mots et s'amuser.
- **Ajustement d'Offset** : Possibilité de décaler la synchro manuellement (+/-) pour compenser la latence réseau.
- **Menu Paramètres** : Une interface intégrée pour activer/désactiver les options (Traduction, Masquage, Jeu) à la volée.

## 🛠 Installation

1. Ajoutez ce dépôt à votre instance Home Assistant :
   - Allez dans **Paramètres** > **Greffons (Add-ons)** > **Boutique des greffons**.
   - Cliquez sur les trois points en haut à droite > **Dépôts**.
   - Ajoutez l'URL de votre dépôt GitHub.
2. Recherchez "SyncLyrics" et cliquez sur **Installer**.
3. Dans l'onglet **Configuration**, renseignez l'ID de votre lecteur Spotify (ex: `media_player.spotify_user`) et optionnellement vos jetons **Musixmatch** et **Genius**.
4. Démarrez l'add-on.

## 📺 Utilisation dans un Dashboard

Pour afficher les paroles dans votre interface Lovelace :
1. Ajoutez une carte **Page Web** (Webpage).
2. URL : `http://VOTRE_IP_HA:8099` (Assurez-vous que le port 8099 est ouvert dans la configuration).

## 📄 Licence

Ce projet est sous licence MIT.
