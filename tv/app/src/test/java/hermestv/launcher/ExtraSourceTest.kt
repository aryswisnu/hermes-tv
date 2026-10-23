package hermestv.launcher

import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ExtraSourceTest {
    private val built = Extra.source
    private val demo = ExtraSource("demo.app", "demo", "Demo")

    @Before
    fun on() {
        Extra.source = demo
    }

    @After
    fun restore() {
        Extra.source = built
    }

    private fun url(u: String) = Actions.plan(Action("open_url", url = u))

    @Test
    fun extraLinkOpensPackageWithClearTask() {
        listOf("demo://title/movie/amelie-2001", "demo://title/series/chernobyl-2019", "demo://title/movie/a").forEach {
            assertEquals(it, Launch(it, "demo.app"), url(it))
        }
        // A running app keeps its old screen on NEW_TASK alone.
        assertEquals(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK,
            Actions.flags(Launch("demo://title/movie/a", "demo.app")))
    }

    @Test
    fun extraRejectsLookAlikes() {
        listOf(
            "demo://title/movie/a?x=1",
            "demo://title/movie/a#b",
            "demo://other/movie/a",
            "demo://title/episode/a",
            "demo://title/movie/A",
            "demo://title/movie/-a",
            "demo://title/movie/a/b",
            "demo://title/movie/",
            "demo://title/movie/" + "a".repeat(121),
            "DEMO://title/movie/a",
            "intent://title/movie/a",
            "other://title/movie/a",
            "https://title/movie/a",
            " demo://title/movie/a",
        ).forEach { assertNull(it, url(it)) }
    }

    @Test
    fun extraOffRejectsEverything() {
        listOf(
            ExtraSource("", "demo", "Demo"),
            ExtraSource("demo.app", "", "Demo"),
            ExtraSource("demo.app", "demo", ""),
            ExtraSource("demo.app", "https", "Demo"),
            ExtraSource("demo.app", "intent", "Demo"),
            ExtraSource("demo.app", "Demo", "Demo"),
            ExtraSource("demo.app", "d", "Demo"),
        ).forEach {
            Extra.source = it
            assertFalse(it.toString(), it.on)
            assertNull(it.toString(), url("${it.scheme}://title/movie/a"))
        }
        Extra.source = ExtraSource("", "", "")
        assertEquals(Intent.FLAG_ACTIVITY_NEW_TASK, Actions.flags(Launch("x://title/movie/a", "")))
    }

    @Test
    fun existingHostsUnchanged() {
        assertEquals("com.netflix.ninja", url("https://www.netflix.com/title/1")?.pkg)
        assertEquals("com.wbd.stream", url("https://play.max.com/movie/1")?.pkg)
        assertEquals("com.google.android.youtube.tv", url("https://youtu.be/JCuycfS8KuM")?.pkg)
        assertEquals(Intent.FLAG_ACTIVITY_NEW_TASK, Actions.flags(Launch("https://www.netflix.com/title/1", Actions.NETFLIX)))
    }

    private fun picks(vararg services: String): String = JSONObject()
        .put("slot", "night").put("label", "From Hermes").put("row", "hermes").put("updated_at", JSONObject.NULL)
        .put("films", JSONArray(services.map { s ->
            JSONObject().put("title", s).put("year", 2001).put("runtime_min", JSONObject.NULL).put("rating", JSONObject.NULL)
                .put("service", s).put("url", if (s == "extra") "demo://title/movie/a" else "https://www.netflix.com/title/1")
                .put("poster", JSONObject.NULL).put("backdrop", JSONObject.NULL).put("genres", JSONArray()).put("why", "")
        }))
        .toString()

    @Test
    fun extraCardDroppedWhenOff() {
        assertEquals(listOf("netflix", "extra"), Picks.parse(picks("netflix", "extra")).films.map { it.service })
        Extra.source = ExtraSource("", "", "")
        assertEquals(listOf("netflix"), Picks.parse(picks("netflix", "extra")).films.map { it.service })
    }

    @Test
    fun extraServiceLabelIsName() {
        assertEquals("Demo", Meta.service("extra"))
        assertEquals("Netflix", Meta.service("netflix"))
        assertTrue(demo.on)
    }

    @Test
    fun extraItemKeepsItsLink() {
        // Netflix and HBO Max cards open the app (TMDB has no title link); the extra source opens the title itself.
        val film = Film("Amélie", 2001, null, null, "extra", "demo://title/movie/amelie-2001", null, null, emptyList(), "")
        assertEquals(Launch(film.url, "demo.app"), Actions.forItem(film))
        assertEquals(Launch(null, Actions.NETFLIX), Actions.forItem(film.copy(service = "netflix", url = "https://www.netflix.com/")))
        // A wrong setting that names the Netflix app must not turn Netflix cards into links.
        Extra.source = ExtraSource(Actions.NETFLIX, "demo", "Demo")
        assertEquals(Launch(null, Actions.NETFLIX), Actions.forItem(film.copy(service = "netflix", url = "https://www.netflix.com/")))
    }
}
