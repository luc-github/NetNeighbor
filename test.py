import win32wnet
import win32netcon
import subprocess

def open_smb_with_prompt(ip: str, share: str = None):
    unc = f"\\\\{ip}"
    if share:
        unc += f"\\{share}"
    
    print(f"Ouverture de {unc} avec prompt de credentials...")
    
    try:
        # Nettoyage préalable (recommandé)
        try:
            win32wnet.WNetCancelConnection2(unc, 0, True)
        except:
            pass
        
        # Flags importants pour afficher la boîte de login
        flags = win32netcon.CONNECT_INTERACTIVE | win32netcon.CONNECT_PROMPT
        
        win32wnet.WNetAddConnection2(
            win32netcon.RESOURCETYPE_DISK,   # Type disque
            None,                            # Local name (None = pas de lettre de lecteur)
            unc,                             # UNC path
            None,                            # Provider
            "",                              # Username vide
            "",                              # Password vide
            flags                            # Flags interactifs
        )
        
        print("Connexion réussie ou prompt affiché.")
        
        # Ouvre l'explorateur une fois le prompt validé
        subprocess.Popen(['explorer.exe', unc])
        
    except Exception as e:
        # Erreur 1223 = utilisateur a annulé → normal
        if "1223" in str(e):
            print("Utilisateur a annulé la boîte de login.")
        else:
            print(f"Erreur : {e}")


# Utilisation
open_smb_with_prompt("192.168.1.104")
# open_smb_with_prompt("192.168.1.122", "MonDossier")
