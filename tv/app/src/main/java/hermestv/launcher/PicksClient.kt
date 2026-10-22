package hermestv.launcher

import android.util.Log
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.MalformedURLException
import java.net.URL
import java.time.ZonedDateTime

class BridgeUnavailable(val userMessage: String) : IOException(userMessage)

/** ON: Hermes answers. OFF: it or the bridge does not. NONE: the bridge runs without Hermes. */
enum class HermesState { ON, OFF, NONE }

internal object TailnetRoute {
    const val TAILSCALE_OFFLINE = "Tailscale is disconnected. Open Tailscale and reconnect."

    fun require(host: String) {
        val addresses = InetAddress.getAllByName(host)
        if (addresses.none(::isTailnet)) {
            throw BridgeUnavailable(TAILSCALE_OFFLINE)
        }
    }

    fun isTailnet(address: InetAddress): Boolean {
        val b = address.address
        return when (b.size) {
            4 -> (b[0].toInt() and 0xff) == 100 && (b[1].toInt() and 0xff) in 64..127
            16 -> (b[0].toInt() and 0xff) == 0xfd && (b[1].toInt() and 0xff) == 0x7a
            else -> false
        }
    }
}

class PicksClient(private val key: String = BuildConfig.TV_KEY) {
    /** One row of the current slot: films, series or youtube. */
    fun picks(row: String = "films"): Picks {
        val text = request("GET", "/v1/picks?row=$row", null, TIMEOUT_MS)
        return try {
            Picks.parse(text)
        } catch (e: Exception) {
            throw IOException("bad /v1/picks body", e)
        }
    }

    fun health(): HermesState = hermesState(request("GET", "/v1/health", null, TIMEOUT_MS))

    fun schedule(): ScheduleList {
        val day = ZonedDateTime.now(Clock.zone()).toLocalDate()
        return ScheduleList(day, Schedule.parse(request("GET", "/v1/schedule", null, SCHEDULE_TIMEOUT_MS), day))
    }

    fun scenery(): Scenery = Idle.parseScenery(request("GET", "/v1/scenery", null, TIMEOUT_MS))

    fun opened(f: Film) {
        request("POST", "/v1/opened", openedBody(f), TIMEOUT_MS)
    }

    /** "Not for us"; with [channel], hides the video's whole channel. */
    fun hide(f: Film, channel: Boolean) {
        request("POST", "/v1/hide", hideBody(f, channel), TIMEOUT_MS)
    }

    internal fun request(method: String, path: String, body: JSONObject?, readTimeoutMs: Int): String =
        call(method, path, body, readTimeoutMs).toString(Charsets.UTF_8)

    /** One bridge call: tailnet only, the TV key, no redirects; any status but 200 throws. Every bridge client uses it. */
    private fun call(method: String, path: String, body: JSONObject?, readTimeoutMs: Int): ByteArray {
        val url = URL(BASE_URL + path)
        TailnetRoute.require(url.host)
        val name = path.substringBefore('?')
        val c = url.openConnection() as HttpURLConnection
        try {
            c.connectTimeout = TIMEOUT_MS
            c.readTimeout = readTimeoutMs
            c.requestMethod = method
            // A redirect could carry the key to another host.
            c.instanceFollowRedirects = false
            c.setRequestProperty("Authorization", "Bearer $key")
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            val code = c.responseCode
            Log.i(Actions.TAG, "bridge $name HTTP $code")
            if (code != 200) throw IOException("HTTP $code")
            return c.inputStream.use { it.readBytes() }
        } catch (e: IOException) {
            Log.w(Actions.TAG, "bridge $name failed: $e")
            throw e
        } finally {
            c.disconnect()
        }
    }

    companion object {
        const val BASE_URL = BuildConfig.BRIDGE_URL
        const val TIMEOUT_MS = 10_000
        // The bridge may wait 20 s for Hermes before it answers with the last list.
        const val SCHEDULE_TIMEOUT_MS = 30_000

        /** The Authorization header for an image [url]: the TV key goes to the bridge at [base] only, never to another host. */
        fun authFor(url: String, key: String = BuildConfig.TV_KEY, base: String = BASE_URL): String? {
            val u = try {
                URL(url)
            } catch (e: MalformedURLException) {
                return null
            }
            val bridge = URL(base)
            val same = u.protocol == bridge.protocol && u.host.equals(bridge.host, ignoreCase = true) && u.port == bridge.port && u.userInfo == null
            return if (same) "Bearer $key" else null
        }

        fun hermesState(json: String): HermesState = JSONObject(json).let {
            when {
                !it.optBoolean("enabled", true) -> HermesState.NONE
                it.getBoolean("hermes") -> HermesState.ON
                else -> HermesState.OFF
            }
        }

        fun openedBody(f: Film): JSONObject = item(f).put("url", f.url)

        fun hideBody(f: Film, channel: Boolean): JSONObject =
            item(f).apply { if (channel && f.kind == "video") put("channel", f.channel) }

        // The bridge refuses video_id on a film or series (422) and needs it on a video.
        private fun item(f: Film) = JSONObject().put("kind", f.kind).put("title", f.title).apply {
            if (f.kind == "video") put("video_id", f.videoId) else put("year", f.year)
        }
    }
}
