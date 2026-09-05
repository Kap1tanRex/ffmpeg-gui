"""Что изменилось — до того, как что-то скачано.

Новый выпуск приходит тегом и полотном Markdown. Это окно превращает его в три
вещи, которых достаточно для решения: какая версия, когда вышла и что в ней —
по карточке на выпуск, новые сверху.

Повторяет ``ReleaseNotesDialog`` из ZapretGUI: значок-«новость», версия, дата,
разделы описания списком, а внизу — «Скачать» и «Просмотр истории изменений».
"""

from __future__ import annotations

import threading
import tkinter
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from ..core.models import format_size
from ..core.release_notes import NoteSection, parse_release_notes
from ..services.update_service import ReleaseInfo, UpdateError, UpdateService
from .theming import font, pair, radius
from .widgets.scroll import ScrollFrame
from .widgets.surface import GAP, PAGE_PAD, Card, button, hairline, muted

WINDOW_SIZE = "720x760"
BADGE_SIZE = 44


class ReleaseCard(ctk.CTkFrame):
    """Один выпуск: значок, версия, дата и описание под ними."""

    def __init__(self, master, info: ReleaseInfo, empty_text: str = "") -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        header = Card(self, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        # Тянется колонка с версией, а не колонка значка — иначе значок уезжает
        # в середину карточки, а подписи отрываются от него.
        body = header.label_grid()

        # Значок — круг акцентного цвета с восклицательным знаком. Рисовать
        # нечем и незачем: подпись в круглой рамке выглядит так же.
        badge = ctk.CTkLabel(
            body,
            text="!",
            width=BADGE_SIZE,
            height=BADGE_SIZE,
            corner_radius=BADGE_SIZE // 2,
            fg_color=pair("bg.selected"),
            text_color=pair("accent"),
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        badge.grid(row=0, column=0, rowspan=2, padx=(0, 16))

        ctk.CTkLabel(
            body, text=f"v{info.version}", anchor="w", font=font("hero", bold=True)
        ).grid(row=0, column=1, sticky="ew")
        date_row = ctk.CTkFrame(body, fg_color="transparent")
        date_row.grid(row=1, column=1, sticky="ew")
        ctk.CTkLabel(
            date_row, text="◷", text_color=pair("accent"), font=font("small")
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            date_row, text=info.date_text or "—", text_color=pair("accent"), font=font("small")
        ).pack(side="left")

        sections = parse_release_notes(info.notes)
        row = 1
        if sections:
            for section in sections:
                _section(self, section).grid(row=row, column=0, sticky="ew", pady=(GAP, 0))
                row += 1
        elif empty_text:
            muted(self, empty_text).grid(row=row, column=0, sticky="ew", pady=(GAP, 0))


def _section(master, section: NoteSection) -> ctk.CTkFrame:
    holder = ctk.CTkFrame(master, fg_color="transparent")
    holder.grid_columnconfigure(0, weight=1)
    row = 0
    if section.title:
        ctk.CTkLabel(
            holder, text=section.title, anchor="w", font=font("body", bold=True)
        ).grid(row=row, column=0, sticky="ew", pady=(0, 6))
        row += 1
    for item in section.items:
        ctk.CTkLabel(
            holder,
            text=f"•  {item.text}",
            anchor="w",
            justify="left",
            wraplength=600 - item.level * 22,
            font=font("small") if item.level else font("body"),
            text_color=pair("fg.secondary") if item.level else pair("fg.primary"),
        ).grid(row=row, column=0, sticky="ew", padx=(8 + item.level * 22, 0), pady=1)
        row += 1
    return holder


class UpdateWindow(ctk.CTkToplevel):
    """Окно «Доступно обновление»: выпуски, загрузка и ссылка на историю."""

    def __init__(
        self,
        master,
        releases: tuple[ReleaseInfo, ...],
        service: UpdateService,
        *,
        title: str = "Доступно обновление",
        on_history: Callable[[], None] | None = None,
        empty_text: str = "Описание выпуска не заполнено.",
    ) -> None:
        super().__init__(master)
        self.service = service
        self.releases = releases
        self._empty_text = empty_text
        self._downloading = False

        self.title(title)
        self.geometry(WINDOW_SIZE)
        self.transient(master.winfo_toplevel())
        self.configure(fg_color=pair("bg.window"))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text=title, anchor="w", font=font("hero", bold=True)).grid(
            row=0, column=0, sticky="ew", padx=PAGE_PAD + 4, pady=(PAGE_PAD, GAP)
        )

        self.body = ScrollFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=PAGE_PAD, pady=0)
        self.body.grid_columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=PAGE_PAD, pady=PAGE_PAD)
        actions.grid_columnconfigure(0, weight=1)

        self.progress_label = muted(actions, "")
        self.progress_label.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.download_button = button(
            actions, "↓   Скачать", self._download, variant="primary", height=40
        )
        self.download_button.grid(row=1, column=0, sticky="ew")

        self.history_button = button(
            actions, "☰   Просмотр истории изменений", on_history or self._open_page, height=36
        )
        self.history_button.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        self._render()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # -- содержимое ----------------------------------------------------------
    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        row = 0
        for index, info in enumerate(self.releases):
            if index:
                hairline(self.body).grid(row=row, column=0, sticky="ew", pady=GAP + 6)
                row += 1
            ReleaseCard(self.body, info, empty_text=self._empty_text).grid(
                row=row, column=0, sticky="ew"
            )
            row += 1

        target = self._downloadable()
        if target is None:
            self.download_button.configure(
                text="↓   Открыть страницу выпуска" if self._page_url() else "↓   Скачать",
                state="normal" if self._page_url() else "disabled",
            )

    def _downloadable(self) -> ReleaseInfo | None:
        return next((r for r in self.releases if r.asset is not None), None)

    def _page_url(self) -> str:
        return next((r.page_url for r in self.releases if r.page_url), "")

    # -- загрузка ------------------------------------------------------------
    def _download(self) -> None:
        if self._downloading:
            self.service.cancel()
            return

        target = self._downloadable()
        if target is None or target.asset is None:
            self._open_page()
            return

        path = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить обновление",
            initialfile=target.asset.name,
            defaultextension=Path(target.asset.name).suffix,
        )
        if not path:
            return

        asset = target.asset
        destination = Path(path)
        self._downloading = True
        self.download_button.configure(text="Отменить загрузку")
        self.history_button.configure(state="disabled")
        self.progress_label.configure(text=f"Загрузка {asset.name}…")

        def report(received: int, total: int) -> None:
            self._safe(self._show_progress, received, total)

        def run() -> None:
            try:
                self.service.download(asset, destination, progress=report)
            except UpdateError as exc:
                self._safe(self._finish, None, str(exc))
            else:
                self._safe(self._finish, destination, "")

        threading.Thread(target=run, daemon=True).start()

    def _show_progress(self, received: int, total: int) -> None:
        share = f" из {format_size(total)}" if total else ""
        self.progress_label.configure(text=f"Загружено {format_size(received)}{share}")

    def _finish(self, path: Path | None, error: str) -> None:
        self._downloading = False
        self.download_button.configure(text="↓   Скачать")
        self.history_button.configure(state="normal")
        if path is None:
            self.progress_label.configure(text=f"Не удалось скачать: {error}")
            return
        self.progress_label.configure(text=f"Сохранено: {path}")

    def _open_page(self) -> None:
        url = self._page_url()
        if url:
            webbrowser.open(url)

    # -- служебное -----------------------------------------------------------
    def _safe(self, method: Callable, *args: object) -> None:
        """Ответ фонового потока доходит только пока окно живо."""
        try:
            self.after(0, method, *args)
        except (tkinter.TclError, RuntimeError):  # pragma: no cover - окно закрыто
            pass

    def _close(self) -> None:
        self.service.cancel()
        self.destroy()
