import os
import sys
import win32gui
import win32ui
import win32con
import win32api
from pathlib import Path
from threading import Thread
from queue import Queue
import hashlib
import re


EXECUTABLE_EXTENSIONS = {".exe", ".appref-ms", ".lnk"}
ICON_SOURCE_EXTENSIONS = {".ico", ".png", ".jpg", ".jpeg", ".svg"}


def get_user_icon_dir():
    """Get the user-writable command icon directory."""
    if sys.platform == "win32":
        app_data = Path(os.environ.get("APPDATA", "")) / "Dash" / "assets" / "icons"
    else:
        app_data = Path.home() / ".Dash" / "assets" / "icons"
    app_data.mkdir(parents=True, exist_ok=True)
    return app_data


class IconManager:
    def __init__(self, settings):
        self.settings = settings
        self.icon_store_dir = get_user_icon_dir()
        self.bundled_icons = self._load_bundled_icons()

        # Background icon download queue
        self.download_queue = Queue()
        self._start_download_worker()

    def get_icon_path(self, command_config):
        icon = command_config.get("icon")
        location = command_config.get("location", "")
        cmd_type = command_config.get("type", "")

        is_url = cmd_type == "url" or location.startswith(("http://", "https://"))

        # A URL command that was saved with the shared placeholder icon is one
        # whose favicon had not finished downloading yet. If it has arrived
        # since, prefer it over the placeholder.
        if is_url and self.is_shared_default_icon(icon):
            cached_favicon = self._check_favicon_cache(location)
            if cached_favicon:
                return cached_favicon

        # Priority order:
        # 1. Absolute path to custom icon
        if icon and os.path.isabs(icon) and os.path.exists(icon):
            print("Loaded path specified icon1")
            return icon

        # 2. Relative path that exists
        if icon and os.path.exists(icon):
            print("Loaded path specified icon2")
            return icon

        # 3. Bundled icon by name (e.g., "settings" finds "settings.png")
        if icon:
            # Try the icon name directly
            if icon in self.bundled_icons:
                print("Loaded named icon")
                return self.bundled_icons[icon]
            # Try without extension
            icon_stem = Path(icon).stem
            if icon_stem in self.bundled_icons:
                print("Loaded named icon")
                return self.bundled_icons[icon_stem]

        # 4. Auto-extract from executable (Windows)
        if location.endswith(".exe") and os.path.exists(location):
            extracted = self._extract_exe_icon(location)
            if extracted:
                print("Loaded extracted exe icon")
                return extracted

        # 5. Auto-download favicon for URLs (check cache first, download later if needed)
        if is_url:
            cached_favicon = self._check_favicon_cache(location)
            if cached_favicon:
                print("Loaded favicon icon")
                return cached_favicon
            else:
                # Queue for background download, return default for now
                self._queue_favicon_download(location)
                return self.settings.paths.url_command_icon

        # 6. Shared defaults for local folders and regular files
        if location and os.path.isdir(location):
            print("Loaded folder icon")
            return self.settings.paths.folder_icon

        if location and os.path.exists(location):
            print("Loaded file icon")
            return self.settings.paths.file_icon

        # 7. Default fallback
        print("Loaded default icon")
        return self.settings.paths.default_command_icon

    def command_icon_path(self, command_name):
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(command_name).strip()).strip("._")
        return self.icon_store_dir / f"{stem or 'command'}.png"

    def save_command_icon(self, icon, command_name, size=256):
        """Persist a resolved QIcon/QPixmap as this command's permanent icon.

        Returns the full path to the icon file, or None if nothing was saved.
        """
        from PyQt6.QtGui import QIcon, QPixmap
        from PyQt6.QtCore import QSize, Qt

        if isinstance(icon, QIcon):
            if icon.isNull():
                return None
            pixmap = icon.pixmap(QSize(size, size))
        elif isinstance(icon, QPixmap):
            pixmap = icon
        else:
            return None

        if pixmap.isNull():
            return None

        pixmap = self._normalized_icon_pixmap(pixmap, size)

        icon_path = self.command_icon_path(command_name)
        if not pixmap.save(str(icon_path), "PNG"):
            return None
        return str(icon_path)

    def _normalized_icon_pixmap(self, pixmap, size=256, padding=18):
        from PyQt6.QtCore import QRect, QSize, Qt
        from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

        image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        bounds = image.rect()
        left = bounds.right()
        top = bounds.bottom()
        right = bounds.left()
        bottom = bounds.top()

        for y in range(image.height()):
            for x in range(image.width()):
                if image.pixelColor(x, y).alpha() == 0:
                    continue
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)

        if left > right or top > bottom:
            return QPixmap(size, size)

        cropped = pixmap.copy(QRect(left, top, right - left + 1, bottom - top + 1))
        target_size = max(1, size - padding * 2)
        scaled = cropped.scaled(
            QSize(target_size, target_size),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        canvas = QPixmap(size, size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        painter.end()
        return canvas

    def delete_command_icon(self, icon_path):
        if not icon_path:
            return
        path = Path(icon_path)
        try:
            if path.exists() and path.resolve().parent == self.icon_store_dir.resolve():
                path.unlink()
        except OSError:
            pass

    def is_stored_icon(self, icon_path):
        """True if the path points at an existing file inside the icon store."""
        if not icon_path:
            return False
        path = Path(icon_path)
        try:
            return path.is_file() and path.resolve().parent == self.icon_store_dir.resolve()
        except OSError:
            return False

    def is_shared_default_icon(self, icon_path):
        if not icon_path:
            return False
        defaults = {
            self.settings.paths.default_command_icon,
            self.settings.paths.folder_icon,
            self.settings.paths.file_icon,
            self.settings.paths.url_command_icon,
        }
        return str(icon_path) in defaults

    def resolve_command_icon(self, command):
        """Resolve the best available icon for a command as a QIcon.

        Order: existing custom icon file, cached favicon (urls), OS/shell icon,
        extracted exe icon, then the type-appropriate default. Favicons are
        never downloaded here; a cache miss queues a background download and
        the URL placeholder is used until it lands.
        """
        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QFileInfo
        from PyQt6.QtWidgets import QFileIconProvider

        icon_path = command.get("icon")
        location = str(command.get("location", "") or "")
        cmd_type = command.get("type", "")
        is_url = cmd_type == "url" or location.startswith(("http://", "https://"))

        if icon_path and os.path.exists(icon_path):
            icon = QIcon(icon_path)
            if not icon.isNull():
                return icon

        if icon_path and icon_path in self.bundled_icons:
            icon = QIcon(self.bundled_icons[icon_path])
            if not icon.isNull():
                return icon

        if is_url:
            favicon = self._check_favicon_cache(location)
            if favicon:
                icon = QIcon(favicon)
                if not icon.isNull():
                    return icon
            else:
                self._queue_favicon_download(location)
            return QIcon(self.settings.paths.url_command_icon)

        if Path(location).suffix.lower() in ICON_SOURCE_EXTENSIONS and os.path.exists(location):
            icon = QIcon(location)
            if not icon.isNull():
                return icon

        if location.lower().endswith(".exe") and os.path.exists(location):
            extracted = self._extract_exe_icon(location)
            if extracted:
                return QIcon(extracted)

        if location and QFileInfo(location).exists():
            provider = QFileIconProvider()
            icon = provider.icon(QFileInfo(location))
            if not icon.isNull() and icon.availableSizes():
                return icon
            if os.path.isdir(location):
                return provider.icon(QFileIconProvider.IconType.Folder)

        return QIcon(self.settings.paths.default_command_icon)

    def resolve_command_icon_path(self, command):
        """Resolve an icon path for a command without caching shared defaults."""
        from PyQt6.QtCore import QFileInfo
        from PyQt6.QtGui import QIcon

        icon_path = command.get("icon")
        location = str(command.get("location", "") or "")
        cmd_type = command.get("type", "")
        is_url = cmd_type == "url" or location.startswith(("http://", "https://"))

        if icon_path and os.path.exists(icon_path):
            return icon_path

        if icon_path and icon_path in self.bundled_icons:
            return self.bundled_icons[icon_path]

        if is_url:
            # Cache only: this runs on the GUI thread at startup, so a slow or
            # unreachable site must not block the launcher from appearing.
            favicon = self._check_favicon_cache(location)
            if favicon:
                saved = self.save_command_icon(QIcon(favicon), command.get("name", ""))
                if saved:
                    return saved
            else:
                self._queue_favicon_download(location)
            return self.settings.paths.url_command_icon

        path = Path(location).expanduser() if location else None
        file_info = QFileInfo(location) if location else None
        if path and (path.is_dir() or (file_info is not None and file_info.exists() and file_info.isDir())):
            return self.settings.paths.folder_icon

        source_suffix = Path(location).suffix.lower()
        if location and source_suffix in (EXECUTABLE_EXTENSIONS | ICON_SOURCE_EXTENSIONS) and file_info is not None and file_info.exists():
            icon = self.resolve_command_icon(command)
            saved = self.save_command_icon(icon, command.get("name", ""))
            if saved:
                return saved

        if location and file_info is not None and file_info.exists():
            return self.settings.paths.file_icon

        return self.settings.paths.default_command_icon

    def reprocess_command_icons(self, commands, force=False):
        """Give every command a permanent icon file in the icon store.

        `commands` is an iterable of command dicts. Commands whose icon already
        lives in the icon store are skipped unless `force` is set. Returns a
        {command_name: icon_path} map for the commands that were (re)written.
        """
        updated = {}
        for command in commands:
            name = command.get("name")
            if not name:
                continue
            if self.is_stored_icon(command.get("icon")) or (force and self.is_shared_default_icon(command.get("icon"))):
                if not force:
                    continue
                # Re-derive from the source instead of re-saving the stored copy
                command = {**command, "icon": None}
            icon_path = self.resolve_command_icon_path(command)
            if icon_path:
                updated[name] = icon_path
        self._delete_unreferenced_auto_icons(updated.values())
        return updated

    def _delete_unreferenced_auto_icons(self, referenced_paths):
        referenced = set()
        for icon_path in referenced_paths:
            try:
                path = Path(icon_path)
                if path.is_absolute():
                    referenced.add(path.resolve())
            except OSError:
                pass

        for icon_path in self.icon_store_dir.glob("auto_*.png"):
            try:
                if icon_path.resolve() not in referenced:
                    icon_path.unlink()
            except OSError:
                pass

    def _extract_exe_icon(self, exe_path):
        """Extract icon from Windows executable and cache it as PNG"""
        try:
            # Create cache filename based on exe path hash (stable across runs)
            exe_hash = hashlib.md5(exe_path.encode()).hexdigest()[:16]
            cache_path = self.icon_store_dir / f"auto_exe_{exe_hash}.png"

            # Return cached icon if it exists and exe hasn't been modified
            if cache_path.exists():
                exe_mtime = os.path.getmtime(exe_path)
                cache_mtime = os.path.getmtime(cache_path)
                if cache_mtime > exe_mtime:
                    return str(cache_path)

            # Extract icon from exe
            ico_x = win32api.GetSystemMetrics(win32con.SM_CXICON)
            ico_y = win32api.GetSystemMetrics(win32con.SM_CYICON)

            large, small = win32gui.ExtractIconEx(exe_path, 0)
            if not large:
                return None

            # Use the first large icon
            hicon = large[0]

            # Convert to bitmap
            hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
            hbmp = win32ui.CreateBitmap()
            hbmp.CreateCompatibleBitmap(hdc, ico_x, ico_y)
            hdc_bitmap = hdc.CreateCompatibleDC()

            hdc_bitmap.SelectObject(hbmp)
            hdc_bitmap.DrawIcon((0, 0), hicon)

            # Save as BMP first (Win32 limitation)
            temp_bmp = self.icon_store_dir / f"temp_{exe_hash}.bmp"
            hbmp.SaveBitmapFile(hdc_bitmap, str(temp_bmp))

            # Convert BMP to PNG using PyQt6
            from PyQt6.QtGui import QPixmap

            pixmap = QPixmap(str(temp_bmp))
            pixmap.save(str(cache_path), "PNG")

            # Cleanup
            temp_bmp.unlink()

            # Properly cleanup icon handles from ExtractIconEx
            # Wrap in try-except as some handles may be shared system resources
            for icon in large:
                try:
                    win32gui.DestroyIcon(icon)
                except Exception:
                    pass
            for icon in small:
                try:
                    win32gui.DestroyIcon(icon)
                except Exception:
                    pass

            return str(cache_path)

        except Exception as e:
            print(f"Failed to extract icon from {exe_path}: {e}")
            return None

    def _load_bundled_icons(self):
        """Load bundled icons from the icons directory"""
        bundled_icons = {}

        # Get icons directory - handle both dev and exe
        if hasattr(sys, "_MEIPASS"):
            # Running as compiled exe
            base_path = Path(sys._MEIPASS)  # type: ignore
        else:
            # Running in development
            base_path = Path(__file__).parent.parent

        icons_dir = base_path / "assets" / "icons"  # Changed from "icons" to "assets/icons"

        if not icons_dir.exists():
            return bundled_icons

        # Scan for common icon formats
        for ext in ["*.png", "*.ico", "*.jpg", "*.jpeg", "*.svg"]:
            for icon_file in icons_dir.glob(ext):
                icon_name = icon_file.stem
                bundled_icons[icon_name] = str(icon_file.absolute())

        return bundled_icons

    def _download_icon(self, url, min_size=0):
        """Download and cache an icon from a URL.

        Multi-resolution .ico files keep their largest frame. Results smaller
        than `min_size` are rejected so the caller can try a better source.
        """
        try:
            from urllib.request import urlopen
            from urllib.error import URLError, HTTPError
            from PyQt6.QtGui import QImage

            # Create cache filename from URL (stable across runs)
            url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
            cache_path = self.icon_store_dir / f"auto_web_{url_hash}.png"

            if cache_path.exists():
                if min_size:
                    cached = QImage(str(cache_path))
                    if cached.isNull() or max(cached.width(), cached.height()) < min_size:
                        return None
                return str(cache_path)

            # Download icon
            with urlopen(url, timeout=5) as response:
                image_data = response.read()

            image = self._largest_frame(image_data)
            if image is None:
                return None

            if min_size and max(image.width(), image.height()) < min_size:
                return None

            if not image.save(str(cache_path), "PNG"):
                return None

            return str(cache_path)
        except (URLError, HTTPError):
            # Silent fail - 404s and 403s are expected for many sites
            return None
        except Exception as e:
            # Only print unexpected errors
            print(f"Unexpected error downloading icon from {url}: {e}")
            return None

    def _largest_frame(self, image_data):
        """Decode image bytes, returning the biggest frame of a multi-size .ico."""
        from PyQt6.QtCore import QBuffer, QByteArray, QIODeviceBase
        from PyQt6.QtGui import QImage, QImageReader

        buffer_data = QByteArray(image_data)  # QBuffer does not own its backing array
        buffer = QBuffer(buffer_data)
        buffer.open(QIODeviceBase.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)

        best = QImage()
        for index in range(max(reader.imageCount(), 1)):
            if index and not reader.jumpToImage(index):
                break
            frame = reader.read()
            if frame.isNull():
                continue
            if frame.width() * frame.height() > best.width() * best.height():
                best = frame

        return None if best.isNull() else best

    def _favicon_sources(self, url):
        """Candidate favicon URLs, highest expected resolution first."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        if not domain:
            return []

        scheme = parsed.scheme or "https"
        return [
            f"https://www.google.com/s2/favicons?domain={domain}&sz=256",
            f"https://icons.duckduckgo.com/ip3/{domain}.ico",
            f"{scheme}://{domain}/favicon.ico",
        ]

    def _get_favicon_for_url(self, url):
        """Get favicon for a website URL automatically"""
        sources = self._favicon_sources(url)

        # First pass demands a usable resolution; second accepts anything
        for min_size in (64, 0):
            for favicon_url in sources:
                result = self._download_icon(favicon_url, min_size=min_size)
                if result:
                    return result

        return None

    def _check_favicon_cache(self, url):
        """Check if favicon is already cached"""
        from PyQt6.QtGui import QImage

        for favicon_url in self._favicon_sources(url):
            url_hash = hashlib.md5(favicon_url.encode()).hexdigest()[:16]
            cache_path = self.icon_store_dir / f"auto_web_{url_hash}.png"
            if not cache_path.exists():
                continue
            cached = QImage(str(cache_path))
            if not cached.isNull() and max(cached.width(), cached.height()) >= 64:
                return str(cache_path)

        return None

    def _queue_favicon_download(self, url):
        """Queue a favicon for background download"""
        self.download_queue.put(url)

    def _start_download_worker(self):
        """Start background thread for downloading favicons"""

        def worker():
            while True:
                url = self.download_queue.get()
                if url is None:  # Poison pill to stop thread
                    break
                try:
                    self._get_favicon_for_url(url)
                except Exception:
                    pass  # Silent fail
                self.download_queue.task_done()

        thread = Thread(target=worker, daemon=True)
        thread.start()
