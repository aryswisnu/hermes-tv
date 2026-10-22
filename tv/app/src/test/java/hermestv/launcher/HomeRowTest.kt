package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeRowTest {
    private val night = Picks.parse(javaClass.classLoader!!.getResource("picks_night.json")!!.readText())
    private val films = night.films
    private val row = HomeRow(Line.FILMS)
    private fun cards(list: List<Film>) = list.map { RowItem.Film(it) } + RowItem.AllFilms

    @Test
    fun noFilmsOfflineCard() {
        row.show(PickerView.Offline("Hermes is offline"))
        assertEquals(listOf(RowItem.Message("Hermes is offline"), RowItem.AllFilms), row.items)
        assertEquals(0, row.focus)
        row.show(PickerView.NoFilms)
        assertEquals(listOf(RowItem.Message("No films found right now"), RowItem.AllFilms), row.items)
        // A list with no films never leaves the row without its message card.
        row.show(PickerView.Showing(night.copy(films = emptyList()), refreshing = false))
        assertEquals(listOf(RowItem.Message("No films found right now"), RowItem.AllFilms), row.items)
        assertEquals(0, row.focus)
    }

    @Test
    fun findingCard() {
        assertEquals(listOf(RowItem.Message("Finding films…"), RowItem.AllFilms), row.items)
        assertEquals(RowItem.Message("Finding films…"), row.focused)
        assertTrue(row.next())
        assertEquals(RowItem.AllFilms, row.focused)
        row.show(PickerView.Finding)
        assertEquals(listOf(RowItem.Message("Finding films…"), RowItem.AllFilms), row.items)
    }

    @Test
    fun carouselShiftsByOne() {
        row.show(PickerView.Showing(night, refreshing = true))
        assertEquals(cards(films), row.items)
        assertEquals(cards(films)[0], row.focused)
        assertFalse(row.prev())
        assertTrue(row.next())
        // The next film is now the big card in the left slot; the old one left the slide.
        assertEquals(1, row.focus)
        assertEquals(cards(films)[1], row.focused)
        assertTrue(row.next())
        assertEquals(cards(films)[2], row.focused)
        assertTrue(row.prev())
        assertEquals(cards(films)[1], row.focused)
        repeat(3) { assertTrue(row.next()) }
        assertEquals(RowItem.AllFilms, row.focused)
        assertFalse(row.next())
        assertEquals(films.size, row.focus)
        row.home()
        assertEquals(0, row.focus)
        assertEquals(RowItem.Film(films[0]), row.focused)
    }

    @Test
    fun swapKeepsFocusedFilm() {
        row.show(PickerView.Showing(night, refreshing = true))
        row.next()
        assertEquals("The Night Orchard", (row.focused as RowItem.Film).film.title)

        val reversed = films.reversed()
        assertTrue(row.show(PickerView.Showing(night.copy(films = reversed), refreshing = false)))
        assertEquals(cards(reversed), row.items)
        assertEquals(2, row.focus)

        // Same film after the bridge re-titles it: case and punctuation do not matter, the year does.
        val retitled = reversed.map {
            if (it.title == "The Night Orchard") it.copy(title = "THE NIGHT ORCHARD!", url = "https://www.max.com/x") else it
        }
        row.show(PickerView.Showing(night.copy(films = retitled), refreshing = false))
        assertEquals(2, row.focus)
        assertFalse(row.show(PickerView.Showing(night.copy(films = retitled), refreshing = false)))
        assertEquals(2, row.focus)

        val remake = retitled.map { if (it.title == "THE NIGHT ORCHARD!") it.copy(year = 1999) else it }
        row.show(PickerView.Showing(night.copy(films = remake), refreshing = false))
        assertEquals(0, row.focus)

        // On "All films ›" the focus stays on it.
        repeat(films.size) { row.next() }
        assertEquals(RowItem.AllFilms, row.focused)
        row.show(PickerView.Showing(night.copy(films = films.take(2)), refreshing = false))
        assertEquals(RowItem.AllFilms, row.focused)

        // A new message keeps the focus on "All films ›" too.
        row.show(PickerView.Offline("Hermes is offline"))
        assertEquals(RowItem.AllFilms, row.focused)
        row.prev()
        row.show(PickerView.Showing(night, refreshing = false))
        assertEquals(0, row.focus)
    }

    @Test
    fun untouchedRowStartsANewListFromItsFirstCard() {
        // The bridge keeps up to 3 old picks and puts them last; after onStart or Home the row does not follow them.
        row.show(PickerView.Showing(night, refreshing = true))
        val rotated = films.drop(1) + films[0]
        row.show(PickerView.Showing(night.copy(films = rotated), refreshing = false))
        assertEquals(0, row.focus)
        assertEquals(RowItem.Film(rotated[0]), row.focused)

        // Once ◀ ▶ moved in the row, the focused film is kept, even back on the first card.
        row.next()
        row.prev()
        assertEquals(RowItem.Film(rotated[0]), row.focused)
        row.show(PickerView.Showing(night, refreshing = false))
        assertEquals(RowItem.Film(rotated[0]), row.focused)
        assertEquals(1, row.focus)

        // onStart: the next list starts from its first card again; Home does the same.
        row.restart()
        row.show(PickerView.Showing(night.copy(films = rotated), refreshing = false))
        assertEquals(0, row.focus)
        row.next()
        row.home()
        row.show(PickerView.Showing(night, refreshing = false))
        assertEquals(0, row.focus)
        assertEquals(RowItem.Film(films[0]), row.focused)
    }
}
