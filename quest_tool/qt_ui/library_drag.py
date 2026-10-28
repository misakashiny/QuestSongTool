"""In-app copy gestures open the existing confirmed transfer flow."""
import secrets

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtWidgets import QAbstractItemView

from ..messages import tr

MIME='application/x-quest-library-transfer'


class LibraryDrag:
    def __init__(self, window):
        self.window=window;self.ticket=None;self.plan=None
        for side,model,views in (
            ('local',window.model,(window.table,window.grid)),
            ('quest',window.quest_model,(window.quest_table,)),
            ('missing',window.missing_model,(window.missing_table,))):
            model.drag_coordinator=self
            for view in views:
                view.setDragEnabled(True);view.setAcceptDrops(side!='missing')
                view.setDropIndicatorShown(True);view.setDefaultDropAction(Qt.DropAction.CopyAction)
                view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
                view.setToolTip(tr('ui.library.drag_hint'))

    def context(self):
        w=self.window;selected=w.device_browser.selected
        return (str(w.target),w.generation,w.quest_identity,w.device_browser.read_epoch,
                selected['identity'] if selected else None)

    def mime(self, model, indexes):
        rows=sorted({v.row() for v in indexes if v.isValid()});names=[];origins=set()
        for i in rows:
            row=model.rows[i];origin=row.get('origin') if model.origin=='missing' else model.origin
            if row.get('error') or origin not in ('local','quest'):continue
            names.append(row['folder']);origins.add(origin)
        data=QMimeData();self.plan=None;self.ticket=None
        if not names or len(names)>2000 or len(origins)!=1:return data
        self.ticket=secrets.token_hex(16)
        self.plan=(model.origin,next(iter(origins)),tuple(names),self.context())
        data.setData(MIME,self.ticket.encode('ascii'));return data

    def accepts(self, model, data, action):
        if action!=Qt.DropAction.CopyAction or not data.hasFormat(MIME) or not self.plan or not self.ticket:return False
        if bytes(data.data(MIME))!=self.ticket.encode('ascii') or model.origin=='missing':return False
        _source,origin,_names,context=self.plan
        return origin!=model.origin and context==self.context() and not self.window.closed

    def drop(self, model, data, action):
        if not self.accepts(model,data,action):return False
        source,origin,names,_context=self.plan;self.plan=None;self.ticket=None
        direction='to_quest' if origin=='local' else 'to_local'
        before=self.window.transfers.dialog
        self.window.transfers.open(source,direction,folders=names)
        if self.window.transfers.dialog is before:
            self.window.statusBar().showMessage(tr('qt.transfer.blocked'));return False
        return True
