package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DockAppsTest {
    private fun app(pkg: String, label: String) = AppEntry(pkg, label, "$pkg.Main")
    private val netflix = app("com.netflix.ninja", "Netflix")
    private val youtube = app("com.google.android.youtube.tv", "YouTube")
    private val hbo = app("com.wbd.stream", "HBO Max")
    private val video = app("com.example.video", "Video")
    private val audio = app("com.example.music", "Music")
    private val game = app("com.example.game", "Games")
    private val music = app("com.google.android.youtube.tvmusic", "YouTube Music")

    private fun pkgs(apps: List<AppEntry>) = apps.map { it.pkg }

    @Test
    fun pinnedFirst() {
        val got = DockApps.order(listOf(video, hbo, game, youtube, netflix), mapOf(video.pkg to 50, game.pkg to 9))
        assertEquals(listOf(netflix, youtube, hbo, video, game), got)
        // A pinned app that is not installed leaves no gap.
        assertEquals(listOf(netflix, hbo, video), DockApps.order(listOf(video, hbo, netflix), emptyMap()))
    }

    @Test
    fun hiddenNeverShown() {
        val hidden = listOf(
            "hermestv.launcher", "me.efesser.flauncher", "com.android.tv.settings", "com.tcl.suspension", "com.tcl.tv",
            "com.tcl.dashboard", "com.tcl.guard", "com.tcl.partnercustomizer", "com.tcl.magiconnectfree",
        )
        assertEquals(hidden.toSet(), DockApps.HIDDEN)
        val apps = hidden.map { app(it, it) } + netflix + app("com.tcl.ui_mediaCenter", "Media Center")
        val counts = hidden.associateWith { 99 }
        assertEquals(listOf("com.netflix.ninja", "com.tcl.ui_mediaCenter"), pkgs(DockApps.order(apps, counts)))
    }

    @Test
    fun countsOrderRest() {
        val got = DockApps.order(
            listOf(music, game, audio, video, netflix),
            mapOf(audio.pkg to 3, game.pkg to 7, netflix.pkg to 1),
        )
        // Never-opened apps keep alphabetical order by label; a second activity of one app shows once.
        assertEquals(listOf(netflix, game, audio, video, music), got)
        assertEquals(listOf(game, video), DockApps.order(listOf(video, game, video.copy(activity = "x.Other")), emptyMap()))
        val apple = app("com.apple.atve.androidtv.appletv", "apple TV")
        val bravo = app("com.bravo.tv", "Bravo")
        assertEquals(listOf(apple, bravo), DockApps.order(listOf(bravo, apple), emptyMap()))
    }

    @Test
    fun removedAppDisappears() {
        val counts = mapOf(video.pkg to 40, game.pkg to 2)
        assertEquals(listOf(netflix, video, game), DockApps.order(listOf(netflix, game, video), counts))
        assertEquals(listOf(netflix, game), DockApps.order(listOf(netflix, game), counts))
    }

    @Test
    fun hiddenByUser() {
        val all = listOf(netflix, youtube, hbo, video, game)
        val counts = mapOf(game.pkg to 5)
        // A pinned app can be hidden too.
        assertEquals(listOf(netflix, hbo, video), DockApps.order(all, counts, hidden = setOf(youtube.pkg, game.pkg)))
        // Shown again in its usual place once unhidden.
        assertEquals(listOf(netflix, youtube, hbo, game, video), DockApps.order(all, counts, hidden = emptySet()))
        // The user's list cannot bring back an app the TV always hides.
        assertEquals(listOf(netflix), DockApps.order(listOf(netflix, app("com.tcl.tv", "TV")), emptyMap(), hidden = emptySet()))
    }

    @Test
    fun defaultHiddenSeededOnce() {
        val store = app("com.android.vending", "Google Play Store")
        val tailscale = app("com.tailscale.ipn", "Tailscale")
        assertEquals(setOf(store.pkg, tailscale.pkg), DockApps.DEFAULT_HIDDEN)
        val all = listOf(netflix, store, tailscale, video, music)
        // First start: the two join what the user hid already, leave the dock, and the flag is set.
        val first = DockApps.seed(DockApps.Hidden(setOf(video.pkg), seeded = false))
        assertEquals(DockApps.Hidden(setOf(video.pkg, store.pkg, tailscale.pkg), seeded = true), first)
        assertEquals(listOf(netflix, music), DockApps.order(all, emptyMap(), first.apps))
        // The user shows the Play Store again; the next start, from what the first one stored, changes nothing.
        val shown = first.copy(apps = first.apps - store.pkg)
        val later = DockApps.seed(shown)
        assertEquals(shown, later)
        assertEquals(listOf(netflix, store, music), DockApps.order(all, emptyMap(), later.apps))
    }

    @Test
    fun logoForKnownPackages() {
        assertEquals(R.drawable.logo_netflix, DockApps.logo(netflix.pkg))
        assertEquals(R.drawable.logo_youtube, DockApps.logo(youtube.pkg))
        assertEquals(R.drawable.logo_hbomax, DockApps.logo(hbo.pkg))
        assertEquals(R.drawable.logo_youtubemusic, DockApps.logo(music.pkg))
        // Any other app shows its name.
        assertNull(DockApps.logo(video.pkg))
        assertNull(DockApps.logo(audio.pkg))
    }

    @Test
    fun logoOnlyNoName() {
        // Netflix, YouTube, HBO Max, YouTube Music, Inputs and Settings show their logo or glyph alone.
        val logos = listOf(netflix, youtube, hbo, music).map { Tile(it.label, DockApps.logo(it.pkg)) } +
            Tile("Inputs", R.drawable.glyph_inputs) + Tile("Settings", R.drawable.glyph_settings)
        logos.forEach { assertNull(it.label, DockApps.word(it)) }
        // Apps without a logo show their name as the word.
        assertEquals("Video", DockApps.word(Tile(video.label, DockApps.logo(video.pkg))))
        assertEquals("Music", DockApps.word(Tile(audio.label, DockApps.logo(audio.pkg))))
        // The thin "max" wordmark is drawn bigger than the square logos.
        assertEquals(48, DockApps.logoHeight(R.drawable.logo_hbomax))
        assertEquals(36, DockApps.logoHeight(R.drawable.logo_netflix))
        assertEquals(36, DockApps.logoHeight(R.drawable.glyph_settings))
    }

    @Test
    fun stepInEmptyDock() {
        // No tile yet (before the first load): ◀ ▶ keep the focus and do not throw.
        assertNull(DockApps.step(0, 1, 0))
        assertNull(DockApps.step(0, -1, 0))
        assertEquals(1, DockApps.step(0, 1, 5))
        assertEquals(3, DockApps.step(4, -1, 5))
        // The ends hold.
        assertNull(DockApps.step(4, 1, 5))
        assertNull(DockApps.step(0, -1, 5))
        // A failed app query still gives Inputs and Settings.
        assertEquals(listOf("Inputs", "Settings"), DockApps.tiles(emptyList()).map { it.label })
        assertEquals(listOf("Netflix", "Video", "Inputs", "Settings"), DockApps.tiles(listOf(netflix, video)).map { it.label })
    }

    @Test
    fun camerasTileFourth() {
        val cameras = DockApps.cameras(camera = true)!!
        assertEquals("Cameras", cameras.label)
        assertEquals(CamerasActivity::class.java.name, cameras.activity)
        // Pinned after HBO Max: an app opened more often, or one that sorts first by name, still comes after it.
        val apple = app("com.apple.atve.androidtv.appletv", "Apple TV")
        val got = DockApps.order(listOf(apple, video, cameras, hbo, youtube, netflix), mapOf(video.pkg to 50))
        assertEquals(listOf(netflix, youtube, hbo, cameras, video, apple), got)
        assertEquals(listOf(null, null, null, null, "Video"), DockApps.tiles(got).take(5).map(DockApps::word))
        assertEquals(R.drawable.glyph_cameras, DockApps.tiles(got)[3].logo)
        // It can be hidden like an app.
        assertEquals(listOf(netflix, youtube, hbo, video), DockApps.order(listOf(video, cameras, hbo, youtube, netflix), emptyMap(), setOf(cameras.pkg)))
        // A build without a camera URL has no Cameras tile.
        assertNull(DockApps.cameras(camera = false))
    }
}
