package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

class PicksParseTest {
    private val night = Picks.parse(javaClass.classLoader!!.getResource("picks_night.json")!!.readText())

    @Test
    fun parsesBridgeFixture() {
        assertEquals("night", night.slot)
        assertEquals("Tonight: horror", night.label)
        assertEquals("2026-10-20T19:04:12+08:00", night.updatedAt)
        assertFalse(night.refreshing)
        assertEquals(listOf("The Lantern Keeper", "The Night Orchard", "Midnight Ferry", "Paper Lighthouse"), night.films.map { it.title })
        val orchard = night.films[1]
        assertEquals(2025, orchard.year)
        assertEquals(129, orchard.runtimeMin)
        assertEquals(7.4, orchard.rating!!, 0.0)
        assertEquals("film", orchard.kind)
        assertEquals("films", night.row)
        assertEquals("hbomax", orchard.service)
        assertEquals("https://image.tmdb.org/t/p/w1280/nightOrchardBackdrop02.jpg", orchard.backdrop)
        assertEquals(listOf("horror", "thriller"), orchard.genres)
    }

    @Test
    fun nullsStayNull() {
        val p = Picks.parse("""{"slot":"any","label":"Top rated for you","films":[],"updated_at":null,"refreshing":true}""")
        assertEquals(emptyList<Film>(), p.films)
        assertNull(p.updatedAt)
        val f = Picks.parse(
            """{"slot":"any","label":"x","updated_at":null,"refreshing":false,"films":[{"title":"A","year":2018,
            "runtime_min":null,"rating":7.5,"service":"netflix","url":"https://www.netflix.com/",
            "poster":null,"backdrop":null,"genres":[],"why":""}]}""",
        ).films.single()
        assertNull(f.runtimeMin)
        assertNull(f.poster)
        assertNull(f.backdrop)
    }

    // A From Hermes pick can be a TMDB title with no votes yet: it stays in the row and its line has no rating.
    @Test
    fun unratedHermesPickKeepsItsPlace() {
        val p = Picks.parse(
            """{"slot":"night","label":"From Hermes","row":"hermes","updated_at":"2026-10-20T20:01:00+08:00","refreshing":false,
            "films":[{"title":"Quiet Harbour","year":2026,"runtime_min":98,"rating":null,"service":"netflix",
            "url":"https://www.netflix.com/title/1","poster":null,"backdrop":null,"genres":["drama"],"why":"New and calm."},
            {"title":"Signal Lost","year":2026,"runtime_min":null,"rating":null,"service":"hbomax",
            "url":"https://play.max.com/show/1","poster":null,"backdrop":null,"genres":[],"why":"","kind":"series","seasons":1}]}""",
        )
        assertEquals(listOf("Quiet Harbour", "Signal Lost"), p.films.map { it.title })
        val (film, show) = p.films
        assertNull(film.rating)
        assertEquals("2026 · 1 h 38", Meta.line(film))
        assertEquals("2026 · 1 h 38 · Netflix", Meta.card(film))
        assertEquals("2026 · 1 season · HBO Max", Meta.card(show))
        assertEquals(p, Picks.parse(p.toJson()))
    }

    @Test
    fun reasonOnlyOnHermesCards() {
        val f = night.films[0].copy(why = " A slow forest chill for tonight ")
        assertEquals("A slow forest chill for tonight", Meta.reason(f, Line.HERMES))
        assertNull(Meta.reason(f.copy(why = "  "), Line.HERMES))
        // The other rows keep their two lines.
        assertNull(Meta.reason(f, Line.FILMS))
        assertNull(Meta.reason(f, Line.SERIES))
    }

    @Test
    fun localCopyRoundTrips() {
        assertEquals(night, Picks.parse(night.toJson()))
    }

    @Test
    fun metaLine() {
        assertEquals("2025 · 2 h 17 · TMDB 7.5", Meta.line(night.films[0]))
        assertEquals("2025 · 2 h 09 · TMDB 7.4", Meta.line(night.films[1]))
        assertEquals("2024 · 2 h 13 · TMDB 7.1", Meta.line(night.films[2]))
        assertEquals("2018 · 1 h 30 · TMDB 7.0", Meta.line(night.films[0].copy(year = 2018, runtimeMin = 90, rating = 7.0)))
        assertEquals("2018 · TMDB 7.5", Meta.line(night.films[0].copy(year = 2018, runtimeMin = null)))
        assertEquals("Netflix", Meta.service("netflix"))
        assertEquals("HBO Max", Meta.service("hbomax"))
    }

    @Test
    fun slotBoundaries() {
        fun at(h: Int, m: Int) = Slot.at(ZonedDateTime.of(2026, 10, 20, h, m, 0, 0, ZoneId.of("Asia/Tokyo")))
        mapOf(
            (5 to 59) to "late", (6 to 0) to "morning", (10 to 59) to "morning", (11 to 0) to "lunch",
            (13 to 59) to "lunch", (14 to 0) to "afternoon", (16 to 59) to "afternoon", (17 to 0) to "dinner",
            (18 to 59) to "dinner", (19 to 0) to "night", (0 to 0) to "night", (3 to 59) to "night", (4 to 0) to "late",
        ).forEach { (t, slot) -> assertEquals("$t", slot, at(t.first, t.second)) }
        // The slot follows the zone of the time it is given: 09:00 in Tokyo is 20:00 the day before in New York.
        val tokyo = ZonedDateTime.of(2026, 10, 20, 9, 0, 0, 0, ZoneId.of("Asia/Tokyo"))
        assertEquals("morning", Slot.at(tokyo))
        assertEquals("night", Slot.at(tokyo.withZoneSameInstant(ZoneId.of("America/New_York"))))
    }

    @Test
    fun seriesAndVideoItems() {
        val p = Picks.parse(
            """{"slot":"lunch","label":"Lunch together","row":"youtube","updated_at":"2026-10-20T12:15:03+08:00","refreshing":false,
            "films":[{"title":"Kitchen Table Stories","year":2014,"runtime_min":25,"rating":8.9,"service":"netflix",
            "url":"https://www.netflix.com/","poster":null,"backdrop":"https://image.tmdb.org/t/p/w1280/kitchenTableBackdrop02.jpg",
            "genres":["comedy"],"why":"","seasons":2,"kind":"series","channel":null,"duration_min":null,"video_id":null},
            {"title":"THE CASE THAT WAS SOLVED","year":null,"runtime_min":null,"rating":null,"service":"youtube",
            "url":"https://www.youtube.com/watch?v=vid00000001","poster":"https://i.ytimg.com/vi/vid00000001/mqdefault.jpg",
            "backdrop":"https://i.ytimg.com/vi/vid00000001/mqdefault.jpg","genres":[],"why":"An old case.",
            "seasons":null,"kind":"video","channel":"Night Files","duration_min":30,"video_id":"vid00000001"}]}""",
        )
        assertEquals("youtube", p.row)
        val (show, video) = p.films
        assertEquals("series", show.kind)
        assertEquals(2, show.seasons)
        assertEquals("video", video.kind)
        assertNull(video.year)
        assertNull(video.rating)
        assertEquals("Night Files", video.channel)
        assertEquals(30, video.durationMin)
        assertEquals("vid00000001", video.videoId)
        assertEquals("2014 · 2 seasons · TMDB 8.9 · Netflix", Meta.card(show))
        assertEquals("2014 · 1 season · TMDB 8.9 · Netflix", Meta.card(show.copy(seasons = 1)))
        assertEquals("Night Files · 30 min", Meta.card(video))
        assertEquals("Night Files", Meta.card(video.copy(durationMin = null)))
        assertEquals("2025 · 2 h 09 · TMDB 7.4 · HBO Max", Meta.card(night.films[1]))
        assertEquals(p, Picks.parse(p.toJson()))
        // An old saved list without a row is the films row.
        assertEquals("films", Picks.parse("""{"slot":"night","label":"x","films":[],"updated_at":null}""").row)
    }
}
