package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

class ScheduleTest {
    private val today = LocalDate.of(2026, 10, 20)
    private val tomorrow = today.plusDays(1)
    private fun local(h: Int, m: Int) = ZonedDateTime.of(2026, 10, 20, h, m, 0, 0, ZoneId.of("Asia/Tokyo"))
    private fun item(date: LocalDate, time: String?, title: String, note: String = "") =
        ScheduleItem(date, time?.let(LocalTime::parse), title, note, "job")

    @Test
    fun nextTodayThenTomorrow() {
        // The bridge order is not trusted: tomorrow's item comes first here.
        val items = listOf(
            item(tomorrow, "07:00", "Water the plants", "front garden"),
            item(today, "18:30", "Pay rent"),
            item(today, "15:00", "Book club", "bring the book"),
            item(today.minusDays(1), "23:00", "Yesterday"),
            item(today.plusDays(2), "06:00", "Day after"),
        )
        assertEquals("Today 15:00 · Book club", Schedule.line(items, local(9, 0)))
        // Due this minute still shows; a minute later it has passed.
        assertEquals("Today 15:00 · Book club", Schedule.line(items, local(15, 0)))
        assertEquals("Today 18:30 · Pay rent", Schedule.line(items, local(15, 1)))
        // Today's last item has passed: tomorrow's shows, however far ahead, not the day after.
        assertEquals("Tomorrow 07:00 · Water the plants", Schedule.line(items, local(18, 31)))
        assertEquals(items[0], Schedule.next(items, local(23, 59)))
        // At midnight tomorrow becomes today.
        assertEquals("Today 07:00 · Water the plants", Schedule.line(items, local(23, 59).plusMinutes(1)))
        // The day and time are those of the given zone: 15:01 in Tokyo is 02:01 in New York, before the book club.
        val newYork = local(15, 1).withZoneSameInstant(ZoneId.of("America/New_York"))
        assertEquals("Today 15:00 · Book club", Schedule.line(items, newYork))
        // Nothing today or tomorrow hides the line.
        assertNull(Schedule.line(items.drop(3), local(9, 0)))
        assertNull(Schedule.line(emptyList(), local(9, 0)))
    }

    @Test
    fun untimedLabels() {
        val items = listOf(item(today, null, "Gym", "front garden"), item(today, "10:30", "Pay rent"), item(tomorrow, null, "Car service"))
        // A timed item of today comes before an untimed one.
        assertEquals("Today 10:30 · Pay rent", Schedule.line(items, local(1, 0)))
        assertEquals("Today · Gym", Schedule.line(items, local(10, 31)))
        assertEquals("Today · Gym", Schedule.line(items, local(20, 59)))
        // Today's untimed item counts until 21:00; tomorrow's untimed one then shows.
        assertEquals("Tomorrow · Car service", Schedule.line(items, local(21, 0)))
        assertEquals("Tomorrow", Schedule.head(items[2], local(21, 0)))
        assertEquals("Today", Schedule.head(items[0], local(9, 0)))
        assertEquals("Tomorrow 07:00", Schedule.head(item(tomorrow, "07:00", "x"), local(9, 0)))
        assertEquals("Today 07:05", Schedule.head(item(today, "07:05", "x"), local(7, 0)))
        // Tomorrow's items come in time order, untimed last.
        val later = listOf(item(tomorrow, null, "Car service"), item(tomorrow, "19:00", "Lock up"), item(tomorrow, "07:00", "Gym"))
        assertEquals(listOf("Gym", "Lock up", "Car service"), Schedule.upcoming(later, local(22, 0)).map { it.title })
    }

    @Test
    fun parsesBridgeReplyAndRoundTrips() {
        val items = Schedule.parse(
            """{"items":[{"date":"2026-10-21","time":"07:00","title":"Water the plants","note":"Bring an umbrella.","job":"Plants Reminder"},
            {"time":null,"title":"Pay rent","note":"","job":"Pay rent"}]}""",
            today,
        )
        // An item without a date, from an older bridge, is for the day it was fetched.
        assertEquals(listOf(item(tomorrow, "07:00", "Water the plants", "Bring an umbrella.").copy(job = "Plants Reminder"),
            item(today, null, "Pay rent").copy(job = "Pay rent")), items)
        val list = ScheduleList(today, items)
        assertEquals(list, ScheduleList.parse(list.toJson()))
        // A list stored by the old TV build has no dates: its items are for its day, so they never show the next day.
        val old = ScheduleList.parse("""{"day":"2026-10-19","items":[{"time":"10:00","title":"Pay rent","note":"","job":"Pay rent"}]}""")
        assertEquals(LocalDate.of(2026, 10, 19), old.items.single().date)
        assertNull(Schedule.line(old.items, local(9, 0)))
    }
}
