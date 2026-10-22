package hermestv.launcher

import android.content.Context
import android.content.Intent

/** One TV app in the dock; activity is the LEANBACK_LAUNCHER activity class. */
data class AppEntry(val pkg: String, val label: String, val activity: String)

object DockApps {
    /** The Cameras tile's key: it is listed, ordered and hidden like an app, and opens [CamerasActivity]. */
    const val CAMERAS = "hermestv.launcher.cameras"
    val PINNED = listOf("com.netflix.ninja", "com.google.android.youtube.tv", "com.wbd.stream", CAMERAS)
    val HIDDEN = setOf(
        "hermestv.launcher", "me.efesser.flauncher", "com.android.tv.settings", "com.tcl.suspension", "com.tcl.tv",
        "com.tcl.dashboard", "com.tcl.guard", "com.tcl.partnercustomizer", "com.tcl.magiconnectfree",
    )
    /** Hidden on the first start as if the user hid them, so "Show hidden apps" brings them back. */
    val DEFAULT_HIDDEN = setOf("com.android.vending", "com.tailscale.ipn")
    private val LOGOS = mapOf(
        "com.netflix.ninja" to R.drawable.logo_netflix,
        "com.google.android.youtube.tv" to R.drawable.logo_youtube,
        "com.wbd.stream" to R.drawable.logo_hbomax,
        "com.google.android.youtube.tvmusic" to R.drawable.logo_youtubemusic,
        CAMERAS to R.drawable.glyph_cameras,
    )

    /** The Cameras tile as an app entry; none in a build without a [camera] URL. */
    fun cameras(camera: Boolean): AppEntry? = if (camera) AppEntry(CAMERAS, "Cameras", CamerasActivity::class.java.name) else null

    /** The app's white logo for its dock tile; null shows the name alone. */
    fun logo(pkg: String): Int? = LOGOS[pkg]

    /** The name a dock tile shows: none when it has a logo, which stands alone. */
    fun word(tile: Tile): String? = if (tile.logo == null) tile.label else null

    /** A logo's height on its tile in dp; the stacked "HBO max" mark is wider than tall, so it gets more. */
    fun logoHeight(logo: Int): Int = if (logo == R.drawable.logo_hbomax) 48 else 36

    /** The dock's tiles: the [shown] apps, then Inputs and Settings, which are there even when the app query fails. */
    fun tiles(shown: List<AppEntry>): List<Tile> =
        shown.map { Tile(it.label, logo(it.pkg)) } + Tile("Inputs", R.drawable.glyph_inputs) + Tile("Settings", R.drawable.glyph_settings)

    /** The tile ◀ ▶ ([step] -1 or 1) moves to from [index] in a dock of [size] tiles; null when the focus stays, also with no tile. */
    fun step(index: Int, step: Int, size: Int): Int? {
        if (size == 0) return null
        return (index + step).coerceIn(0, size - 1).takeIf { it != index }
    }

    /** The user-hidden list and the seeded flag as stored. */
    data class Hidden(val apps: Set<String>, val seeded: Boolean)

    /** What to store after a start: [DEFAULT_HIDDEN] joins the list once, then the flag stops it. */
    fun seed(stored: Hidden): Hidden = if (stored.seeded) stored else Hidden(stored.apps + DEFAULT_HIDDEN, true)

    fun order(apps: List<AppEntry>, counts: Map<String, Int>, hidden: Set<String> = emptySet()): List<AppEntry> {
        val shown = apps.filter { it.pkg !in HIDDEN && it.pkg !in hidden }.distinctBy { it.pkg }
        val (pinned, rest) = shown.partition { it.pkg in PINNED }
        return pinned.sortedBy { PINNED.indexOf(it.pkg) } +
            rest.sortedWith(compareByDescending<AppEntry> { counts[it.pkg] ?: 0 }.thenBy(String.CASE_INSENSITIVE_ORDER) { it.label })
    }
}

/** The dock's apps, and the ones the user hid from it, from one PackageManager query. */
class Dock(val shown: List<AppEntry>, val hidden: List<AppEntry>)

/** Reads the TV apps from PackageManager, counts launches from the dock and keeps the apps hidden from it. */
class DockAppsSource(private val context: Context) {
    private val prefs = context.getSharedPreferences("dock", Context.MODE_PRIVATE)
    private val hiddenPrefs = context.getSharedPreferences("dock_hidden", Context.MODE_PRIVATE)

    fun apps(): Dock {
        val pm = context.packageManager
        val query = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LEANBACK_LAUNCHER)
        val apps = pm.queryIntentActivities(query, 0).map {
            AppEntry(it.activityInfo.packageName, it.loadLabel(pm).toString(), it.activityInfo.name)
        } + listOfNotNull(DockApps.cameras(Cameras.ALL.isNotEmpty()))
        val hidden = seed()
        val shown = DockApps.order(apps, prefs.all.mapNotNull { (k, v) -> (v as? Int)?.let { k to it } }.toMap(), hidden)
        val gone = apps.filter { it.pkg in hidden && it.pkg !in DockApps.HIDDEN }.distinctBy { it.pkg }.sortedBy { it.label.lowercase() }
        return Dock(shown, gone)
    }

    private fun seed(): Set<String> {
        val stored = DockApps.Hidden(hiddenPrefs.all.keys, prefs.getBoolean(SEEDED, false))
        val next = DockApps.seed(stored)
        if (next == stored) return next.apps
        hiddenPrefs.edit().apply { (next.apps - stored.apps).forEach { putBoolean(it, true) } }.apply()
        prefs.edit().putBoolean(SEEDED, next.seeded).apply()
        return next.apps
    }

    fun opened(app: AppEntry) {
        prefs.edit().putInt(app.pkg, prefs.getInt(app.pkg, 0) + 1).apply()
    }

    fun hide(app: AppEntry) {
        hiddenPrefs.edit().putBoolean(app.pkg, true).apply()
    }

    fun unhide(app: AppEntry) {
        hiddenPrefs.edit().remove(app.pkg).apply()
    }

    private companion object {
        // Kept with the launch counts: a package name always has a dot, this key has none.
        const val SEEDED = "default_hidden_seeded"
    }
}
