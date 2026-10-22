package hermestv.launcher

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import java.time.ZonedDateTime
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/** The cameras' grid streams in a 2x2 grid, with the info cell when three or fewer. OK opens the focused camera full screen; Back returns to Home. */
class CamerasActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var io: ExecutorService
    private lateinit var weather: WeatherState
    private val frames = ArrayList<FrameLayout>()
    private val players = ArrayList<CameraPlayer>()
    private var focus = 0
    private lateinit var time: TextView
    private lateinit var date: TextView
    private lateinit var weatherRow: LinearLayout
    private lateinit var weatherIcon: Glyph
    private lateinit var temp: TextView

    private val clockTick = object : Runnable {
        override fun run() {
            val now = ZonedDateTime.now(Clock.zone())
            time.text = Status.clock(now)
            date.text = Status.date(now)
            main.postDelayed(this, 60_000L - System.currentTimeMillis() % 60_000L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        CameraPlayer.quiet()
        io = Executors.newSingleThreadExecutor()
        weather = WeatherState.of(this)
        val metrics = resources.displayMetrics
        val ring = dp(RING)
        val cellH = (metrics.heightPixels - 2 * dp(MARGIN) - 4 * ring) / 2
        val cellW = cellH * 16 / 9
        val grid = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        for (r in 0..1) {
            val row = LinearLayout(this)
            for (c in 0..1) {
                val what = Cameras.cell(r * 2 + c)
                val cell = when (what) {
                    is Cameras.Cell.Live -> camera(what.camera)
                    Cameras.Cell.Info -> info()
                    Cameras.Cell.Empty -> empty()
                }
                row.addView(FrameLayout(this).apply {
                    setPadding(ring, ring, ring, ring)
                    addView(cell, FrameLayout.LayoutParams(cellW, cellH))
                    if (what is Cameras.Cell.Live) frames += this
                }, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
            }
            grid.addView(row)
        }
        setContentView(FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(grid, FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT, Gravity.CENTER))
        })
        focus(0)
    }

    override fun onStart() {
        super.onStart()
        players.forEach(CameraPlayer::play)
        if (Cameras.INFO == null) return
        clockTick.run()
        setWeather(weather.last)
        io.execute {
            val w = weather.refresh()
            main.post { setWeather(w) }
        }
    }

    override fun onStop() {
        main.removeCallbacks(clockTick)
        players.forEach(CameraPlayer::stop)
        super.onStop()
    }

    override fun onDestroy() {
        io.shutdownNow()
        super.onDestroy()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode in HomeActivity.OK_KEYS) {
            if (event.repeatCount == 0 && Cameras.cell(focus) is Cameras.Cell.Live) startActivity(Intent(this, CameraActivity::class.java).putExtra(CameraActivity.EXTRA_CAMERA, focus))
            return true
        }
        return when (keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> {
                focus(Cameras.move(focus, keyCode))
                true
            }
            else -> super.onKeyDown(keyCode, event)
        }
    }

    private fun focus(cell: Int) {
        focus = cell
        frames.forEachIndexed { i, f ->
            f.foreground = if (i == cell) GradientDrawable().apply {
                cornerRadius = (dp(CELL_R) + dp(RING) - dp(OUTLINE) / 2).toFloat()
                setStroke(dp(OUTLINE), Color.WHITE)
            } else null
        }
    }

    private fun camera(camera: Camera): View {
        val surface = SurfaceView(this).apply { keepScreenOn = true }
        val message = CameraPlayer.message(this)
        players += CameraPlayer(surface, message, camera.grid, "${camera.name} grid")
        val name = GlassLayout(this, null, dp(14).toFloat(), HomeScreen.CAPTION).apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), 0, dp(14), 0)
            addView(text(15f, HomeScreen.SEMIBOLD).apply { text = camera.name })
        }
        return FrameLayout(this).apply {
            setBackgroundColor(CELL)
            addView(surface, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            addView(message, FrameLayout.LayoutParams(WRAP_CONTENT, dp(CameraPlayer.MESSAGE_DP), Gravity.CENTER))
            addView(name, FrameLayout.LayoutParams(WRAP_CONTENT, dp(32), Gravity.BOTTOM or Gravity.START).apply {
                setMargins(dp(14), 0, 0, dp(14))
            })
            addView(Corners(context, dp(CELL_R).toFloat(), Color.BLACK), FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        }
    }

    // No SurfaceView: an idle one punches a black hole through the panel.
    private fun empty(): View = View(this).apply {
        background = GradientDrawable().apply {
            cornerRadius = dp(CELL_R).toFloat()
            setColor(CELL)
        }
    }

    private fun info(): View = GlassLayout(this, null, dp(CELL_R).toFloat(), HomeScreen.PANEL).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER_VERTICAL
        setPadding(dp(40), 0, dp(40), 0)
        time = text(64f, Typeface.DEFAULT_BOLD)
        date = text(22f, HomeScreen.MEDIUM)
        weatherIcon = Glyph(context)
        temp = text(22f, HomeScreen.MEDIUM)
        weatherRow = LinearLayout(context).apply {
            gravity = Gravity.CENTER_VERTICAL
            addView(weatherIcon, LinearLayout.LayoutParams(dp(26), dp(26)))
            addView(temp, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { marginStart = dp(10) })
        }
        addView(time)
        addView(date)
        addView(weatherRow, below(24))
    }

    private fun setWeather(w: Weather?) {
        weatherRow.visibility = if (w == null) View.GONE else View.VISIBLE
        if (w == null) return
        weatherIcon.weather(Weather.icon(w.code), w.isDay)
        temp.text = w.text
    }

    private fun below(gap: Int) = LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { topMargin = dp(gap) }

    private fun text(sp: Float, face: Typeface) = TextView(this).apply {
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        setTextColor(HomeScreen.INK)
        typeface = face
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    /** Paints the corners outside a rounded rect: a parent's outline clip does not round a SurfaceView's picture. */
    private class Corners(context: Context, private val radius: Float, color: Int) : View(context) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { this.color = color }
        private val path = Path().apply { fillType = Path.FillType.EVEN_ODD }

        override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
            path.reset()
            path.addRect(0f, 0f, w.toFloat(), h.toFloat(), Path.Direction.CW)
            path.addRoundRect(0f, 0f, w.toFloat(), h.toFloat(), radius, radius, Path.Direction.CW)
        }

        override fun onDraw(canvas: Canvas) = canvas.drawPath(path, paint)
    }

    private companion object {
        const val MARGIN = 24
        const val RING = 6 // room for the outline, 3 dp off the cell; two rings make the gap between cells
        const val OUTLINE = 3
        const val CELL_R = 16
        val CELL = 0xFF1E1F22.toInt() // the info panel's look, so empty cells match it
    }
}
