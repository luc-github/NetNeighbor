#!/usr/bin/env python3
"""
Script de generation d'image preview en grille pour un repertoire d'images.

Usage:
    python3 grid_preview.py <repertoire> [options]

Exemples:
    python3 grid_preview.py ./clay3d
    python3 grid_preview.py ./darkrgb --cols 8 --thumb 128 --padding 10 --bg "#1a1a2e"
    python3 grid_preview.py ./whitefrost --title "White Frost Style" --label --font-size 10
"""

import os
import sys
import argparse
import math
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Erreur: Pillow n'est pas installe.")
    print("Installation: pip install Pillow")
    sys.exit(1)


def get_images(directory, extensions=None):
    """Recupere et trie les fichiers images du repertoire."""
    if extensions is None:
        extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
    
    files = []
    for f in sorted(os.listdir(directory)):
        if f.lower().endswith(extensions):
            path = os.path.join(directory, f)
            if os.path.isfile(path):
                files.append(path)
    return files


def load_font(size):
    """Charge une police adaptee au systeme."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",              # Mac
        "C:/Windows/Fonts/arial.ttf",                        # Windows
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    
    return ImageFont.load_default()


def create_grid_preview(
    image_paths,
    output_path,
    thumb_size=256,
    cols=6,
    padding=20,
    margin=30,
    background=(30, 30, 46),
    show_labels=True,
    label_color=(200, 200, 220),
    font_size=11,
    title=None,
    title_color=(255, 255, 255),
    title_font_size=28,
    gap=4,
    label_height=35,
    title_height=60
):
    """Cree une image preview en grille."""
    
    num_images = len(image_paths)
    if num_images == 0:
        print("Aucune image trouvee.")
        return False
    
    rows = math.ceil(num_images / cols)
    
    # Calcul des dimensions
    cell_width = thumb_size + gap * 2
    cell_height = thumb_size + label_height if show_labels else thumb_size
    
    grid_width = margin * 2 + cols * cell_width + (cols - 1) * padding
    grid_height = margin * 2 + rows * cell_height + (rows - 1) * padding
    
    if title:
        grid_height += title_height
    
    # Creation de l'image
    img = Image.new('RGB', (grid_width, grid_height), background)
    draw = ImageDraw.Draw(img)
    
    # Titre
    y_offset = margin
    if title:
        title_font = load_font(title_font_size)
        bbox = draw.textbbox((0, 0), title, font=title_font)
        title_w = bbox[2] - bbox[0]
        title_x = (grid_width - title_w) // 2
        draw.text((title_x, y_offset), title, fill=title_color, font=title_font)
        y_offset += title_height
    
    # Police pour les labels
    label_font = load_font(font_size) if show_labels else None
    
    # Placement des images
    for idx, path in enumerate(image_paths):
        row = idx // cols
        col = idx % cols
        
        x = margin + col * (cell_width + padding)
        y = y_offset + row * (cell_height + padding)
        
        try:
            thumb = Image.open(path)
            thumb = thumb.convert('RGBA')
            
            # Redimensionnement avec conservation ratio
            thumb.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
            
            # Centrage dans la case
            tx = x + (cell_width - thumb.width) // 2
            ty = y + (thumb_size - thumb.height) // 2
            
            # Fond blanc pour les images transparentes
            if thumb.mode == 'RGBA':
                bg = Image.new('RGBA', thumb.size, background + (255,))
                thumb = Image.alpha_composite(bg, thumb)
                thumb = thumb.convert('RGB')
            
            img.paste(thumb, (tx, ty))
            
        except Exception as e:
            print(f"  Erreur chargement {os.path.basename(path)}: {e}")
            continue
        
        # Label
        if show_labels:
            name = os.path.splitext(os.path.basename(path))[0]
            # Tronquer si trop long
            max_chars = thumb_size // 7
            if len(name) > max_chars:
                name = name[:max_chars - 2] + ".."
            
            bbox = draw.textbbox((0, 0), name, font=label_font)
            text_w = bbox[2] - bbox[0]
            text_x = x + (cell_width - text_w) // 2
            text_y = y + thumb_size + 8
            
            draw.text((text_x, text_y), name, fill=label_color, font=label_font)
    
    img.save(output_path, quality=95, optimize=True)
    print(f"\nPreview sauvegardee: {output_path}")
    print(f"  Dimensions: {grid_width}x{grid_height}")
    print(f"  Images: {num_images} ({cols} colonnes x {rows} lignes)")
    print(f"  Miniatures: {thumb_size}x{thumb_size}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Genere une image preview en grille a partir d'un repertoire d'images.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  %(prog)s ./clay3d
  %(prog)s ./darkrgb --cols 8 --thumb 128 --bg "#1a1a2e"
  %(prog)s ./whitefrost --title "White Frost" --label --font-size 10
  %(prog)s ./win11fluent --cols 10 --thumb 96 --no-label --padding 5
        """
    )
    
    parser.add_argument("directory", help="Repertoire contenant les images")
    parser.add_argument("-o", "--output", default=None, 
                        help="Chemin de sortie (defaut: <repertoire>_preview.png)")
    parser.add_argument("--cols", type=int, default=8,
                        help="Nombre de colonnes (defaut: 8)")
    parser.add_argument("--thumb", type=int, default=128,
                        help="Taille des miniatures en pixels (defaut: 128)")
    parser.add_argument("--padding", type=int, default=15,
                        help="Espacement entre les cellules en pixels (defaut: 15)")
    parser.add_argument("--margin", type=int, default=25,
                        help="Marge exterieure en pixels (defaut: 25)")
    parser.add_argument("--bg", type=str, default="#1e1e2e",
                        help="Couleur de fond en hex (defaut: #1e1e2e)")
    parser.add_argument("--title", type=str, default=None,
                        help="Titre affiche en haut de l'image")
    parser.add_argument("--label", dest="show_labels", action="store_true", default=True,
                        help="Afficher les noms de fichiers (defaut: oui)")
    parser.add_argument("--no-label", dest="show_labels", action="store_false",
                        help="Masquer les noms de fichiers")
    parser.add_argument("--font-size", type=int, default=10,
                        help="Taille de police des labels (defaut: 10)")
    parser.add_argument("--title-font-size", type=int, default=24,
                        help="Taille de police du titre (defaut: 24)")
    
    args = parser.parse_args()
    
    # Verifier le repertoire
    if not os.path.isdir(args.directory):
        print(f"Erreur: '{args.directory}' n'est pas un repertoire valide.")
        sys.exit(1)
    
    # Recuperer les images
    print(f"Analyse de: {args.directory}")
    image_paths = get_images(args.directory)
    
    if not image_paths:
        print("Aucune image trouvee dans ce repertoire.")
        sys.exit(1)
    
    print(f"Images trouvees: {len(image_paths)}")
    
    # Chemin de sortie par defaut
    if args.output is None:
        dir_name = os.path.basename(os.path.abspath(args.directory))
        args.output = f"{dir_name}_preview.png"
    
    # Convertir couleur hex en RGB
    bg_hex = args.bg.lstrip('#')
    background = tuple(int(bg_hex[i:i+2], 16) for i in (0, 2, 4))
    
    # Generer la preview
    success = create_grid_preview(
        image_paths=image_paths,
        output_path=args.output,
        thumb_size=args.thumb,
        cols=args.cols,
        padding=args.padding,
        margin=args.margin,
        background=background,
        show_labels=args.show_labels,
        font_size=args.font_size,
        title=args.title,
        title_font_size=args.title_font_size
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
