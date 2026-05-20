#!/usr/bin/env python3
"""
Redimensionne toutes les images PNG d'un repertoire en multiples resolutions
avec ImageMagick. Cree un sous-repertoire pour chaque resolution.

Usage:
    python3 resize_icons.py <source_directory> [resolutions...]

Exemples:
    python3 resize_icons.py ./mes_icons
    python3 resize_icons.py ./mes_icons 16 32 64 128 256
    
Sous Windows avec ImageMagick 7+:
    python resize_icons.py .\1024x1024 16 22 32 48 64 96 128 256 512
"""

import os
import sys
import subprocess
import glob
import shutil
import platform

# Resolutions par defaut (KDE Crystal standard)
DEFAULT_RESOLUTIONS = [16, 22, 32, 48, 64, 96, 128, 256, 512]

def detect_imagemagick():
    """Detecte la commande ImageMagick disponible et sa version."""
    system = platform.system()
    
    # Sous Windows, essayer 'magick' d'abord (ImageMagick 7+)
    # puis 'convert' (ImageMagick 6) — mais attention, 'convert' 
    # est aussi une commande Windows native pour les volumes NTFS !
    if system == "Windows":
        for cmd in ["magick", "convert"]:
            if shutil.which(cmd):
                # Verifier que c'est bien ImageMagick
                try:
                    result = subprocess.run(
                        [cmd, "-version"], 
                        capture_output=True, text=True, timeout=5
                    )
                    if "ImageMagick" in result.stdout:
                        # ImageMagick 7+ : 'magick convert ...'
                        # ImageMagick 6 : 'convert ...'
                        if cmd == "magick":
                            return "magick convert", result.stdout.splitlines()[0]
                        return cmd, result.stdout.splitlines()[0]
                except Exception:
                    pass
    else:
        # Linux/Mac : 'convert' (IM6) ou 'magick' (IM7)
        for cmd in ["convert", "magick"]:
            if shutil.which(cmd):
                try:
                    result = subprocess.run(
                        [cmd, "-version"], 
                        capture_output=True, text=True, timeout=5
                    )
                    if "ImageMagick" in result.stdout:
                        if cmd == "magick":
                            return "magick convert", result.stdout.splitlines()[0]
                        return cmd, result.stdout.splitlines()[0]
                except Exception:
                    pass
    
    return None, None

def run(cmd_list):
    """Execute une commande et retourne (code, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd_list, 
            capture_output=True, 
            text=True, 
            timeout=30,
            shell=False  # shell=False pour securite et chemies avec espaces
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout apres 30 secondes"
    except FileNotFoundError as e:
        return -1, "", f"Commande introuvable: {e}"
    except Exception as e:
        return -1, "", str(e)

def get_png_files(source_dir):
    """Recupere la liste des fichiers PNG tries."""
    pattern = os.path.join(source_dir, "*.png")
    files = sorted(glob.glob(pattern))
    return [f for f in files if os.path.isfile(f)]

def resize_image(im_cmd, source_path, dest_path, size):
    """Redimensionne une image avec ImageMagick."""
    # Construire la commande ImageMagick
    # IM7: magick convert input -resize ... output
    # IM6: convert input -resize ... output
    
    cmd_parts = im_cmd.split()  # ex: ["magick", "convert"] ou ["convert"]
    
    args = cmd_parts + [
        source_path,
        "-resize", f"{size}x{size}",
        "-background", "none",
        "-gravity", "center",
        "-extent", f"{size}x{size}",
        "-quality", "100",
        dest_path
    ]
    
    return run(args)

def format_bar(current, total, width=40):
    """Cree une barre de progression."""
    progress = current / total if total > 0 else 0
    filled = int(width * progress)
    return '=' * filled + '-' * (width - filled)

def main():
    print("=" * 60)
    print("  Redimensionneur d'icones - ImageMagick")
    print("=" * 60)
    
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    source_dir = os.path.abspath(sys.argv[1])
    
    if not os.path.isdir(source_dir):
        print(f"\n[ERREUR] '{source_dir}' n'est pas un repertoire valide.")
        sys.exit(1)
    
    # Detection d'ImageMagick
    print("\n[1] Detection d'ImageMagick...")
    im_cmd, im_version = detect_imagemagick()
    
    if im_cmd is None:
        print("\n[ERREUR] ImageMagick n'a pas ete trouve !")
        print("\nInstallation:")
        print("  - Windows: telechargez https://imagemagick.org/script/download.php#windows")
        print("             ou choco install imagemagick")
        print("  - Linux:   sudo apt-get install imagemagick")
        print("  - Mac:     brew install imagemagick")
        sys.exit(1)
    
    print(f"    OK -> {im_cmd}")
    print(f"    {im_version}")
    
    # Resolutions
    if len(sys.argv) >= 3:
        try:
            resolutions = [int(x) for x in sys.argv[2:]]
        except ValueError:
            print("\n[ERREUR] Les resolutions doivent etre des entiers.")
            sys.exit(1)
    else:
        resolutions = DEFAULT_RESOLUTIONS
    
    # Fichiers PNG
    print(f"\n[2] Analyse du repertoire...")
    png_files = get_png_files(source_dir)
    
    if not png_files:
        print(f"\n[ERREUR] Aucun fichier PNG trouve dans '{source_dir}'")
        sys.exit(1)
    
    print(f"    Repertoire: {source_dir}")
    print(f"    Images:     {len(png_files)} fichiers PNG")
    print(f"    Sorties:    {', '.join(str(r) for r in resolutions)}")
    
    # Verification en redimensionnant une image test
    print(f"\n[3] Test avec la premiere image...")
    test_file = png_files[0]
    test_dest = os.path.join(source_dir, "_test_output.png")
    ret, out, err = resize_image(im_cmd, test_file, test_dest, 32)
    
    if ret != 0:
        print(f"\n[ERREUR] Le test a echoue !")
        print(f"\nCommande testee:")
        print(f"    {im_cmd} \"{test_file}\" -resize 32x32 ...")
        print(f"\nSortie stdout:\n    {out}")
        print(f"\nSortie stderr:\n    {err}")
        if os.path.exists(test_dest):
            os.remove(test_dest)
        sys.exit(1)
    
    os.remove(test_dest)
    print(f"    OK -> test reussi")
    
    # Traitement
    print(f"\n[4] Redimensionnement en cours...")
    print("-" * 60)
    
    total_files = len(png_files)
    total_ops = total_files * len(resolutions)
    current_op = 0
    success_count = 0
    error_count = 0
    errors_log = []
    
    for size in resolutions:
        dest_dir = os.path.join(source_dir, str(size))
        os.makedirs(dest_dir, exist_ok=True)
        
        print(f"\n--> {size}x{size}  ({dest_dir})")
        
        for i, filepath in enumerate(png_files, 1):
            current_op += 1
            filename = os.path.basename(filepath)
            dest_path = os.path.join(dest_dir, filename)
            
            ret, out, err = resize_image(im_cmd, filepath, dest_path, size)
            
            bar = format_bar(i, total_files)
            
            if ret == 0:
                success_count += 1
                print(f"\r  [{bar}] {i}/{total_files} {filename}", end="")
                sys.stdout.flush()
            else:
                error_count += 1
                errors_log.append({
                    'file': filename,
                    'size': size,
                    'stdout': out,
                    'stderr': err
                })
                print(f"\r  [ERREUR] {filename}")
                print(f"           stdout: {out.strip()}")
                print(f"           stderr: {err.strip()}")
    
    print()  # saut de ligne final
    print("-" * 60)
    print(f"\n[5] Resultat:")
    print(f"    Succes:  {success_count}")
    print(f"    Erreurs: {error_count}")
    print(f"    Total:   {current_op} operations")
    
    # Arborescence
    print(f"\n[6] Repertoires crees:")
    for size in resolutions:
        dest_dir = os.path.join(source_dir, str(size))
        count = len(glob.glob(os.path.join(dest_dir, "*.png")))
        status = "OK" if count == total_files else f"{count}/{total_files}"
        print(f"    {dest_dir:45s} -> {status}")
    
    # Log d'erreurs detaille
    if errors_log:
        print(f"\n[7] Details des erreurs:")
        for e in errors_log[:10]:  # Afficher max 10 erreurs
            print(f"\n    {e['file']} -> {e['size']}x{e['size']}")
            if e['stderr']:
                print(f"    stderr: {e['stderr'][:200]}")
        if len(errors_log) > 10:
            print(f"    ... et {len(errors_log) - 10} autres erreurs")
    
    print(f"\n{'=' * 60}")
    if error_count == 0:
        print("  TERMINE AVEC SUCCES !")
    else:
        print(f"  TERMINE AVEC {error_count} ERREUR(S)")
    print(f"{'=' * 60}")
    
    return 0 if error_count == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
