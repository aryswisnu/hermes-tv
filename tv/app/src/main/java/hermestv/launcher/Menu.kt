package hermestv.launcher

/**
 * Tells a short OK press from a long one (≥ 600 ms). A short press acts on key up. A long press acts once, at
 * the first key repeat or timer tick past 600 ms, and its key up then does nothing.
 */
class Press(private val longMs: Long = LONG_MS) {
    enum class Kind { NONE, SHORT, LONG }

    private var downAt: Long? = null
    private var fired = false

    fun down(time: Long, repeat: Int): Kind {
        if (repeat > 0) return held(time)
        downAt = time
        fired = false
        return Kind.NONE
    }

    fun held(time: Long): Kind {
        val at = downAt ?: return Kind.NONE
        if (fired || time - at < longMs) return Kind.NONE
        fired = true
        return Kind.LONG
    }

    fun up(time: Long, canceled: Boolean = false): Kind {
        val at = downAt ?: return Kind.NONE
        downAt = null
        return when {
            canceled || fired -> Kind.NONE
            time - at >= longMs -> Kind.LONG
            else -> Kind.SHORT
        }
    }

    companion object {
        const val LONG_MS = 600L
    }
}

enum class Choice(val text: String) {
    NOT_FOR_US("Not for us"),
    HIDE_CHANNEL("Hide this channel"),
    HIDE_APP("Hide from dock"),
    SHOW_HIDDEN("Show hidden apps"),
    CANCEL("Cancel"),
}

/** The long-press menus: on a card, and on a dock tile. */
object Menus {
    /** Empty for a video without an id: the bridge would refuse its hide (422). */
    fun card(f: Film): List<Choice> = when {
        f.kind == "video" && f.videoId == null -> emptyList()
        else -> listOfNotNull(Choice.NOT_FOR_US, Choice.HIDE_CHANNEL.takeIf { f.kind == "video" && f.channel != null }, Choice.CANCEL)
    }

    /** [app] is false on Inputs and Settings, which cannot be hidden; [hidden] counts the apps hidden now. */
    fun dock(app: Boolean, hidden: Int): List<Choice> {
        val list = listOfNotNull(Choice.HIDE_APP.takeIf { app }, Choice.SHOW_HIDDEN.takeIf { hidden > 0 })
        return if (list.isEmpty()) emptyList() else list + Choice.CANCEL
    }
}

/** An open menu: its entries and the focused one. It opens on the last entry, Cancel. */
class OpenMenu(val entries: List<String>, val choose: (Int) -> Unit) {
    var focus = entries.lastIndex
        private set

    fun move(by: Int): Boolean {
        val to = focus + by
        if (to !in entries.indices) return false
        focus = to
        return true
    }
}

/**
 * Routes the OK key. A short press chooses the focused entry when a menu is open, else opens the focused card;
 * a long press opens the menu, and does nothing while one is open.
 */
class OkKey(
    private val menu: () -> OpenMenu?,
    private val open: () -> Unit,
    private val showMenu: () -> Unit,
    private val choose: (OpenMenu) -> Unit,
) {
    private val press = Press()

    fun down(time: Long, repeat: Int) = act(press.down(time, repeat))

    fun held(time: Long) = act(press.held(time))

    fun up(time: Long, canceled: Boolean) = act(press.up(time, canceled))

    private fun act(kind: Press.Kind) {
        val m = menu()
        when (kind) {
            Press.Kind.SHORT -> if (m != null) choose(m) else open()
            Press.Kind.LONG -> if (m == null) showMenu()
            Press.Kind.NONE -> Unit
        }
    }
}
