package hermestv.launcher

import hermestv.launcher.Line.DOCK
import hermestv.launcher.Line.FILMS
import hermestv.launcher.Line.HERMES
import hermestv.launcher.Line.SERIES
import hermestv.launcher.Line.YOUTUBE
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

class HomeRowsTest {
    private val night = Picks.parse(javaClass.classLoader!!.getResource("picks_night.json")!!.readText())
    private val films = night.films
    private val series = films.map { it.copy(title = it.title + " The Series", kind = "series", seasons = 2) }
    private val videos = (1..4).map { i ->
        Film(
            "Case file $i", null, null, null, "youtube", "https://www.youtube.com/watch?v=abcdefghij$i", null, null, emptyList(),
            "", kind = "video", channel = "Story Vault", durationMin = 20 + i, videoId = "abcdefghij$i",
        )
    }

    // Hermes's picks: a film and a series, each with Hermes's one-line why.
    private val hermes = listOf(films[0].copy(why = "A slow forest chill for tonight"), series[1].copy(why = "Short episodes, big twists"))

    private fun local(h: Int, m: Int) = ZonedDateTime.of(2026, 10, 20, h, m, 0, 0, ZoneId.of("Asia/Tokyo"))
    private fun showing(list: List<Film>) = PickerView.Showing(night.copy(films = list), refreshing = false)
    private fun focused(r: HomeRows, line: Line) = (r.row(line).focused as RowItem.Film).film

    private fun lunch(h: Int = 12, m: Int = 30) = HomeRows(local(h, m)).apply {
        show(FILMS, showing(films))
        show(SERIES, showing(series))
        show(YOUTUBE, showing(videos))
    }

    @Test
    fun verticalSlide() {
        val r = lunch()
        assertEquals(listOf(FILMS, SERIES, YOUTUBE, DOCK), r.lines)
        assertEquals(FILMS, r.focus)
        assertFalse(r.up())
        // ▼ makes the next line the focused one; the screen puts it at the top and the line after it below.
        assertTrue(r.down())
        assertEquals(SERIES, r.focus)
        assertEquals(1, r.index)
        assertTrue(r.down())
        assertEquals(YOUTUBE, r.focus)
        assertTrue(r.down())
        assertEquals(DOCK, r.focus)
        // The dock is the last line.
        assertFalse(r.down())
        assertFalse(r.next())
        assertTrue(r.up())
        assertEquals(YOUTUBE, r.focus)
        assertTrue(r.next())
        assertEquals(videos[1], focused(r, YOUTUBE))
        r.home()
        assertEquals(FILMS, r.focus)
        assertEquals(0, r.row(YOUTUBE).focus)
    }

    @Test
    fun emptyRowHidden() {
        val r = HomeRows(local(20, 0))
        // Before any answer the films row shows its "Finding films…" card; the other rows are hidden.
        assertEquals(listOf(FILMS, DOCK), r.lines)
        assertEquals(listOf(RowItem.Message(HomeRow.FINDING), RowItem.AllFilms), r.row(FILMS).items)
        r.show(SERIES, PickerView.Finding)
        r.show(SERIES, PickerView.Offline("Hermes is offline"))
        r.show(YOUTUBE, showing(emptyList()))
        r.show(YOUTUBE, PickerView.NoFilms)
        assertEquals(listOf(FILMS, DOCK), r.lines)
        r.show(FILMS, PickerView.Offline("Hermes is offline"))
        assertEquals(listOf(RowItem.Message("Hermes is offline"), RowItem.AllFilms), r.row(FILMS).items)

        r.show(SERIES, showing(series))
        assertEquals(listOf(FILMS, SERIES, DOCK), r.lines)
        // Only the films row ends with "All films ›".
        assertEquals(series.map { RowItem.Film(it) }, r.row(SERIES).items)

        // "Not for us" on the last series hides the row; the focus moves to the line above.
        assertTrue(r.down())
        assertEquals(SERIES, r.focus)
        series.forEach { r.remove(SERIES, it) }
        assertEquals(listOf(FILMS, DOCK), r.lines)
        assertEquals(FILMS, r.focus)

        // The films row never hides: its last film gives way to the "No films" card.
        r.show(FILMS, showing(films))
        films.forEach { r.remove(FILMS, it) }
        assertEquals(listOf(RowItem.Message(HomeRow.NO_FILMS), RowItem.AllFilms), r.row(FILMS).items)
    }

    @Test
    fun slotChangeRebuildsRows() {
        val r = lunch(13, 59)
        assertEquals("lunch", r.slot)
        r.down()
        r.down()
        assertEquals(YOUTUBE, r.focus)
        assertFalse(r.onMinute(local(13, 59)))
        assertTrue(r.onMinute(local(14, 0)))
        assertEquals("afternoon", r.slot)
        // The afternoon states start from their own saved lists: there is no YouTube row in the afternoon.
        r.show(YOUTUBE, PickerView.Finding)
        r.show(SERIES, showing(series.reversed()))
        assertEquals(listOf(FILMS, SERIES, DOCK), r.lines)
        assertEquals(SERIES, r.focus)
        // The six bridge slots.
        mapOf(
            local(17, 0) to "dinner", local(19, 0) to "night", local(4, 0) to "late", local(6, 0) to "morning",
            local(11, 0) to "lunch", local(14, 0) to "afternoon",
        ).forEach { (t, slot) ->
            assertTrue("$t", r.onMinute(t))
            assertEquals(slot, r.slot)
        }
    }

    @Test
    fun swapKeepsFocusPerRow() {
        val r = lunch()
        r.next()
        assertEquals(films[1], focused(r, FILMS))
        r.down()
        r.next()
        r.next()
        assertEquals(series[2], focused(r, SERIES))
        // A new series list keeps the same series focused; the films row keeps its own focus.
        assertTrue(r.show(SERIES, showing(series.reversed())))
        assertEquals(series[2], focused(r, SERIES))
        assertEquals(1, r.row(FILMS).focus)
        r.up()
        assertEquals(films[1], focused(r, FILMS))

        // Videos are named by their id: a re-titled video keeps the focus.
        r.down()
        r.down()
        r.next()
        val retitled = videos.map { it.copy(title = it.title.uppercase()) }.reversed()
        r.show(YOUTUBE, showing(retitled))
        assertEquals("abcdefghij2", focused(r, YOUTUBE).videoId)

        // "Not for us": the next card moves into the big slot.
        val index = r.row(YOUTUBE).focus
        r.remove(YOUTUBE, focused(r, YOUTUBE))
        assertEquals(index, r.row(YOUTUBE).focus)
        assertEquals(retitled[index + 1], focused(r, YOUTUBE))
        assertEquals(3, r.row(YOUTUBE).items.size)
    }

    @Test
    fun hermesRowFirstWhenPresent() {
        val r = lunch()
        r.show(HERMES, showing(hermes))
        assertEquals(listOf(HERMES, FILMS, SERIES, YOUTUBE, DOCK), r.lines)
        // The row comes in at the top with its first pick as the big card; like series it has no "All films ›".
        assertEquals(HERMES, r.focus)
        assertEquals(0, r.index)
        assertEquals(hermes.map { RowItem.Film(it) }, r.row(HERMES).items)
        assertEquals(hermes[0], focused(r, HERMES))
        assertFalse(r.up())
        assertTrue(r.next())
        assertEquals(hermes[1], focused(r, HERMES))
        assertTrue(r.down())
        assertEquals(FILMS, r.focus)
        // Home goes back to the top line.
        r.home()
        assertEquals(HERMES, r.focus)
        assertEquals(hermes[0], focused(r, HERMES))

        // No YouTube row at night: From Hermes, Films, Series.
        val night = HomeRows(local(20, 0)).apply {
            show(FILMS, showing(films))
            show(SERIES, showing(series))
            show(HERMES, showing(hermes))
        }
        assertEquals(listOf(HERMES, FILMS, SERIES, DOCK), night.lines)
        assertEquals(HERMES, night.focus)

        // Once ▲ ▼ ◀ ▶ moved after Home, a late Hermes row does not take the focus.
        val late = lunch()
        late.down()
        late.show(HERMES, showing(hermes))
        assertEquals(SERIES, late.focus)
        assertEquals(2, late.index)
    }

    @Test
    fun hermesRowHiddenWhenEmpty() {
        val r = lunch()
        r.show(HERMES, PickerView.Finding)
        r.show(HERMES, PickerView.Offline("Hermes is offline"))
        r.show(HERMES, showing(emptyList()))
        r.show(HERMES, PickerView.NoFilms)
        assertEquals(listOf(FILMS, SERIES, YOUTUBE, DOCK), r.lines)
        assertEquals(FILMS, r.focus)
        assertTrue(r.row(HERMES).items.isEmpty())

        // "Not for us" on the last pick hides the row; the focus goes to the films row below it.
        r.show(HERMES, showing(hermes))
        assertEquals(HERMES, r.focus)
        assertTrue(r.next())
        hermes.forEach { r.remove(HERMES, it) }
        assertEquals(listOf(FILMS, SERIES, YOUTUBE, DOCK), r.lines)
        assertEquals(FILMS, r.focus)

        // An empty answer from the bridge hides the row too.
        r.show(HERMES, showing(hermes))
        r.show(HERMES, showing(emptyList()))
        assertEquals(listOf(FILMS, SERIES, YOUTUBE, DOCK), r.lines)
        assertEquals(FILMS, r.focus)
    }
}
