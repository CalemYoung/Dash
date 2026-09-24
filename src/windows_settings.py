"""Windows Settings pages offered as commands by the recommendations scan.

Settings pages open from "ms-settings:" links. Windows has no API that lists
them, so the pages people actually open are listed here by hand. The list is
checked against the links the Settings program on this PC contains: a link
Windows does not know silently opens the Settings home page rather than
failing, so a page this build lacks is better left out than offered.
"""
import os
import re
from functools import lru_cache
from pathlib import Path

SETTINGS_SCHEME = "ms-settings:"

# Icons are library glyphs, rendered when a page is added as a command.
SETTINGS_ICON_COLOR = "#f3f4f7"
SETTINGS_ICON_BACKGROUND = "#3a4a66"

# (name, page, aliases, glyph). Most commonly opened first; the dialog keeps
# this order. Names are Windows' own US English ones; a British spelling is
# kept as an alias. Names avoid those of Store apps ("Camera", "Windows Security")
# so a page and an app never compete for the same name.
SETTINGS_PAGES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("Windows Settings", "", (), "SETTINGS"),
    ("Display", "display", ("screen", "resolution"), "DEVICE_DESKTOP"),
    ("Sound", "sound", ("volume",), "VOLUME"),
    ("Bluetooth & devices", "bluetooth", ("bluetooth", "bt"), "BLUETOOTH"),
    ("Wi-Fi", "network-wifi", ("wifi",), "WIFI"),
    ("Network & internet", "network-status", ("network",), "NETWORK"),
    ("Windows Update", "windowsupdate", ("updates",), "REFRESH"),
    ("Installed apps", "appsfeatures", ("uninstall",), "LAYOUT_GRID"),
    ("Default apps", "defaultapps", (), "APPS"),
    ("Startup apps", "startupapps", ("startup",), "ROCKET"),
    ("Storage", "storagesense", ("disk space",), "DATABASE"),
    ("Power & battery", "powersleep", ("sleep", "battery"), "BATTERY"),
    ("Notifications", "notifications", (), "BELL"),
    ("Printers & scanners", "printers", ("printers",), "PRINTER"),
    ("Mouse", "mousetouchpad", ("touchpad",), "MOUSE"),
    ("Keyboard", "keyboard", (), "KEYBOARD"),
    ("About this PC", "about", ("pc name", "specs"), "INFO_CIRCLE"),
    ("Night light", "nightlight", (), "MOON"),
    ("Clipboard", "clipboard", (), "CLIPBOARD"),
    ("Volume mixer", "apps-volume", ("mixer",), "ADJUSTMENTS"),
    ("VPN", "network-vpn", (), "SHIELD_LOCK"),
    ("Proxy", "network-proxy", (), "WORLD"),
    ("Ethernet", "network-ethernet", (), "PLUG_CONNECTED"),
    ("Mobile hotspot", "network-mobilehotspot", ("hotspot",), "ACCESS_POINT"),
    ("Personalization", "personalization", ("personalisation",), "PALETTE"),
    ("Background", "personalization-background", ("wallpaper",), "PHOTO"),
    ("Colors", "colors", ("colours", "dark mode"), "BRIGHTNESS_HALF"),
    ("Themes", "themes", (), "SUN_MOON"),
    ("Lock screen", "lockscreen", (), "LOCK"),
    ("Taskbar", "taskbar", (), "LAYOUT_BOTTOMBAR"),
    ("Multitasking", "multitasking", ("snap",), "LAYOUT_GRID"),
    ("Date & time", "dateandtime", ("time zone",), "CALENDAR_TIME"),
    ("Language & region", "regionlanguage", ("language", "region"), "LANGUAGE"),
    ("Fonts", "fonts", (), "TYPOGRAPHY"),
    ("Accounts", "yourinfo", (), "USER"),
    ("Sign-in options", "signinoptions", ("pin",), "KEY"),
    ("Other users", "otherusers", (), "USERS"),
    ("Access work or school", "workplace", (), "BRIEFCASE"),
    ("Privacy & security", "privacy", ("privacy",), "SHIELD_CHECK"),
    ("Microphone access", "privacy-microphone", (), "MICROPHONE"),
    ("Camera access", "privacy-webcam", ("webcam",), "CAMERA"),
    ("Location access", "privacy-location", (), "MAP_PIN"),
    ("Graphics", "display-advancedgraphics", ("gpu",), "ADJUSTMENTS"),
    ("Game Mode", "gaming-gamemode", (), "DEVICE_GAMEPAD_2"),
    ("Accessibility", "easeofaccess", (), "ACCESSIBLE"),
    ("Troubleshoot", "troubleshoot", (), "TOOL"),
    ("Recovery", "recovery", ("reset pc",), "LIFEBUOY"),
    ("Backup", "backup", (), "CLOUD_UPLOAD"),
    ("Optional features", "optionalfeatures", (), "PUZZLE"),
    ("Remote Desktop settings", "remotedesktop", (), "DEVICE_DESKTOP_ANALYTICS"),
    ("Projecting to this PC", "project", (), "CAST"),
    ("For developers", "developers", ("developer mode",), "CODE"),
)

_PAGE_NAMES = {page: name for name, page, _aliases, _glyph in SETTINGS_PAGES}


def settings_library_path() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "ImmersiveControlPanel" / "SystemSettings.dll"


@lru_cache(maxsize=1)
def available_pages() -> frozenset[str] | None:
    """Every Settings page this build of Windows links to, from the Settings
    program itself. None when that cannot be read, meaning "offer them all"."""
    try:
        data = settings_library_path().read_bytes()
    except OSError:
        return None
    pattern = re.compile("ms-settings:".encode("utf-16-le") + rb"((?:[A-Za-z0-9_\-]\x00){1,80})")
    pages = frozenset(match.decode("utf-16-le").casefold() for match in pattern.findall(data))
    return pages or None


def discover_settings_pages() -> list[dict]:
    """Recommendations for the listed Settings pages this PC has."""
    available = available_pages()
    candidates = []
    for name, page, aliases, glyph in SETTINGS_PAGES:
        if page and available is not None and page not in available:
            continue
        candidates.append(
            {
                "name": name,
                "aliases": list(aliases),
                "location": SETTINGS_SCHEME + page,
                "description": settings_description(name),
                "type": "file",
                "group": "settings",
                "icon_glyph": f"outline:{glyph}",
                "icon_color": SETTINGS_ICON_COLOR,
                "icon_background": SETTINGS_ICON_BACKGROUND,
            }
        )
    return candidates


def is_settings_location(location: str) -> bool:
    return str(location).strip().casefold().startswith(SETTINGS_SCHEME)


def settings_page(location: str) -> str:
    """"display" for "ms-settings:display"; empty for the home page."""
    return str(location).strip()[len(SETTINGS_SCHEME) :].strip().casefold()


def settings_page_name(location: str) -> str:
    """The page's name as listed, or its link turned into words when it is
    not listed: "ms-settings:advanceddisplay" is "Advanceddisplay" and
    "ms-settings:privacy-contacts" is "Privacy contacts"."""
    page = settings_page(location)
    if page in _PAGE_NAMES:
        return _PAGE_NAMES[page]
    words = re.sub(r"[-_]+", " ", page).strip()
    return words[:1].upper() + words[1:]


def settings_description(name: str) -> str:
    if name == "Windows Settings":
        return "Opens Windows Settings"
    return f"Opens {name} in Windows Settings"
