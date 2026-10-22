package hermestv.launcher

import android.content.Context
import org.json.JSONObject
import java.io.IOException

enum class WeatherIcon { CLEAR, CLOUDY, RAIN, THUNDER, FOG }

// Mirrors the bridge's GET /v1/weather body.
data class Weather(val tempC: Int, val code: Int, val isDay: Boolean, val updatedAt: String) {
    val text: String get() = "$tempC°"

    fun toJson(): String = JSONObject()
        .put("temp_c", tempC).put("code", code).put("is_day", isDay).put("updated_at", updatedAt)
        .toString()

    companion object {
        fun parse(json: String): Weather = JSONObject(json).let {
            Weather(it.getInt("temp_c"), it.getInt("code"), it.getBoolean("is_day"), it.getString("updated_at"))
        }

        /** WMO weather interpretation codes, as Open-Meteo sends them. */
        fun icon(code: Int): WeatherIcon = when (code) {
            0, 1 -> WeatherIcon.CLEAR
            45, 48 -> WeatherIcon.FOG
            in 51..67, in 71..77, in 80..86 -> WeatherIcon.RAIN
            95, 96, 99 -> WeatherIcon.THUNDER
            else -> WeatherIcon.CLOUDY
        }
    }
}

class WeatherClient(key: String = BuildConfig.TV_KEY) {
    private val bridge = PicksClient(key)

    fun weather(): Weather {
        val text = bridge.request("GET", "/v1/weather", null, PicksClient.TIMEOUT_MS)
        return try {
            Weather.parse(text)
        } catch (e: Exception) {
            throw IOException("bad /v1/weather body", e)
        }
    }
}

/** The last weather value; a failed refresh keeps it. null means no value yet, so the pill hides the weather. */
class WeatherState(saved: Weather?, private val fetch: () -> Weather, private val save: (Weather) -> Unit) {
    @Volatile var last: Weather? = saved
        private set

    /** Blocking: call it on the io thread. */
    fun refresh(): Weather? {
        runCatching(fetch).onSuccess {
            last = it
            save(it)
        }
        return last
    }

    companion object {
        private const val KEY = "last"

        /** A WeatherState backed by SharedPreferences and the bridge. */
        fun of(context: Context, client: WeatherClient = WeatherClient()): WeatherState {
            val prefs = context.getSharedPreferences("weather", Context.MODE_PRIVATE)
            val saved = prefs.getString(KEY, null)?.let { runCatching { Weather.parse(it) }.getOrNull() }
            return WeatherState(saved, client::weather) { prefs.edit().putString(KEY, it.toJson()).apply() }
        }
    }
}
