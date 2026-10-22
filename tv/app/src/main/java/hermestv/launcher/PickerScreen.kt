package hermestv.launcher

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.Outline
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.ViewOutlineProvider
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

/** Design A: the focused film fills the screen as a blurred backdrop, with one row of posters at the bottom. */
class PickerScreen(private val context: Context, private val images: ImageLoader) {
    private val backA = backdrop()
    private val backB = backdrop().apply { alpha = 0f }
    private var showingB = false
    private var wantedBackdrop: String? = null

    private val eyebrow = text(20f, 0xFFC9CCD3.toInt()).apply { letterSpacing = 0.02f }
    private val title = text(56f, Color.WHITE, bold = true).apply {
        maxLines = 1
        ellipsize = android.text.TextUtils.TruncateAt.END
    }
    private val meta = text(22f, 0xFFD3D6DC.toInt())
    private val pill = text(19f, 0xFFF2F2F2.toInt()).apply {
        setPadding(dp(14), dp(3), dp(14), dp(3))
        background = GradientDrawable().apply {
            cornerRadius = dp(999).toFloat()
            setColor(0x24FFFFFF)
        }
    }
    private val why = text(22f, 0xFFE3E5EA.toInt()).apply {
        maxLines = 2
        ellipsize = android.text.TextUtils.TruncateAt.END
        setLineSpacing(0f, 1.3f)
    }
    private val metaRow = LinearLayout(context).apply {
        gravity = Gravity.CENTER_VERTICAL
        addView(meta)
        addView(pill, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { marginStart = dp(16) })
    }
    private val details = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        addView(title, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(10); bottomMargin = dp(6) })
        addView(metaRow)
        addView(why, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { topMargin = dp(14) })
    }
    private val row = LinearLayout(context).apply { gravity = Gravity.BOTTOM }
    private val scroll = HorizontalScrollView(context).apply {
        isHorizontalScrollBarEnabled = false
        isFocusable = false
        isSmoothScrollingEnabled = true
        addView(row)
    }
    private val hint = text(18f, 0xFF9EA3AD.toInt()).apply { text = "◀ ▶ choose · OK opens in Netflix or HBO Max" }
    private val refresh = text(18f, 0xFFC9CCD3.toInt()).apply { text = "Refreshing picks…" }

    private var films: List<Film> = emptyList()
    private var focus = 0

    val root: View = FrameLayout(context).apply {
        setBackgroundColor(0xFF0B0C10.toInt())
        addView(backA, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        addView(backB, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        addView(View(context).apply {
            background = GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
                intArrayOf(0xC708090C.toInt(), 0x6608090C, 0x1A08090C))
        }, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(48), dp(36), dp(48), dp(30))
            addView(eyebrow)
            addView(details)
            addView(View(context), LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
            addView(scroll, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            addView(hint, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { topMargin = dp(12) })
        }, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        addView(refresh, FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT, Gravity.TOP or Gravity.END).apply {
            topMargin = dp(36)
            marginEnd = dp(48)
        })
    }

    init {
        // why is at most 46 % of the screen wide, as in the mockup.
        why.maxWidth = context.resources.displayMetrics.widthPixels * 46 / 100
        message("")
    }

    fun focused(): Film? = films.getOrNull(focus)

    fun render(view: PickerView) {
        when (view) {
            PickerView.Finding -> message("Finding films…")
            PickerView.NoFilms -> message("No films found right now")
            is PickerView.Offline -> message(view.message)
            is PickerView.Showing -> {
                eyebrow.text = view.picks.label
                refresh.visibility = if (view.refreshing) View.VISIBLE else View.GONE
                if (view.picks.films != films) {
                    val index = PickerState.keepFocus(films, focus, view.picks.films)
                    films = view.picks.films
                    fillRow()
                    select(index)
                }
            }
        }
    }

    fun move(step: Int) {
        if (films.isEmpty()) return
        val next = (focus + step).coerceIn(0, films.size - 1)
        if (next != focus) select(next)
    }

    private fun message(text: String) {
        films = emptyList()
        row.removeAllViews()
        eyebrow.text = ""
        title.text = text
        metaRow.visibility = View.GONE
        why.visibility = View.GONE
        hint.visibility = View.GONE
        refresh.visibility = View.GONE
        showBackdrop(null)
    }

    private fun fillRow() {
        row.removeAllViews()
        films.forEach { film ->
            val image = ImageView(context).apply {
                scaleType = ImageView.ScaleType.CENTER_CROP
                setBackgroundColor(0xFF1B1E25.toInt())
                clipToOutline = true
                outlineProvider = object : ViewOutlineProvider() {
                    override fun getOutline(view: View, outline: Outline) =
                        outline.setRoundRect(0, 0, view.width, view.height, dp(8).toFloat())
                }
            }
            images.load(film.poster, dp(POSTER_FOCUSED_W)) { image.setImageBitmap(it) }
            row.addView(FrameLayout(context).apply {
                setPadding(dp(OUTLINE), dp(OUTLINE), dp(OUTLINE), dp(OUTLINE))
                addView(image, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            })
        }
        hint.visibility = View.VISIBLE
    }

    private fun select(index: Int) {
        focus = index
        for (i in 0 until row.childCount) {
            val item = row.getChildAt(i)
            val on = i == index
            val w = dp(if (on) POSTER_FOCUSED_W else POSTER_W) + 2 * dp(OUTLINE)
            item.layoutParams = LinearLayout.LayoutParams(w, w * 3 / 2).apply { marginEnd = dp(10) }
            item.alpha = if (on) 1f else 0.78f
            item.background = if (on) GradientDrawable().apply {
                cornerRadius = dp(12).toFloat()
                setStroke(dp(3), Color.WHITE)
            } else null
        }
        scroll.post(::keepFocusVisible)
        val film = films[index]
        showBackdrop(film.backdrop ?: film.poster, fallback = film.poster)
        details.animate().cancel()
        details.animate().alpha(0f).setDuration(FADE_MS / 2).withEndAction {
            bind(films.getOrNull(focus))
            details.animate().alpha(1f).setDuration(FADE_MS / 2)
        }
    }

    private fun bind(film: Film?) {
        if (film == null) return
        title.text = film.title
        meta.text = Meta.line(film)
        pill.text = Meta.service(film.service)
        metaRow.visibility = View.VISIBLE
        why.text = film.why
        why.visibility = if (film.why.isBlank()) View.GONE else View.VISIBLE
    }

    private fun keepFocusVisible() {
        val item = row.getChildAt(focus) ?: return
        val pad = dp(48)
        val x = scroll.scrollX
        when {
            item.left - pad < x -> scroll.smoothScrollTo(maxOf(0, item.left - pad), 0)
            item.right + pad > x + scroll.width -> scroll.smoothScrollTo(item.right + pad - scroll.width, 0)
        }
    }

    private fun showBackdrop(url: String?, fallback: String? = null) {
        wantedBackdrop = url
        images.load(url, BACKDROP_W, BLUR_PX) { bitmap ->
            if (url != wantedBackdrop) return@load
            if (bitmap == null && fallback != null && fallback != url) showBackdrop(fallback) else crossFade(bitmap)
        }
    }

    // backB lies over backA: fading it in or out cross-fades between the two images.
    private fun crossFade(bitmap: Bitmap?) {
        backB.animate().cancel()
        showingB = !showingB
        (if (showingB) backB else backA).setImageBitmap(bitmap)
        backB.animate().alpha(if (showingB) 1f else 0f).setDuration(FADE_MS)
    }

    private fun backdrop() = ImageView(context).apply {
        scaleType = ImageView.ScaleType.CENTER_CROP
        scaleX = 1.15f
        scaleY = 1.15f
        // The home screen's backdrop filter; the mockup's brightness(.45) read too dark on the TV.
        colorFilter = ColorMatrixColorFilter(ColorMatrix().apply { setSaturation(1.4f) })
    }

    private fun text(sp: Float, color: Int, bold: Boolean = false) = TextView(context).apply {
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        setTextColor(color)
        includeFontPadding = false
        if (bold) typeface = Typeface.DEFAULT_BOLD
    }

    private fun dp(v: Int) = (v * context.resources.displayMetrics.density).toInt()

    companion object {
        const val POSTER_W = 112
        const val POSTER_FOCUSED_W = 140
        const val OUTLINE = 5 // 3 dp white line + 2 dp gap, as the mockup's outline-offset
        // Blurred at 180 px wide, then scaled up to the screen: radius 5 is about the mockup's blur(18px).
        const val BACKDROP_W = 180
        const val BLUR_PX = 5
        const val FADE_MS = 180L
    }
}
