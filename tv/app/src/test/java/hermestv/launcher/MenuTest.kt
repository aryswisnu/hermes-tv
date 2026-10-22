package hermestv.launcher

import hermestv.launcher.Press.Kind.LONG
import hermestv.launcher.Press.Kind.NONE
import hermestv.launcher.Press.Kind.SHORT
import org.junit.Assert.assertEquals
import org.junit.Test

class MenuTest {
    private val film = Film("Jumbo", 2025, 102, 7.7, "netflix", "https://www.netflix.com/title/1", null, null, emptyList(), "")
    private val video = Film(
        "The Lighthouse Mystery", null, null, null, "youtube", "https://www.youtube.com/watch?v=Myst0000001", null, null, emptyList(), "",
        kind = "video", channel = "Cold Case Hour", durationMin = 30, videoId = "Myst0000001",
    )

    @Test
    fun shortPressOpensOnKeyUp() {
        val p = Press()
        assertEquals(NONE, p.down(1_000, repeat = 0))
        assertEquals(NONE, p.held(1_599))
        assertEquals(SHORT, p.up(1_150))
        // A second short press works the same way.
        assertEquals(NONE, p.down(2_000, repeat = 0))
        assertEquals(SHORT, p.up(2_599))
    }

    @Test
    fun longPressAt600msShowsTheMenuOnceAndNeverOpens() {
        val p = Press()
        p.down(1_000, repeat = 0)
        assertEquals(NONE, p.down(1_500, repeat = 1))
        assertEquals(LONG, p.down(1_600, repeat = 2))
        assertEquals(NONE, p.down(1_650, repeat = 3))
        assertEquals(NONE, p.held(1_700))
        assertEquals(NONE, p.up(2_000))

        // Without key repeats the 600 ms timer shows the menu.
        p.down(5_000, repeat = 0)
        assertEquals(NONE, p.held(5_599))
        assertEquals(LONG, p.held(5_600))
        assertEquals(NONE, p.up(5_900))

        // A release after 600 ms that neither a repeat nor the timer saw is still a long press.
        p.down(8_000, repeat = 0)
        assertEquals(LONG, p.up(8_700))
    }

    @Test
    fun strayEventsDoNothing() {
        val p = Press()
        // The key up of a press that started in another app, and a held key that came in already repeating.
        assertEquals(NONE, p.up(1_000))
        assertEquals(NONE, p.down(2_000, repeat = 5))
        assertEquals(NONE, p.held(3_000))
        assertEquals(NONE, p.up(3_100))
        p.down(4_000, repeat = 0)
        assertEquals(NONE, p.up(4_100, canceled = true))
        assertEquals(NONE, p.up(4_200))
    }

    @Test
    fun menuEntries() {
        assertEquals(listOf("Not for us", "Cancel"), Menus.card(film).map { it.text })
        assertEquals(listOf("Not for us", "Cancel"), Menus.card(film.copy(kind = "series", seasons = 2)).map { it.text })
        assertEquals(listOf("Not for us", "Hide this channel", "Cancel"), Menus.card(video).map { it.text })
        assertEquals(listOf("Not for us", "Cancel"), Menus.card(video.copy(channel = null)).map { it.text })
        // Without a video id the bridge refuses the hide, so the card gets no menu.
        assertEquals(emptyList<Choice>(), Menus.card(video.copy(videoId = null)))
        assertEquals(listOf("Hide from dock", "Show hidden apps", "Cancel"), Menus.dock(app = true, hidden = 2).map { it.text })
        assertEquals(listOf("Hide from dock", "Cancel"), Menus.dock(app = true, hidden = 0).map { it.text })
        // Inputs and Settings cannot be hidden; with nothing hidden they get no menu.
        assertEquals(listOf("Show hidden apps", "Cancel"), Menus.dock(app = false, hidden = 1).map { it.text })
        assertEquals(emptyList<Choice>(), Menus.dock(app = false, hidden = 0))
    }

    @Test
    fun menuOpensOnCancel() {
        val m = OpenMenu(Menus.card(video).map { it.text }) {}
        assertEquals(2, m.focus)
        assertEquals(false, m.move(1))
        assertEquals(true, m.move(-2))
        assertEquals(0, m.focus)
        assertEquals(false, m.move(-1))
    }

    private val calls = ArrayList<String>()
    private var menu: OpenMenu? = null
    private val ok = OkKey(
        menu = { menu },
        open = { calls += "open" },
        showMenu = { calls += "menu" },
        choose = { calls += "choose ${it.entries[it.focus]}" },
    )

    @Test
    fun okWithoutMenu() {
        ok.down(0, repeat = 0)
        ok.up(100, canceled = false)
        assertEquals(listOf("open"), calls)
        ok.down(1_000, repeat = 0)
        ok.held(1_600)
        ok.up(1_900, canceled = false)
        assertEquals(listOf("open", "menu"), calls)
    }

    @Test
    fun shortPressInMenuChoosesAndLongPressDoesNothing() {
        menu = OpenMenu(listOf("Not for us", "Cancel")) {}
        ok.down(0, repeat = 0)
        ok.held(600)
        ok.down(650, repeat = 3)
        ok.up(900, canceled = false)
        assertEquals(emptyList<String>(), calls)
        ok.down(2_000, repeat = 0)
        ok.up(2_100, canceled = false)
        assertEquals(listOf("choose Cancel"), calls)
    }
}
