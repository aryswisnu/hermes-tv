package hermestv.launcher

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Outline
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.view.View
import android.view.ViewOutlineProvider
import android.widget.LinearLayout
import kotlin.math.max

/**
 * Fake glass: no live blur (a RenderEffect cost 134 MB PSS). A source holds pre-blurred bitmaps laid
 * center-cropped over its [frame] view, with an optional top-to-bottom [tint]; each [GlassLayout] draws the
 * part of the source that lies under it.
 */
class GlassSource(private val frame: View, private val zoom: Float = 1f, private val tint: IntArray? = null, private val base: Int = 0) {
    private class Layer(val bitmap: Bitmap?, var alpha: Float)

    private val layers = ArrayList<Layer>(MAX_LAYERS + 1)
    private val panels = mutableSetOf<View>()
    private val matrix = Matrix()
    private val at = IntArray(2)
    private val origin = IntArray(2)
    private val tintPaint = Paint()
    private var tintHeight = 0

    fun attach(view: View) {
        panels += view
    }

    fun detach(view: View) {
        panels -= view
    }

    /** Fades [bitmap] in over what shows now, in [ms]; null fades to the plain base colour. */
    fun fadeTo(bitmap: Bitmap?, ms: Long) {
        val top = layers.lastOrNull()
        if (if (top == null) bitmap == null else top.bitmap === bitmap) return
        val layer = Layer(bitmap, 0f)
        layers += layer
        // Drop the oldest fading layer, not the opaque bottom one, so a held ◀ ▶ never dims the screen.
        if (layers.size > MAX_LAYERS) layers.removeAt(1)
        ValueAnimator.ofFloat(0f, 1f).setDuration(ms).apply {
            addUpdateListener {
                layer.alpha = it.animatedValue as Float
                // Once opaque, the layers under it no longer show.
                if (layer.alpha >= 1f) layers.indexOf(layer).let { i -> if (i > 0) layers.subList(0, i).clear() }
                invalidate()
            }
            start()
        }
    }

    /** Shows [under] with [over] on top at opacity [mix]. */
    fun set(under: Bitmap?, over: Bitmap?, mix: Float) {
        if (layers.size == 2 && layers[0].bitmap === under && layers[1].bitmap === over && layers[1].alpha == mix) return
        layers.clear()
        layers += Layer(under, 1f)
        layers += Layer(over, mix)
        invalidate()
    }

    /** Draws the source as it lies under [view], in view coordinates. [paint] sets the colour filter. */
    fun draw(canvas: Canvas, view: View, paint: Paint) {
        val w = frame.width
        val h = frame.height
        if (w == 0 || h == 0) return
        view.getLocationInWindow(at)
        frame.getLocationInWindow(origin)
        canvas.save()
        canvas.translate((origin[0] - at[0]).toFloat(), (origin[1] - at[1]).toFloat())
        if (base != 0) canvas.drawColor(base)
        for (l in layers) layer(canvas, l.bitmap, (l.alpha * 255).toInt(), w, h, paint)
        if (tint != null) {
            if (tintHeight != h) {
                tintPaint.shader = LinearGradient(0f, 0f, 0f, h.toFloat(), tint, null, Shader.TileMode.CLAMP)
                tintHeight = h
            }
            canvas.drawRect(0f, 0f, w.toFloat(), h.toFloat(), tintPaint)
        }
        canvas.restore()
    }

    private fun layer(canvas: Canvas, bitmap: Bitmap?, alpha: Int, w: Int, h: Int, paint: Paint) {
        if (alpha <= 0) return
        if (bitmap == null) {
            if (base != 0) canvas.drawColor((alpha shl 24) or (base and 0xFFFFFF))
            return
        }
        val s = max(w / bitmap.width.toFloat(), h / bitmap.height.toFloat()) * zoom
        matrix.setScale(s, s)
        matrix.postTranslate((w - bitmap.width * s) / 2, (h - bitmap.height * s) / 2)
        paint.alpha = alpha
        canvas.drawBitmap(bitmap, matrix, paint)
    }

    private fun invalidate() {
        frame.invalidate()
        panels.forEach(View::invalidate)
    }

    private companion object {
        // Three images can show at once while ◀ ▶ are held; older ones drop out.
        const val MAX_LAYERS = 3
    }
}

/** How a glass panel looks over its source: a colour filter for the source, a fill, a border and a top edge. */
class GlassStyle(saturation: Float, brightness: Float, val fill: Int, val border: Int, val top: Int) {
    val filter = ColorMatrixColorFilter(ColorMatrix().apply {
        setSaturation(saturation)
        postConcat(ColorMatrix().apply { setScale(brightness, brightness, brightness, 1f) })
    })
}

/** A LinearLayout with rounded corners that shows its [source] through a tint. */
class GlassLayout(context: Context, private val source: GlassSource?, private val radius: Float, private val look: GlassStyle) :
    LinearLayout(context) {
    private val image = Paint(Paint.FILTER_BITMAP_FLAG).apply { colorFilter = look.filter }
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeWidth = context.resources.displayMetrics.density }
    private val edge = RectF()

    init {
        setWillNotDraw(false)
        clipToOutline = true
        outlineProvider = object : ViewOutlineProvider() {
            override fun getOutline(view: View, outline: Outline) = outline.setRoundRect(0, 0, view.width, view.height, radius)
        }
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        source?.attach(this)
    }

    override fun onDetachedFromWindow() {
        source?.detach(this)
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        source?.draw(canvas, this, image)
        canvas.drawColor(look.fill)
        val px = line.strokeWidth
        line.style = Paint.Style.FILL
        line.color = look.top
        canvas.drawRect(0f, 0f, width.toFloat(), px, line)
        line.style = Paint.Style.STROKE
        line.color = look.border
        edge.set(px / 2, px / 2, width - px / 2, height - px / 2)
        canvas.drawRoundRect(edge, radius, radius, line)
    }
}
