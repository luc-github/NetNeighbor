# Roadmap NetNeighbor 2.0 — UI Qt (PySide6), fin du GTK

Document de suivi pour le passage **v2.0** : réécriture complète de la couche présentation en **Qt 6 / PySide6**, support **Linux, Windows et macOS**, **sans** GTK, PyGObject ni GLib dans le produit livré.

**Historique 1.x (GTK)** : voir [`archive/ROADMAP.md`](archive/ROADMAP.md) et [`UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) (état actuel jusqu’à la bascule).

---

## Comment mettre à jour ce document

- Cochez `[x]` les étapes terminées ; laissez `[ ]` le reste.
- Ajoutez une courte note sous une phase (date, PR, lien ticket) si utile.
- En cas de découpe en PRs, une même case peut rester `[ ]` jusqu’à ce que **toute** la sous-liste soit cochée.

---

## Convention de développement et tests par OS

Accord de travail pour la migration 2.0 :

- **Poste principal** : développement au quotidien sous **Windows** (vélocité, Qt, chemins utilisateur, packaging).
- **Linux** : **relecture / smoke test** aux jalons suivants (cocher quand fait) :
  - [x] Après **bootstrap Qt** utilisable (`DiscoveryManager` + fenêtre minimale, démarrage/arrêt discovery).
  - [x] Après **liste + détails** utilisables (menus contextuels, ouverture liens/commandes, config `~/.config/netneighbor/`).
  - [ ] **Avant release candidate** : tray (X11/Wayland selon votre bureau), autostart XDG, parcours complet utilisateur.
- **macOS** : pas d’environnement pour l’instant — prévoir une **passe dédiée** (machine, VM ou CI macOS) **avant** une release publique ciblant macOS ; cela ne bloque pas l’avancement Windows/Linux.

---

## Objectifs 2.0 (rappel)

- [x] Une seule stack UI : **PySide6** sur les trois OS cibles.
- [x] **Aucune** dépendance runtime à `gi` / GTK / GLib pour l’application graphique.
- [x] Découverte réseau (`discovery/`, `model/`) réutilisée ; synchronisation **thread discovery → thread UI** sans `GLib.idle_add`.
- [x] Parité fonctionnelle raisonnable avec 1.x (liste + grille, détails, préférences, tray où pertinent, raccourcis, i18n).
- [x] Packaging ou instructions d’installation documentés pour **Windows** et **macOS** (en plus du Linux déjà couvert par l’existant).

---

## Phase 0 — Préparation du chantier

- [x] Branche de travail dédiée (`2.0`) et politique de merge vers `main`.
- [x] Décision sur le **numéro de version** dans `VERSION` : `2.0.0` dès que `main` = Qt-only.
- [ ] CI : matrice cible (ex. Linux + Windows ; macOS si runner disponible) pour **tests** et **lint** avec PySide6 installé.
- [x] Inventaire des **fonctionnalités 1.x** à reproduire, dérivé de `USER_DOCUMENTATION.md` et des menus actuels.

---

## Phase 1 — Backend sans GLib / GTK

Objectif : le cœur discovery peut tourner et être testé **sans** boucle GLib, prêt pour un pont Qt.

- [x] **`discovery/manager.py`** — Hook injecté `schedule_on_main_thread` (défaut `lambda fn: fn()`). L’app GTK passe `gtk_idle_schedule` (`utils/scheduling.py`).
- [x] **`discovery/mdns.py`** — Timers **`threading.Timer`** + même hook pour enregistrer les browsers / grace-remove sur le thread UI.
- [x] **`utils/custom_command.py`** — Paramètre optionnel `schedule_on_main` ; GTK : `gtk_idle_schedule` depuis `ui/device_list.py`.
- [x] **`discovery/manager.py` / hooks présence** — Docstring mise à jour (callbacks hors thread UI ; marshalling à la charge de l’appelant).
- [x] **`docs/BACKEND_ARCHITECTURE.md`** — Schéma et texte mis à jour (plus de dépendance à `GLib.idle_add` dans la description).

---

## Phase 2 — Bootstrap application Qt

- [x] Dépendances : **`requirements-qt.txt`** inclut **PySide6** (voir [`QT_DEV_REQUIREMENTS.md`](QT_DEV_REQUIREMENTS.md)).
- [x] Point d’entrée **`app_qt.py`** : `QApplication`, `setup_i18n()` + logging partagé (`utils/app_logging.py`).
- [x] **Instance unique (Qt)** : `utils/qt_single_instance.py` — `QLocalServer` / `QLocalSocket`, message `ACTIVATE`, fenêtre au premier plan (`app_qt.py`). (GTK conserve `fcntl` + socket Unix.)
- [x] Connexion **`DiscoveryManager`** : `schedule_on_main_thread` via signal **`MainThreadScheduler.invoke`** ; **`add_listener`** met à jour un label via la même file (`MainThreadScheduler`).
- [x] Fenêtre placeholder (**`QMainWindow`** + compteur d’appareils).

---

## Phase 3 — Fenêtre principale et chrome

- [x] `QMainWindow` : barre de menus (View/Tools/Help), état fenêtre, raccourci **F11** plein écran.
- [x] Chargement / persistance des préférences existantes (`utils/ui_prefs.py`, chemins inchangés).
- [x] Préférences utilisateur (General, Notifications, Locations, Types, Applications + Maintenance).
- [x] Boîte **À propos** (version depuis `VERSION`, crédits PySide6, zeroconf, WSDiscovery, etc.).

---

## Phase 4 — Liste des appareils et navigation

- [x] Vue **liste** (colonnes triables) équivalente au `Gtk.TreeView` actuel.
- [x] Vue **icônes** / sections groupées (type ou emplacement) équivalente à la grille + sidebar.
- [x] Menus contextuels : Open (sous-menus), Details, Options, Monitor/Unfollow, Rename, Location, Device type, Hide device, Run custom command.
- [x] Icônes appareils : assets bundled-freedesktop + cache remote ; intégration `QIcon` / pixmaps via `ui/`.

---

## Phase 5 — Détails appareil

- [x] Dialog **Détails** avec onglets (Overview, Services, Device data, Options) alignés sur le comportement 1.x.
- [x] Règles de mapping TXT, commandes personnalisées par appareil (Override / Additional), validations.

---

## Phase 6 — Plateforme : tray, notifications, démarrage session

- [x] **Zone de notification** : `QSystemTrayIcon` + menu (Show window, Hide window, Quit).
- [x] **Notifications** desktop : via `utils/notifications.py` (Qt/OS natif selon la plateforme).
- [x] **Démarrage à la connexion** : `utils/session_autostart.py` — XDG Linux, `winreg` Windows, LaunchAgents macOS ; flag `--start-minimized-to-tray`.
- [x] Options **fermer vers le tray**, **démarrer minimisé**, cohérentes avec 1.x.

---

## Phase 7 — Retrait GTK et documentation

- [x] Supprimer modules GTK-only : `app.py`, `ui/*.py` GTK (seul `ui/icons.py` conservé comme utilitaire partagé), `utils/gtk_dialog.py`, imports `gi` retirés.
- [x] **`i18n.py`** nettoyé des branches spécifiques GTK.
- [x] **`README.md`**, **`USER_DOCUMENTATION.md`**, **`docs/PACKAGING.md`** mis à jour ; **`docs/UI_ARCHITECTURE.md`** archivé (mention GTK 3).
- [x] Scripts `.deb` / AppImage / tarball / Windows : dépendances GTK retirées ; `python3-pip`, `samba-common-bin` ; notes Windows/macOS documentées.

---

## Phase 8 — Qualification et release 2.0.0

- [x] Checklist **smoke test** Windows 11 : découverte, ouverture URL, tray, préférences, redémarrage — validé (2026-05-19).
- [ ] Checklist **smoke test** Linux : tray (X11/Wayland), autostart XDG, parcours complet utilisateur.
- [ ] Tests automatisés ciblés (non régression discovery ; tests UI optionnels avec offscreen Qt).
- [ ] **Gel** : tag `2.0.0`, changelog utilisateur, communication sur la rupture de stack (migration depuis paquets GTK 1.x).

---

## Référence rapide — fichiers 1.x à remplacer ou adapter

| Zone | Fichiers / modules typiques |
|------|-----------------------------|
| Entrée & cycle de vie | `main.py`, `app.py` |
| UI | `ui/main_window.py`, `ui/device_list.py`, `ui/device_details.py`, `ui/tray_indicator.py` |
| Dialogues GTK | `utils/gtk_dialog.py` |
| Intégration session / notif | `utils/session_autostart.py`, `utils/notifications.py` |
| GLib résiduel | `discovery/mdns.py`, `discovery/manager.py`, `utils/custom_command.py`, `ui/device_list.py` (timers / idle) |

---

## Définition de « done » pour la roadmap

Toutes les cases des phases **0 à 8** sont cochées, le dépôt **main** (ou la branche de release) ne contient plus de chemin d’exécution GTK, et une release **2.0.0** est publiée avec artefacts ou instructions pour Linux, Windows et macOS.
