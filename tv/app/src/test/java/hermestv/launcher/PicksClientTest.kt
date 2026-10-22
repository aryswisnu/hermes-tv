package hermestv.launcher

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PicksClientTest {
    private val film = Film("Jumbo", 2025, 102, 7.7, "netflix", "https://www.netflix.com/title/1", null, null, emptyList(), "")
    private val video = Film(
        "The Lighthouse Mystery", null, null, null, "youtube", "https://www.youtube.com/watch?v=Myst0000001", null, null, emptyList(), "",
        kind = "video", channel = "Cold Case Hour", durationMin = 30, videoId = "Myst0000001",
    )

    private fun same(expected: String, got: JSONObject) = assertTrue("$got", JSONObject(expected).similar(got))

    @Test
    fun bearerOnlyForBridge() {
        val base = "https://tv-bridge.example.ts.net:8444"
        assertEquals("Bearer k", PicksClient.authFor("$base/v1/scenery/day/01-a.jpg", "k", base))
        assertEquals("Bearer k", PicksClient.authFor(base.replace("tv-bridge", "TV-BRIDGE") + "/v1/x", "k", base))
        for (url in listOf(
            "https://image.tmdb.org/t/p/w1280/x.jpg",
            "$base.evil.example/x.jpg",
            "https://evil.example:8444/x.jpg",
            base.replace("https://", "https://user@evil.example#") + "/x.jpg",
            base.replace(":8444", "") + "/x.jpg",
            base.replace(":8444", ":8445") + "/x.jpg",
            base.replace("https://", "http://") + "/x.jpg",
            "not a url",
        )) {
            assertNull(url, PicksClient.authFor(url, "k", base))
        }
    }

    @Test
    fun healthWithoutHermesIsNone() {
        assertEquals(HermesState.NONE, PicksClient.hermesState("""{"hermes":false,"enabled":false}"""))
        assertEquals(HermesState.ON, PicksClient.hermesState("""{"hermes":true,"enabled":true}"""))
        assertEquals(HermesState.OFF, PicksClient.hermesState("""{"hermes":false,"enabled":true}"""))
    }

    // A bridge from before the switch sends no "enabled": it always runs with Hermes.
    @Test
    fun healthMissingEnabledIsOnOrOff() {
        assertEquals(HermesState.ON, PicksClient.hermesState("""{"hermes":true}"""))
        assertEquals(HermesState.OFF, PicksClient.hermesState("""{"hermes":false}"""))
    }

    // The bridge answers 422 to video_id or channel on a film or series.
    @Test
    fun openedAndHideBodies() {
        same("""{"kind":"film","title":"Jumbo","year":2025,"url":"https://www.netflix.com/title/1"}""", PicksClient.openedBody(film))
        same(
            """{"kind":"series","title":"Jumbo","year":2025,"url":"https://www.netflix.com/title/1"}""",
            PicksClient.openedBody(film.copy(kind = "series", seasons = 2)),
        )
        same(
            """{"kind":"video","title":"The Lighthouse Mystery","video_id":"Myst0000001","url":"https://www.youtube.com/watch?v=Myst0000001"}""",
            PicksClient.openedBody(video),
        )
        same("""{"kind":"film","title":"Jumbo","year":2025}""", PicksClient.hideBody(film, channel = false))
        same("""{"kind":"video","title":"The Lighthouse Mystery","video_id":"Myst0000001"}""", PicksClient.hideBody(video, channel = false))
        same(
            """{"kind":"video","title":"The Lighthouse Mystery","video_id":"Myst0000001","channel":"Cold Case Hour"}""",
            PicksClient.hideBody(video, channel = true),
        )
    }
}
