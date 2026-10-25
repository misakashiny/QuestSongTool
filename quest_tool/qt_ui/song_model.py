"""UI-only immutable fixtures and paged model; no business service or I/O."""
from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal

from ..messages import tr


@dataclass(frozen=True)
class Song:
    key: str
    title: str
    artist: str
    mapper: str
    seconds: float | None
    bpm: float | None
    rating: float | None
    digest: str = ""
    published: str = ""
    requirements: tuple = ()
    cover_url: str = ""
    preview_url: str = ""
    diffs: tuple = ()
    description: str = ""

    @property
    def duration(self):
        if not self.seconds:
            return tr("search.unknown")
        seconds = int(self.seconds)
        return f"{seconds//60}:{seconds%60:02}"

    @property
    def rating_text(self):
        return tr("search.unknown") if self.rating is None else f"{self.rating:.1f}%"

    @property
    def bpm_text(self):
        return tr("search.unknown") if self.bpm is None else f"{self.bpm:g}"

    @classmethod
    def from_result(cls, item):
        return cls(item["id"], item["title"], item["artist"], item["mapper"],
                   item["duration"], item["bpm"],
                   None if item["rating"] is None else item["rating"]*100,
                   item["hash"], item["published"], tuple(item["requirements"]),
                   item["coverURL"], item["previewURL"],
                   tuple(f"{diff['characteristic']} · {diff['difficulty']}" +
                         (f" · {diff['nps']:g} NPS" if diff["nps"] is not None else "")
                         for diff in item["diffs"]), item["description"])


def sample_songs():
    """Fictional records, intentionally not BeatSaver metadata."""
    names = ("Blue Horizon", "星海回声", "Electric Garden", "Morning Light",
             "Paper Moon", "流光", "Silver Lines", "Quiet Orbit", "Summer Rain")
    return tuple(Song(f"fixture-{i}", names[i % len(names)] + (f" · {i//9+1}" if i >= 9 else ""),
                      "Studio Demo", "Sample Mapper", 120+i*3, 100+i*2,
                      round(80+(i % 20)*.9, 1)) for i in range(48))


class SongModel(QAbstractListModel):
    selectionChanged = Signal()
    limitReached = Signal()

    def __init__(self, songs, parent=None):
        super().__init__(parent)
        self.songs = tuple(songs)
        if len({song.key for song in self.songs}) != len(self.songs):
            raise ValueError("Song keys must be unique")
        self.checked = set()
        self.selected_records = {}
        self.status_labels = {}
        self.remote = False
        self.has_next = False
        self.page = 0
        self.page_size = 21

    @property
    def page_count(self):
        return max(1, (len(self.songs)+self.page_size-1)//self.page_size)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.visible_songs())

    def visible_songs(self):
        if self.remote:
            return self.songs
        start = self.page*self.page_size
        return self.songs[start:start+self.page_size]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < self.rowCount():
            return None
        song = self.visible_songs()[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return song
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            text = f"{song.title} · {song.artist} · {song.mapper} · {song.duration}"
            return text+(' · '+self.status_labels[song.key] if self.status_labels.get(song.key) else '')
        if role == Qt.ItemDataRole.ToolTipRole:
            text = f"{song.title}\n{song.artist}\n{song.mapper}"
            if self.status_labels.get(song.key): text += '\n'+self.status_labels[song.key]
            return "<qt>" + escape(text).replace("\n", "<br>") + "</qt>"
        if role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if song.key in self.checked else Qt.CheckState.Unchecked
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid():
            return False
        song = self.data(index, Qt.ItemDataRole.UserRole)
        if song is None:
            return False
        if value == Qt.CheckState.Checked:
            if len(self.checked) >= 200 and song.key not in self.checked:
                self.limitReached.emit()
                return False
            self.checked.add(song.key)
            self.selected_records[song.key] = song
        else:
            self.checked.discard(song.key)
            self.selected_records.pop(song.key, None)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.selectionChanged.emit()
        return True

    def paginate(self, page, page_size):
        if page_size < 1:
            raise ValueError("Positive page size required")
        super().beginResetModel()
        self.page_size = page_size
        self.page = min(max(0, page), self.page_count-1)
        super().endResetModel()

    def replace_page(self, records, page, page_size, has_next):
        records = tuple(records)
        if len({song.key for song in records}) != len(records):
            raise ValueError("Song keys must be unique")
        super().beginResetModel()
        self.remote = True
        self.songs = records
        self.page, self.page_size, self.has_next = page, page_size, has_next
        for song in records:
            if song.key in self.checked:
                self.selected_records[song.key] = song
        super().endResetModel()

    def select_page(self):
        for song in self.visible_songs():
            if len(self.checked) < 200 or song.key in self.checked:
                self.checked.add(song.key)
                self.selected_records[song.key] = song
            else:
                self.limitReached.emit()
                break
        self.notify_checks()

    def clear_checks(self):
        self.checked.clear()
        self.selected_records.clear()
        self.notify_checks()

    def notify_checks(self):
        if self.rowCount():
            self.dataChanged.emit(super().index(0), super().index(self.rowCount()-1),
                                  [Qt.ItemDataRole.CheckStateRole])
        self.selectionChanged.emit()
