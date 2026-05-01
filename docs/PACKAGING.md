# Packaging (`.deb` + `tar.gz`)

This project ships helper scripts under `packaging/` to build:

- a Debian package (`.deb`) for Ubuntu/Debian/Linux Mint
- a portable source/runtime archive (`tar.gz`)

## Prerequisites (build machine)

- Debian/Ubuntu-like environment
- `git`
- `dpkg-deb`
- `tar`

Optional (post-install hooks in `.deb` call these if available):

- `desktop-file-utils` (`update-desktop-database`)
- `gtk-update-icon-cache`

## Build commands

From repository root:

```bash
chmod +x packaging/build_deb.sh packaging/build_tarball.sh packaging/netneighbor
./packaging/build_deb.sh 0.6.0
./packaging/build_tarball.sh 0.6.0
```

Outputs:

- `dist/netneighbor_0.6.0_<arch>.deb`
- `dist/netneighbor-0.6.0.tar.gz`

## `.deb` package layout

- App files: `/usr/share/netneighbor/`
- Launcher: `/usr/bin/netneighbor`
- Desktop entry: `/usr/share/applications/netneighbor.desktop`
- Icon: `/usr/share/icons/hicolor/256x256/apps/io.esp3d.netneighbor.png`

The `.deb` embeds runtime-relevant tracked paths only (`discovery/`, `ui/`, `utils/`, `model/`, `data/`, `config/`, `assets/`, `locale/`, plus entrypoint/docs files listed in `packaging/build_deb.sh`).

Desktop category is network-oriented:

```ini
Categories=Network;Utility;GTK;
```

## Runtime dependencies in `.deb`

`control` currently declares:

- `python3`
- `python3-gi`
- `python3-gi-cairo`
- `gir1.2-gtk-3.0`
- `python3-zeroconf`

## Install / uninstall (`.deb`)

```bash
sudo apt install ./dist/netneighbor_0.6.0_amd64.deb
netneighbor
sudo apt remove netneighbor
```

Notes:

- `apt remove` / `apt purge` do not delete per-user data under `~/.config/netneighbor` and `~/.cache/netneighbor`.
- For explicit user-data cleanup, run:

```bash
./packaging/cleanup-user-data.sh
```

## `tar.gz` usage

```bash
tar -xzf dist/netneighbor-0.6.0.tar.gz
cd netneighbor-0.6.0
./run.sh
```

Notes:

- `tar.gz` does not register desktop launchers/icons automatically.
- `install-desktop.sh` included in the tarball installs launcher + icon under `~/.local`.
- System GTK/PyGObject dependencies must already be present.
