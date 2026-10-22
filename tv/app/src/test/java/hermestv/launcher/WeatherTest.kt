package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.IOException

class WeatherTest {
    private val bridge = """{"temp_c":29,"code":1,"is_day":true,"updated_at":"2026-10-20T08:45:00+08:00"}"""
    private val sunny = Weather(29, 1, true, "2026-10-20T08:45:00+08:00")

    @Test
    fun iconMapping() {
        mapOf(
            0 to WeatherIcon.CLEAR, 1 to WeatherIcon.CLEAR, 2 to WeatherIcon.CLOUDY, 3 to WeatherIcon.CLOUDY,
            45 to WeatherIcon.FOG, 48 to WeatherIcon.FOG, 51 to WeatherIcon.RAIN, 57 to WeatherIcon.RAIN,
            61 to WeatherIcon.RAIN, 67 to WeatherIcon.RAIN, 71 to WeatherIcon.RAIN, 77 to WeatherIcon.RAIN,
            80 to WeatherIcon.RAIN, 82 to WeatherIcon.RAIN, 85 to WeatherIcon.RAIN, 86 to WeatherIcon.RAIN,
            95 to WeatherIcon.THUNDER, 96 to WeatherIcon.THUNDER, 99 to WeatherIcon.THUNDER, 42 to WeatherIcon.CLOUDY,
        ).forEach { (code, icon) -> assertEquals("$code", icon, Weather.icon(code)) }
        assertEquals("29°", sunny.text)
        assertEquals("-2°", sunny.copy(tempC = -2).text)
    }

    @Test
    fun parsesBridgeBody() {
        assertEquals(sunny, Weather.parse(bridge))
        assertEquals(sunny, Weather.parse(sunny.toJson()))
    }

    @Test
    fun noValueHidden() {
        val saved = ArrayList<Weather>()
        val s = WeatherState(null, fetch = { throw IOException("HTTP 503") }, save = { saved += it })
        assertNull(s.last)
        assertNull(s.refresh())
        assertEquals(emptyList<Weather>(), saved)
    }

    @Test
    fun keepsLastOnError() {
        val saved = ArrayList<Weather>()
        val replies = ArrayDeque<() -> Weather>(listOf({ sunny }, { throw IOException("HTTP 503") }, { throw RuntimeException("bad body") }))
        val s = WeatherState(null, fetch = { replies.removeFirst()() }, save = { saved += it })
        assertEquals(sunny, s.refresh())
        assertEquals(sunny, s.refresh())
        assertEquals(sunny, s.refresh())
        assertEquals(listOf(sunny), saved)

        // After a restart the saved value shows until the bridge answers.
        val rain = Weather(26, 61, false, "2026-10-20T20:15:00+08:00")
        val restarted = WeatherState(sunny, fetch = { throw IOException("down") }, save = { saved += it })
        assertEquals(sunny, restarted.last)
        assertEquals(sunny, restarted.refresh())
        assertEquals(rain, WeatherState(sunny, fetch = { rain }, save = { saved += it }).refresh())
        assertEquals(listOf(sunny, rain), saved)
    }
}
