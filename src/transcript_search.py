"""Transcript search window: find anything you've said across ~/.speakrflow.

Results are grouped by recording. Each recording row is followed by one row
per mention; selecting a row shows the whole transcript on the right with
every mention highlighted and the selected one scrolled into view.
"""
from datetime import datetime, timedelta

import objc
from Foundation import (
    NSObject, NSMakeRect, NSMakeSize, NSMakeRange, NSURL, NSIndexSet, NSMutableAttributedString,
)
from AppKit import (
    NSWindow,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSBackingStoreBuffered,
    NSSearchField,
    NSTableView,
    NSTableColumn,
    NSTableCellView,
    NSScrollView,
    NSSplitView,
    NSTextView,
    NSTextField,
    NSButton,
    NSView,
    NSColor,
    NSFont,
    NSApp,
    NSFloatingWindowLevel,
    NSWorkspace,
    NSSound,
    NSPasteboard,
    NSPasteboardTypeString,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSBackgroundColorAttributeName,
    NSParagraphStyleAttributeName,
    NSMutableParagraphStyle,
)

from . import archive, paths
from .diagnostics import logger


_open_controller = None

# AppKit constants (values are stable; not all are exported by PyObjC).
WIDTH_SIZABLE, HEIGHT_SIZABLE = 2, 16
MIN_X_MARGIN, MIN_Y_MARGIN = 1, 8
NSBezelRounded = 1
NSLineBreakByTruncatingTail = 4
NSSplitViewDividerStyleThin = 2
NSTableViewStyleInset = 2
NSTableViewLastColumnOnlyAutoresizingStyle = 4

RECORDING_ROW_H = 30
BROWSE_ROW_H = 48
MATCH_ROW_H = 42


def _protocols(*names):
    found = []
    for name in names:
        try:
            found.append(objc.protocolNamed(name))
        except objc.error:
            pass
    return found


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def when(dt, now=None):
    """'Today at 3:04 PM', 'Yesterday at 9:15 AM', or 'Oct 3, 2026 at 3:04 PM'."""
    if dt is None:
        return "Unknown time"
    now = now or datetime.now()
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == now.date():
        return f"Today at {clock}"
    if dt.date() == (now - timedelta(days=1)).date():
        return f"Yesterday at {clock}"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} at {clock}"


def summary(query, results, total):
    query = " ".join(query.split())
    if not query:
        if not total:
            return "No recordings yet. Everything you dictate is saved in ~/.speakrflow."
        return f"{plural(total, 'recording')}. Type to search everything you've said."
    if not results:
        return f"No matches for “{query}”"
    mentions = sum(len(r["matches"]) for r in results)
    return f"{plural(mentions, 'mention')} in {plural(len(results), 'recording')}"


def build_rows(results):
    """Flatten results into table rows: a recording row, then its mentions."""
    rows = []
    for result in results:
        rows.append(("recording", result, None))
        for i in range(len(result["matches"])):
            rows.append(("match", result, i))
    return rows


def _attributed(parts):
    """parts: list of (text, attributes dict) -> NSMutableAttributedString."""
    out = NSMutableAttributedString.alloc().init()
    for text, attrs in parts:
        out.appendAttributedString_(
            NSMutableAttributedString.alloc().initWithString_attributes_(text, attrs),
        )
    return out


class SearchController(NSObject, protocols=_protocols(
    "NSTableViewDataSource", "NSTableViewDelegate",
    "NSSearchFieldDelegate", "NSWindowDelegate", "NSSoundDelegate",
)):
    def init(self):
        self = objc.super(SearchController, self).init()
        if self is None:
            return None
        self.rows = []
        self.query = ""
        self.sound = None
        self.shown = None  # result currently displayed on the right
        self._build_window()
        self._run_search(select_first=True)
        return self

    # ---------- layout ----------

    @objc.python_method
    def _label(self, text, frame, size=13, bold=False, secondary=False):
        f = NSTextField.labelWithString_(text)
        f.setFrame_(frame)
        f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        f.setLineBreakMode_(NSLineBreakByTruncatingTail)
        if secondary:
            f.setTextColor_(NSColor.secondaryLabelColor())
        return f

    @objc.python_method
    def _button(self, title, frame, action):
        b = NSButton.alloc().initWithFrame_(frame)
        b.setTitle_(title)
        b.setBezelStyle_(NSBezelRounded)
        b.setTarget_(self)
        b.setAction_(action)
        return b

    @objc.python_method
    def _build_window(self):
        W, H = 880, 580
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False,
        )
        self.window.setTitle_("Search Transcripts")
        self.window.setLevel_(NSFloatingWindowLevel)  # same level as Settings
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.setContentMinSize_(NSMakeSize(640, 400))
        cv = self.window.contentView()

        # Top: search field and result summary.
        self.search_field = NSSearchField.alloc().initWithFrame_(NSMakeRect(16, H - 46, W - 32, 30))
        self.search_field.setFont_(NSFont.systemFontOfSize_(15))
        self.search_field.setPlaceholderString_("Search everything you've said…")
        self.search_field.setTarget_(self)
        self.search_field.setAction_("searchChanged:")
        self.search_field.setDelegate_(self)
        self.search_field.setAutoresizingMask_(WIDTH_SIZABLE | MIN_Y_MARGIN)
        cv.addSubview_(self.search_field)

        self.summary_label = self._label("", NSMakeRect(18, H - 70, W - 36, 16), size=12, secondary=True)
        self.summary_label.setAutoresizingMask_(WIDTH_SIZABLE | MIN_Y_MARGIN)
        cv.addSubview_(self.summary_label)

        # Middle: results list | transcript.
        bottom_bar = 52
        split_h = H - 80 - bottom_bar
        split = NSSplitView.alloc().initWithFrame_(NSMakeRect(0, bottom_bar, W, split_h))
        split.setVertical_(True)
        split.setDividerStyle_(NSSplitViewDividerStyleThin)
        split.setAutoresizingMask_(WIDTH_SIZABLE | HEIGHT_SIZABLE)

        left_w = 330
        list_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, left_w, split_h))
        list_scroll.setHasVerticalScroller_(True)
        list_scroll.setAutohidesScrollers_(True)
        list_scroll.setDrawsBackground_(False)
        table = NSTableView.alloc().initWithFrame_(list_scroll.bounds())
        column = NSTableColumn.alloc().initWithIdentifier_("result")
        column.setResizingMask_(1)  # NSTableColumnAutoresizingMask
        column.setWidth_(left_w)
        table.addTableColumn_(column)
        table.setHeaderView_(None)
        table.setColumnAutoresizingStyle_(NSTableViewLastColumnOnlyAutoresizingStyle)
        if hasattr(table, "setStyle_"):
            table.setStyle_(NSTableViewStyleInset)
        table.setBackgroundColor_(NSColor.clearColor())
        table.setDataSource_(self)
        table.setDelegate_(self)
        list_scroll.setDocumentView_(table)
        table.sizeLastColumnToFit()
        self.table = table
        split.addSubview_(list_scroll)

        right_w = W - left_w - 1
        right = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, right_w, split_h))
        self.title_label = self._label("", NSMakeRect(20, split_h - 32, right_w - 40, 22), size=17, bold=True)
        self.title_label.setAutoresizingMask_(WIDTH_SIZABLE | MIN_Y_MARGIN)
        right.addSubview_(self.title_label)
        self.detail_label = self._label("", NSMakeRect(20, split_h - 52, right_w - 40, 16), size=12, secondary=True)
        self.detail_label.setAutoresizingMask_(WIDTH_SIZABLE | MIN_Y_MARGIN)
        right.addSubview_(self.detail_label)

        text_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(8, 0, right_w - 16, split_h - 62))
        text_scroll.setHasVerticalScroller_(True)
        text_scroll.setAutohidesScrollers_(True)
        text_scroll.setDrawsBackground_(False)
        text_scroll.setAutoresizingMask_(WIDTH_SIZABLE | HEIGHT_SIZABLE)
        size = text_scroll.contentSize()
        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, size.width, size.height))
        tv.setMinSize_(NSMakeSize(0, size.height))
        tv.setMaxSize_(NSMakeSize(1e7, 1e7))
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.setAutoresizingMask_(WIDTH_SIZABLE)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.setTextContainerInset_(NSMakeSize(12, 4))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setDrawsBackground_(False)
        text_scroll.setDocumentView_(tv)
        self.text_view = tv
        right.addSubview_(text_scroll)
        split.addSubview_(right)
        cv.addSubview_(split)
        split.adjustSubviews()
        split.setPosition_ofDividerAtIndex_(left_w, 0)

        # Bottom bar.
        cv.addSubview_(self._button("Open Folder", NSMakeRect(16, 12, 120, 28), "openFolder:"))
        x = W - 16
        self.play_button = self.finder_button = self.copy_button = None
        for attr, title, action, w in (("play_button", "Play", "playRecording:", 90),
                                       ("finder_button", "Show in Finder", "showInFinder:", 130),
                                       ("copy_button", "Copy Text", "copyText:", 110)):
            x -= w
            b = self._button(title, NSMakeRect(x, 12, w, 28), action)
            b.setAutoresizingMask_(MIN_X_MARGIN)
            cv.addSubview_(b)
            setattr(self, attr, b)
            x -= 8

        self.window.center()

    # ---------- searching ----------

    @objc.python_method
    def _selection_key(self):
        row = self.table.selectedRow()
        if 0 <= row < len(self.rows):
            kind, result, i = self.rows[row]
            return result["number"], i
        return None

    @objc.python_method
    def _run_search(self, select_first):
        key = None if select_first else self._selection_key()
        try:
            entries = archive.list_recordings()
        except Exception:
            logger.exception("Could not read transcript archive")
            entries = []
        results = archive.search(self.query, entries)
        self.browsing = not self.query.strip()
        self.rows = build_rows(results)
        self.summary_label.setStringValue_(summary(self.query, results, len(entries)))
        self.table.reloadData()

        target = 0 if self.rows else -1
        if key is not None:
            for index, (_, result, i) in enumerate(self.rows):
                if (result["number"], i) == key:
                    target = index
                    break
        if target >= 0 and not self.browsing and self.rows[target][0] == "recording" and select_first:
            target += 1  # land on the first mention, not the header
        if target >= 0:
            self.table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(target), False,
            )
            self.table.scrollRowToVisible_(target)
        else:
            self.table.deselectAll_(None)
            self._show(None, None)

    def searchChanged_(self, sender):
        self.query = str(sender.stringValue())
        self._run_search(select_first=True)

    def control_textView_doCommandBySelector_(self, control, text_view, selector):
        # ↓ or Return in the search field moves into the results list.
        if selector in (b"moveDown:", "moveDown:", b"insertNewline:", "insertNewline:") and self.rows:
            self.window.makeFirstResponder_(self.table)
            return True
        return False

    @objc.python_method
    def refresh(self):
        self._run_search(select_first=False)

    # ---------- table ----------

    def numberOfRowsInTableView_(self, table):
        return len(self.rows)

    def tableView_heightOfRow_(self, table, row):
        if self.rows[row][0] == "match":
            return MATCH_ROW_H
        return BROWSE_ROW_H if self.browsing else RECORDING_ROW_H

    def tableView_viewForTableColumn_row_(self, table, column, row):
        kind, result, i = self.rows[row]
        cell = table.makeViewWithIdentifier_owner_(kind, self)
        if cell is None:
            cell = NSTableCellView.alloc().initWithFrame_(NSMakeRect(0, 0, column.width(), MATCH_ROW_H))
            cell.setIdentifier_(kind)
            field = NSTextField.wrappingLabelWithString_("")
            field.setFrame_(NSMakeRect(4, 3, column.width() - 8, MATCH_ROW_H - 6))
            field.setAutoresizingMask_(WIDTH_SIZABLE | HEIGHT_SIZABLE)
            field.setMaximumNumberOfLines_(2)
            field.cell().setTruncatesLastVisibleLine_(True)
            cell.addSubview_(field)
            cell.setTextField_(field)
        cell.textField().setAttributedStringValue_(self._row_text(kind, result, i))
        return cell

    @objc.python_method
    def _row_text(self, kind, result, i):
        secondary = {NSFontAttributeName: NSFont.systemFontOfSize_(12),
                     NSForegroundColorAttributeName: NSColor.secondaryLabelColor()}
        if kind == "recording":
            bold = {NSFontAttributeName: NSFont.boldSystemFontOfSize_(13),
                    NSForegroundColorAttributeName: NSColor.labelColor()}
            parts = [(f"#{result['number']}", bold), (f"   {when(result['time'])}", secondary)]
            if result["matches"]:
                parts.append((f"  ·  {plural(len(result['matches']), 'mention')}", secondary))
            elif self.browsing:
                preview = " ".join((result["text"] or "").split()) or "(no transcript)"
                parts.append(("\n" + preview, secondary))
            return _attributed(parts)

        excerpt, s, e = archive.snippet(result["text"], *result["matches"][i])
        indent = NSMutableParagraphStyle.alloc().init()
        indent.setFirstLineHeadIndent_(14)
        indent.setHeadIndent_(14)
        plain = {NSFontAttributeName: NSFont.systemFontOfSize_(12),
                 NSForegroundColorAttributeName: NSColor.labelColor(),
                 NSParagraphStyleAttributeName: indent}
        hit = dict(plain)
        hit[NSFontAttributeName] = NSFont.boldSystemFontOfSize_(12)
        hit[NSBackgroundColorAttributeName] = NSColor.systemYellowColor().colorWithAlphaComponent_(0.45)
        return _attributed([(excerpt[:s], plain), (excerpt[s:e], hit), (excerpt[e:], plain)])

    def tableViewSelectionDidChange_(self, notification):
        row = self.table.selectedRow()
        if 0 <= row < len(self.rows):
            _, result, i = self.rows[row]
            self._show(result, i if i is not None else (0 if result["matches"] else None))
        else:
            self._show(None, None)

    # ---------- transcript pane ----------

    @objc.python_method
    def _show(self, result, match_index):
        if self.shown is None or result is None or self.shown["number"] != result["number"]:
            self._stop_sound()
        self.shown = result
        has_audio = bool(result and result["audio"])
        self.play_button.setEnabled_(has_audio)
        self.finder_button.setEnabled_(bool(result))
        self.copy_button.setEnabled_(bool(result and result["text"]))

        if result is None:
            self.title_label.setStringValue_("")
            self.detail_label.setStringValue_("")
            self.text_view.textStorage().setAttributedString_(_attributed([]))
            return

        self.title_label.setStringValue_(f"Recording #{result['number']}")
        detail = [when(result["time"])]
        if result["matches"]:
            detail.append(plural(len(result["matches"]), "mention"))
            if match_index is not None:
                detail.append(f"showing {match_index + 1} of {len(result['matches'])}")
        if not has_audio:
            detail.append("audio file missing")
        self.detail_label.setStringValue_("  ·  ".join(detail))

        para = NSMutableParagraphStyle.alloc().init()
        para.setLineSpacing_(4)
        base = {NSFontAttributeName: NSFont.systemFontOfSize_(15),
                NSForegroundColorAttributeName: NSColor.labelColor(),
                NSParagraphStyleAttributeName: para}
        if not result["text"]:
            base[NSForegroundColorAttributeName] = NSColor.secondaryLabelColor()
            self.text_view.textStorage().setAttributedString_(_attributed([(
                "No transcript was saved for this recording (transcription failed "
                "or returned nothing). You can still play the audio.", base)]))
            return

        text = _attributed([(result["text"], base)])
        for k, (s, e) in enumerate(result["matches"]):
            alpha = 1.0 if k == match_index else 0.4
            text.addAttribute_value_range_(
                NSBackgroundColorAttributeName,
                NSColor.systemYellowColor().colorWithAlphaComponent_(alpha),
                NSMakeRange(s, e - s),
            )
        self.text_view.textStorage().setAttributedString_(text)
        if match_index is not None and result["matches"]:
            s, e = result["matches"][match_index]
            target = NSMakeRange(s, e - s)
            self.text_view.scrollRangeToVisible_(target)
            if self.window.isVisible():
                self.text_view.showFindIndicatorForRange_(target)
        else:
            self.text_view.scrollRangeToVisible_(NSMakeRange(0, 0))

    # ---------- buttons ----------

    def playRecording_(self, sender):
        if self.sound is not None:
            self._stop_sound()
            return
        if not (self.shown and self.shown["audio"]):
            return
        sound = NSSound.alloc().initWithContentsOfFile_byReference_(str(self.shown["audio"]), True)
        if sound is None:
            logger.warning("Could not open archived recording #%d", self.shown["number"])
            return
        sound.setDelegate_(self)
        self.sound = sound
        sound.play()
        self.play_button.setTitle_("Stop")

    def sound_didFinishPlaying_(self, sound, finished):
        if sound is self.sound:
            self.sound = None
            self.play_button.setTitle_("Play")

    @objc.python_method
    def _stop_sound(self):
        sound, self.sound = self.sound, None
        if sound is not None:
            sound.stop()
        self.play_button.setTitle_("Play")

    def showInFinder_(self, sender):
        if not self.shown:
            return
        path = self.shown["audio"] or self.shown["transcript"]
        NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(
            [NSURL.fileURLWithPath_(str(path))],
        )

    def copyText_(self, sender):
        if self.shown and self.shown["text"]:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(self.shown["text"], NSPasteboardTypeString)

    def openFolder_(self, sender):
        paths.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(paths.ARCHIVE_DIR)))

    # ---------- window ----------

    @objc.python_method
    def show(self):
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.search_field)

    def windowWillClose_(self, notification):
        global _open_controller
        self._stop_sound()
        _open_controller = None


def open_search():
    """Open (or focus) the transcript search window."""
    global _open_controller
    if _open_controller is None:
        _open_controller = SearchController.alloc().init()
    _open_controller.show()


def refresh_if_open():
    """Pick up a newly saved transcript without losing the current selection."""
    if _open_controller is not None:
        _open_controller.refresh()
