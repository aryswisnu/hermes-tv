package hermestv.launcher

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import java.net.URI

data class Action(
    val type: String,
    val url: String? = null,
)

/** Opens [url] in the app [pkg]; a null url opens the app itself. */
data class Launch(val url: String?, val pkg: String)

// The only thing the TV may do: open a film or series URL in Netflix or HBO Max, or a video in YouTube.
object Actions {
    const val NETFLIX = "com.netflix.ninja"
    const val HBO_MAX = "com.wbd.stream"
    const val YOUTUBE = "com.google.android.youtube.tv"
    const val TAG = "HermesTv"

    private val HOSTS = listOf(
        "netflix.com" to NETFLIX,
        "max.com" to HBO_MAX,
        "hbomax.com" to HBO_MAX,
    )

    // One exact shape, so no other YouTube page, extra parameter or look-alike host can open.
    private val YOUTUBE_URL = Regex("https://(www\\.youtube\\.com/watch\\?v=|youtu\\.be/)[A-Za-z0-9_-]{11}")

    fun plan(action: Action): Launch? =
        if (action.type == "open_url") action.url?.let(::viewFor) else null

    /** What OK on [f] opens: a video at its watch URL, a film or series its app (TMDB has no title link). */
    fun forItem(f: Film): Launch? {
        val launch = plan(Action("open_url", url = f.url)) ?: return null
        return if (launch.pkg == YOUTUBE) launch else Launch(null, launch.pkg)
    }

    private fun viewFor(url: String): Launch? {
        if (YOUTUBE_URL.matches(url)) return Launch(url, YOUTUBE)
        val uri = try { URI(url) } catch (e: Exception) { return null }
        if (uri.scheme?.lowercase() !in setOf("http", "https")) return null
        val host = uri.host?.lowercase() ?: return null
        val pkg = HOSTS.firstOrNull { (domain, _) -> host == domain || host.endsWith(".$domain") }?.second
        return pkg?.let { Launch(url, it) }
    }

    /** Opens [f]. Returns false when it is not allowed or no app takes it. */
    fun open(f: Film, context: Context): Boolean {
        val launch = forItem(f)
        if (launch == null) {
            Log.w(TAG, "open rejected: ${f.kind}")
            return false
        }
        val intent = if (launch.url == null) {
            context.packageManager.getLeanbackLaunchIntentForPackage(launch.pkg)
        } else {
            Intent(Intent.ACTION_VIEW, Uri.parse(launch.url)).setPackage(launch.pkg)
        }
        if (intent == null) {
            Log.w(TAG, "no launch intent for ${launch.pkg}")
            return false
        }
        return try {
            context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            true
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "no activity for $intent")
            false
        } catch (e: SecurityException) {
            Log.w(TAG, "not allowed to start $intent")
            false
        }
    }
}
