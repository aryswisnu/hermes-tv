package hermestv.launcher

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.view.View

/** A small white line icon for the status pill, drawn on a 24-unit grid. */
class Glyph(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFF4F5F7.toInt()
        strokeWidth = 2.2f
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val oval = RectF()
    private var shape: (Canvas) -> Unit = {}

    fun net(state: NetState) = show {
        when (state) {
            is NetState.Wifi -> wifi(lit = maxOf(1, state.level))
            NetState.Offline -> {
                wifi(lit = 0)
                stroke(255)
                drawLine(4f, 4f, 20f, 20f, paint)
            }
            NetState.Ethernet -> {
                stroke(255)
                oval.set(6f, 3f, 18f, 13f)
                drawRoundRect(oval, 2f, 2f, paint)
                for (x in floatArrayOf(9.5f, 12f, 14.5f)) drawLine(x, 6f, x, 8f, paint)
                drawLine(12f, 13f, 12f, 21f, paint)
            }
        }
    }

    fun weather(icon: WeatherIcon, isDay: Boolean) = show {
        when (icon) {
            WeatherIcon.CLEAR -> if (isDay) sun() else moon()
            WeatherIcon.CLOUDY -> cloud(0f)
            WeatherIcon.RAIN -> {
                cloud(-3f)
                for (x in floatArrayOf(8f, 12f, 16f)) drawLine(x, 17f, x - 1.5f, 21f, paint)
            }
            WeatherIcon.THUNDER -> {
                cloud(-3f)
                drawPath(Path().apply { moveTo(13f, 15f); lineTo(10.5f, 18.5f); lineTo(13.5f, 18.5f); lineTo(11f, 22f) }, paint)
            }
            WeatherIcon.FOG -> {
                stroke(255)
                drawLine(4f, 8f, 20f, 8f, paint)
                drawLine(6f, 12f, 18f, 12f, paint)
                drawLine(4f, 16f, 20f, 16f, paint)
            }
        }
    }

    private fun show(draw: Canvas.() -> Unit) {
        shape = { draw(it) }
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        canvas.save()
        canvas.scale(width / 24f, height / 24f)
        shape(canvas)
        canvas.restore()
    }

    // Dot plus three arcs around (12, 19); the first [lit] parts are bright, the rest dim.
    private fun Canvas.wifi(lit: Int) {
        for (k in 1..3) {
            stroke(if (k < lit) 255 else DIM)
            val r = 5f * k
            oval.set(12f - r, 19.2f - r, 12f + r, 19.2f + r)
            drawArc(oval, 226f, 88f, false, paint)
        }
        paint.style = Paint.Style.FILL
        paint.alpha = if (lit > 0) 255 else DIM
        drawCircle(12f, 19f, 1.4f, paint)
    }

    private fun Canvas.sun() {
        stroke(255)
        drawCircle(12f, 12f, 4.5f, paint)
        for (i in 0 until 8) {
            save()
            rotate(i * 45f, 12f, 12f)
            drawLine(12f, 2.5f, 12f, 4.5f, paint)
            restore()
        }
    }

    private fun Canvas.moon() {
        paint.style = Paint.Style.FILL
        paint.alpha = 255
        val disc = Path().apply { addCircle(12f, 12f, 8f, Path.Direction.CW) }
        disc.op(Path().apply { addCircle(16.5f, 8f, 7f, Path.Direction.CW) }, Path.Op.DIFFERENCE)
        drawPath(disc, paint)
    }

    private fun Canvas.cloud(dy: Float) {
        stroke(255)
        val p = Path().apply { addCircle(9f, 14f + dy, 4.5f, Path.Direction.CW) }
        p.op(Path().apply { addCircle(14.5f, 12f + dy, 5.5f, Path.Direction.CW) }, Path.Op.UNION)
        p.op(Path().apply { addRect(5f, 14f + dy, 19.5f, 18.5f + dy, Path.Direction.CW) }, Path.Op.UNION)
        drawPath(p, paint)
    }

    private fun stroke(alpha: Int) {
        paint.style = Paint.Style.STROKE
        paint.alpha = alpha
    }

    companion object {
        const val DIM = 0x55
    }
}
