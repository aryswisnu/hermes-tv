package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.TimeZone

class StatusTest {
    @Test
    fun ramPercent() {
        // Available over total, rounded to a whole percent: not bytes, not a fraction of a percent.
        assertEquals("RAM 41% free", Status.ramText(820_000_000L, 2_000_000_000L))
        assertEquals("RAM 41% free", Status.ramText(829_999_999L, 2_000_000_000L))
        assertEquals("RAM 42% free", Status.ramText(830_000_000L, 2_000_000_000L))
        assertEquals("RAM 49% free", Status.ramText(1_018_945_536L, 2_066_427_904L))
        assertEquals("RAM 0% free", Status.ramText(0L, 2_000_000_000L))
        assertEquals("RAM 100% free", Status.ramText(2_000_000_000L, 2_000_000_000L))
    }

    @Test
    fun clockFollowsTheGivenZone() {
        val saved = TimeZone.getDefault()
        try {
            // The zone of the time given wins over the JVM default; the caller passes Clock.zone().
            TimeZone.setDefault(TimeZone.getTimeZone("America/New_York"))
            val tokyo = ZoneId.of("Asia/Tokyo")
            val utc = ZonedDateTime.of(2026, 10, 20, 12, 41, 0, 0, ZoneId.of("UTC"))
            assertEquals("12:41", Status.clock(utc))
            assertEquals("21:41", Status.clock(utc.withZoneSameInstant(tokyo)))
            assertEquals("Tue 20 Oct", Status.date(utc.withZoneSameInstant(tokyo)))
            // 16:30 UTC on Monday is already Tuesday in Tokyo.
            val late = ZonedDateTime.of(2026, 10, 19, 16, 30, 0, 0, ZoneId.of("UTC"))
            assertEquals("Mon 19 Oct", Status.date(late))
            assertEquals("01:30", Status.clock(late.withZoneSameInstant(tokyo)))
            assertEquals("Tue 20 Oct", Status.date(late.withZoneSameInstant(tokyo)))
            assertEquals("Sun 6 Sep", Status.date(ZonedDateTime.of(2026, 9, 6, 8, 5, 0, 0, tokyo)))
        } finally {
            TimeZone.setDefault(saved)
        }
    }

    @Test
    fun networkStates() {
        assertEquals(NetState.Offline, Status.network(wifi = false, ethernet = false, level = 3, maxLevel = 4))
        assertEquals(NetState.Ethernet, Status.network(wifi = false, ethernet = true, level = 0, maxLevel = 4))
        assertEquals(NetState.Ethernet, Status.network(wifi = true, ethernet = true, level = 2, maxLevel = 4))
        assertEquals(NetState.Wifi(4), Status.network(wifi = true, ethernet = false, level = 4, maxLevel = 4))
        assertEquals(NetState.Wifi(0), Status.network(wifi = true, ethernet = false, level = 0, maxLevel = 4))
        // Some radios report more steps; the icon has 0..4.
        assertEquals(NetState.Wifi(2), Status.network(wifi = true, ethernet = false, level = 5, maxLevel = 10))
        assertEquals(NetState.Wifi(4), Status.network(wifi = true, ethernet = false, level = 7, maxLevel = 4))
        assertEquals(NetState.Wifi(0), Status.network(wifi = true, ethernet = false, level = 3, maxLevel = 0))
    }
}
