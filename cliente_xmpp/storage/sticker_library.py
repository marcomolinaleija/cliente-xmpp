from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from cliente_xmpp.config.settings import APP_DIR
from cliente_xmpp.media.outgoing_stickers import prepare_outgoing_sticker
from cliente_xmpp.models.stickers import LibrarySticker, StickerPack

STICKERS_DIR = APP_DIR / "stickers"
MAX_DESCRIPTION_LENGTH = 8000


def bounded_text(value: str, limit: int, label: str, *, required: bool = False) -> str:
    value = value.strip()
    if (
        len(value) > limit
        or any(ord(c) < 32 and c not in "\n\t\r" for c in value)
        or (required and not value)
    ):
        raise ValueError(f"{label}: escribe entre {1 if required else 0} y {limit} caracteres.")
    return value


class StickerLibrary:
    """Independent user library. Each operation opens its own bounded transaction."""

    def __init__(self, root: Path = STICKERS_DIR) -> None:
        self._requested_root = Path(root).absolute()
        self.root = self._requested_root.resolve()
        self.files = self.root / "files"
        self.path = self.root / "library.sqlite3"

    def _validate_root(self) -> None:
        for path in (
            self._requested_root,
            *self._requested_root.parents,
            self.root,
            self.files,
            self.path,
        ):
            if path.is_symlink() or path.is_junction():
                raise ValueError("La biblioteca no puede usar enlaces o rutas redirigidas.")

    @contextmanager
    def _connect(self):
        self._validate_root()
        self.files.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.create_function("can_fold", 1, lambda value: str(value).casefold(), deterministic=True)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            if conn.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise ValueError("La biblioteca pertenece a una versión más reciente de CAN.")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS stickers (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,
                    animated INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS packs (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, author TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS membership (
                    pack_id INTEGER REFERENCES packs(id) ON DELETE CASCADE,
                    sticker_id TEXT REFERENCES stickers(id) ON DELETE CASCADE,
                    PRIMARY KEY(pack_id, sticker_id)
                );
                PRAGMA user_version=1;
            """)
            with conn:
                yield conn
        finally:
            conn.close()

    def managed_path(self, filename: str) -> Path:
        self._validate_root()
        if re.fullmatch(r"sticker-[0-9a-f]{32}\.webp", filename) is None:
            raise ValueError("Ruta de sticker no válida.")
        path = self.files / filename
        if path.is_symlink() or path.resolve().parent != self.files.resolve():
            raise ValueError("Ruta de sticker fuera de la biblioteca.")
        return path

    def _entry(self, row: sqlite3.Row) -> LibrarySticker:
        return LibrarySticker(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            path=str(self.managed_path(row["filename"])),
            favorite=bool(row["favorite"]),
            animated=bool(row["animated"]),
            revision=row["revision"],
            pack_ids=tuple(sorted(int(x) for x in (row["pack_ids"] or "").split(",") if x)),
        )

    _SELECT = """SELECT s.*, GROUP_CONCAT(m.pack_id) AS pack_ids FROM stickers s
                 LEFT JOIN membership m ON m.sticker_id=s.id"""

    def list_stickers(
        self,
        *,
        pack_id: int | None = None,
        favorites: bool = False,
        query: str = "",
        offset: int = 0,
        limit: int = 200,
    ) -> list[LibrarySticker]:
        clauses, args = [], []
        if pack_id is not None:
            clauses.append(
                "EXISTS(SELECT 1 FROM membership x WHERE x.sticker_id=s.id AND x.pack_id=?)"
            )
            args.append(pack_id)
        if favorites:
            clauses.append("s.favorite=1")
        if query.strip():
            clauses.append(
                "(INSTR(can_fold(s.name), can_fold(?))>0 OR "
                "INSTR(can_fold(s.description), can_fold(?))>0)"
            )
            args.extend([query.strip(), query.strip()])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                self._SELECT + where + " GROUP BY s.id ORDER BY s.name COLLATE NOCASE, s.id "
                "LIMIT ? OFFSET ?",
                [*args, min(200, max(1, limit)), max(0, offset)],
            ).fetchall()
            return [self._entry(row) for row in rows]

    def get(self, sticker_id: str) -> LibrarySticker:
        with self._connect() as conn:
            row = conn.execute(
                self._SELECT + " WHERE s.id=? GROUP BY s.id", (sticker_id,)
            ).fetchone()
            if row is None:
                raise ValueError("El sticker ya no está en la biblioteca.")
            return self._entry(row)

    def packs(self) -> list[StickerPack]:
        with self._connect() as conn:
            return [
                StickerPack(*row)
                for row in conn.execute(
                    "SELECT p.id,p.name,p.author,COUNT(m.sticker_id) FROM packs p "
                    "LEFT JOIN membership m ON m.pack_id=p.id GROUP BY p.id "
                    "ORDER BY p.name COLLATE NOCASE,p.id"
                )
            ]

    def create_pack(self, name: str, author: str = "CAN") -> int:
        name = bounded_text(name, 128, "Nombre", required=True)
        author = bounded_text(author, 128, "Autor", required=True)
        with self._connect() as conn:
            return conn.execute(
                "INSERT INTO packs(name,author) VALUES(?,?)", (name, author)
            ).lastrowid

    def rename_pack(self, pack_id: int, name: str) -> None:
        name = bounded_text(name, 128, "Nombre", required=True)
        with self._connect() as conn:
            conn.execute("UPDATE packs SET name=? WHERE id=?", (name, pack_id))

    def delete_pack(self, pack_id: int) -> None:
        # Removing a group must not remove the user's stickers or other memberships.
        with self._connect() as conn:
            conn.execute("DELETE FROM packs WHERE id=?", (pack_id,))

    def assign_pack(self, sticker_id: str, pack_id: int, *, remove: bool = False) -> None:
        with self._connect() as conn:
            if remove:
                conn.execute(
                    "DELETE FROM membership WHERE pack_id=? AND sticker_id=?", (pack_id, sticker_id)
                )
            else:
                conn.execute("INSERT OR IGNORE INTO membership VALUES(?,?)", (pack_id, sticker_id))

    def add(self, source: Path, *, name: str = "", description: str = "") -> LibrarySticker:
        return self.add_many([(source, name or source.stem, description)])[0]

    def add_many(
        self, entries: list[tuple[Path, str, str]], *, pack_name: str = "", author: str = "CAN"
    ) -> list[LibrarySticker]:
        prepared: list[tuple[Path, str, str, str, bool]] = []
        created_paths: list[Path] = []
        retained: set[Path] = set()
        try:
            with self._connect() as conn:
                for source, name, description in entries:
                    name = bounded_text(name, 128, "Nombre", required=True)
                    description = bounded_text(description, MAX_DESCRIPTION_LENGTH, "Descripción")
                    path = prepare_outgoing_sticker(
                        source, output_dir=self.files, copy_compatible=True
                    )
                    created_paths.append(path)
                    with Image.open(path) as image:
                        animated = image.n_frames > 1
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    prepared.append((path, digest, name, description, animated))
                pack_id = None
                if pack_name:
                    pack_id = conn.execute(
                        "INSERT INTO packs(name,author) VALUES(?,?)",
                        (
                            bounded_text(pack_name, 128, "Paquete", required=True),
                            bounded_text(author, 128, "Autor", required=True),
                        ),
                    ).lastrowid
                ids = []
                for path, digest, name, description, animated in prepared:
                    inserted = conn.execute(
                        "INSERT OR IGNORE INTO stickers(id,name,description,filename,animated) "
                        "VALUES(?,?,?,?,?)",
                        (digest, name, description, path.name, int(animated)),
                    ).rowcount
                    if not inserted:
                        # Imported/generated descriptions never overwrite manual edits.
                        conn.execute(
                            "UPDATE stickers SET description=?, revision=revision+1 "
                            "WHERE id=? AND description='' AND ?<>''",
                            (description, digest, description),
                        )
                    else:
                        retained.add(path)
                    ids.append(digest)
                    if pack_id is not None:
                        conn.execute(
                            "INSERT OR IGNORE INTO membership VALUES(?,?)", (pack_id, digest)
                        )
                result = [
                    self._entry(
                        conn.execute(
                            self._SELECT + " WHERE s.id=? GROUP BY s.id", (sticker_id,)
                        ).fetchone()
                    )
                    for sticker_id in ids
                ]
        except Exception:
            retained.clear()
            raise
        finally:
            for path in created_paths:
                if path not in retained:
                    self.managed_path(path.name).unlink(missing_ok=True)
        return result

    def edit(
        self,
        sticker_id: str,
        *,
        description: str | None = None,
        name: str | None = None,
        favorite: bool | None = None,
        expected_revision: int | None = None,
    ) -> None:
        assignments, args = ["revision=revision+1"], []
        for key, value, limit in (
            ("description", description, MAX_DESCRIPTION_LENGTH),
            ("name", name, 128),
        ):
            if value is not None:
                assignments.append(f"{key}=?")
                args.append(bounded_text(value, limit, key, required=key == "name"))
        if favorite is not None:
            assignments.append("favorite=?")
            args.append(int(favorite))
        with self._connect() as conn:
            clause = "id=?"
            args.append(sticker_id)
            if expected_revision is not None:
                clause += " AND revision=?"
                args.append(expected_revision)
            if (
                conn.execute(
                    "UPDATE stickers SET " + ",".join(assignments) + " WHERE " + clause, args
                ).rowcount
                != 1
            ):
                raise ValueError("El sticker cambió; revisa su descripción antes de reemplazarla.")

    def delete(self, sticker_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT filename FROM stickers WHERE id=?", (sticker_id,)).fetchone()
            if row is None:
                return
            path = self.managed_path(row[0])
            # A failure removing a file must leave metadata available for retry.
            path.unlink(missing_ok=True)
            conn.execute("DELETE FROM stickers WHERE id=?", (sticker_id,))
