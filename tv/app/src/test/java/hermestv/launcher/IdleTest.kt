package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

class IdleTest {
    // UTC+8 all year, like the weather times below: 20:35 on Tue 20 Oct is 12:35 UTC.
    private val zone = ZoneId.of("Australia/Perth")
    private val now = ZonedDateTime.of(2026, 10, 20, 20, 35, 0, 0, zone)
    private val today = LocalDate.of(2026, 10, 20)

    private fun local(h: Int, m: Int) = ZonedDateTime.of(2026, 10, 20, h, m, 0, 0, zone)

    @Test
    fun periodAt() {
        val expected = listOf(
            "04:59" to "evening", "05:00" to "morning", "10:59" to "morning", "11:00" to "day", "14:59" to "day",
            "15:00" to "afternoon", "17:59" to "afternoon", "18:00" to "evening", "23:59" to "evening", "00:00" to "evening",
        )
        for ((hhmm, period) in expected) {
            val (h, m) = hhmm.split(":").map(String::toInt)
            assertEquals(hhmm, period, Idle.periodAt(local(h, m)))
            // The period is the hour in the given zone, whatever zone it is: the same hour in UTC gives the same period.
            assertEquals(hhmm, period, Idle.periodAt(ZonedDateTime.of(2026, 10, 20, h, m, 0, 0, ZoneId.of("UTC"))))
        }
    }

    @Test
    fun scheduleShowsTodayThenTomorrow() {
        val tomorrow = today.plusDays(1)
        val list = ScheduleList(today, listOf(
            ScheduleItem(today, LocalTime.of(7, 0), "Water the plants", "front garden", "Plants Reminder"),
            ScheduleItem(today, LocalTime.of(20, 35), "Book club", "bring the book", "Book club"),
            ScheduleItem(today, LocalTime.of(21, 0), "Pay rent", "", "Pay rent"),
            ScheduleItem(today, null, "Car service", "oil change", "Car service"),
            ScheduleItem(tomorrow, LocalTime.of(7, 0), "Gym", "", "Gym"),
            ScheduleItem(tomorrow, null, "Lock up", "", "Lock up"),
        ))
        // At 20:35 the 07:00 item has passed and the 20:35 one is due now; at most 4 rows, with the home line's labels.
        assertEquals(listOf(
            IdleRow("Today 20:35", "Book club", "bring the book"),
            IdleRow("Today 21:00", "Pay rent", ""),
            IdleRow("Today", "Car service", "oil change"),
            IdleRow("Tomorrow 07:00", "Gym", ""),
        ), Idle.rows(list, now))
        // At 21:00 the untimed item of today hides, which leaves room for tomorrow's untimed one.
        assertEquals(listOf("Today 21:00", "Tomorrow 07:00", "Tomorrow"), Idle.rows(list, local(21, 0)).map { it.time })
        // An empty list and no list give no rows, so the panel hides.
        assertEquals(emptyList<IdleRow>(), Idle.rows(ScheduleList(today, emptyList()), now))
        assertEquals(emptyList<IdleRow>(), Idle.rows(null, now))
    }

    @Test
    fun photosKeepOneShuffledOrderAndGoRound() {
        val urls = (1..6).map { "p$it" }
        val order = Idle.order(urls, 20_381L)
        assertEquals(urls.toSet(), order.toSet())
        assertEquals(order, Idle.order(urls.reversed(), 20_381L))
        assertEquals(order[1], Idle.nextPhoto(order, order[0]))
        assertEquals(order[0], Idle.nextPhoto(order, order[5]))
        // After a photo of another period, or none, the set starts at its first photo.
        assertEquals(order[0], Idle.nextPhoto(order, "other"))
        assertEquals(order[0], Idle.nextPhoto(order, null))
        assertNull(Idle.nextPhoto(emptyList(), "p1"))
    }

    @Test
    fun sceneryUrlsAreOnTheBridge() {
        val s = Idle.parseScenery(
            """{"period":"evening","items":[{"id":"01-fuji","url":"/v1/scenery/evening/01-fuji.jpg"},
            {"id":"x","url":"https://elsewhere.example/x.jpg"}]}""",
        )
        assertEquals("evening", s.period)
        assertEquals(listOf(PicksClient.BASE_URL + "/v1/scenery/evening/01-fuji.jpg"), s.urls)
    }

    @Test
    fun panelHasClockDateAndWeather() {
        val panel = Idle.panel(now, Weather(29, 2, false, "2026-10-20T19:00:00+08:00"))
        assertEquals("20:35", panel.clock)
        assertEquals("Tue 20 Oct", panel.date)
        assertEquals("29°", panel.weather?.text)
        assertNull(Idle.panel(now, null).weather)
    }

    @Test
    fun weatherOlderThanThreeHoursHides() {
        assertEquals("29°", Idle.panel(now, Weather(29, 2, false, "2026-10-20T17:35:00+08:00")).weather?.text)
        assertNull(Idle.panel(now, Weather(29, 2, false, "2026-10-20T17:34:00+08:00")).weather)
        assertNull(Idle.panel(now, Weather(29, 2, false, "2026-10-20T04:00:00Z")).weather)
        assertNull(Idle.panel(now, Weather(29, 2, false, "not a time")).weather)
    }

    @Test
    fun msToNextMinute() {
        assertEquals(60_000L, Idle.msToNextMinute(120_000L))
        assertEquals(1L, Idle.msToNextMinute(119_999L))
        assertEquals(59_000L, Idle.msToNextMinute(121_000L))
    }
}
