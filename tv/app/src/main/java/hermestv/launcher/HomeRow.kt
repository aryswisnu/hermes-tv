package hermestv.launcher

import java.time.ZonedDateTime

sealed interface RowItem {
    data class Film(val film: hermestv.launcher.Film) : RowItem
    data class Message(val text: String) : RowItem
    data object AllFilms : RowItem
}

/** The lines of the home screen, top to bottom: four rows of picks, then the app dock. */
enum class Line(val key: String, val label: String) {
    HERMES("hermes", "From Hermes"),
    FILMS("films", "Films"),
    SERIES("series", "Series"),
    YOUTUBE("youtube", "YouTube"),
    DOCK("dock", "Your apps"),
}

/**
 * The home screen's lines. ▲ ▼ move the focus between the lines that show (films and the dock always, the
 * other rows when they have items); the screen puts the focused line at the top. Each row keeps its own focus.
 * Until a key moves the focus after Home, it stays on the top line, so a From Hermes row that comes in is in view.
 */
class HomeRows(now: ZonedDateTime) {
    private val rows = ROWS.associateWith { HomeRow(it) }
    var slot: String = Slot.at(now)
        private set
    var focus = Line.FILMS
        private set
    private var moved = false

    val lines: List<Line> get() = Line.entries.filter { it == Line.FILMS || it == Line.DOCK || row(it).items.isNotEmpty() }
    val index: Int get() = lines.indexOf(focus)

    fun row(line: Line): HomeRow = rows.getValue(line)

    /** Swaps in the row's new view. Returns true when the row changed. */
    fun show(line: Line, view: PickerView): Boolean = row(line).show(view).also { keepFocus() }

    /** Takes [film] out of the row at once ("Not for us"). */
    fun remove(line: Line, film: hermestv.launcher.Film) {
        row(line).remove(film)
        keepFocus()
    }

    fun down(): Boolean = step(1)

    fun up(): Boolean = step(-1)

    fun next(): Boolean = (focus != Line.DOCK && row(focus).next()).also { if (it) moved = true }

    fun prev(): Boolean = (focus != Line.DOCK && row(focus).prev()).also { if (it) moved = true }

    fun home() {
        rows.values.forEach(HomeRow::home)
        moved = false
        focus = lines.first()
    }

    fun restart() = rows.values.forEach(HomeRow::restart)

    /** Returns true when the slot changed; the caller then loads the picks of the new slot. */
    fun onMinute(now: ZonedDateTime): Boolean {
        val s = Slot.at(now)
        if (s == slot) return false
        slot = s
        return true
    }

    private fun step(by: Int): Boolean {
        val shown = lines
        val to = shown.getOrNull(shown.indexOf(focus) + by) ?: return false
        focus = to
        moved = true
        return true
    }

    // A hidden row hands the focus to the nearest line above it, From Hermes to the films row, which always shows.
    private fun keepFocus() {
        val shown = lines
        focus = when {
            !moved -> shown.first()
            focus in shown -> focus
            else -> Line.entries.take(focus.ordinal).lastOrNull { it in shown } ?: shown.first()
        }
    }

    companion object {
        val ROWS = listOf(Line.HERMES, Line.FILMS, Line.SERIES, Line.YOUTUBE)
    }
}

/**
 * One row of picks: Hermes's picks, films (then "All films ›"), series or videos. The focused item is always the
 * big card in the left slot, so the visible slide is the row from the focus on; ◀ ▶ move it by one. Only the
 * films row shows a message card when it has nothing; the other rows are then empty and hidden.
 */
class HomeRow(val kind: Line) {
    var items: List<RowItem> = cards(emptyList(), PickerView.Finding)
        private set
    var focus = 0
        private set
    private var touched = false
    val focused: RowItem? get() = items.getOrNull(focus)

    /** Swaps in the new view, keeping the focused film when it is still there. Returns true when the row changed. */
    fun show(view: PickerView): Boolean {
        val new = cards((view as? PickerView.Showing)?.picks?.films.orEmpty(), view)
        if (new == items) return false
        // Until ◀ ▶ move in the row after onStart or Home, a new list shows from the start: the bridge puts kept picks last.
        focus = when (val old = focused) {
            is RowItem.Film -> if (!touched) 0 else new.indexOfFirst { it is RowItem.Film && key(it.film) == key(old.film) }.coerceAtLeast(0)
            RowItem.AllFilms -> new.lastIndex
            is RowItem.Message, null -> 0
        }
        items = new
        return true
    }

    /** Removes [film]; the card after it moves into the big slot. */
    fun remove(film: hermestv.launcher.Film) {
        val films = items.mapNotNull { (it as? RowItem.Film)?.film }
        val left = films.filter { key(it) != key(film) }
        if (left.size == films.size) return
        items = cards(left, PickerView.NoFilms)
        focus = focus.coerceAtMost(items.lastIndex).coerceAtLeast(0)
    }

    fun next(): Boolean = (focus < items.lastIndex).also { if (it) move(1) }

    fun prev(): Boolean = (focus > 0).also { if (it) move(-1) }

    fun home() {
        focus = 0
        touched = false
    }

    /** The screen came back (onStart): the next list starts from its first card unless ◀ ▶ move first. */
    fun restart() {
        touched = false
    }

    private fun move(by: Int) {
        focus += by
        touched = true
    }

    private fun cards(films: List<hermestv.launcher.Film>, view: PickerView): List<RowItem> = when {
        kind != Line.FILMS -> films.map { RowItem.Film(it) }
        films.isEmpty() -> message(view)
        else -> films.map { RowItem.Film(it) } + RowItem.AllFilms
    }

    companion object {
        const val FINDING = "Finding films…"
        const val NO_FILMS = "No films found right now"

        private fun message(view: PickerView): List<RowItem> {
            val text = when (view) {
                PickerView.Finding -> FINDING
                is PickerView.Offline -> view.message
                else -> NO_FILMS
            }
            return listOf(RowItem.Message(text), RowItem.AllFilms)
        }

        // The bridge's url can change between refreshes; title and year name a film or series, the id a video.
        fun key(f: hermestv.launcher.Film): String =
            f.videoId?.let { "v:$it" } ?: (f.title.lowercase().filter(Char::isLetterOrDigit) + ":" + f.year)
    }
}
