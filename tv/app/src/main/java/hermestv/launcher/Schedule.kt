package hermestv.launcher

import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit

/** One item of the family schedule from GET /v1/schedule on [date]; [time] is null for "some time that day". */
data class ScheduleItem(val date: LocalDate, val time: LocalTime?, val title: String, val note: String, val job: String)

/** The schedule as fetched on [day]; the TV keeps the last one it got. */
data class ScheduleList(val day: LocalDate, val items: List<ScheduleItem>) {
    fun toJson(): String = JSONObject().put("day", day.toString()).put("items", Schedule.json(items)).toString()

    companion object {
        fun parse(json: String): ScheduleList = JSONObject(json).let {
            val day = LocalDate.parse(it.getString("day"))
            ScheduleList(day, Schedule.items(it.getJSONArray("items"), day))
        }
    }
}

object Schedule {
    private val LAST_UNTIMED = LocalTime.of(21, 0)

    /** The bridge's reply fetched on [day]; an item without a date is for that day (an older bridge). */
    fun parse(json: String, day: LocalDate): List<ScheduleItem> = items(JSONObject(json).getJSONArray("items"), day)

    /**
     * The items at [now] that have not passed, in order: today's timed items from the current minute on, today's
     * untimed items until 21:00, then tomorrow's. Other days never show.
     */
    fun upcoming(items: List<ScheduleItem>, now: ZonedDateTime): List<ScheduleItem> {
        val today = now.toLocalDate()
        val t = now.toLocalTime().truncatedTo(ChronoUnit.MINUTES)
        return items.filter {
            when (it.date) {
                today -> if (it.time == null) t.isBefore(LAST_UNTIMED) else !it.time.isBefore(t)
                today.plusDays(1) -> true
                else -> false
            }
        }.sortedWith(compareBy<ScheduleItem> { it.date }.thenBy { it.time == null }.thenBy { it.time })
    }

    fun next(items: List<ScheduleItem>, now: ZonedDateTime): ScheduleItem? = upcoming(items, now).firstOrNull()

    /** `Today 18:30`, `Tomorrow 07:00`, `Today` or `Tomorrow`. */
    fun head(item: ScheduleItem, now: ZonedDateTime): String {
        val day = if (item.date == now.toLocalDate()) "Today" else "Tomorrow"
        return if (item.time == null) day else "$day ${item.time}"
    }

    /** The line under the status pill, `Today 18:30 · title`; null when nothing is next. */
    fun line(items: List<ScheduleItem>, now: ZonedDateTime): String? =
        next(items, now)?.let { listOf(head(it, now), it.title).filter(String::isNotBlank).joinToString(" · ") }

    internal fun items(a: JSONArray, day: LocalDate): List<ScheduleItem> = List(a.length()) {
        val o = a.getJSONObject(it)
        ScheduleItem(o.text("date")?.let(LocalDate::parse) ?: day, o.text("time")?.let(LocalTime::parse),
            o.text("title").orEmpty(), o.text("note").orEmpty(), o.text("job").orEmpty())
    }

    internal fun json(items: List<ScheduleItem>) = JSONArray(items.map {
        JSONObject().put("date", it.date.toString()).put("time", it.time?.toString() ?: JSONObject.NULL)
            .put("title", it.title).put("note", it.note).put("job", it.job)
    })

    // optString turns JSON null into the text "null"; isNull is also true for a missing key.
    private fun JSONObject.text(key: String): String? = if (isNull(key)) null else getString(key)
}
