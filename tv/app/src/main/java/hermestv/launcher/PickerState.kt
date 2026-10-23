package hermestv.launcher

import java.util.concurrent.Executor

/** What the picker screen shows. */
sealed interface PickerView {
    data object Finding : PickerView
    data object NoFilms : PickerView
    data class Offline(val message: String) : PickerView
    data class Showing(val picks: Picks, val refreshing: Boolean) : PickerView
}

fun interface Cancel { fun cancel() }
fun interface Timer { fun after(ms: Long, task: Runnable): Cancel }

/**
 * Shows the saved list of [slot] at once, asks the bridge, and polls while the bridge refreshes.
 * A new list replaces the shown one only when its updated_at or its films differ (the bridge drops hidden and
 * opened films at once, with the same updated_at). A list for another slot is never shown or saved: at a slot
 * change the bridge's clock can be a little behind the TV's, so it is asked again. After stop(), late replies
 * are dropped. An empty list keeps the shown one, unless [clearOnEmpty] (From Hermes: an empty list means no picks).
 */
class PickerState(
    private val slot: String,
    local: Picks?,
    private val fetch: () -> Picks,
    private val save: (Picks) -> Unit,
    private val timer: Timer,
    private val io: Executor,
    private val clearOnEmpty: Boolean = false,
    private val show: (PickerView) -> Unit,
) {
    private var current: Picks? = local?.takeIf { it.films.isNotEmpty() }
    private var polls = 0
    private var poll: Cancel? = null
    @Volatile private var stopped = false

    fun start() {
        show(current?.let { PickerView.Showing(it, refreshing = true) } ?: PickerView.Finding)
        request()
    }

    @Synchronized
    fun stop() {
        stopped = true
        poll?.cancel()
        poll = null
    }

    /** Takes [film] out of the list ("Not for us") and saves the rest, so a later show or restart keeps it out. */
    @Synchronized
    fun drop(film: Film) {
        val c = current ?: return
        val rest = c.copy(films = c.films.filterNot { HomeRow.key(it) == HomeRow.key(film) })
        if (rest.films.size == c.films.size) return
        current = rest
        io.execute { save(rest) }
    }

    private fun request() = io.execute {
        if (stopped) return@execute
        val reply = try {
            fetch()
        } catch (e: Exception) {
            failed(e)
            return@execute
        }
        answered(reply)
    }

    @Synchronized
    private fun answered(r: Picks) {
        if (stopped) return
        val ours = r.slot == slot
        if (ours && (r.films.isNotEmpty() || clearOnEmpty) && (r.updatedAt != current?.updatedAt || r.films != current?.films)) {
            val fresh = r.copy(refreshing = false)
            current = fresh
            save(fresh)
        }
        val more = (r.refreshing || !ours) && polls < MAX_POLLS
        if (more) {
            polls++
            poll = timer.after(POLL_MS) { request() }
        }
        show(current?.let { PickerView.Showing(it, more) } ?: if (more) PickerView.Finding else PickerView.NoFilms)
    }

    @Synchronized
    private fun failed(e: Exception) {
        if (stopped) return
        val message = (e as? BridgeUnavailable)?.userMessage ?: OFFLINE
        show(current?.let { PickerView.Showing(it, refreshing = false) } ?: PickerView.Offline(message))
    }

    companion object {
        const val OFFLINE = "Hermes is offline"
        const val POLL_MS = 8_000L
        const val MAX_POLLS = 12

        /** The index of the same film in the new list, else 0. */
        fun keepFocus(old: List<Film>, index: Int, new: List<Film>): Int {
            val key = old.getOrNull(index)?.let(HomeRow::key) ?: return 0
            return new.indexOfFirst { HomeRow.key(it) == key }.coerceAtLeast(0)
        }
    }
}
