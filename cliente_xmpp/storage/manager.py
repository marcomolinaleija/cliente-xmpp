from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from cliente_xmpp.config.settings import APP_DIR
from cliente_xmpp.media.downloads import sanitize_filename
from cliente_xmpp.storage.message_store import (
    DATABASE_PATH,
    MessageStore,
    StorageChatRecord,
    StorageMediaRecord,
)

ACTIVE_FILE_GRACE_SECONDS = 10 * 60


@dataclass(frozen=True, slots=True)
class StorageCategoryUsage:
    key: str
    label: str
    description: str
    size_bytes: int
    file_count: int
    reclaimable_bytes: int
    reclaimable_file_count: int
    file_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StorageChatUsage:
    account_jid: str
    chat_jid: str
    name: str
    is_group: bool
    size_bytes: int
    file_count: int
    referenced_file_count: int
    unreferenced_file_count: int
    message_count: int
    reclaimable_bytes: int
    file_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StorageSnapshot:
    total_bytes: int
    file_count: int
    reclaimable_bytes: int
    categories: tuple[StorageCategoryUsage, ...]
    chats: tuple[StorageChatUsage, ...]

    def category(self, key: str) -> StorageCategoryUsage | None:
        return next((item for item in self.categories if item.key == key), None)


@dataclass(frozen=True, slots=True)
class StorageCleanupResult:
    attempted_file_count: int
    deleted_file_count: int
    reclaimed_bytes: int
    cleared_database_references: int = 0
    deleted_paths: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


@dataclass(slots=True)
class _UsageAccumulator:
    size_bytes: int = 0
    file_count: int = 0
    reclaimable_bytes: int = 0
    reclaimable_file_count: int = 0
    file_paths: list[str] | None = None

    def __post_init__(self) -> None:
        if self.file_paths is None:
            self.file_paths = []


@dataclass(slots=True)
class _ChatAccumulator:
    record: StorageChatRecord | None
    size_bytes: int = 0
    file_count: int = 0
    referenced_file_count: int = 0
    unreferenced_file_count: int = 0
    reclaimable_bytes: int = 0
    file_paths: list[str] | None = None

    def __post_init__(self) -> None:
        if self.file_paths is None:
            self.file_paths = []


CATEGORY_DETAILS = {
    "database": (
        "Base de datos local",
        "Mensajes, conversaciones, participantes, búsquedas y estados guardados en SQLite.",
    ),
    "settings": (
        "Configuración",
        "Preferencias de conexión, accesibilidad, sonidos y notificaciones.",
    ),
    "audio": (
        "Audios descargados",
        "Notas de voz y otros audios que siguen vinculados con un mensaje.",
    ),
    "images": (
        "Imágenes descargadas",
        "Fotografías e imágenes que siguen vinculadas con un mensaje.",
    ),
    "videos": (
        "Videos descargados",
        "Videos y notas de video que siguen vinculados con un mensaje.",
    ),
    "stickers": (
        "Stickers descargados",
        "Stickers estáticos o previsualizaciones locales de stickers animados.",
    ),
    "files": (
        "Otros archivos descargados",
        "Documentos y adjuntos que siguen vinculados con un mensaje.",
    ),
    "orphan_downloads": (
        "Descargas sin referencia",
        "Archivos que están en Descargas pero ya no están asociados a la ruta local de un mensaje.",
    ),
    "recordings": (
        "Grabaciones temporales",
        "Notas de voz preparadas para enviar. Las recientes se protegen mientras podrían subirse.",
    ),
    "clipboard": (
        "Archivos del portapapeles",
        "Imágenes temporales creadas al pegar contenido en una conversación.",
    ),
    "avatars": (
        "Fotos de perfil",
        "Copias locales de avatares; se pueden volver a descargar.",
    ),
    "temporary": (
        "Descargas incompletas",
        "Archivos .part de descargas interrumpidas. Los recientes se protegen.",
    ),
    "other_cache": (
        "Otras cachés",
        "QR de vinculación y archivos de integraciones locales. Los recientes se protegen.",
    ),
    "other_data": (
        "Otros datos",
        "Elementos no reconocidos dentro de la carpeta de la aplicación; no se borran "
        "individualmente.",
    ),
}

CATEGORY_ORDER = tuple(CATEGORY_DETAILS)
MANAGED_DIRECTORY_NAMES = {
    "downloads",
    "recordings",
    "clipboard",
    "avatars",
    "whatsapp-linking",
    "rayoai-media",
}


class StorageManager:
    def __init__(
        self,
        message_store: MessageStore,
        *,
        app_dir: Path = APP_DIR,
        active_file_grace_seconds: int = ACTIVE_FILE_GRACE_SECONDS,
    ) -> None:
        self.message_store = message_store
        self.app_dir = Path(app_dir)
        self.database_path = Path(message_store.path or DATABASE_PATH)
        self.active_file_grace_seconds = max(0, active_file_grace_seconds)

    def build_snapshot(self) -> StorageSnapshot:
        chat_records = self._load_chat_records()
        media_records = self._load_media_records()
        media_by_path = self._media_records_by_path(media_records)
        chats_by_key = {
            (record.account_jid, record.chat_jid): record for record in chat_records
        }
        chats_by_directory = self._chats_by_directory(chat_records)
        categories: dict[str, _UsageAccumulator] = defaultdict(_UsageAccumulator)
        chats: dict[tuple[str, str], _ChatAccumulator] = {}
        total_bytes = 0
        file_count = 0
        now = time.time()

        for path in self._iter_files(self.app_dir):
            try:
                stat = path.stat(follow_symlinks=False)
                relative = path.relative_to(self.app_dir)
            except (OSError, ValueError):
                continue

            size = max(0, int(stat.st_size))
            total_bytes += size
            file_count += 1
            path_key = _path_key(path)
            media_record = media_by_path.get(path_key)
            category_key = self._category_key(path, relative, media_record)
            deletable = self._is_individually_deletable(path, relative, stat.st_mtime, now)
            category = categories[category_key]
            category.size_bytes += size
            category.file_count += 1
            if deletable:
                category.reclaimable_bytes += size
                category.reclaimable_file_count += 1
                category.file_paths.append(str(path))

            if relative.parts and relative.parts[0].casefold() == "downloads":
                chat_key = self._chat_key_for_file(
                    path,
                    relative,
                    media_record,
                    chats_by_directory,
                )
                if chat_key is not None:
                    accumulator = chats.setdefault(
                        chat_key,
                        _ChatAccumulator(record=chats_by_key.get(chat_key)),
                    )
                    accumulator.size_bytes += size
                    accumulator.file_count += 1
                    if media_record is None:
                        accumulator.unreferenced_file_count += 1
                    else:
                        accumulator.referenced_file_count += 1
                    if deletable:
                        accumulator.reclaimable_bytes += size
                        accumulator.file_paths.append(str(path))

        category_rows = tuple(
            self._build_category(key, categories.get(key, _UsageAccumulator()))
            for key in CATEGORY_ORDER
            if key in categories
        )
        chat_rows = tuple(
            sorted(
                (self._build_chat(key, value) for key, value in chats.items()),
                key=lambda item: (-item.size_bytes, item.name.casefold(), item.chat_jid),
            )
        )
        return StorageSnapshot(
            total_bytes=total_bytes,
            file_count=file_count,
            reclaimable_bytes=sum(item.reclaimable_bytes for item in category_rows),
            categories=category_rows,
            chats=chat_rows,
        )

    def delete_files(self, paths: tuple[str, ...] | list[str]) -> StorageCleanupResult:
        unique_paths = tuple(dict.fromkeys(str(path) for path in paths))
        deleted: list[str] = []
        failures: list[str] = []
        reclaimed_bytes = 0

        for raw_path in unique_paths:
            path = Path(raw_path)
            if not self._is_path_in_managed_directory(path):
                failures.append(f"Ruta fuera de las carpetas administradas: {path.name}")
                continue
            if path.is_symlink():
                failures.append(f"Se omitió un vínculo: {path.name}")
                continue
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")
                continue
            deleted.append(str(path))
            reclaimed_bytes += max(0, int(size))

        self._remove_empty_managed_directories()
        cleared_references = 0
        try:
            cleared_references = self.message_store.clear_missing_media_local_paths()
        except Exception as exc:
            failures.append(f"No se pudieron actualizar las referencias locales: {exc}")

        return StorageCleanupResult(
            attempted_file_count=len(unique_paths),
            deleted_file_count=len(deleted),
            reclaimed_bytes=reclaimed_bytes,
            cleared_database_references=cleared_references,
            deleted_paths=tuple(deleted),
            failures=tuple(failures),
        )

    def compact_database(self) -> int:
        before = self._database_family_size()
        self.message_store.compact_database()
        return max(0, before - self._database_family_size())

    def delete_all_data(self) -> StorageCleanupResult:
        self._validate_total_deletion_root()
        files = list(self._iter_files(self.app_dir, include_symlinks=True))
        database_paths = {
            _path_key(Path(f"{self.database_path}{suffix}"))
            for suffix in ("", "-wal", "-shm", "-journal")
        }
        settings_path_key = _path_key(self.app_dir / "settings.json")
        control_paths = database_paths | {settings_path_key}
        ordinary_files = [path for path in files if _path_key(path) not in control_paths]
        control_files = [path for path in files if _path_key(path) in control_paths]
        database_path_key = _path_key(self.database_path)
        control_files.sort(
            key=lambda path: (
                _path_key(path) in database_paths,
                _path_key(path) == database_path_key,
            )
        )
        deleted: list[str] = []
        failures: list[str] = []
        reclaimed_bytes = 0
        for path in ordinary_files:
            try:
                size = path.stat(follow_symlinks=False).st_size
                path.unlink(missing_ok=True)
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")
                continue
            deleted.append(str(path))
            reclaimed_bytes += max(0, int(size))

        # Keep settings and SQLite intact when another file is still open.  That lets
        # the running application report the partial cleanup and try again safely.
        if not failures:
            for path in control_files:
                try:
                    size = path.stat(follow_symlinks=False).st_size
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    failures.append(f"{path.name}: {exc}")
                    break
                deleted.append(str(path))
                reclaimed_bytes += max(0, int(size))

        for directory in self._directories_deepest_first(self.app_dir):
            try:
                directory.rmdir()
            except OSError:
                continue
        try:
            self.app_dir.rmdir()
        except OSError:
            pass

        return StorageCleanupResult(
            attempted_file_count=len(files),
            deleted_file_count=len(deleted),
            reclaimed_bytes=reclaimed_bytes,
            deleted_paths=tuple(deleted),
            failures=tuple(failures),
        )

    def _load_chat_records(self) -> list[StorageChatRecord]:
        try:
            return self.message_store.load_storage_chat_records()
        except Exception:
            return []

    def _load_media_records(self) -> list[StorageMediaRecord]:
        try:
            return self.message_store.load_storage_media_records()
        except Exception:
            return []

    @staticmethod
    def _media_records_by_path(
        records: list[StorageMediaRecord],
    ) -> dict[str, StorageMediaRecord]:
        result: dict[str, StorageMediaRecord] = {}
        for record in records:
            if record.local_path:
                result.setdefault(_path_key(Path(record.local_path)), record)
        return result

    def _chats_by_directory(
        self,
        records: list[StorageChatRecord],
    ) -> dict[str, tuple[str, str] | None]:
        result: dict[str, tuple[str, str] | None] = {}
        downloads_dir = self.app_dir / "downloads"
        for record in records:
            directory = downloads_dir / sanitize_filename(
                record.account_jid or "cuenta"
            ) / sanitize_filename(record.chat_jid or "chat")
            directory_key = _path_key(directory)
            chat_key = (record.account_jid, record.chat_jid)
            if directory_key in result and result[directory_key] != chat_key:
                result[directory_key] = None
            else:
                result[directory_key] = chat_key
        return result

    @staticmethod
    def _chat_key_for_file(
        path: Path,
        relative: Path,
        media_record: StorageMediaRecord | None,
        chats_by_directory: dict[str, tuple[str, str] | None],
    ) -> tuple[str, str] | None:
        if media_record is not None:
            return media_record.account_jid, media_record.chat_jid
        if len(relative.parts) < 4:
            return None
        return chats_by_directory.get(_path_key(path.parent))

    @staticmethod
    def _category_key(
        path: Path,
        relative: Path,
        media_record: StorageMediaRecord | None,
    ) -> str:
        if not relative.parts:
            return "other_data"
        top = relative.parts[0].casefold()
        name = relative.name.casefold()
        if top.startswith("messages.sqlite3"):
            return "database"
        if top == "settings.json":
            return "settings"
        if top == "downloads":
            if name.endswith(".part"):
                return "temporary"
            if media_record is None:
                return "orphan_downloads"
            if media_record.is_sticker:
                return "stickers"
            return {
                "audio": "audio",
                "image": "images",
                "video": "videos",
                "file": "files",
            }.get(media_record.media_kind, "files")
        if top == "recordings":
            return "recordings"
        if top == "clipboard":
            return "clipboard"
        if top == "avatars":
            return "avatars"
        if top in {"whatsapp-linking", "rayoai-media"}:
            return "other_cache"
        return "other_data"

    def _is_individually_deletable(
        self,
        path: Path,
        relative: Path,
        modified_at: float,
        now: float,
    ) -> bool:
        if path.is_symlink() or not relative.parts:
            return False
        top = relative.parts[0].casefold()
        if top not in MANAGED_DIRECTORY_NAMES:
            return False
        if top in {
            "recordings",
            "clipboard",
            "whatsapp-linking",
            "rayoai-media",
        } or relative.name.casefold().endswith(".part"):
            return now - modified_at >= self.active_file_grace_seconds
        return True

    def _is_path_in_managed_directory(self, path: Path) -> bool:
        absolute = Path(os.path.abspath(path))
        for name in MANAGED_DIRECTORY_NAMES:
            root = Path(os.path.abspath(self.app_dir / name))
            try:
                lexical_match = (
                    os.path.commonpath((str(root), str(absolute))) == str(root)
                    and absolute != root
                )
                resolved_root = root.resolve(strict=False)
                resolved_path = absolute.resolve(strict=False)
                resolved_match = (
                    os.path.commonpath((str(resolved_root), str(resolved_path)))
                    == str(resolved_root)
                    and resolved_path != resolved_root
                )
                if lexical_match and resolved_match:
                    return True
            except (OSError, ValueError):
                continue
        return False

    def _remove_empty_managed_directories(self) -> None:
        for name in MANAGED_DIRECTORY_NAMES:
            root = self.app_dir / name
            for directory in self._directories_deepest_first(root):
                try:
                    directory.rmdir()
                except OSError:
                    continue

    def _database_family_size(self) -> int:
        return sum(
            _file_size(Path(f"{self.database_path}{suffix}"))
            for suffix in ("", "-wal", "-shm", "-journal")
        )

    def _validate_total_deletion_root(self) -> None:
        absolute = Path(os.path.abspath(self.app_dir))
        unsafe = (
            absolute.parent == absolute
            or absolute == Path.home()
            or absolute.name != ".cliente-xmpp"
        )
        if unsafe:
            raise ValueError("La carpeta de datos no es una ruta segura para el borrado total.")

    @staticmethod
    def _iter_files(root: Path, *, include_symlinks: bool = False):
        if not root.is_dir():
            return
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                path = Path(entry.path)
                try:
                    if entry.is_symlink():
                        if include_symlinks:
                            yield path
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        yield path
                except OSError:
                    continue

    @staticmethod
    def _directories_deepest_first(root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        directories: list[Path] = []
        for current, child_dirs, _files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            for child in tuple(child_dirs):
                child_path = current_path / child
                if child_path.is_symlink():
                    child_dirs.remove(child)
                    continue
                directories.append(child_path)
        return sorted(directories, key=lambda item: len(item.parts), reverse=True)

    @staticmethod
    def _build_category(
        key: str,
        accumulator: _UsageAccumulator,
    ) -> StorageCategoryUsage:
        label, description = CATEGORY_DETAILS[key]
        return StorageCategoryUsage(
            key=key,
            label=label,
            description=description,
            size_bytes=accumulator.size_bytes,
            file_count=accumulator.file_count,
            reclaimable_bytes=accumulator.reclaimable_bytes,
            reclaimable_file_count=accumulator.reclaimable_file_count,
            file_paths=tuple(accumulator.file_paths),
        )

    @staticmethod
    def _build_chat(
        key: tuple[str, str],
        accumulator: _ChatAccumulator,
    ) -> StorageChatUsage:
        record = accumulator.record
        return StorageChatUsage(
            account_jid=key[0],
            chat_jid=key[1],
            name=record.name if record is not None else "Conversación no reconocida",
            is_group=record.is_group if record is not None else False,
            size_bytes=accumulator.size_bytes,
            file_count=accumulator.file_count,
            referenced_file_count=accumulator.referenced_file_count,
            unreferenced_file_count=accumulator.unreferenced_file_count,
            message_count=record.message_count if record is not None else 0,
            reclaimable_bytes=accumulator.reclaimable_bytes,
            file_paths=tuple(accumulator.file_paths),
        )


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _file_size(path: Path) -> int:
    try:
        return max(0, int(path.stat().st_size))
    except OSError:
        return 0
