package hermestv.launcher

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.ZonedDateTime
import java.util.Locale

// Mirrors the bridge's PickFilmOut (bridge/hermestv/picks.py): a film, a series or a YouTube video.
data class Film(
    val title: String,
    val year: Int?,
    val runtimeMin: Int?,
    val rating: Double?,
    val service: String,
    val url: String,
    val poster: String?,
    val backdrop: String?,
    val genres: List<String>,
    val why: String,
    val kind: String = "film",
    val seasons: Int? = null,
    val channel: String? = null,
    val durationMin: Int? = null,
    val videoId: String? = null,
)

// Mirrors the bridge's PicksOut.
data class Picks(
    val slot: String,
    val label: String,
    val films: List<Film>,
    val updatedAt: String?,
    val refreshing: Boolean = false,
    val row: String = "films",
) {
    fun toJson(): String = JSONObject()
        .put("slot", slot)
        .put("label", label)
        .put("row", row)
        .put("updated_at", updatedAt ?: JSONObject.NULL)
        .put("refreshing", refreshing)
        .put("films", JSONArray(films.map { f ->
            JSONObject()
                .put("title", f.title)
                .put("year", f.year ?: JSONObject.NULL)
                .put("runtime_min", f.runtimeMin ?: JSONObject.NULL)
                .put("rating", f.rating ?: JSONObject.NULL)
                .put("service", f.service)
                .put("url", f.url)
                .put("poster", f.poster ?: JSONObject.NULL)
                .put("backdrop", f.backdrop ?: JSONObject.NULL)
                .put("genres", JSONArray(f.genres))
                .put("why", f.why)
                .put("kind", f.kind)
                .put("seasons", f.seasons ?: JSONObject.NULL)
                .put("channel", f.channel ?: JSONObject.NULL)
                .put("duration_min", f.durationMin ?: JSONObject.NULL)
                .put("video_id", f.videoId ?: JSONObject.NULL)
        }))
        .toString()

    companion object {
        fun parse(json: String): Picks {
            val o = JSONObject(json)
            val films = o.getJSONArray("films")
            return Picks(
                slot = o.getString("slot"),
                label = o.getString("label"),
                films = List(films.length()) { film(films.getJSONObject(it)) },
                updatedAt = o.str("updated_at"),
                refreshing = o.optBoolean("refreshing", false),
                row = o.str("row") ?: "films",
            )
        }

        private fun film(f: JSONObject): Film {
            val genres = f.optJSONArray("genres")
            return Film(
                title = f.getString("title"),
                year = f.int("year"),
                runtimeMin = f.int("runtime_min"),
                rating = if (f.isNull("rating")) null else f.getDouble("rating"),
                service = f.getString("service"),
                url = f.getString("url"),
                poster = f.str("poster"),
                backdrop = f.str("backdrop"),
                genres = if (genres == null) emptyList() else List(genres.length()) { genres.getString(it) },
                why = f.optString("why", ""),
                kind = f.str("kind") ?: "film",
                seasons = f.int("seasons"),
                channel = f.str("channel"),
                durationMin = f.int("duration_min"),
                videoId = f.str("video_id"),
            )
        }

        // optString turns JSON null into the text "null", so test for null first.
        private fun JSONObject.str(key: String): String? = if (isNull(key)) null else getString(key)

        private fun JSONObject.int(key: String): Int? = if (isNull(key)) null else getInt(key)
    }
}

/** The saved list of each slot and row, picks-<slot>-<row>.json in [dir]; the picker and the home screen share it. */
class PicksFile(private val dir: File) {
    fun local(slot: String, row: String): Picks? = try {
        file(slot, row).takeIf { it.exists() }?.readText()?.let(Picks::parse)
    } catch (e: Exception) {
        Log.w(Actions.TAG, "saved picks unreadable: $e")
        null
    }

    // Runs on the io thread.
    fun save(picks: Picks) {
        var tmp: File? = null
        try {
            // A unique name: two io threads (picker and home) can save the same slot at once.
            tmp = File.createTempFile("picks-${picks.slot}-${picks.row}", ".tmp", dir)
            tmp.writeText(picks.toJson())
            if (tmp.renameTo(file(picks.slot, picks.row))) return
            Log.w(Actions.TAG, "cannot save picks: rename failed")
        } catch (e: Exception) {
            Log.w(Actions.TAG, "cannot save picks: $e")
        }
        tmp?.delete()
    }

    private fun file(slot: String, row: String) = File(dir, "picks-$slot-$row.json")
}

object Meta {
    /** `2018 · 1 h 30 · TMDB 7.5`; the service is shown apart, as a pill. */
    fun line(f: Film): String = listOfNotNull(
        f.year?.toString(),
        f.runtimeMin?.let(::runtime),
        f.rating?.let(::rating),
    ).joinToString(" · ")

    /** The card's line: a film's [line] and service; `2014 · 2 seasons · TMDB 8.9 · Netflix`; `Night Files · 30 min`. */
    fun card(f: Film): String = when (f.kind) {
        "video" -> listOfNotNull(f.channel, f.durationMin?.let { "$it min" })
        "series" -> listOfNotNull(
            f.year?.toString(),
            f.seasons?.let { if (it == 1) "1 season" else "$it seasons" },
            f.rating?.let(::rating),
            service(f.service),
        )
        else -> listOf(line(f), service(f.service))
    }.joinToString(" · ")

    /** The extra line on the big card of the From Hermes row: Hermes's reason, when it gave one. */
    fun reason(f: Film, line: Line): String? = if (line == Line.HERMES) f.why.trim().ifEmpty { null } else null

    fun service(name: String): String = when (name) {
        "netflix" -> "Netflix"
        "hbomax" -> "HBO Max"
        "youtube" -> "YouTube"
        else -> name
    }

    private fun rating(score: Double) = String.format(Locale.US, "TMDB %.1f", score)

    private fun runtime(min: Int): String = when {
        min < 60 -> "$min min"
        min % 60 == 0 -> "${min / 60} h"
        else -> String.format(Locale.US, "%d h %02d", min / 60, min % 60)
    }
}

/**
 * The bridge's time slots (bridge/hermestv/slots.py); picks which saved lists to show before the bridge answers.
 * Labels always come from the bridge.
 */
object Slot {
    fun at(now: ZonedDateTime): String = when (now.hour) {
        in 6..10 -> "morning"
        in 11..13 -> "lunch"
        in 14..16 -> "afternoon"
        in 17..18 -> "dinner"
        in 4..5 -> "late"
        else -> "night"
    }
}
