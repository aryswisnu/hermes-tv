package hermestv.launcher

import android.animation.ObjectAnimator
import android.animation.PropertyValuesHolder
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.service.dreams.DreamService
import android.text.TextUtils
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject
import java.time.Duration
import java.time.OffsetDateTime
import java.time.ZonedDateTime
import java.time.format.DateTimeParseException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.random.Random

/** The idle screen's clock panel; a null [weather] hides its line. */
data class IdlePanel(val clock: String, val date: String, val weather: Weather?)

/** One line of the idle screen's schedule panel; [time] is a [Schedule.head], such as "Tomorrow 07:00". */
data class IdleRow(val time: String, val title: String, val note: String)

/** The bridge's scenery photos of one [period], as full URLs. */
data class Scenery(val period: String, val urls: List<String>)

/** What the idle screen shows. */
object Idle {
    /** The scenery set at [now]: morning 05:00-10:59, day 11:00-14:59, afternoon 15:00-17:59, evening 18:00-04:59. */
    fun periodAt(now: ZonedDateTime): String = when (now.hour) {
        in 5..10 -> "morning"
        in 11..14 -> "day"
        in 15..17 -> "afternoon"
        else -> "evening"
    }

    /** [urls] shuffled the same way for the same [seed], whatever order the bridge sends them in. */
    fun order(urls: List<String>, seed: Long): List<String> = urls.sorted().shuffled(Random(seed))

    /** The photo after [last] in [order], going round; the first when [last] is not in it. */
    fun nextPhoto(order: List<String>, last: String?): String? =
        if (order.isEmpty()) null else order[(order.indexOf(last) + 1) % order.size]

    // The bridge sends paths on itself; any other URL is not one of its photos.
    fun parseScenery(json: String): Scenery = JSONObject(json).let { o ->
        val items = o.getJSONArray("items")
        val paths = List(items.length()) { items.getJSONObject(it).getString("url") }
        Scenery(o.getString("period"), paths.filter { it.startsWith("/") }.map { PicksClient.BASE_URL + it })
    }

    /** The [Schedule.upcoming] items of today and tomorrow at [now], at most 4. */
    fun rows(list: ScheduleList?, now: ZonedDateTime): List<IdleRow> =
        Schedule.upcoming(list?.items.orEmpty(), now).take(MAX_ROWS).map { IdleRow(Schedule.head(it, now), it.title, it.note) }

    fun panel(now: ZonedDateTime, weather: Weather?) = IdlePanel(Status.clock(now), Status.date(now), weather?.takeIf { fresh(it, now) })

    fun msToNextMinute(nowMs: Long): Long = MINUTE_MS - nowMs % MINUTE_MS

    private fun fresh(weather: Weather, now: ZonedDateTime): Boolean = try {
        Duration.between(OffsetDateTime.parse(weather.updatedAt).toInstant(), now.toInstant()) <= WEATHER_MAX_AGE
    } catch (e: DateTimeParseException) {
        false
    }

    private val WEATHER_MAX_AGE: Duration = Duration.ofHours(3)
    private const val MINUTE_MS = 60_000L
    private const val MAX_ROWS = 4
}

/** Hermes TV's screensaver: scenery for the time of day with a slow pan, the clock and the next schedule items in glass. */
class IdleDream : DreamService() {
    private val main = Handler(Looper.getMainLooper())
    private val client = PicksClient()
    private var images: ImageLoader? = null
    private var io: ExecutorService? = null
    private var pan: ValueAnimator? = null
    private var softwareLogged = false
    private var schedule: ScheduleList? = null
    private var rowsShown: List<IdleRow>? = null
    private var shown: String? = null
    private val prefs by lazy { getSharedPreferences(HomeActivity.PREFS, Context.MODE_PRIVATE) }

    private lateinit var stage: FrameLayout
    private lateinit var front: ImageView
    private lateinit var back: ImageView
    private lateinit var clock: TextView
    private lateinit var date: TextView
    private lateinit var weatherIcon: Glyph
    private lateinit var temp: TextView
    private lateinit var agenda: LinearLayout

    private val tick = object : Runnable {
        override fun run() {
            // The dream runs in the Home process: a crash here would take Home down with it.
            runCatching {
                val now = ZonedDateTime.now(Clock.zone())
                show(Idle.panel(now, WeatherState.of(this@IdleDream).last))
                showRows(Idle.rows(schedule, now))
            }.onFailure { Log.w(Actions.TAG, "idle panel failed: $it") }
            main.postDelayed(this, Idle.msToNextMinute(System.currentTimeMillis()))
        }
    }

    private val photoTick = Runnable {
        runCatching(::nextPhoto).onFailure { Log.w(Actions.TAG, "idle photo failed: $it") }
    }

    private val scheduleTick = object : Runnable {
        override fun run() {
            val now = SystemClock.elapsedRealtime()
            val last = scheduleAt
            if (last != null && now - last < SCHEDULE_MS) {
                main.postDelayed(this, SCHEDULE_MS - (now - last))
                return
            }
            scheduleAt = now
            val pool = io ?: return
            pool.execute {
                try {
                    val list = client.schedule()
                    main.post { if (io === pool) runCatching { setSchedule(list) } }
                } catch (e: Exception) {
                    Log.w(Actions.TAG, "idle schedule refresh failed: $e")
                }
            }
            main.postDelayed(this, SCHEDULE_MS)
        }
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        isInteractive = false
        isFullscreen = true
        isScreenBright = true
        setContentView(build())
    }

    override fun onDreamingStarted() {
        super.onDreamingStarted()
        schedule = savedSchedule()
        tick.run()
        runCatching {
            images = ImageLoader(this)
            // Two threads: the schedule call can wait 20 s for Hermes and must not hold up the photo list.
            io = Executors.newFixedThreadPool(2)
            nextPhoto()
            scheduleTick.run()
        }.onFailure { Log.w(Actions.TAG, "idle screen start failed: $it") }
    }

    override fun onDreamingStopped() {
        stop()
        super.onDreamingStopped()
    }

    override fun onDetachedFromWindow() {
        stop()
        super.onDetachedFromWindow()
    }

    private fun stop() {
        main.removeCallbacksAndMessages(null)
        pan?.cancel()
        pan = null
        if (::front.isInitialized) {
            front.animate().cancel()
            back.animate().cancel()
        }
        io?.shutdownNow()
        io = null
        images?.close()
        images = null
    }

    // The photo list comes from the bridge once per period; the photos themselves from the disk cache after the first time.
    private fun nextPhoto() {
        val pool = io ?: return
        val loader = images ?: return
        val period = Idle.periodAt(ZonedDateTime.now(Clock.zone()))
        pool.execute {
            val set = cached?.takeIf { it.period == period } ?: fetchScenery()
            main.post {
                if (images !== loader) return@post
                runCatching {
                    val last = shown ?: prefs.getString(LAST_PHOTO, null)
                    val day = ZonedDateTime.now(Clock.zone()).toLocalDate().toEpochDay()
                    val next = Idle.nextPhoto(Idle.order(set?.urls.orEmpty(), day), last)
                    load(loader, listOfNotNull(next, last).distinct().filter { it != shown })
                }.onFailure { Log.w(Actions.TAG, "idle photo failed: $it") }
                main.postDelayed(photoTick, PHOTO_MS)
            }
        }
    }

    private fun fetchScenery(): Scenery? = try {
        client.scenery().also { cached = it }
    } catch (e: Exception) {
        Log.w(Actions.TAG, "idle scenery list failed: $e")
        null
    }

    private fun load(loader: ImageLoader, urls: List<String>) {
        if (urls.isEmpty()) return
        val width = minOf(resources.displayMetrics.widthPixels, IMAGE_W)
        loader.load(urls.first(), width) { bitmap ->
            // A load queued before stop() can still answer.
            when {
                images !== loader -> Unit
                bitmap == null -> load(loader, urls.drop(1))
                else -> runCatching { crossFade(bitmap, urls.first()) }.onFailure { Log.w(Actions.TAG, "idle screen image failed: $it") }
            }
        }
    }

    private fun crossFade(bitmap: Bitmap, url: String) {
        val old = front
        front = back
        back = old
        shown = url
        prefs.edit().putString(LAST_PHOTO, url).apply()
        front.setImageBitmap(bitmap)
        front.alpha = 0f
        front.bringToFront()
        front.animate().alpha(1f).setDuration(FADE_MS).withEndAction {
            // At most two photos in RAM: the old one goes once the new one covers it, and the loader keeps none.
            old.setImageDrawable(null)
            images?.clear()
            io?.execute { Runtime.getRuntime().gc() }
        }.start()
        if (pan == null) startPan()
    }

    // Scale never goes below 1.06, so the ±16 dp drift never shows an edge.
    private fun startPan() {
        // In software rendering each pan frame redraws the full-screen bitmap on the CPU; the still image is enough.
        if (!stage.isHardwareAccelerated) {
            if (!softwareLogged) Log.w(Actions.TAG, "idle screen: no hardware rendering, no pan")
            softwareLogged = true
            return
        }
        val drift = dp(16).toFloat()
        pan = ObjectAnimator.ofPropertyValuesHolder(
            stage,
            PropertyValuesHolder.ofFloat(View.SCALE_X, 1.06f, 1.16f),
            PropertyValuesHolder.ofFloat(View.SCALE_Y, 1.06f, 1.16f),
            PropertyValuesHolder.ofFloat(View.TRANSLATION_X, -drift, drift),
        ).apply {
            duration = PAN_MS
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE
            start()
        }
    }

    private fun savedSchedule(): ScheduleList? = try {
        prefs.getString(HomeActivity.SCHEDULE_KEY, null)?.let(ScheduleList::parse)
    } catch (e: Exception) {
        Log.w(Actions.TAG, "saved schedule unreadable: $e")
        null
    }

    // Home reads the same key when it starts, so the newest list is not lost.
    private fun setSchedule(list: ScheduleList) {
        schedule = list
        prefs.edit().putString(HomeActivity.SCHEDULE_KEY, list.toJson()).apply()
        showRows(Idle.rows(list, ZonedDateTime.now(Clock.zone())))
    }

    private fun show(panel: IdlePanel) {
        clock.text = panel.clock
        date.text = panel.date
        val w = panel.weather
        weatherIcon.visibility = if (w == null) View.GONE else View.VISIBLE
        temp.visibility = weatherIcon.visibility
        if (w != null) {
            weatherIcon.weather(Weather.icon(w.code), w.isDay)
            temp.text = w.text
        }
    }

    private fun showRows(rows: List<IdleRow>) {
        if (rows == rowsShown) return
        rowsShown = rows
        agenda.removeAllViews()
        agenda.visibility = if (rows.isEmpty()) View.GONE else View.VISIBLE
        rows.forEachIndexed { i, row ->
            val lines = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                addView(line(text(18f, HomeScreen.SEMIBOLD), row.title))
                if (row.note.isNotBlank()) {
                    addView(line(text(15f, HomeScreen.MEDIUM), row.note).apply { alpha = SECONDARY },
                        LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { topMargin = dp(4) })
                }
            }
            agenda.addView(LinearLayout(this).apply {
                addView(text(18f, HomeScreen.MEDIUM).apply {
                    text = row.time
                    alpha = SECONDARY
                }, LinearLayout.LayoutParams(dp(150), WRAP_CONTENT))
                addView(lines)
            }, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { if (i > 0) topMargin = dp(14) })
        }
    }

    private fun line(view: TextView, value: String) = view.apply {
        text = value
        maxLines = 1
        ellipsize = TextUtils.TruncateAt.END
        maxWidth = dp(400)
    }

    private fun build(): View {
        front = photo()
        back = photo()
        stage = FrameLayout(this).apply {
            addView(back, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            addView(front, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        }
        clock = text(64f, Typeface.DEFAULT_BOLD)
        date = text(20f, HomeScreen.MEDIUM)
        weatherIcon = Glyph(this)
        temp = text(20f, HomeScreen.MEDIUM)
        val panel = GlassLayout(this, null, dp(24).toFloat(), HomeScreen.CAPTION).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(28), dp(22), dp(28), dp(22))
            addView(clock)
            addView(LinearLayout(context).apply {
                gravity = Gravity.CENTER_VERTICAL
                addView(date)
                addView(weatherIcon, LinearLayout.LayoutParams(dp(22), dp(22)).apply { marginStart = dp(18) })
                addView(temp, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { marginStart = dp(6) })
            }, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { topMargin = dp(10) })
        }
        agenda = GlassLayout(this, null, dp(24).toFloat(), HomeScreen.CAPTION).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(28), dp(22), dp(28), dp(22))
            visibility = View.GONE
        }
        return FrameLayout(this).apply {
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(0xFF1C1F27.toInt(), 0xFF0A0B0F.toInt()))
            addView(stage, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            addView(View(context).apply {
                background = GradientDrawable(GradientDrawable.Orientation.BOTTOM_TOP, intArrayOf(0x99000000.toInt(), 0))
            }, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            addView(panel, FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT, Gravity.BOTTOM or Gravity.START).apply {
                leftMargin = dp(HomeScreen.LEFT)
                bottomMargin = dp(44)
            })
            addView(agenda, FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT, Gravity.BOTTOM or Gravity.END).apply {
                rightMargin = dp(HomeScreen.LEFT)
                bottomMargin = dp(44)
            })
        }
    }

    private fun photo() = ImageView(this).apply {
        scaleType = ImageView.ScaleType.CENTER_CROP
        alpha = 0f
    }

    private fun text(sp: Float, face: Typeface) = TextView(this).apply {
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        setTextColor(HomeScreen.INK)
        includeFontPadding = false
        typeface = face
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private companion object {
        const val IMAGE_W = 1280
        const val PAN_MS = 60_000L
        const val FADE_MS = 1_500L
        const val PHOTO_MS = 5 * 60_000L
        const val SCHEDULE_MS = 30 * 60_000L
        const val SECONDARY = 0.72f
        const val LAST_PHOTO = "idle_photo"

        // Kept for the life of the Home process, so a new dream does not ask the bridge again.
        @Volatile var cached: Scenery? = null
        var scheduleAt: Long? = null
    }
}
