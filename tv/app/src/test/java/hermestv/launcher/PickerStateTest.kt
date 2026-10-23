package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.util.concurrent.Executor

class PickerStateTest {
    private val night = Picks.parse(javaClass.classLoader!!.getResource("picks_night.json")!!.readText())
    private val newer = night.copy(updatedAt = "2026-10-20T19:30:00+08:00", films = night.films.reversed())
    private val pending = Picks("night", "Tonight: horror", emptyList(), null, refreshing = true)

    private class FakeTimer : Timer {
        val tasks = ArrayList<Pair<Long, Runnable>>()
        override fun after(ms: Long, task: Runnable): Cancel {
            val entry = ms to task
            tasks += entry
            return Cancel { tasks.remove(entry) }
        }

        fun fire() {
            val (_, task) = tasks.removeAt(0)
            task.run()
        }
    }

    private val timer = FakeTimer()
    private val views = ArrayList<PickerView>()
    private val saved = ArrayList<Picks>()
    private var calls = 0

    private fun state(local: Picks?, io: Executor = Executor { it.run() }, clearOnEmpty: Boolean = false, vararg replies: () -> Picks) =
        PickerState("night", local, fetch = { replies[minOf(calls++, replies.size - 1)]() }, save = { saved += it },
            timer = timer, io = io, clearOnEmpty = clearOnEmpty, show = { views += it })

    @Test
    fun otherSlotNeverShownOrSaved() {
        // 19:00 on the TV, still dinner on the bridge: the dinner list is not taken for the night.
        val dinner = night.copy(slot = "dinner", label = "Dinner together")
        state(null, replies = arrayOf({ dinner }, { night })).start()
        assertEquals(listOf(PickerView.Finding, PickerView.Finding), views)
        assertTrue(saved.isEmpty())
        timer.fire()
        assertEquals(PickerView.Showing(night, refreshing = false), views.last())
        assertEquals(listOf(night), saved)
        assertTrue(timer.tasks.isEmpty())
    }

    @Test
    fun otherSlotKeepsSavedListAndStopsAsking() {
        val dinner = night.copy(slot = "dinner", films = night.films.reversed())
        state(night, replies = arrayOf({ dinner })).start()
        repeat(PickerState.MAX_POLLS) { timer.fire() }
        // The saved night list stays; after the last poll it no longer says it refreshes.
        assertTrue(views.all { it is PickerView.Showing && it.picks == night })
        assertEquals(PickerView.Showing(night, refreshing = false), views.last())
        assertTrue(saved.isEmpty())
        assertTrue(timer.tasks.isEmpty())
        assertEquals(PickerState.MAX_POLLS + 1, calls)
    }

    @Test
    fun emptyCacheShowsFinding() {
        state(null, replies = arrayOf({ pending }, { night })).start()
        assertEquals(listOf(PickerView.Finding, PickerView.Finding), views)
        assertEquals(8_000L, timer.tasks.single().first)
        timer.fire()
        assertEquals(PickerView.Showing(night, refreshing = false), views.last())
        assertEquals(listOf(night), saved)
        assertTrue(timer.tasks.isEmpty())
    }

    @Test
    fun localListShownFirst() {
        val held = ArrayList<Runnable>()
        state(night, io = Executor { held += it }, replies = arrayOf({ newer })).start()
        // Before the bridge answers, the saved list is on screen with the refresh note.
        assertEquals(listOf(PickerView.Showing(night, refreshing = true)), views)
        assertEquals(0, calls)
        held.single().run()
        assertEquals(PickerView.Showing(newer, refreshing = false), views.last())
    }

    @Test
    fun swapsWhenUpdatedAtChanges() {
        val same = night.copy(refreshing = true)
        state(night, replies = arrayOf({ same }, { same }, { newer })).start()
        assertEquals(PickerView.Showing(night, refreshing = true), views.last())
        timer.fire()
        assertEquals(PickerView.Showing(night, refreshing = true), views.last())
        timer.fire()
        assertEquals(PickerView.Showing(newer, refreshing = false), views.last())
        assertEquals(newer, saved.last())
        assertTrue(timer.tasks.isEmpty())
    }

    @Test
    fun offlineKeepsLocalList() {
        state(night, replies = arrayOf({ throw IOException("down") })).start()
        assertEquals(PickerView.Showing(night, refreshing = false), views.last())
        assertTrue(saved.isEmpty())

        views.clear()
        state(null, replies = arrayOf({ throw IOException("down") })).start()
        assertEquals(PickerView.Offline("Hermes is offline"), views.last())

        views.clear()
        state(null, replies = arrayOf({ throw BridgeUnavailable(TailnetRoute.TAILSCALE_OFFLINE) })).start()
        assertEquals(PickerView.Offline(TailnetRoute.TAILSCALE_OFFLINE), views.last())
    }

    @Test
    fun stopsPollingAfter12() {
        state(null, replies = arrayOf({ pending })).start()
        repeat(12) { timer.fire() }
        assertEquals(13, calls)
        assertTrue(timer.tasks.isEmpty())
        // The bridge never finished: no endless "Finding films…".
        assertEquals(PickerView.NoFilms, views.last())

        views.clear()
        calls = 0
        state(night, replies = arrayOf({ night.copy(refreshing = true) })).start()
        repeat(12) { timer.fire() }
        assertEquals(13, calls)
        assertTrue(timer.tasks.isEmpty())
        assertEquals(PickerView.Showing(night, refreshing = false), views.last())
    }

    @Test
    fun stopDropsLateReplies() {
        val held = ArrayList<Runnable>()
        val s = state(night, io = Executor { held += it }, replies = arrayOf({ newer.copy(refreshing = true) }))
        s.start()
        s.stop()
        held.single().run()
        assertEquals(listOf(PickerView.Showing(night, refreshing = true)), views)
        assertTrue(timer.tasks.isEmpty())
    }

    @Test
    fun filteredListWithSameUpdatedAtIsShown() {
        // The bridge drops a hidden or opened film from its list at once, without a new updated_at.
        val filtered = night.copy(films = night.films.drop(1))
        state(night, replies = arrayOf({ filtered })).start()
        assertEquals(PickerView.Showing(filtered, refreshing = false), views.last())
        assertEquals(listOf(filtered), saved)
    }

    @Test
    fun emptyAnswerClearsOnlyWhenAsked() {
        val empty = night.copy(films = emptyList())
        // Films: an empty answer is a refresh that found nothing; the saved list stays.
        state(night, replies = arrayOf({ empty })).start()
        assertEquals(PickerView.Showing(night, refreshing = false), views.last())
        assertTrue(saved.isEmpty())

        // From Hermes: an empty answer means Hermes asks for nothing now; the list goes.
        views.clear()
        calls = 0
        state(night, clearOnEmpty = true, replies = arrayOf({ empty })).start()
        assertEquals(PickerView.Showing(empty, refreshing = false), views.last())
        assertEquals(listOf(empty), saved)
    }

    @Test
    fun dropRemovesAndSaves() {
        val s = state(night, replies = arrayOf({ night }))
        s.start()
        assertEquals(emptyList<Picks>(), saved)
        s.drop(night.films[0])
        assertEquals(listOf(night.copy(films = night.films.drop(1))), saved)
    }

    @Test
    fun focusFollowsTheSameFilm() {
        val films = night.films
        assertEquals(2, PickerState.keepFocus(films, 1, films.reversed()))
        assertEquals(0, PickerState.keepFocus(films, 1, films.drop(2)))
        assertEquals(0, PickerState.keepFocus(emptyList(), 0, films))
    }

    // Every Netflix item has the same url (the app home), so the url does not name a film.
    @Test
    fun focusFollowsTheSameFilmOfOneService() {
        val films = night.films
        assertEquals(listOf("netflix", "hbomax", "netflix", "netflix"), films.map { it.service })
        assertEquals(3, PickerState.keepFocus(films, 0, films.reversed()))
        assertEquals(1, PickerState.keepFocus(films, 2, films.reversed()))
        // Re-titled by the bridge: case and punctuation do not matter, the year does.
        val retitled = films.reversed().map { if (it.title == "The Lantern Keeper") it.copy(title = "THE LANTERN-KEEPER!") else it }
        assertEquals(3, PickerState.keepFocus(films, 0, retitled))
        assertEquals(0, PickerState.keepFocus(films, 0, retitled.map { it.copy(year = 1999) }))
    }
}
