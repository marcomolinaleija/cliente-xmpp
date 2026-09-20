from __future__ import annotations

from collections.abc import Callable

import wx

from cliente_xmpp.storage.manager import (
    DatabaseBackupInfo,
    DatabaseOptimizationPreview,
    DatabaseOptimizationResult,
    StorageCategoryUsage,
    StorageChatUsage,
    StorageCleanupResult,
    StorageSnapshot,
)
from cliente_xmpp.ui.theme import apply_theme

SnapshotCallback = Callable[[StorageSnapshot | None, str], None]
SnapshotLoader = Callable[[SnapshotCallback], None]
CleanupCallback = Callable[[StorageCleanupResult | None, str], None]
FileCleaner = Callable[[tuple[str, ...], CleanupCallback], None]
DatabasePreviewLoader = Callable[
    [Callable[[DatabaseOptimizationPreview | None, str], None]],
    None,
]
DatabaseBackupLoader = Callable[
    [Callable[[tuple[DatabaseBackupInfo, ...] | None, str], None]],
    None,
]
DatabaseOptimizationCallback = Callable[[DatabaseOptimizationResult | None, str], None]
DatabaseOptimizer = Callable[[DatabaseOptimizationCallback], None]
DatabaseBackupCleaner = Callable[[Callable[[StorageCleanupResult | None, str], None]], None]
TotalCleaner = Callable[[CleanupCallback], None]

DOWNLOAD_CATEGORY_KEYS = {
    "audio",
    "images",
    "videos",
    "stickers",
    "files",
    "orphan_downloads",
    "temporary",
}
AUXILIARY_CATEGORY_KEYS = {"recordings", "clipboard", "avatars", "other_cache"}


class StorageManagerDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        load_snapshot: SnapshotLoader,
        load_database_preview: DatabasePreviewLoader,
        load_database_backups: DatabaseBackupLoader,
        delete_files: FileCleaner,
        optimize_database: DatabaseOptimizer,
        delete_database_backups: DatabaseBackupCleaner,
        delete_all_data: TotalCleaner,
    ) -> None:
        super().__init__(
            parent,
            title="Gestor de almacenamiento",
            size=(920, 680),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetMinSize((780, 560))
        self._load_snapshot = load_snapshot
        self._load_database_preview = load_database_preview
        self._load_database_backups = load_database_backups
        self._delete_files = delete_files
        self._optimize_database = optimize_database
        self._delete_database_backups = delete_database_backups
        self._delete_all_data = delete_all_data
        self._snapshot: StorageSnapshot | None = None
        self._database_preview: DatabaseOptimizationPreview | None = None
        self._database_backups: tuple[DatabaseBackupInfo, ...] = ()
        self._visible_chats: tuple[StorageChatUsage, ...] = ()
        self._visible_categories: tuple[StorageCategoryUsage, ...] = ()
        self._request_id = 0
        self._database_request_id = 0
        self._active = True
        self._busy = False
        self._can_close_while_busy = True

        self.notebook = wx.Notebook(self)
        self.summary = self._create_summary_page()
        self.chats = self._create_chats_page()
        self.elements = self._create_elements_page()
        self.database_report, self.database_candidates = self._create_database_page()
        self._create_maintenance_page()

        self.status = wx.StaticText(self, label="Calculando el espacio utilizado...")
        self.status.SetName("Estado del gestor de almacenamiento")
        self.refresh_button = wx.Button(self, label="Actualizar")
        self.close_button = wx.Button(self, wx.ID_CLOSE, "Cerrar")

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.refresh_button, 0, wx.RIGHT, 8)
        buttons.AddStretchSpacer(1)
        buttons.Add(self.close_button, 0)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.notebook, 1, wx.ALL | wx.EXPAND, 12)
        box.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        box.Add(buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        self.SetSizer(box)

        self.refresh_button.Bind(wx.EVT_BUTTON, self._on_refresh)
        self.close_button.Bind(wx.EVT_BUTTON, self._on_close_button)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        apply_theme(self)
        self.CentreOnParent()
        wx.CallAfter(self._refresh)
        wx.CallAfter(self._refresh_database_audit)
        wx.CallAfter(self._refresh_database_backups)

    def deactivate(self) -> None:
        self._active = False
        self._request_id += 1

    def _create_summary_page(self) -> wx.TextCtrl:
        page = wx.Panel(self.notebook)
        intro = wx.StaticText(
            page,
            label=(
                "Este resumen cuenta los archivos que existen realmente en la carpeta de "
                "WhatsApp CAN. El espacio recuperable excluye la base de datos, la configuración "
                "y archivos recientes que todavía podrían estar en uso."
            ),
        )
        summary = wx.TextCtrl(
            page,
            value="Calculando...",
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        summary.SetName("Resumen del almacenamiento")
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(intro, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(summary, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Resumen")
        return summary

    def _create_chats_page(self) -> wx.ListCtrl:
        page = wx.Panel(self.notebook)
        intro = wx.StaticText(
            page,
            label=(
                "Marca una o varias conversaciones con Espacio o con el ratón. Ctrl+A marca "
                "todas. Sólo se borrarán sus copias locales; los mensajes seguirán guardados."
            ),
        )
        chats = wx.ListCtrl(page, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        chats.SetName("Uso de almacenamiento por conversación")
        chats.EnableCheckBoxes(True)
        for index, (label, width) in enumerate(
            (
                ("Conversación", 250),
                ("Cuenta", 180),
                ("Tipo", 85),
                ("Espacio", 100),
                ("Archivos", 85),
                ("Sin referencia", 115),
                ("Mensajes locales", 125),
            )
        ):
            chats.InsertColumn(index, label, width=width)
        chats.Bind(wx.EVT_KEY_DOWN, lambda event: self._on_checklist_key_down(event, chats))
        chats.Bind(wx.EVT_LEFT_DOWN, lambda event: self._on_checklist_mouse_down(event, chats))
        chats.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_check_state_changed)
        chats.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_check_state_changed)
        self.delete_chats_button = wx.Button(page, label="Eliminar archivos marcados...")
        self.delete_chats_button.Enable(False)
        self.delete_chats_button.Bind(wx.EVT_BUTTON, self._on_delete_selected_chats)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(intro, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(chats, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        box.Add(self.delete_chats_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Por chats")
        return chats

    def _create_elements_page(self) -> wx.ListCtrl:
        page = wx.Panel(self.notebook)
        intro = wx.StaticText(
            page,
            label=(
                "Marca uno o varios tipos con Espacio o con el ratón. Ctrl+A marca todos los "
                "eliminables. Recuperable indica lo que puede borrarse ahora."
            ),
        )
        elements = wx.ListCtrl(page, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        elements.SetName("Uso de almacenamiento por tipo de elemento")
        elements.EnableCheckBoxes(True)
        for index, (label, width) in enumerate(
            (
                ("Elemento", 245),
                ("Espacio", 110),
                ("Recuperable", 110),
                ("Archivos", 85),
                ("Explicación", 390),
            )
        ):
            elements.InsertColumn(index, label, width=width)
        elements.Bind(wx.EVT_KEY_DOWN, lambda event: self._on_checklist_key_down(event, elements))
        elements.Bind(
            wx.EVT_LEFT_DOWN,
            lambda event: self._on_checklist_mouse_down(event, elements),
        )
        elements.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_check_state_changed)
        elements.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_check_state_changed)
        self.delete_elements_button = wx.Button(page, label="Eliminar elementos marcados...")
        self.delete_elements_button.Enable(False)
        self.delete_elements_button.Bind(wx.EVT_BUTTON, self._on_delete_selected_elements)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(intro, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(elements, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        box.Add(self.delete_elements_button, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Por elementos")
        return elements

    def _create_database_page(self) -> tuple[wx.TextCtrl, wx.ListCtrl]:
        page = wx.Panel(self.notebook)
        intro = wx.StaticText(
            page,
            label=(
                "Esta auditoría sólo lee la base. La optimización conserva mensajes, chats, "
                "audios y transcripciones; únicamente retira participantes de grupos que no "
                "tienen mensajes locales y después reorganiza SQLite."
            ),
        )
        report = wx.TextCtrl(
            page,
            value="Calculando la auditoría...",
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        report.SetName("Informe accesible de optimización de la base de datos")

        candidates = wx.ListCtrl(page, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        candidates.SetName("Datos candidatos a la optimización de la base de datos")
        for index, (label, width) in enumerate(
            (
                ("Candidato", 245),
                ("Registros", 100),
                ("Recuperación estimada", 150),
                ("Motivo y protección", 430),
            )
        ):
            candidates.InsertColumn(index, label, width=width)

        self.database_audit_button = wx.Button(page, label="Auditar de nuevo")
        self.database_audit_button.SetName("Auditar de nuevo la base de datos")
        self.database_audit_button.Bind(wx.EVT_BUTTON, self._on_database_audit)
        self.database_optimize_button = wx.Button(
            page,
            label="Crear respaldo y optimizar...",
        )
        self.database_optimize_button.SetName("Crear respaldo y optimizar base de datos")
        self.database_optimize_button.Enable(False)
        self.database_optimize_button.Bind(wx.EVT_BUTTON, self._on_database_optimize)
        self.database_delete_backups_button = wx.Button(page, label="Eliminar respaldos...")
        self.database_delete_backups_button.SetName("Eliminar respaldos de la base de datos")
        self.database_delete_backups_button.Hide()
        self.database_delete_backups_button.Bind(
            wx.EVT_BUTTON,
            self._on_delete_database_backups,
        )

        actions = wx.BoxSizer(wx.HORIZONTAL)
        actions.Add(self.database_audit_button, 0, wx.RIGHT, 8)
        actions.Add(self.database_optimize_button, 0, wx.RIGHT, 8)
        actions.Add(self.database_delete_backups_button, 0)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(intro, 0, wx.ALL | wx.EXPAND, 10)
        box.Add(report, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        box.Add(candidates, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        box.Add(actions, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Optimizar base de datos")
        return report, candidates

    def _create_maintenance_page(self) -> None:
        page = wx.Panel(self.notebook)
        explanation = wx.TextCtrl(
            page,
            value=(
                "Limpieza recomendada\n"
                "Elimina descargas incompletas y archivos que ya no están vinculados a un "
                "mensaje.\n\n"
                "Todas las descargas\n"
                "Borra audio, fotos, videos, stickers, documentos y descargas sin referencia. "
                "Los mensajes permanecen y los archivos vinculados pueden descargarse otra vez.\n\n"
                "Cachés auxiliares\n"
                "Borra grabaciones temporales antiguas, imágenes pegadas, avatares y otros "
                "archivos regenerables.\n\n"
                "Optimizar base de datos\n"
                "Reorganiza SQLite y recupera páginas libres. No elimina mensajes.\n\n"
                "Borrar todos los datos\n"
                "Elimina de forma irreversible la base, configuración y todos los archivos. "
                "La aplicación se cerrará y pedirá confirmación dos veces."
            ),
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        explanation.SetName("Explicación de las tareas de mantenimiento")
        self.clean_recommended_button = wx.Button(page, label="Ejecutar limpieza recomendada...")
        self.clean_downloads_button = wx.Button(page, label="Eliminar todas las descargas...")
        self.clean_auxiliary_button = wx.Button(page, label="Vaciar cachés auxiliares...")
        self.optimize_button = wx.Button(page, label="Optimizar base de datos")
        self.delete_all_button = wx.Button(page, label="Borrar todos los datos...")
        self.clean_recommended_button.Bind(wx.EVT_BUTTON, self._on_clean_recommended)
        self.clean_downloads_button.Bind(wx.EVT_BUTTON, self._on_clean_downloads)
        self.clean_auxiliary_button.Bind(wx.EVT_BUTTON, self._on_clean_auxiliary)
        self.optimize_button.Bind(wx.EVT_BUTTON, self._on_optimize_database)
        self.delete_all_button.Bind(wx.EVT_BUTTON, self._on_delete_all)

        actions = wx.BoxSizer(wx.VERTICAL)
        for button in (
            self.clean_recommended_button,
            self.clean_downloads_button,
            self.clean_auxiliary_button,
            self.optimize_button,
            self.delete_all_button,
        ):
            actions.Add(button, 0, wx.BOTTOM | wx.EXPAND, 8)
        box = wx.BoxSizer(wx.HORIZONTAL)
        box.Add(explanation, 1, wx.ALL | wx.EXPAND, 10)
        box.Add(actions, 0, wx.TOP | wx.RIGHT | wx.BOTTOM, 10)
        page.SetSizer(box)
        self.notebook.AddPage(page, "Mantenimiento")

    def _on_refresh(self, _event: wx.CommandEvent) -> None:
        self._refresh()
        self._refresh_database_audit()
        self._refresh_database_backups()

    def _refresh(self) -> None:
        if self._busy:
            return
        self._request_id += 1
        request_id = self._request_id
        self._set_busy(True, "Calculando el espacio utilizado...")

        def callback(snapshot: StorageSnapshot | None, error: str) -> None:
            if not self._active or request_id != self._request_id:
                return
            self._set_busy(False)
            if snapshot is None or error:
                detail = error or "No se pudo calcular el almacenamiento."
                self.status.SetLabel(detail)
                self.summary.ChangeValue(detail)
                return
            self._snapshot = snapshot
            self._render(snapshot)

        self._load_snapshot(callback)

    def _on_database_audit(self, _event: wx.CommandEvent) -> None:
        self._refresh_database_audit()
        self._refresh_database_backups()

    def _refresh_database_audit(self) -> None:
        if not self._active:
            return
        self._database_request_id += 1
        request_id = self._database_request_id
        self._database_preview = None
        self.database_audit_button.Enable(False)
        self.database_optimize_button.Enable(False)
        self.database_report.ChangeValue("Calculando la auditoría y una estimación segura...")

        def callback(preview: DatabaseOptimizationPreview | None, error: str) -> None:
            if not self._active or request_id != self._database_request_id:
                return
            self.database_audit_button.Enable(not self._busy)
            if preview is None or error:
                self.database_report.ChangeValue(
                    error or "No se pudo auditar la base de datos local."
                )
                self.database_candidates.DeleteAllItems()
                return
            self._database_preview = preview
            self._render_database_preview(preview)
            self.database_optimize_button.Enable(not self._busy)

        self._load_database_preview(callback)

    def _refresh_database_backups(self) -> None:
        if not self._active:
            return

        def callback(
            backups: tuple[DatabaseBackupInfo, ...] | None,
            _error: str,
        ) -> None:
            if not self._active:
                return
            self._database_backups = backups or ()
            if self._database_preview is not None:
                self._render_database_preview(self._database_preview)
            else:
                self._update_database_backup_button()

        self._load_database_backups(callback)

    def _render_database_preview(self, preview: DatabaseOptimizationPreview) -> None:
        audit = preview.audit
        table_rows = dict(audit.table_rows)
        lines = [
            "Auditoría de la base de datos local",
            f"Tamaño actual: {format_storage_size(audit.total_bytes)}",
            f"SQLite: {audit.integrity_check}; comprobación rápida: {audit.quick_check}",
            f"Modo de diario: {audit.journal_mode.upper()} | "
            f"versión de esquema: {audit.user_version}",
            f"Páginas: {audit.page_count:,} | páginas libres: {audit.freelist_count:,}",
            "",
            "Contenido conservado",
            f"Chats: {audit.chat_count:,} ({audit.empty_chat_count:,} sin mensajes locales; "
            f"{audit.empty_contact_count:,} contactos y {audit.empty_group_chat_count:,} grupos)",
            f"Mensajes: {audit.message_count:,} | audios: {audit.audio_message_count:,}",
            f"Transcripciones Zapia: {audit.zapia_transcription_count:,} | "
            f"Deepgram: {audit.deepgram_transcription_count:,}",
            "Distribución interna: "
            f"messages {table_rows.get('messages', 0):,}; "
            f"group_participants {table_rows.get('group_participants', 0):,}; "
            f"chats {table_rows.get('chats', 0):,}.",
            "",
            "Resultado estimado antes de borrar",
            f"Participantes de grupos sin mensajes locales: "
            f"{preview.estimated_removed_participant_count:,}",
            f"Tamaño final estimado: {format_storage_size(preview.estimated_final_bytes)}",
            f"Espacio recuperable estimado: "
            f"{format_storage_size(preview.estimated_reclaimed_bytes)}",
            f"Respaldos disponibles: {len(self._database_backups):,} | "
            f"{format_storage_size(sum(item.size_bytes for item in self._database_backups))}",
            "",
            "Protecciones: no se borran chats vacíos porque también funcionan como agenda; "
            "tampoco se borran mensajes, audios, transcripciones ni cuentas antiguas.",
            "Al optimizar se detiene temporalmente la sincronización, se hace checkpoint de WAL "
            "y se crea un respaldo antes de cualquier eliminación.",
        ]
        self.database_report.ChangeValue("\n".join(lines))
        self.database_report.SetInsertionPoint(0)

        self.database_candidates.DeleteAllItems()
        row = self.database_candidates.InsertItem(0, "Participantes de grupos sin mensajes")
        self.database_candidates.SetItem(row, 1, str(preview.estimated_removed_participant_count))
        self.database_candidates.SetItem(
            row,
            2,
            format_storage_size(preview.estimated_reclaimed_bytes),
        )
        self.database_candidates.SetItem(
            row,
            3,
            "Caché regenerable al abrir el grupo; no se tocan chats ni mensajes.",
        )
        self._update_database_backup_button()

    def _update_database_backup_button(self) -> None:
        count = len(self._database_backups)
        if count:
            self.database_delete_backups_button.SetLabel(
                f"Eliminar {count} respaldo{'s' if count != 1 else ''}..."
            )
            self.database_delete_backups_button.Show()
            self.database_delete_backups_button.Enable(not self._busy)
        else:
            self.database_delete_backups_button.Hide()
            self.database_delete_backups_button.Disable()
        self.database_delete_backups_button.GetParent().Layout()
        self.notebook.Layout()

    def _render(self, snapshot: StorageSnapshot) -> None:
        self.summary.ChangeValue(self._format_summary(snapshot))
        self.summary.SetInsertionPoint(0)
        self._visible_chats = snapshot.chats
        self.chats.Freeze()
        try:
            self.chats.DeleteAllItems()
            for chat in snapshot.chats:
                index = self.chats.InsertItem(self.chats.GetItemCount(), chat.name)
                self.chats.SetItem(index, 1, chat.account_jid)
                self.chats.SetItem(index, 2, "Grupo" if chat.is_group else "Contacto")
                self.chats.SetItem(index, 3, format_storage_size(chat.size_bytes))
                self.chats.SetItem(index, 4, str(chat.file_count))
                self.chats.SetItem(index, 5, str(chat.unreferenced_file_count))
                self.chats.SetItem(index, 6, str(chat.message_count))
        finally:
            self.chats.Thaw()

        self._visible_categories = snapshot.categories
        self.elements.Freeze()
        try:
            self.elements.DeleteAllItems()
            for category in snapshot.categories:
                index = self.elements.InsertItem(self.elements.GetItemCount(), category.label)
                self.elements.SetItem(index, 1, format_storage_size(category.size_bytes))
                self.elements.SetItem(index, 2, format_storage_size(category.reclaimable_bytes))
                self.elements.SetItem(index, 3, str(category.file_count))
                self.elements.SetItem(index, 4, category.description)
        finally:
            self.elements.Thaw()

        self._update_marked_action_buttons()
        self.status.SetLabel(
            f"{format_storage_size(snapshot.total_bytes)} en {snapshot.file_count} archivos; "
            f"{format_storage_size(snapshot.reclaimable_bytes)} se pueden administrar desde aquí."
        )

    @staticmethod
    def _format_summary(snapshot: StorageSnapshot) -> str:
        lines = [
            f"Espacio total: {format_storage_size(snapshot.total_bytes)}",
            f"Archivos contabilizados: {snapshot.file_count}",
            f"Espacio administrable ahora: {format_storage_size(snapshot.reclaimable_bytes)}",
            "",
            "Desglose",
        ]
        for category in snapshot.categories:
            line = (
                f"{category.label}: {format_storage_size(category.size_bytes)} "
                f"en {category.file_count} archivos"
            )
            if category.reclaimable_bytes and category.reclaimable_bytes != category.size_bytes:
                line += f"; recuperable ahora: {format_storage_size(category.reclaimable_bytes)}"
            lines.extend((line, f"  {category.description}"))
        lines.extend(
            (
                "",
                "Al borrar una descarga local no se elimina el mensaje de WhatsApp ni el archivo "
                "remoto. Si continúa disponible en el servidor, se podrá descargar de nuevo.",
            )
        )
        return "\n".join(lines)

    def _on_delete_selected_chats(self, _event: wx.CommandEvent) -> None:
        selected = [self._visible_chats[index] for index in self._checked_rows(self.chats)]
        paths = _unique_paths(item.file_paths for item in selected)
        size = sum(item.reclaimable_bytes for item in selected)
        self._confirm_and_delete(
            paths,
            size,
            "Eliminar archivos de chats",
            "¿Eliminar las copias locales de las conversaciones seleccionadas? Los mensajes "
            "permanecerán disponibles.",
        )

    def _on_delete_selected_elements(self, _event: wx.CommandEvent) -> None:
        selected = [
            self._visible_categories[index] for index in self._checked_rows(self.elements)
        ]
        paths = _unique_paths(item.file_paths for item in selected)
        size = sum(item.reclaimable_bytes for item in selected)
        self._confirm_and_delete(
            paths,
            size,
            "Eliminar elementos",
            "¿Eliminar las copias locales de los tipos seleccionados?",
        )

    def _on_clean_recommended(self, _event: wx.CommandEvent) -> None:
        self._delete_categories(
            {"orphan_downloads", "temporary"},
            "Limpieza recomendada",
            "¿Eliminar descargas incompletas antiguas y archivos sin referencia?",
        )

    def _on_clean_downloads(self, _event: wx.CommandEvent) -> None:
        self._delete_categories(
            DOWNLOAD_CATEGORY_KEYS,
            "Eliminar todas las descargas",
            "¿Eliminar todas las copias locales descargadas? Los mensajes permanecerán y los "
            "archivos vinculados podrán descargarse otra vez.",
        )

    def _on_clean_auxiliary(self, _event: wx.CommandEvent) -> None:
        self._delete_categories(
            AUXILIARY_CATEGORY_KEYS,
            "Vaciar cachés auxiliares",
            "¿Eliminar grabaciones temporales antiguas, portapapeles, avatares y otras cachés?",
        )

    def _delete_categories(self, keys: set[str], title: str, message: str) -> None:
        if self._snapshot is None:
            return
        categories = [item for item in self._snapshot.categories if item.key in keys]
        paths = _unique_paths(item.file_paths for item in categories)
        size = sum(item.reclaimable_bytes for item in categories)
        self._confirm_and_delete(paths, size, title, message)

    def _confirm_and_delete(
        self,
        paths: tuple[str, ...],
        size: int,
        title: str,
        message: str,
    ) -> None:
        if not paths:
            wx.MessageBox(
                "No hay archivos recuperables en esta selección.",
                title,
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        result = wx.MessageBox(
            f"{message}\n\nSe intentará liberar {format_storage_size(size)} en "
            f"{len(paths)} archivos.",
            title,
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self,
        )
        if result != wx.YES:
            return
        self._set_busy(True, "Eliminando archivos...")
        self._delete_files(paths, self._finish_cleanup)

    def _finish_cleanup(self, result: StorageCleanupResult | None, error: str) -> None:
        if not self._active:
            return
        self._set_busy(False)
        if result is None or error:
            wx.MessageBox(
                error or "No se pudo completar la limpieza.",
                "Gestor de almacenamiento",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        message = (
            f"Se eliminaron {result.deleted_file_count} archivos y se liberaron "
            f"{format_storage_size(result.reclaimed_bytes)}."
        )
        if result.failures:
            message += f"\n\n{len(result.failures)} elementos no se pudieron eliminar."
        wx.MessageBox(
            message,
            "Limpieza terminada",
            wx.OK | (wx.ICON_WARNING if result.failures else wx.ICON_INFORMATION),
            self,
        )
        self._refresh()

    def _on_optimize_database(self, _event: wx.CommandEvent) -> None:
        self._on_database_optimize(_event)

    def _on_database_optimize(self, _event: wx.CommandEvent) -> None:
        preview = self._database_preview
        if preview is None:
            return
        result = wx.MessageBox(
            "Se detendrá temporalmente la sincronización para proteger la base. "
            "Primero se creará un respaldo y después se retirará únicamente la caché de "
            f"{preview.estimated_removed_participant_count:,} participantes sin mensajes.\n\n"
            f"Recuperación estimada: {format_storage_size(preview.estimated_reclaimed_bytes)}\n\n"
            "¿Continuar?",
            "Crear respaldo y optimizar base de datos",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self,
        )
        if result != wx.YES:
            return
        self._set_busy(True, "Deteniendo la sincronización y creando el respaldo...")
        self._optimize_database(self._finish_database_optimization)

    def _on_delete_database_backups(self, _event: wx.CommandEvent) -> None:
        if not self._database_backups:
            return
        total_bytes = sum(item.size_bytes for item in self._database_backups)
        result = wx.MessageBox(
            f"Se eliminarán {len(self._database_backups):,} respaldos locales de la base "
            f"({format_storage_size(total_bytes)}).\n\n"
            "Esta acción no elimina la base activa ni los mensajes actuales. ¿Continuar?",
            "Eliminar respaldos de la base de datos",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self,
        )
        if result != wx.YES:
            return
        self._set_busy(True, "Eliminando respaldos de la base de datos...")
        self._delete_database_backups(self._finish_database_backups_deletion)

    def _finish_database_backups_deletion(
        self,
        result: StorageCleanupResult | None,
        error: str,
    ) -> None:
        if not self._active:
            return
        self._set_busy(False)
        if result is None or error:
            wx.MessageBox(
                error or "No se pudieron eliminar los respaldos.",
                "Eliminar respaldos",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        message = (
            f"Se eliminaron {result.deleted_file_count:,} respaldos y se liberaron "
            f"{format_storage_size(result.reclaimed_bytes)}."
        )
        if result.failures:
            message += f"\n\n{len(result.failures):,} respaldos no se pudieron eliminar."
        wx.MessageBox(
            message,
            "Respaldos eliminados",
            wx.OK | (wx.ICON_WARNING if result.failures else wx.ICON_INFORMATION),
            self,
        )
        self._refresh_database_backups()

    def _finish_database_optimization(
        self,
        result: DatabaseOptimizationResult | None,
        error: str,
    ) -> None:
        if not self._active:
            return
        self._set_busy(False)
        if result is None or error:
            wx.MessageBox(
                error or "No se pudo optimizar la base de datos.",
                "Optimizar base de datos",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        self._database_preview = None
        self.database_optimize_button.Enable(False)
        self.database_report.ChangeValue(self._format_optimization_result(result))
        self.database_report.SetInsertionPoint(0)
        wx.MessageBox(
            "La base de datos se optimizó y el respaldo se creó correctamente.\n\n"
            f"Espacio liberado: {format_storage_size(result.reclaimed_bytes)}.",
            "Optimización terminada",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
        self._refresh()
        self._refresh_database_backups()

    @staticmethod
    def _format_optimization_result(result: DatabaseOptimizationResult) -> str:
        before = result.before
        after = result.after
        return "\n".join(
            (
                "Informe de optimización terminado",
                f"Tamaño anterior: {format_storage_size(before.total_bytes)}",
                f"Tamaño final: {format_storage_size(after.total_bytes)}",
                f"Espacio recuperado: {format_storage_size(result.reclaimed_bytes)}",
                f"Participantes de caché retirados: {result.removed_participant_count:,}",
                f"Respaldo: {result.backup_path}",
                "",
                "No se eliminaron mensajes, chats, audios ni transcripciones.",
                f"Comprobación final: {after.integrity_check}; rápida: {after.quick_check}.",
                "Si la sincronización estaba activa, se reanudará automáticamente al terminar.",
            )
        )

    def _on_delete_all(self, _event: wx.CommandEvent) -> None:
        first = wx.MessageBox(
            "Esta acción borrará mensajes locales, descargas, grabaciones, avatares, "
            "configuración y credenciales guardadas. La aplicación se cerrará.\n\n"
            "¿Quieres continuar?",
            "Primera confirmación: borrar todos los datos",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_ERROR,
            self,
        )
        if first != wx.YES:
            return
        dialog = wx.TextEntryDialog(
            self,
            "Segunda y última confirmación. Escribe BORRAR TODO para continuar.",
            "Borrado irreversible",
        )
        try:
            if dialog.ShowModal() != wx.ID_OK or dialog.GetValue().strip() != "BORRAR TODO":
                return
        finally:
            dialog.Destroy()

        self._set_busy(
            True,
            "Borrando todos los datos locales...",
            can_close_while_busy=False,
        )
        self._delete_all_data(self._finish_total_deletion)

    def _finish_total_deletion(self, result: StorageCleanupResult | None, error: str) -> None:
        if not self._active:
            return
        self._set_busy(False)
        if result is None or error or result.failures:
            detail = error or "No se pudieron borrar todos los datos."
            if result is not None and result.failures:
                detail += f"\n\nQuedaron {len(result.failures)} elementos sin borrar."
            wx.MessageBox(
                detail,
                "Borrado incompleto",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self._refresh()
            return
        self._active = False
        self.EndModal(wx.ID_DELETE)

    def _set_busy(
        self,
        busy: bool,
        message: str = "",
        *,
        can_close_while_busy: bool = True,
    ) -> None:
        self._busy = busy
        self._can_close_while_busy = not busy or can_close_while_busy
        for control in (
            self.refresh_button,
            self.delete_chats_button,
            self.delete_elements_button,
            self.clean_recommended_button,
            self.clean_downloads_button,
            self.clean_auxiliary_button,
            self.optimize_button,
            self.database_delete_backups_button,
            self.delete_all_button,
        ):
            control.Enable(not busy)
        self.close_button.Enable(self._can_close_while_busy)
        self.close_button.SetLabel(
            "Cerrar (la operación continúa)"
            if busy and self._can_close_while_busy
            else "Cerrar"
        )
        self.close_button.SetToolTip(
            "Cierra este diálogo. La limpieza ya iniciada continuará en segundo plano."
            if busy and self._can_close_while_busy
            else "El borrado irreversible debe terminar antes de cerrar este diálogo."
            if busy
            else "Cierra el gestor de almacenamiento."
        )
        if not busy:
            self._update_marked_action_buttons()
            self.database_audit_button.Enable(True)
            self.database_optimize_button.Enable(self._database_preview is not None)
            self._update_database_backup_button()
        if message:
            self.status.SetLabel(message)

    @staticmethod
    def _checked_rows(control: wx.ListCtrl) -> list[int]:
        return [
            index
            for index in range(control.GetItemCount())
            if control.IsItemChecked(index)
        ]

    def _on_checklist_key_down(self, event: wx.KeyEvent, control: wx.ListCtrl) -> None:
        key_code = event.GetKeyCode()
        if event.ControlDown() and key_code in (ord("A"), ord("a")):
            valid_rows = self._reclaimable_rows(control)
            should_check = any(not control.IsItemChecked(index) for index in valid_rows)
            for index in valid_rows:
                control.CheckItem(index, should_check)
            self._update_marked_action_buttons()
            return
        if key_code != wx.WXK_SPACE:
            event.Skip()
            return

        index = control.GetNextItem(
            -1,
            wx.LIST_NEXT_ALL,
            wx.LIST_STATE_FOCUSED,
        )
        if index == wx.NOT_FOUND:
            index = control.GetFirstSelected()
        if index != wx.NOT_FOUND:
            control.CheckItem(index, not control.IsItemChecked(index))
            self._update_marked_action_buttons()

    def _on_checklist_mouse_down(self, event: wx.MouseEvent, control: wx.ListCtrl) -> None:
        index, flags = control.HitTest(event.GetPosition())
        if index != wx.NOT_FOUND and not flags & wx.LIST_HITTEST_ONITEMSTATEICON:
            control.CheckItem(index, not control.IsItemChecked(index))
            self._update_marked_action_buttons()
        event.Skip()

    def _on_check_state_changed(self, _event: wx.ListEvent) -> None:
        self._update_marked_action_buttons()

    def _reclaimable_rows(self, control: wx.ListCtrl) -> list[int]:
        values = self._visible_chats if control is self.chats else self._visible_categories
        return [index for index, value in enumerate(values) if value.file_paths]

    def _update_marked_action_buttons(self) -> None:
        if self._busy:
            self.delete_chats_button.Enable(False)
            self.delete_elements_button.Enable(False)
            return
        checked_chats = self._checked_rows(self.chats)
        checked_elements = self._checked_rows(self.elements)
        self.delete_chats_button.Enable(
            any(self._visible_chats[index].file_paths for index in checked_chats)
        )
        self.delete_elements_button.Enable(
            any(self._visible_categories[index].file_paths for index in checked_elements)
        )

    def _on_close_button(self, _event: wx.CommandEvent) -> None:
        if not getattr(self, "_can_close_while_busy", True):
            return
        self.deactivate()
        self.EndModal(wx.ID_CANCEL)

    def _on_close(self, event: wx.CloseEvent) -> None:
        if not getattr(self, "_can_close_while_busy", True):
            event.Veto()
            return
        self.deactivate()
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            event.Skip()

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if (
            event.GetKeyCode() == wx.WXK_ESCAPE
            and getattr(self, "_can_close_while_busy", True)
        ):
            self.deactivate()
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()


def format_storage_size(size: int) -> str:
    value = float(max(0, size))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _unique_paths(groups) -> tuple[str, ...]:
    return tuple(dict.fromkeys(path for group in groups for path in group))
