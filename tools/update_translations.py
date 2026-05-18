"""Update .pot and all .po files with new translatable strings, then compile .mo files."""
import ast
import io
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCE_DIRS = [ROOT / "ui_qt", ROOT / "discovery", ROOT / "utils"]

# ---------------------------------------------------------------------------
# Translations for all 74 new strings.
# Keys are exact msgid strings; values are dicts keyed by locale code.
# ---------------------------------------------------------------------------
TRANSLATIONS: dict[str, dict[str, str]] = {
    "About NetNeighbor": {
        "fr": "À propos de NetNeighbor",
        "de": "Über NetNeighbor",
        "es": "Acerca de NetNeighbor",
        "it": "Informazioni su NetNeighbor",
        "nl": "Over NetNeighbor",
        "ja": "NetNeighbor について",
        "zh_CN": "关于 NetNeighbor",
        "zh_TW": "關於 NetNeighbor",
    },
    "App logo under assets/icons: Luc LEBOSSE": {
        "fr": "Logo de l'application dans assets/icons : Luc LEBOSSE",
        "de": "App-Logo unter assets/icons: Luc LEBOSSE",
        "es": "Logo de la app en assets/icons: Luc LEBOSSE",
        "it": "Logo app in assets/icons: Luc LEBOSSE",
        "nl": "App-logo onder assets/icons: Luc LEBOSSE",
        "ja": "アプリロゴ（assets/icons）: Luc LEBOSSE",
        "zh_CN": "应用图标来自 assets/icons：Luc LEBOSSE",
        "zh_TW": "應用程式圖示位於 assets/icons：Luc LEBOSSE",
    },
    "Applications": {
        "fr": "Applications",
        "de": "Anwendungen",
        "es": "Aplicaciones",
        "it": "Applicazioni",
        "nl": "Toepassingen",
        "ja": "アプリケーション",
        "zh_CN": "应用程序",
        "zh_TW": "應用程式",
    },
    "Apply": {
        "fr": "Appliquer",
        "de": "Anwenden",
        "es": "Aplicar",
        "it": "Applica",
        "nl": "Toepassen",
        "ja": "適用",
        "zh_CN": "应用",
        "zh_TW": "套用",
    },
    "Author: Luc": {
        "fr": "Auteur : Luc",
        "de": "Autor: Luc",
        "es": "Autor: Luc",
        "it": "Autore: Luc",
        "nl": "Auteur: Luc",
        "ja": "作者: Luc",
        "zh_CN": "作者：Luc",
        "zh_TW": "作者：Luc",
    },
    "Close to system tray / panel instead of exiting": {
        "fr": "Réduire dans la barre système au lieu de quitter",
        "de": "In den Systembereich schließen statt beenden",
        "es": "Cerrar al área de notificación en lugar de salir",
        "it": "Chiudi nell'area di notifica invece di uscire",
        "nl": "Sluiten naar systeemvak in plaats van afsluiten",
        "ja": "終了の代わりにシステムトレイに閉じる",
        "zh_CN": "关闭到系统托盘而非退出",
        "zh_TW": "關閉至系統匣而非結束",
    },
    "Command": {
        "fr": "Commande",
        "de": "Befehl",
        "es": "Comando",
        "it": "Comando",
        "nl": "Opdracht",
        "ja": "コマンド",
        "zh_CN": "命令",
        "zh_TW": "命令",
    },
    "Command templates for opening device connections. Leave empty to use the system default.": {
        "fr": "Modèles de commandes pour ouvrir les connexions d'appareils. Laisser vide pour utiliser le défaut système.",
        "de": "Befehlsvorlagen zum Öffnen von Geräteverbindungen. Leer lassen, um die Systemstandards zu verwenden.",
        "es": "Plantillas de comandos para abrir conexiones de dispositivos. Dejar vacío para usar el valor predeterminado del sistema.",
        "it": "Modelli di comandi per aprire connessioni ai dispositivi. Lasciare vuoto per usare il predefinito di sistema.",
        "nl": "Opdrachtsjablonen voor het openen van apparaatverbindingen. Leeg laten om de systeemstandaard te gebruiken.",
        "ja": "デバイス接続を開くためのコマンドテンプレート。システムのデフォルトを使用するには空白のままにしてください。",
        "zh_CN": "打开设备连接的命令模板。留空则使用系统默认值。",
        "zh_TW": "開啟裝置連線的命令範本。留空則使用系統預設值。",
    },
    "Commands": {
        "fr": "Commandes",
        "de": "Befehle",
        "es": "Comandos",
        "it": "Comandi",
        "nl": "Opdrachten",
        "ja": "コマンド",
        "zh_CN": "命令",
        "zh_TW": "命令",
    },
    "Custom command (additional)": {
        "fr": "Commande personnalisée (supplémentaire)",
        "de": "Benutzerdefinierter Befehl (zusätzlich)",
        "es": "Comando personalizado (adicional)",
        "it": "Comando personalizzato (aggiuntivo)",
        "nl": "Aangepaste opdracht (aanvullend)",
        "ja": "カスタムコマンド（追加）",
        "zh_CN": "自定义命令（附加）",
        "zh_TW": "自訂命令（附加）",
    },
    "Dark": {
        "fr": "Sombre",
        "de": "Dunkel",
        "es": "Oscuro",
        "it": "Scuro",
        "nl": "Donker",
        "ja": "ダーク",
        "zh_CN": "深色",
        "zh_TW": "深色",
    },
    "Date / Time": {
        "fr": "Date / Heure",
        "de": "Datum / Uhrzeit",
        "es": "Fecha / Hora",
        "it": "Data / Ora",
        "nl": "Datum / Tijd",
        "ja": "日付 / 時刻",
        "zh_CN": "日期 / 时间",
        "zh_TW": "日期 / 時間",
    },
    "Delete": {
        "fr": "Supprimer",
        "de": "Löschen",
        "es": "Eliminar",
        "it": "Elimina",
        "nl": "Verwijderen",
        "ja": "削除",
        "zh_CN": "删除",
        "zh_TW": "刪除",
    },
    "Desktop notifications": {
        "fr": "Notifications de bureau",
        "de": "Desktop-Benachrichtigungen",
        "es": "Notificaciones de escritorio",
        "it": "Notifiche desktop",
        "nl": "Bureaubladmeldingen",
        "ja": "デスクトップ通知",
        "zh_CN": "桌面通知",
        "zh_TW": "桌面通知",
    },
    "Discovery refresh requested": {
        "fr": "Actualisation de la découverte demandée",
        "de": "Erkennung-Aktualisierung angefordert",
        "es": "Actualización de detección solicitada",
        "it": "Aggiornamento rilevamento richiesto",
        "nl": "Ontdekkingsvernieuwing aangevraagd",
        "ja": "検出の更新を要求しました",
        "zh_CN": "已请求刷新发现",
        "zh_TW": "已要求重新整理探索",
    },
    "Edit": {
        "fr": "Modifier",
        "de": "Bearbeiten",
        "es": "Editar",
        "it": "Modifica",
        "nl": "Bewerken",
        "ja": "編集",
        "zh_CN": "编辑",
        "zh_TW": "編輯",
    },
    "Edit type preset": {
        "fr": "Modifier le préréglage de type",
        "de": "Typvoreinstellung bearbeiten",
        "es": "Editar preajuste de tipo",
        "it": "Modifica preset tipo",
        "nl": "Typepreset bewerken",
        "ja": "タイププリセットを編集",
        "zh_CN": "编辑类型预设",
        "zh_TW": "編輯類型預設",
    },
    "Error": {
        "fr": "Erreur",
        "de": "Fehler",
        "es": "Error",
        "it": "Errore",
        "nl": "Fout",
        "ja": "エラー",
        "zh_CN": "错误",
        "zh_TW": "錯誤",
    },
    "Extra large": {
        "fr": "Très grand",
        "de": "Sehr groß",
        "es": "Muy grande",
        "it": "Extra grande",
        "nl": "Extra groot",
        "ja": "特大",
        "zh_CN": "特大",
        "zh_TW": "特大",
    },
    "Field mapping rules": {
        "fr": "Règles de mappage de champs",
        "de": "Feldzuordnungsregeln",
        "es": "Reglas de mapeo de campos",
        "it": "Regole di mappatura campi",
        "nl": "Veldtoewijzingsregels",
        "ja": "フィールドマッピングルール",
        "zh_CN": "字段映射规则",
        "zh_TW": "欄位對應規則",
    },
    "GNU Lesser General Public License version 3 or later": {
        "fr": "Licence publique générale limitée GNU version 3 ou ultérieure",
        "de": "GNU Lesser General Public License Version 3 oder später",
        "es": "Licencia Pública General Reducida de GNU versión 3 o posterior",
        "it": "Licenza pubblica generica minore GNU versione 3 o successiva",
        "nl": "GNU Lesser General Public License versie 3 of later",
        "ja": "GNU劣等一般公衆利用許諾書 バージョン3以降",
        "zh_CN": "GNU 宽通用公共许可证第3版或更高版本",
        "zh_TW": "GNU 寬鬆通用公共授權條款第3版或更高版本",
    },
    "General": {
        "fr": "Général",
        "de": "Allgemein",
        "es": "General",
        "it": "Generale",
        "nl": "Algemeen",
        "ja": "一般",
        "zh_CN": "通用",
        "zh_TW": "一般",
    },
    "Hidden devices": {
        "fr": "Appareils masqués",
        "de": "Ausgeblendete Geräte",
        "es": "Dispositivos ocultos",
        "it": "Dispositivi nascosti",
        "nl": "Verborgen apparaten",
        "ja": "非表示デバイス",
        "zh_CN": "隐藏的设备",
        "zh_TW": "隱藏的裝置",
    },
    "Hide device": {
        "fr": "Masquer l'appareil",
        "de": "Gerät ausblenden",
        "es": "Ocultar dispositivo",
        "it": "Nascondi dispositivo",
        "nl": "Apparaat verbergen",
        "ja": "デバイスを非表示",
        "zh_CN": "隐藏设备",
        "zh_TW": "隱藏裝置",
    },
    "Hide sidebar": {
        "fr": "Masquer la barre latérale",
        "de": "Seitenleiste ausblenden",
        "es": "Ocultar barra lateral",
        "it": "Nascondi barra laterale",
        "nl": "Zijbalk verbergen",
        "ja": "サイドバーを非表示",
        "zh_CN": "隐藏侧边栏",
        "zh_TW": "隱藏側邊欄",
    },
    "Hide window": {
        "fr": "Masquer la fenêtre",
        "de": "Fenster ausblenden",
        "es": "Ocultar ventana",
        "it": "Nascondi finestra",
        "nl": "Venster verbergen",
        "ja": "ウィンドウを非表示",
        "zh_CN": "隐藏窗口",
        "zh_TW": "隱藏視窗",
    },
    "Icons size": {
        "fr": "Taille des icônes",
        "de": "Symbolgröße",
        "es": "Tamaño de iconos",
        "it": "Dimensione icone",
        "nl": "Pictogramgrootte",
        "ja": "アイコンサイズ",
        "zh_CN": "图标大小",
        "zh_TW": "圖示大小",
    },
    "Invalid command template for this protocol.": {
        "fr": "Modèle de commande invalide pour ce protocole.",
        "de": "Ungültige Befehlsvorlage für dieses Protokoll.",
        "es": "Plantilla de comando no válida para este protocolo.",
        "it": "Modello di comando non valido per questo protocollo.",
        "nl": "Ongeldig opdrachtsjabloon voor dit protocol.",
        "ja": "このプロトコルに対するコマンドテンプレートが無効です。",
        "zh_CN": "此协议的命令模板无效。",
        "zh_TW": "此協議的命令範本無效。",
    },
    "Large": {
        "fr": "Grand",
        "de": "Groß",
        "es": "Grande",
        "it": "Grande",
        "nl": "Groot",
        "ja": "大",
        "zh_CN": "大",
        "zh_TW": "大",
    },
    "Last seen: {n} d ago": {
        "fr": "Vu il y a : {n} j",
        "de": "Zuletzt gesehen: vor {n} Tagen",
        "es": "Visto hace: {n} d",
        "it": "Visto: {n} g fa",
        "nl": "Gezien: {n} dagen geleden",
        "ja": "最終確認: {n} 日前",
        "zh_CN": "最后发现：{n} 天前",
        "zh_TW": "最後發現：{n} 天前",
    },
    "Last seen: {n} h ago": {
        "fr": "Vu il y a : {n} h",
        "de": "Zuletzt gesehen: vor {n} Std.",
        "es": "Visto hace: {n} h",
        "it": "Visto: {n} h fa",
        "nl": "Gezien: {n} uur geleden",
        "ja": "最終確認: {n} 時間前",
        "zh_CN": "最后发现：{n} 小时前",
        "zh_TW": "最後發現：{n} 小時前",
    },
    "Last seen: {n} min ago": {
        "fr": "Vu il y a : {n} min",
        "de": "Zuletzt gesehen: vor {n} Min.",
        "es": "Visto hace: {n} min",
        "it": "Visto: {n} min fa",
        "nl": "Gezien: {n} min geleden",
        "ja": "最終確認: {n} 分前",
        "zh_CN": "最后发现：{n} 分钟前",
        "zh_TW": "最後發現：{n} 分鐘前",
    },
    "Light": {
        "fr": "Clair",
        "de": "Hell",
        "es": "Claro",
        "it": "Chiaro",
        "nl": "Licht",
        "ja": "ライト",
        "zh_CN": "浅色",
        "zh_TW": "淺色",
    },
    "Location name:": {
        "fr": "Nom de l'emplacement :",
        "de": "Standortname:",
        "es": "Nombre de ubicación:",
        "it": "Nome posizione:",
        "nl": "Locatienaam:",
        "ja": "場所の名前：",
        "zh_CN": "位置名称：",
        "zh_TW": "位置名稱：",
    },
    "Locations": {
        "fr": "Emplacements",
        "de": "Standorte",
        "es": "Ubicaciones",
        "it": "Posizioni",
        "nl": "Locaties",
        "ja": "場所",
        "zh_CN": "位置",
        "zh_TW": "位置",
    },
    "Manage location presets shown in the right-click menu.": {
        "fr": "Gérer les préréglages de lieu affichés dans le menu contextuel.",
        "de": "Standortvoreinstellungen im Kontextmenü verwalten.",
        "es": "Administrar los preajustes de ubicación mostrados en el menú contextual.",
        "it": "Gestisci i preset di posizione mostrati nel menu contestuale.",
        "nl": "Locatiepresets beheren die worden weergegeven in het contextmenu.",
        "ja": "右クリックメニューに表示される場所プリセットを管理します。",
        "zh_CN": "管理右键菜单中显示的位置预设。",
        "zh_TW": "管理右鍵選單中顯示的位置預設。",
    },
    "Medium": {
        "fr": "Moyen",
        "de": "Mittel",
        "es": "Mediano",
        "it": "Medio",
        "nl": "Middel",
        "ja": "中",
        "zh_CN": "中",
        "zh_TW": "中",
    },
    "Mode": {
        "fr": "Mode",
        "de": "Modus",
        "es": "Modo",
        "it": "Modalità",
        "nl": "Modus",
        "ja": "モード",
        "zh_CN": "模式",
        "zh_TW": "模式",
    },
    "NetNeighbor {}": {
        "fr": "NetNeighbor {}",
        "de": "NetNeighbor {}",
        "es": "NetNeighbor {}",
        "it": "NetNeighbor {}",
        "nl": "NetNeighbor {}",
        "ja": "NetNeighbor {}",
        "zh_CN": "NetNeighbor {}",
        "zh_TW": "NetNeighbor {}",
    },
    "New name:": {
        "fr": "Nouveau nom :",
        "de": "Neuer Name:",
        "es": "Nuevo nombre:",
        "it": "Nuovo nome:",
        "nl": "Nieuwe naam:",
        "ja": "新しい名前：",
        "zh_CN": "新名称：",
        "zh_TW": "新名稱：",
    },
    "No devices yet": {
        "fr": "Aucun appareil pour l'instant",
        "de": "Noch keine Geräte",
        "es": "Aún no hay dispositivos",
        "it": "Nessun dispositivo ancora",
        "nl": "Nog geen apparaten",
        "ja": "デバイスがありません",
        "zh_CN": "暂无设备",
        "zh_TW": "尚無裝置",
    },
    "No hidden devices": {
        "fr": "Aucun appareil masqué",
        "de": "Keine ausgeblendeten Geräte",
        "es": "No hay dispositivos ocultos",
        "it": "Nessun dispositivo nascosto",
        "nl": "Geen verborgen apparaten",
        "ja": "非表示デバイスなし",
        "zh_CN": "没有隐藏的设备",
        "zh_TW": "沒有隱藏的裝置",
    },
    "No icons found. Add PNGs under assets/icons/bundled-freedesktop or ~/.config/netneighbor/custom_icons/.": {
        "fr": "Aucune icône trouvée. Ajoutez des PNG dans assets/icons/bundled-freedesktop ou ~/.config/netneighbor/custom_icons/.",
        "de": "Keine Symbole gefunden. Fügen Sie PNGs unter assets/icons/bundled-freedesktop oder ~/.config/netneighbor/custom_icons/ hinzu.",
        "es": "No se encontraron iconos. Agregue PNGs en assets/icons/bundled-freedesktop o ~/.config/netneighbor/custom_icons/.",
        "it": "Nessuna icona trovata. Aggiungere PNG in assets/icons/bundled-freedesktop o ~/.config/netneighbor/custom_icons/.",
        "nl": "Geen pictogrammen gevonden. Voeg PNG's toe onder assets/icons/bundled-freedesktop of ~/.config/netneighbor/custom_icons/.",
        "ja": "アイコンが見つかりません。assets/icons/bundled-freedesktop または ~/.config/netneighbor/custom_icons/ に PNG を追加してください。",
        "zh_CN": "未找到图标。请在 assets/icons/bundled-freedesktop 或 ~/.config/netneighbor/custom_icons/ 中添加 PNG。",
        "zh_TW": "找不到圖示。請在 assets/icons/bundled-freedesktop 或 ~/.config/netneighbor/custom_icons/ 下新增 PNG。",
    },
    "Notification delivery depends on the OS and session. Tray-backed behaviour (close to tray, minimized start) will align once the system tray is implemented.": {
        "fr": "La livraison des notifications dépend du système d'exploitation et de la session. Le comportement basé sur la barre système (fermeture dans la barre, démarrage minimisé) sera aligné une fois la barre système implémentée.",
        "de": "Die Benachrichtigungsübermittlung hängt vom Betriebssystem und der Sitzung ab. Das tray-basierte Verhalten (In Tray schließen, minimierter Start) wird ausgerichtet, sobald der Systemtray implementiert ist.",
        "es": "La entrega de notificaciones depende del sistema operativo y la sesión. El comportamiento respaldado por la bandeja (cerrar en la bandeja, inicio minimizado) se alineará una vez que se implemente la bandeja del sistema.",
        "it": "La consegna delle notifiche dipende dal sistema operativo e dalla sessione. Il comportamento basato sul vassoio (chiusura nel vassoio, avvio minimizzato) sarà allineato una volta implementato il vassoio di sistema.",
        "nl": "Meldingslevering is afhankelijk van het besturingssysteem en de sessie. Het tray-gebaseerde gedrag (sluiten naar tray, geminimaliseerd starten) wordt uitgelijnd zodra de systeemtray is geïmplementeerd.",
        "ja": "通知の配信はOSとセッションに依存します。トレイベースの動作（トレイに閉じる、最小化起動）はシステムトレイが実装されると揃います。",
        "zh_CN": "通知的发送取决于操作系统和会话。基于托盘的行为（关闭到托盘、最小化启动）将在系统托盘实现后对齐。",
        "zh_TW": "通知的發送取決於作業系統和工作階段。基於托盤的行為（關閉至托盤、最小化啟動）將在系統托盤實作後對齊。",
    },
    "Notifications": {
        "fr": "Notifications",
        "de": "Benachrichtigungen",
        "es": "Notificaciones",
        "it": "Notifiche",
        "nl": "Meldingen",
        "ja": "通知",
        "zh_CN": "通知",
        "zh_TW": "通知",
    },
    "Offline": {
        "fr": "Hors ligne",
        "de": "Offline",
        "es": "Sin conexión",
        "it": "Offline",
        "nl": "Offline",
        "ja": "オフライン",
        "zh_CN": "离线",
        "zh_TW": "離線",
    },
    "Online": {
        "fr": "En ligne",
        "de": "Online",
        "es": "En línea",
        "it": "Online",
        "nl": "Online",
        "ja": "オンライン",
        "zh_CN": "在线",
        "zh_TW": "線上",
    },
    "Override or add connection endpoints per protocol. For 'custom' scheme, enter the command template in IP/Host ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}). IP/Host and Port are optional for other schemes.": {
        "fr": "Remplacez ou ajoutez des points de connexion par protocole. Pour le schème 'custom', entrez le modèle de commande dans IP/Hôte ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}). IP/Hôte et Port sont optionnels pour les autres schèmes.",
        "de": "Verbindungsendpunkte pro Protokoll überschreiben oder hinzufügen. Für das Schema 'custom' geben Sie die Befehlsvorlage in IP/Host ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}) ein. IP/Host und Port sind für andere Schemata optional.",
        "es": "Anular o agregar puntos de conexión por protocolo. Para el esquema 'custom', ingrese la plantilla de comando en IP/Host ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}). IP/Host y Puerto son opcionales para otros esquemas.",
        "it": "Sovrascrivere o aggiungere endpoint di connessione per protocollo. Per lo schema 'custom', inserire il modello di comando in IP/Host ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}). IP/Host e Porta sono opzionali per altri schemi.",
        "nl": "Verbindingseindpunten per protocol overschrijven of toevoegen. Voor het schema 'custom' voert u de opdrachtsjabloon in IP/Host ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}) in. IP/Host en poort zijn optioneel voor andere schema's.",
        "ja": "プロトコルごとに接続エンドポイントを上書きまたは追加します。'custom' スキームの場合、IP/ホスト ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}) のコマンドテンプレートを入力してください。IP/ホストとポートは他のスキームでは省略可能です。",
        "zh_CN": "按协议覆盖或添加连接端点。对于 'custom' 方案，请在 IP/主机 ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}) 中输入命令模板。对于其他方案，IP/主机和端口是可选的。",
        "zh_TW": "依協議覆蓋或新增連線端點。對於 'custom' 方案，請在 IP/主機 ({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}) 中輸入命令範本。對於其他方案，IP/主機和連接埠是選擇性的。",
    },
    "Preferences…": {
        "fr": "Préférences…",
        "de": "Einstellungen…",
        "es": "Preferencias…",
        "it": "Preferenze…",
        "nl": "Voorkeuren…",
        "ja": "設定…",
        "zh_CN": "首选项…",
        "zh_TW": "偏好設定…",
    },
    "Qt for Python bindings": {
        "fr": "Liaisons Qt pour Python",
        "de": "Qt-Python-Bindungen",
        "es": "Enlaces Qt para Python",
        "it": "Binding Qt per Python",
        "nl": "Qt voor Python-bindingen",
        "ja": "Qt for Python バインディング",
        "zh_CN": "Qt for Python 绑定",
        "zh_TW": "Qt for Python 繫結",
    },
    "Reload discovery": {
        "fr": "Recharger la découverte",
        "de": "Erkennung neu laden",
        "es": "Recargar detección",
        "it": "Ricarica rilevamento",
        "nl": "Ontdekking herladen",
        "ja": "検出を再読み込み",
        "zh_CN": "重新加载发现",
        "zh_TW": "重新載入探索",
    },
    "Scheme": {
        "fr": "Schème",
        "de": "Schema",
        "es": "Esquema",
        "it": "Schema",
        "nl": "Schema",
        "ja": "スキーム",
        "zh_CN": "方案",
        "zh_TW": "方案",
    },
    "See the": {
        "fr": "Voir la",
        "de": "Siehe die",
        "es": "Consulte la",
        "it": "Vedere la",
        "nl": "Zie de",
        "ja": "詳細については",
        "zh_CN": "请参阅",
        "zh_TW": "請參閱",
    },
    "Select an icon, then OK. IDs are stored in your preferences.": {
        "fr": "Sélectionnez une icône, puis OK. Les IDs sont enregistrés dans vos préférences.",
        "de": "Wählen Sie ein Symbol, dann OK. IDs werden in Ihren Einstellungen gespeichert.",
        "es": "Seleccione un icono, luego OK. Los IDs se guardan en sus preferencias.",
        "it": "Seleziona un'icona, poi OK. Gli ID vengono salvati nelle preferenze.",
        "nl": "Selecteer een pictogram, dan OK. ID's worden opgeslagen in uw voorkeuren.",
        "ja": "アイコンを選択してOKを押してください。IDは設定に保存されます。",
        "zh_CN": "选择图标，然后点击确定。ID 存储在您的首选项中。",
        "zh_TW": "選擇圖示，然後點擊確定。ID 儲存在您的偏好設定中。",
    },
    "Service type": {
        "fr": "Type de service",
        "de": "Diensttyp",
        "es": "Tipo de servicio",
        "it": "Tipo di servizio",
        "nl": "Servicetype",
        "ja": "サービスタイプ",
        "zh_CN": "服务类型",
        "zh_TW": "服務類型",
    },
    "Session": {
        "fr": "Session",
        "de": "Sitzung",
        "es": "Sesión",
        "it": "Sessione",
        "nl": "Sessie",
        "ja": "セッション",
        "zh_CN": "会话",
        "zh_TW": "工作階段",
    },
    "Show sidebar": {
        "fr": "Afficher la barre latérale",
        "de": "Seitenleiste anzeigen",
        "es": "Mostrar barra lateral",
        "it": "Mostra barra laterale",
        "nl": "Zijbalk tonen",
        "ja": "サイドバーを表示",
        "zh_CN": "显示侧边栏",
        "zh_TW": "顯示側邊欄",
    },
    "Show window": {
        "fr": "Afficher la fenêtre",
        "de": "Fenster anzeigen",
        "es": "Mostrar ventana",
        "it": "Mostra finestra",
        "nl": "Venster tonen",
        "ja": "ウィンドウを表示",
        "zh_CN": "显示窗口",
        "zh_TW": "顯示視窗",
    },
    "Sidebar": {
        "fr": "Barre latérale",
        "de": "Seitenleiste",
        "es": "Barra lateral",
        "it": "Barra laterale",
        "nl": "Zijbalk",
        "ja": "サイドバー",
        "zh_CN": "侧边栏",
        "zh_TW": "側邊欄",
    },
    "Small": {
        "fr": "Petit",
        "de": "Klein",
        "es": "Pequeño",
        "it": "Piccolo",
        "nl": "Klein",
        "ja": "小",
        "zh_CN": "小",
        "zh_TW": "小",
    },
    "Start Scanning…": {
        "fr": "Démarrage du scan…",
        "de": "Scan wird gestartet…",
        "es": "Iniciando escaneo…",
        "it": "Avvio scansione…",
        "nl": "Scannen starten…",
        "ja": "スキャン中…",
        "zh_CN": "正在扫描…",
        "zh_TW": "正在掃描…",
    },
    "Theme": {
        "fr": "Thème",
        "de": "Thema",
        "es": "Tema",
        "it": "Tema",
        "nl": "Thema",
        "ja": "テーマ",
        "zh_CN": "主题",
        "zh_TW": "主題",
    },
    "This program comes with absolutely no warranty.": {
        "fr": "Ce programme ne comporte absolument aucune garantie.",
        "de": "Für dieses Programm besteht keinerlei Garantie.",
        "es": "Este programa no ofrece ningún tipo de garantía.",
        "it": "Questo programma non fornisce assolutamente alcuna garanzia.",
        "nl": "Dit programma wordt geleverd zonder enige garantie.",
        "ja": "このプログラムには一切の保証がありません。",
        "zh_CN": "本程序不提供任何保证。",
        "zh_TW": "本程式不提供任何保證。",
    },
    "Types": {
        "fr": "Types",
        "de": "Typen",
        "es": "Tipos",
        "it": "Tipi",
        "nl": "Types",
        "ja": "タイプ",
        "zh_CN": "类型",
        "zh_TW": "類型",
    },
    "Use as Friendly name": {
        "fr": "Utiliser comme nom convivial",
        "de": "Als Anzeigenamen verwenden",
        "es": "Usar como nombre descriptivo",
        "it": "Usa come nome descrittivo",
        "nl": "Gebruiken als beschrijvende naam",
        "ja": "フレンドリー名として使用",
        "zh_CN": "用作友好名称",
        "zh_TW": "用作易記名稱",
    },
    "Use as Information": {
        "fr": "Utiliser comme informations",
        "de": "Als Information verwenden",
        "es": "Usar como información",
        "it": "Usa come informazioni",
        "nl": "Gebruiken als informatie",
        "ja": "情報として使用",
        "zh_CN": "用作信息",
        "zh_TW": "用作資訊",
    },
    "Use as Location": {
        "fr": "Utiliser comme emplacement",
        "de": "Als Standort verwenden",
        "es": "Usar como ubicación",
        "it": "Usa come posizione",
        "nl": "Gebruiken als locatie",
        "ja": "場所として使用",
        "zh_CN": "用作位置",
        "zh_TW": "用作位置",
    },
    "Use icon from": {
        "fr": "Utiliser l'icône de",
        "de": "Symbol verwenden von",
        "es": "Usar icono de",
        "it": "Usa icona da",
        "nl": "Pictogram gebruiken van",
        "ja": "アイコンを使用する場所：",
        "zh_CN": "使用图标来自",
        "zh_TW": "使用圖示來自",
    },
    "Version {version}": {
        "fr": "Version {version}",
        "de": "Version {version}",
        "es": "Versión {version}",
        "it": "Versione {version}",
        "nl": "Versie {version}",
        "ja": "バージョン {version}",
        "zh_CN": "版本 {version}",
        "zh_TW": "版本 {version}",
    },
    "WS-Discovery (Windows devices)": {
        "fr": "WS-Discovery (appareils Windows)",
        "de": "WS-Discovery (Windows-Geräte)",
        "es": "WS-Discovery (dispositivos Windows)",
        "it": "WS-Discovery (dispositivi Windows)",
        "nl": "WS-Discovery (Windows-apparaten)",
        "ja": "WS-Discovery（Windowsデバイス）",
        "zh_CN": "WS-Discovery（Windows 设备）",
        "zh_TW": "WS-Discovery（Windows 裝置）",
    },
    "for details.": {
        "fr": "pour plus de détails.",
        "de": "für Details.",
        "es": "para más detalles.",
        "it": "per i dettagli.",
        "nl": "voor details.",
        "ja": "を参照してください。",
        "zh_CN": "以了解详情。",
        "zh_TW": "以瞭解詳情。",
    },
    "mDNS/DNS-SD discovery": {
        "fr": "Découverte mDNS/DNS-SD",
        "de": "mDNS/DNS-SD-Erkennung",
        "es": "Detección mDNS/DNS-SD",
        "it": "Rilevamento mDNS/DNS-SD",
        "nl": "mDNS/DNS-SD-ontdekking",
        "ja": "mDNS/DNS-SD 検出",
        "zh_CN": "mDNS/DNS-SD 发现",
        "zh_TW": "mDNS/DNS-SD 探索",
    },
    "not yet downloaded": {
        "fr": "pas encore téléchargé",
        "de": "noch nicht heruntergeladen",
        "es": "aún no descargado",
        "it": "non ancora scaricato",
        "nl": "nog niet gedownload",
        "ja": "まだダウンロードされていません",
        "zh_CN": "尚未下载",
        "zh_TW": "尚未下載",
    },
    "{} devices": {
        "fr": "{} appareils",
        "de": "{} Geräte",
        "es": "{} dispositivos",
        "it": "{} dispositivi",
        "nl": "{} apparaten",
        "ja": "{} デバイス",
        "zh_CN": "{} 个设备",
        "zh_TW": "{} 個裝置",
    },
}

# ---------------------------------------------------------------------------
# .po / .pot helpers
# ---------------------------------------------------------------------------

def _escape(s: str) -> str:
    """Escape a string for use in a .po msgid/msgstr value."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


def _po_entry(msgid: str, msgstr: str, comment: str = "#: ui_qt/") -> str:
    lines = [comment]
    mid = _escape(msgid)
    mstr = _escape(msgstr)
    if len(mid) > 70:
        lines.append('msgid ""')
        # wrap at word boundaries
        for chunk in [mid[i:i+70] for i in range(0, len(mid), 70)]:
            lines.append(f'"{chunk}"')
    else:
        lines.append(f'msgid "{mid}"')
    if len(mstr) > 70:
        lines.append('msgstr ""')
        for chunk in [mstr[i:i+70] for i in range(0, len(mstr), 70)]:
            lines.append(f'"{chunk}"')
    else:
        lines.append(f'msgstr "{mstr}"')
    lines.append("")
    return "\n".join(lines)


def load_pot_msgids(pot_path: Path) -> set[str]:
    msgids: set[str] = set()
    current: list[str] = []
    in_msgid = False
    for line in pot_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):
            in_msgid = True
            val = line[6:].strip()
            if val.startswith('"') and val.endswith('"'):
                current = [val[1:-1]]
        elif in_msgid and line.startswith('"') and line.endswith('"'):
            current.append(line[1:-1])
        else:
            if in_msgid and current:
                msgids.add("".join(current).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\"))
            in_msgid = False
            current = []
    return msgids


def load_po_msgids(po_path: Path) -> set[str]:
    return load_pot_msgids(po_path)


# ---------------------------------------------------------------------------
# Extract new strings
# ---------------------------------------------------------------------------

def extract_strings(path: Path) -> set[str]:
    out: set[str] = set()
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.add(node.args[0].value)
    return out


all_source_strings: set[str] = set()
for d in SOURCE_DIRS:
    if d.exists():
        for f in d.rglob("*.py"):
            all_source_strings.update(extract_strings(f))

pot_path = ROOT / "locale" / "netneighbor.pot"
existing_pot = load_pot_msgids(pot_path)
new_strings = sorted(s for s in all_source_strings if s and s not in existing_pot)
print(f"New strings to add: {len(new_strings)}")

# ---------------------------------------------------------------------------
# Update .pot
# ---------------------------------------------------------------------------

pot_additions = []
for s in new_strings:
    pot_additions.append(_po_entry(s, "", comment="#: ui_qt/"))

with open(pot_path, "a", encoding="utf-8") as f:
    f.write("\n")
    f.write("\n".join(pot_additions))

print(f"Updated {pot_path.name} (+{len(new_strings)} entries)")

# ---------------------------------------------------------------------------
# Update .po files
# ---------------------------------------------------------------------------

LOCALES = ["fr", "de", "es", "it", "nl", "ja", "zh_CN", "zh_TW"]

for locale in LOCALES:
    po_path = ROOT / "locale" / locale / "LC_MESSAGES" / "netneighbor.po"
    existing = load_po_msgids(po_path)
    additions = []
    for s in new_strings:
        if s in existing:
            continue
        msgstr = TRANSLATIONS.get(s, {}).get(locale, "")
        additions.append(_po_entry(s, msgstr, comment="#: ui_qt/"))
    if not additions:
        print(f"{locale}: nothing to add")
        continue
    with open(po_path, "a", encoding="utf-8") as f:
        f.write("\n")
        f.write("\n".join(additions))
    print(f"{locale}: +{len(additions)} entries")

# ---------------------------------------------------------------------------
# Compile .mo files via babel
# ---------------------------------------------------------------------------

try:
    from babel.messages.pofile import read_po
    from babel.messages.mofile import write_mo
except ImportError:
    print("babel not available — skipping .mo compilation")
    sys.exit(0)

for locale in LOCALES:
    po_path = ROOT / "locale" / locale / "LC_MESSAGES" / "netneighbor.po"
    mo_path = po_path.with_suffix(".mo")
    try:
        with open(po_path, "rb") as f:
            catalog = read_po(f, locale=locale)
        with open(mo_path, "wb") as f:
            write_mo(f, catalog)
        print(f"{locale}: compiled .mo ({mo_path.stat().st_size} bytes)")
    except Exception as e:
        print(f"{locale}: .mo compilation failed: {e}")

print("Done.")
