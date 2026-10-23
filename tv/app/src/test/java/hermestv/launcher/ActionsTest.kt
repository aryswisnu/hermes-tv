package hermestv.launcher

import android.content.Intent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ActionsTest {
    private fun url(u: String) = Actions.plan(Action("open_url", url = u))

    @Test
    fun rejectsOtherHosts() {
        listOf(
            "https://evil.com/title/1",
            "https://netflix.com.evil.com/title/1",
            "https://notnetflix.com/title/1",
            "https://notmax.com/movie/1",
            "intent://www.netflix.com/title/1#Intent;end",
            "javascript:alert(1)",
            "file:///sdcard/x",
            "ftp://www.netflix.com/title/1",
            "not a url",
        ).forEach { assertNull(it, url(it)) }
        assertNull(Actions.plan(Action("open_url", url = null)))
        assertNull(Actions.plan(Action("install_app", url = "https://www.netflix.com/title/1")))
    }

    // Only a film, series or video URL may open: a bad YouTube id or any other action from the bridge is refused.
    @Test
    fun rejectsOtherActions() {
        listOf(
            "https://www.youtube.com/watch?v=x",
            "https://youtube.com/watch?v=x",
            "https://youtu.be/x",
        ).forEach { assertNull(it, url(it)) }
        assertNull(Actions.plan(Action("youtube_search", url = "https://www.youtube.com/results?search_query=x")))
        assertNull(Actions.plan(Action("open_app", url = "https://www.netflix.com/title/1")))
    }

    @Test
    fun netflixUrlTargetsNinja() {
        listOf(
            "http://www.netflix.com/title/81704541",
            "https://netflix.com/title/1",
        ).forEach { assertEquals(it, Launch(it, "com.netflix.ninja"), url(it)) }
    }

    @Test
    fun hboUrlTargetsWbd() {
        listOf(
            "https://play.hbomax.com/movie/0a1b6b28",
            "https://hbomax.com/movie/1",
            "https://play.max.com/movie/1",
            "https://max.com/movie/1",
        ).forEach { assertEquals(it, Launch(it, "com.wbd.stream"), url(it)) }
    }

    @Test
    fun youtubeVideoStartsAFreshTask() {
        // A running YouTube app only comes to the front on NEW_TASK and keeps its old video.
        val video = url("https://www.youtube.com/watch?v=vid00000001")!!
        assertEquals(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK, Actions.flags(video))
        assertEquals(Intent.FLAG_ACTIVITY_NEW_TASK, Actions.flags(url("https://www.netflix.com/title/1")!!))
        assertEquals(Intent.FLAG_ACTIVITY_NEW_TASK, Actions.flags(Launch(null, Actions.YOUTUBE)))
    }

    @Test
    fun youtubeWatchUrlTargetsYoutubeTv() {
        listOf(
            "https://www.youtube.com/watch?v=vid00000001",
            "https://www.youtube.com/watch?v=a_b-C1d2E3f",
            "https://youtu.be/vid00000001",
        ).forEach { assertEquals(it, Launch(it, "com.google.android.youtube.tv"), url(it)) }
    }

    @Test
    fun rejectsYoutubeLookAlikes() {
        listOf(
            "https://www.youtube.com/watch?v=vid0000000",
            "https://www.youtube.com/watch?v=vid00000001x",
            "https://www.youtube.com/watch?v=vid0000000!",
            "https://www.youtube.com/watch?v=vid00000001&list=PL1",
            "https://www.youtube.com/watch?list=PL1&v=vid00000001",
            "https://www.youtube.com/watch?v=vid00000001#t=1",
            "https://www.youtube.com/watch?v=vid00000001\n",
            "http://www.youtube.com/watch?v=vid00000001",
            "https://youtube.com/watch?v=vid00000001",
            "https://m.youtube.com/watch?v=vid00000001",
            "https://music.youtube.com/watch?v=vid00000001",
            "https://www.youtube.com.evil.com/watch?v=vid00000001",
            "https://www.youtube.com@evil.com/watch?v=vid00000001",
            "https://evil.com/https://www.youtube.com/watch?v=vid00000001",
            "https://www.youtube.com/shorts/vid00000001",
            "https://www.youtube.com/embed/vid00000001",
            "https://youtu.be/vid00000001?t=10",
            "https://youtu.be.evil.com/vid00000001",
            "https://www.youtube-nocookie.com/watch?v=vid00000001",
            "vnd.youtube:vid00000001",
        ).forEach { assertNull(it, url(it)) }
    }

    // Each kind of URL opens in its own app only; nothing targets the phone YouTube app or another package.
    @Test
    fun otherPackagesNeverTargeted() {
        assertEquals("com.netflix.ninja", url("https://www.netflix.com/title/1")?.pkg)
        assertEquals("com.wbd.stream", url("https://play.max.com/movie/1")?.pkg)
        assertEquals("com.google.android.youtube.tv", url("https://youtu.be/vid00000001")?.pkg)
        assertNull(url("https://www.netflix.com.youtube.com/watch?v=vid00000001"))
        assertNull(url("https://www.youtube.com/watch?v=vid00000001&pkg=com.google.android.youtube"))
    }

    private val film = Film("The Lantern Keeper", 2025, 112, 7.5, "netflix", "https://www.netflix.com/", null, null, emptyList(), "")

    @Test
    fun netflixFilmLaunchesNetflixApp() {
        assertEquals(Launch(null, "com.netflix.ninja"), Actions.forItem(film))
    }

    @Test
    fun hboMaxSeriesLaunchesApp() {
        val show = film.copy(title = "The Hollow Line", service = "hbomax", url = "https://www.max.com/", kind = "series", seasons = 3)
        assertEquals(Launch(null, "com.wbd.stream"), Actions.forItem(show))
        // A link saved by an older bridge opens the app too, not the old title page.
        assertEquals(Launch(null, "com.wbd.stream"), Actions.forItem(show.copy(url = "https://play.hbomax.com/show/a1b2")))
    }

    @Test
    fun youtubeWatchUrlStillOpensExactVideo() {
        val video = film.copy(
            service = "youtube", kind = "video", url = "https://www.youtube.com/watch?v=vid00000001", videoId = "vid00000001",
        )
        assertEquals(Launch(video.url, "com.google.android.youtube.tv"), Actions.forItem(video))
        assertNull(Actions.forItem(video.copy(url = "https://www.youtube.com/watch?v=vid00000001&list=PL1")))
    }

    @Test
    fun foreignHostRejected() {
        assertNull(Actions.forItem(film.copy(url = "https://evil.com/")))
        assertNull(Actions.forItem(film.copy(url = "https://www.netflix.com.evil.com/")))
        assertNull(Actions.forItem(film.copy(service = "hbomax", kind = "series", url = "intent://www.max.com/#Intent;end")))
    }
}
