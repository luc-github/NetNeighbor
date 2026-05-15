# Environnement de développement — NetNeighbor 2.0 (Qt / PySide6)

Ce document liste les **prérequis par OS** pour coder et tester la branche Qt. Les paquets **Python** communs sont dans [`requirements-qt.txt`](../requirements-qt.txt) (`PySide6` + dépendances discovery existantes).

**Python** : **3.10 ou plus** (aligné avec le [`README.md`](../README.md) du dépôt).

---

## Commun (tous OS)

1. Créer un environnement virtuel (recommandé) :

   ```bash
   python -m venv .venv
   ```

2. Activer le venv puis installer (**obligatoire** ; sinon `ModuleNotFoundError: No module named 'PySide6'` même avec `(.venv)` dans le prompt) :

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements-qt.txt
   ```

   Sous **Windows**, si `Activate.ps1` ouvre un éditeur au lieu d’activer le venv, utilisez **`.\.venv\Scripts\activate.bat`** dans une invite de commandes ou PowerShell. Vous pouvez aussi éviter l’activation et appeler directement **`.\.venv\Scripts\python.exe -m pip install -r requirements-qt.txt`**.

3. **Backend discovery** (inchangé par rapport à 1.x) : même besoin réseau que l’app actuelle — multicast/broadcast LAN pour SSDP, mDNS, etc. Si rien n’apparaît, vérifier **pare-feu** et **interfaces réseau** (Wi‑Fi vs Ethernet, VPN).

---

## Windows (dev principal)

| Élément | Détail |
|--------|--------|
| **Python** | 3.10+ **64 bits** ([python.org](https://www.python.org/downloads/windows/) ou `winget install Python.Python.3.12`). Cocher « Add python.exe to PATH » à l’installation. |
| **Visual C++ Redistributable** | Les roues Qt/PySide6 lient souvent au runtime MSVC. Installer **[VC++ 2015–2022 x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)** si l’import `PySide6` ou le lancement plante avec une erreur DLL. |
| **Pare-feu** | Autoriser **python.exe** pour le réseau privé si la découverte reste vide. |
| **NetBIOS (`nmblookup`)** | Optionnel. La découverte NetBIOS du projet appelle l’outil **`nmblookup`** (Samba). Sous Windows il peut être absent du `PATH` ; sans lui, cette couche est inactive — le reste (SSDP, mDNS, WSD, etc.) fonctionne. |

Pas de paquets système obligatoires au-delà de Python + redist si besoin : **PySide6** est fourni en roue précompilée.

---

## Linux (double-check / CI)

| Élément | Détail |
|--------|--------|
| **Python** | `python3` 3.10+ ; paquets `python3-venv` (Debian/Ubuntu : `sudo apt install python3-venv python3-pip`). |
| **Bibliothèques Qt / X11 / Wayland** | Les roues **PySide6** embarquent Qt, mais le chargeur dynamique peut still nécessiter des libs système pour afficher une fenêtre. Si au lancement vous voyez une erreur du type *Could not load Qt platform plugin* ou *libxcb* : installer les paquets usuels pour Qt6 desktop, par exemple sur **Debian/Ubuntu** : `sudo apt install libxcb-xinerama0 libxcb-cursor0 libxkbcommon-x11-0 libegl1 libdbus-1-3` (liste indicative ; ajuster selon le message d’erreur exact). |
| **Wayland** | Sous session Wayland, Qt choisit généralement le bon backend ; en cas de souci, tester avec `QT_QPA_PLATFORM=xcb` pour forcer XWayland si besoin. |
| **Notifications / tray** | Dépend du bureau (DBus, extensions). À valider sur votre distro / session (X11 vs Wayland). |
| **WSL** | Pour une GUI Qt depuis WSL2 : **WSLg** (Windows 11) ou serveur X sur l’hôte ; sans ça, pas de fenêtre native. |

---

## macOS (quand une machine sera disponible)

| Élément | Détail |
|--------|--------|
| **Python** | 3.10+ via [python.org](https://www.python.org/downloads/macos/) ou **Homebrew** (`brew install python@3.12`). |
| **Outils de build** | **Xcode Command Line Tools** : `xcode-select --install` (utile pour certaines extensions ou outils, pas toujours pour les seules roues PySide6). |
| **Certificats / pare-feu** | Première exécution : macOS peut demander l’accès réseau pour Python ; accepter pour la découverte LAN. |

Les roues PySide6 pour macOS incluent les binaires Qt ; peu de dépendances Homebrew obligatoires pour démarrer.

---

## Vérification rapide après installation

```bash
python -c "from PySide6.QtWidgets import QApplication; print('PySide6 OK')"
python -c "import zeroconf; print('zeroconf OK')"
python -c "import wsdiscovery; print('wsdiscovery OK')"
python app_qt.py
```

La dernière commande ouvre une fenêtre **Qt preview** : le nombre d’appareils découverts doit s’incrémenter si le LAN est actif (fermer la fenêtre pour quitter).

---

## Voir aussi

- [`ROADMAP_QT_2_0.md`](ROADMAP_QT_2_0.md) — jalons et convention Windows + contrôles Linux.
- [`requirements-qt.txt`](../requirements-qt.txt) — liste pip pour la v2.
