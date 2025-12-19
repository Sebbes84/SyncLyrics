# Documentation SyncLyrics

## Configuration détaillée

| Option | Type | Description |
| --- | --- | --- |
| `spotify_entity` | string | L'entity ID de votre lecteur Spotify (ex: `media_player.spotify_me`). |
| `cache_size_mb` | int | Taille maximale du cache pour les fichiers .lrc (par défaut 100 Mo). |
| `show_header` | boolean | Affiche/Masque l'artiste et le titre en haut de l'écran. |
| `show_progress_bar` | boolean | Affiche/Masque la barre de progression en bas. |
| `show_background` | boolean | Affiche la pochette d'album floutée en arrière-plan. |
| `game_mode_enabled` | boolean | Active le masquage aléatoire de mots pour le jeu. |
| `lyric_providers` | list | Liste des sources ordonnées (ex: `["lrclib", "musixmatch", "genius"]`). |
| `musixmatch_token` | string | (Optionnel) Votre token utilisateur Musixmatch. |
| `genius_token` | string | (Optionnel) Votre token d'accès client Genius. |

## Ports réseau

L'add-on utilise le port **8099/tcp** pour servir l'interface web. Ce port doit rester ouvert pour que l'intégration par iframe (ou carte Lovelace) fonctionne.

## Stockage des données

Les fichiers de paroles récupérés sont stockés dans le dossier interne `/data/lyrics`. Ces fichiers sont au format standard `.lrc` et peuvent être réutilisés par d'autres applications si besoin.

## Dépannage

### Les paroles ne sont pas synchronisées
Vérifiez que l'entité Spotify est bien en cours de lecture dans Home Assistant. Si vous utilisez des enceintes connectées avec beaucoup de latence (ex: Sonos, Google Cast), utilisez les boutons **+ / -** sur l'écran pour ajuster l'offset.

### Les paroles ne s'affichent pas du tout
L'add-on utilise par défaut **LRCLIB** (gratuit). Pour de meilleurs résultats, vous pouvez ajouter vos jetons **Musixmatch** ou **Genius** dans la configuration. Certaines chansons très récentes ou très rares peuvent ne pas être répertoriées sur toutes les plateformes.

