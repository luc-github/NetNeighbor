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
  - [ ] Après **bootstrap Qt** utilisable (`DiscoveryManager` + fenêtre minimale, démarrage/arrêt discovery).
  - [ ] Après **liste + détails** utilisables (menus contextuels, ouverture liens/commandes, config `~/.config/netneighbor/`).
  - [ ] **Avant release candidate** : tray (X11/Wayland selon votre bureau), autostart XDG, parcours complet utilisateur.
- **macOS** : pas d’environnement pour l’instant — prévoir une **passe dédiée** (machine, VM ou CI macOS) **avant** une release publique ciblant macOS ; cela ne bloque pas l’avancement Windows/Linux.

---

## Objectifs 2.0 (rappel)

- [ ] Une seule stack UI : **PySide6** sur les trois OS cibles.
- [ ] **Aucune** dépendance runtime à `gi` / GTK / GLib pour l’application graphique.
- [ ] Découverte réseau (`discovery/`, `model/`) réutilisée ; synchronisation **thread discovery → thread UI** sans `GLib.idle_add`.
- [ ] Parité fonctionnelle raisonnable avec 1.x (liste + grille, détails, préférences, tray où pertinent, raccourcis, i18n).
- [ ] Packaging ou instructions d’installation documentés pour **Windows** et **macOS** (en plus du Linux déjà couvert par l’existant).

---

## Phase 0 — Préparation du chantier

- [ ] Branche de travail dédiée (ex. `v2/qt` ou équivalent) et politique de merge vers `main`.
- [ ] Décision sur le **numéro de version** dans `VERSION` : garder 1.x sur `main` tant que l’artefact par défaut est GTK, ou basculer dès que `main` = Qt-only (à trancher en équipe).
- [ ] CI : matrice cible (ex. Linux + Windows ; macOS si runner disponible) pour **tests** et **lint** avec PySide6 installé.
- [ ] Inventaire des **fonctionnalités 1.x** à reproduire (checklist de parité), dérivé de `USER_DOCUMENTATION.md` et des menus actuels.

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

- [ ] `QMainWindow` (ou équivalent) : barre de menus, état fenêtre, raccourci **F11** plein écran.
- [ ] Chargement / persistance des préférences existantes (`utils/ui_prefs.py`, chemins inchangés si possible).
- [ ] Préférences utilisateur (équivalent des dialogs / onglets nécessaires) — périmètre minimal puis itérations.
- [ ] Boîte **À propos** (version depuis `VERSION`, crédits bibliothèques mises à jour : PySide6, zeroconf, WSDiscovery, etc.).

---

## Phase 4 — Liste des appareils et navigation

- [ ] Vue **liste** (colonnes / modèle) équivalente au `Gtk.TreeView` actuel.
- [ ] Vue **icônes** / sections (type ou emplacement) équivalente à la grille + sidebar.
- [ ] Menus contextuels : Open (sous-menus), Détails, Options, Monitor, Renommer, Emplacement, Type — branchement vers `DiscoveryManager` / `utils/double_click_open.py` / lanceurs existants.
- [ ] Icônes appareils : réutiliser `ui/icons.py`, assets et cache remote comme aujourd’hui ; intégration `QIcon` / pixmaps.

---

## Phase 5 — Détails appareil

- [ ] Fenêtre ou dialog **Détails** avec onglets (aperçu, services, dépannage, TXT, données brutes, options / commandes) alignés sur le comportement 1.x.
- [ ] Règles de mapping, commandes personnalisées, validations — réutiliser la logique métier déjà centralisée où possible.

---

## Phase 6 — Plateforme : tray, notifications, démarrage session

- [ ] **Zone de notification** : `QSystemTrayIcon` + menu (Ouvrir, Réduire, Quitter).
- [ ] **Notifications** desktop : API Qt ou intégration par OS ; remplacer / adapter `utils/notifications.py`.
- [ ] **Démarrage à la connexion** : remplacer ou compléter `utils/session_autostart.py` (XDG Linux ; Startup Windows ; LaunchAgents macOS si applicable).
- [ ] Options **fermer vers le tray**, **démarrer minimisé**, cohérentes avec 1.x.

---

## Phase 7 — Retrait GTK et documentation

- [ ] Supprimer modules et utilitaires GTK-only : `app.py` (shell GTK), `ui/*.py` GTK, `utils/gtk_dialog.py`, imports `gi` restants.
- [ ] Nettoyer **`i18n.py`** des branches spécifiques GTK si devenues inutiles.
- [ ] Mettre à jour **`README.md`**, **`USER_DOCUMENTATION.md`**, **`docs/PACKAGING.md`**, **`docs/README.md`** (index), et remplacer ou archiver **`docs/UI_ARCHITECTURE.md`** par une **UI Qt** équivalente.
- [ ] Scripts / `.deb` / release : plus de dépendances `python3-gi`, AppIndicator, etc. ; ajouter notes **Windows / macOS**.

---

## Phase 8 — Qualification et release 2.0.0

- [ ] Checklist **smoke test** manuelle sur les trois OS (découverte, ouverture URL, tray, préférences, redémarrage).
- [ ] Tests automatisés ciblés (non régression discovery ; tests UI optionnels avec offscreen Qt si pertinent).
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
