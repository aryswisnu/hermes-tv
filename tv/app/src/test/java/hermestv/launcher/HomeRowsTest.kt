package hermestv.launcher

import hermestv.launcher.Line.DOCK
import hermestv.launcher.Line.FILMS
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
}
