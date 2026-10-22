package hermestv.launcher

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Outline
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.ViewOutlineProvider
import android.view.animation.DecelerateInterpolator
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import java.time.ZonedDateTime
import kotlin.math.abs

/** One dock tile: a white logo or line glyph alone, else the label as a bold word (see [DockApps.word]). */
class Tile(val label: String, val logo: Int?)

/**
 * Hermes TV Home: the focused card blurred behind dark glass, the status pill at the top right with the schedule
 * banner under it, and the lines (films, series, YouTube, the app dock). The focused line is at the top with
 * its focused card big in the left slot; the next line peeks below. [dropped] runs when cards let go of images.
 */
class HomeScreen(private val context: Context, private val images: ImageLoader, private val dropped: () -> Unit) {
    private val backdrop: View = object : View(context) {
        override fun onDraw(canvas: Canvas) = back.draw(canvas, this, backPaint)
    }
    private val back: GlassSource = GlassSource(backdrop, zoom = 1.1f, tint = intArrayOf(0x590A0B0F, 0x9E0A0B0F.toInt()), base = 0xFF16181E.toInt())
    private val backPaint = Paint(Paint.FILTER_BITMAP_FLAG).apply { colorFilter = GlassStyle(1.4f, 1f, 0, 0, 0).filter }
    private var wanted: Film? = null
    private val hold = Hold()

    private val label = text(30f, Color.WHITE, Typeface.DEFAULT_BOLD)
    private val subtitle = text(15f, 0xFFC9CCD3.toInt())

    private val net = Glyph(context)
    private val ram = text(15f, INK, MEDIUM)
    private val weatherIcon = Glyph(context)
    private val temp = text(15f, INK, MEDIUM)
    private val time = text(15f, INK, Typeface.DEFAULT_BOLD)
    private val date = text(15f, INK, MEDIUM)
    private val hermes = ImageView(context)
    private val pill = GlassLayout(context, back, dp(20).toFloat(), PANEL).apply {
        gravity = Gravity.CENTER_VERTICAL
        setPadding(dp(16), 0, dp(16), 0)
        addView(net, LinearLayout.LayoutParams(dp(18), dp(18)))
        addView(ram, gap(18))
        addView(weatherIcon, LinearLayout.LayoutParams(dp(18), dp(18)).apply { marginStart = dp(18) })
        addView(temp, gap(6))
        addView(time, gap(18))
        addView(date, gap(6))
        addView(hermes, LinearLayout.LayoutParams(dp(20), dp(20)).apply { marginStart = dp(16) })
    }

    private val bannerText = text(15f, INK, MEDIUM).apply {
        maxLines = 1
        ellipsize = TextUtils.TruncateAt.END
    }
    private val banner = GlassLayout(context, back, dp(17).toFloat(), PANEL).apply {
        gravity = Gravity.CENTER_VERTICAL
        setPadding(dp(16), 0, dp(16), 0)
        visibility = View.GONE
        addView(bannerText)
    }

    private val tiles = LinearLayout(context)
    // The tiles slide out to the screen edges; the padding keeps the focused one inside the margins.
    private val dockScroll = HorizontalScrollView(context).apply {
        isHorizontalScrollBarEnabled = false
        isFocusable = false
        clipToPadding = false
        setPadding(dp(LEFT - RING_PAD), 0, dp(RIGHT - RING_PAD), 0)
        addView(tiles)
        setOnScrollChangeListener { _, _, _, _, _ -> dockMoved() }
    }

    private val lanes = Line.entries.associateWith { Lane(it) }
    private val lines = Lines()

    private val menuTitle = text(14f, 0xFFC9CCD3.toInt()).apply {
        maxLines = 1
        ellipsize = TextUtils.TruncateAt.END
    }
    private val menuList = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
    private val menu = FrameLayout(context).apply {
        setBackgroundColor(0x80000000.toInt())
        visibility = View.GONE
        addView(GlassLayout(context, back, dp(20).toFloat(), PANEL).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(10), dp(14), dp(10), dp(10))
            addView(menuTitle, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(dp(16), 0, dp(16), dp(6)) })
            addView(menuList)
        }, FrameLayout.LayoutParams(dp(360), WRAP_CONTENT, Gravity.CENTER))
    }

    val root: View = FrameLayout(context).apply {
        addView(backdrop, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(label)
            addView(subtitle, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { topMargin = dp(6) })
        }, FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply {
            leftMargin = dp(LEFT)
            topMargin = dp(32)
        })
        addView(lines, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        addView(pill, FrameLayout.LayoutParams(WRAP_CONTENT, dp(40), Gravity.TOP or Gravity.END).apply {
            topMargin = dp(28)
            rightMargin = dp(RIGHT)
        })
        addView(banner, FrameLayout.LayoutParams(WRAP_CONTENT, dp(34), Gravity.TOP or Gravity.END).apply {
            topMargin = dp(78)
            rightMargin = dp(RIGHT)
        })
        addView(menu, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
    }

    init {
        weatherIcon.visibility = View.GONE
        temp.visibility = View.GONE
        setHermes(HermesState.ON)
    }

    fun setLabel(text: String) {
        label.text = text
    }

    fun setSubtitle(text: String) {
        subtitle.text = text
    }

    /**
     * Draws the lines: rebuilds the cards of the rows in [refill], slides (or jumps, when not [animate]) each
     * row to its focus and the focused line to the top.
     */
    fun render(rows: HomeRows, refill: Collection<Line> = emptyList(), animate: Boolean = true) {
        val restore = hold.released
        hold.render()
        for (line in HomeRows.ROWS) {
            val row = rows.row(line)
            val cards = lanes.getValue(line).cards!!
            when {
                line in refill -> cards.fill(row.items, row.focus)
                animate -> cards.slideTo(row.focus)
                else -> cards.jumpTo(row.focus)
            }
            cards.focused = rows.focus == line
        }
        lines.set(rows.lines, rows.index, animate)
        when (val item = if (rows.focus == Line.DOCK) null else rows.row(rows.focus).focused) {
            is RowItem.Film -> showBackdrop(item.film)
            is RowItem.Message -> showBackdrop(null)
            // The dock and "All films ›" keep the last backdrop, which release() dropped.
            RowItem.AllFilms, null -> if (restore) showBackdrop(wanted)
        }
    }

    /**
     * Drops every card image and the backdrop while the home screen is in the background; a running slide or
     * layout pass does not load them again before the next [render].
     */
    fun release() {
        hold.release()
        lanes.values.forEach { it.cards?.onScreen = false }
        back.set(null, null, 0f)
    }

    /** The white outline on dock tile [index]; null when the dock is not focused. */
    fun focusDock(index: Int?) {
        for (i in 0 until tiles.childCount) {
            tiles.getChildAt(i).background = if (i == index) ring(dp(TILE_R + RING_PAD)) else null
        }
        if (index != null) dockScroll.post { keepVisible(index) }
    }

    fun setDock(list: List<Tile>) {
        tiles.removeAllViews()
        list.forEachIndexed { i, t -> tiles.addView(tile(t, i == 0)) }
    }

    fun setStatus(state: NetState, ramText: String) {
        net.net(state)
        ram.text = ramText
    }

    fun setClock(now: ZonedDateTime) {
        time.text = Status.clock(now)
        date.text = Status.date(now)
    }

    /** The Hermes logo: white when Hermes answers, dimmed with a small × when it or the bridge does not, none without Hermes. */
    fun setHermes(state: HermesState) {
        hermes.visibility = if (state == HermesState.NONE) View.GONE else View.VISIBLE
        val online = state == HermesState.ON
        hermes.setImageResource(if (online) R.drawable.hermes_mark else R.drawable.hermes_offline)
        hermes.contentDescription = if (online) "Hermes online" else "Hermes offline"
    }

    /** The schedule banner under the pill; null hides it. */
    fun setBanner(text: String?) {
        banner.visibility = if (text == null) View.GONE else View.VISIBLE
        bannerText.text = text
    }

    /** The long-press menu, centred over a dimmed screen: an optional [title] and the [entries]. */
    fun showMenu(title: String?, entries: List<String>, focus: Int) {
        menuTitle.text = title
        menuTitle.visibility = if (title == null) View.GONE else View.VISIBLE
        menuList.removeAllViews()
        entries.forEach {
            menuList.addView(text(17f, INK, MEDIUM).apply {
                text = it
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(16), 0, dp(16), 0)
            }, LinearLayout.LayoutParams(MATCH_PARENT, dp(44)).apply { topMargin = dp(4) })
        }
        focusMenu(focus)
        menu.visibility = View.VISIBLE
    }

    fun focusMenu(index: Int) {
        for (i in 0 until menuList.childCount) {
            val entry = menuList.getChildAt(i) as TextView
            val on = i == index
            entry.background = if (on) GradientDrawable().apply {
                cornerRadius = dp(12).toFloat()
                setColor(0xE6FFFFFF.toInt())
            } else null
            entry.setTextColor(if (on) 0xFF14161C.toInt() else INK)
        }
    }

    fun hideMenu() {
        menu.visibility = View.GONE
    }

    fun setWeather(weather: Weather?) {
        val on = if (weather == null) View.GONE else View.VISIBLE
        weatherIcon.visibility = on
        temp.visibility = on
        if (weather == null) return
        weatherIcon.weather(Weather.icon(weather.code), weather.isDay)
        temp.text = weather.text
    }

    private fun showBackdrop(film: Film?) {
        wanted = film
        if (film == null) return back.fadeTo(null, FADE_MS)
        load(wide(film), BACKDROP_W, BLUR_PX) { if (wanted === film && !hold.released) back.fadeTo(it, FADE_MS) }
    }

    private fun keepVisible(index: Int) {
        val item = tiles.getChildAt(index) ?: return
        val x = dockScroll.scrollX
        val w = dockScroll.width - dockScroll.paddingLeft - dockScroll.paddingRight
        when {
            item.left < x -> dockScroll.smoothScrollTo(item.left, 0)
            item.right > x + w -> dockScroll.smoothScrollTo(item.right - w, 0)
        }
    }

    // Tile glass shows the backdrop under it, which changes when the dock scrolls or its line moves.
    private fun dockMoved() {
        for (i in 0 until tiles.childCount) (tiles.getChildAt(i) as ViewGroup).getChildAt(0).invalidate()
    }

    private fun tile(t: Tile, first: Boolean): View {
        val face = GlassLayout(context, back, dp(TILE_R).toFloat(), PANEL).apply {
            gravity = Gravity.CENTER
            contentDescription = t.label
            val word = DockApps.word(t)
            if (word != null) {
                addView(text(17f, Color.WHITE, Typeface.DEFAULT_BOLD).apply {
                    text = word
                    gravity = Gravity.CENTER
                    maxLines = 2
                    ellipsize = TextUtils.TruncateAt.END
                    setLineSpacing(0f, 1.1f)
                    setPadding(dp(10), 0, dp(10), 0)
                })
            } else {
                val size = dp(DockApps.logoHeight(t.logo!!))
                addView(ImageView(context).apply { setImageResource(t.logo) }, LinearLayout.LayoutParams(size, size))
            }
        }
        return FrameLayout(context).apply {
            setPadding(dp(RING_PAD), dp(RING_PAD), dp(RING_PAD), dp(RING_PAD))
            addView(face, FrameLayout.LayoutParams(dp(TILE_W), dp(TILE_H)))
            if (!first) layoutParams = LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { marginStart = dp(TILE_GAP - 2 * RING_PAD) }
        }
    }

    // Loads the first url that gives an image.
    private fun load(urls: List<String>, width: Int, blur: Int, done: (Bitmap?) -> Unit) {
        val url = urls.firstOrNull() ?: return done(null)
        images.load(url, width, blur) { if (it != null || urls.size == 1) done(it) else load(urls.drop(1), width, blur, done) }
    }

    private fun wide(f: Film) = listOfNotNull(f.backdrop, f.poster).distinct()

    /** One line: its small label, then its cards or the dock. */
    private inner class Lane(line: Line) : FrameLayout(context) {
        val cards: CardRow? = if (line == Line.DOCK) null else CardRow(wide = line != Line.FILMS)

        init {
            addView(text(13f, 0xFFE6E7EA.toInt(), SEMIBOLD).apply { text = line.label },
                FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { leftMargin = dp(LEFT) })
            if (cards != null) {
                addView(cards, FrameLayout.LayoutParams(MATCH_PARENT, dp(CARD_H + 2 * RING)).apply { topMargin = dp(LABEL_H) })
            } else {
                addView(dockScroll, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(LABEL_H + RING) })
            }
        }

        // Glass that shows the backdrop draws what lies under it, which changes when the line moves.
        fun moved() {
            if (cards == null) dockMoved() else cards.moved()
        }
    }

    /**
     * The lines, in order. Line i sits at d = i - pos: d = 0 is the top position, d = 1 peeks below, so ▲ ▼
     * slide every line and the focused row's first card grows big as it reaches the top.
     */
    private inner class Lines : ViewGroup(context) {
        private var order: List<Line> = emptyList()
        private var pos = 0f
        private var slide: ValueAnimator? = null

        init {
            lanes.values.forEach { addView(it) }
        }

        /** Shows [shown] with line [index] at the top; a changed set of lines jumps instead of sliding. */
        fun set(shown: List<Line>, index: Int, animate: Boolean) {
            val target = index.toFloat()
            val jump = !animate || shown != order
            order = shown
            slide?.cancel()
            if (jump || pos == target) {
                pos = target
                place()
                return
            }
            slide = ValueAnimator.ofFloat(pos, target).setDuration(SLIDE_MS).apply {
                interpolator = DecelerateInterpolator()
                addUpdateListener {
                    pos = it.animatedValue as Float
                    place()
                }
                start()
            }
        }

        override fun onMeasure(widthSpec: Int, heightSpec: Int) =
            setMeasuredDimension(MeasureSpec.getSize(widthSpec), MeasureSpec.getSize(heightSpec))

        override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) = place()

        private fun place() {
            if (width == 0) return
            val h = dp(LABEL_H + CARD_H + 2 * RING)
            lanes.forEach { (line, lane) ->
                val i = order.indexOf(line)
                val d = i - pos
                if (i < 0 || !InView.line(d)) {
                    lane.cards?.onScreen = false
                    lane.visibility = View.INVISIBLE
                    return@forEach
                }
                lane.visibility = View.VISIBLE
                // Laid out once at the top position; a slide only moves it.
                if (lane.width != width || lane.isLayoutRequested) {
                    lane.measure(MeasureSpec.makeMeasureSpec(width, MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(h, MeasureSpec.EXACTLY))
                    lane.layout(0, dp(TOP_Y), width, dp(TOP_Y) + h)
                }
                lane.translationY = d * dp(PEEK_Y - TOP_Y)
                lane.alpha = if (d < 0) 1 + d else 1f
                // A line leaving at the top stays big; the line coming up grows.
                lane.cards?.lift = if (d < 0) 1f else (1 - d).coerceIn(0f, 1f)
                // Set once the lane is laid out and lifted, so this pass loads the images it shows.
                lane.cards?.onScreen = hold.line(d)
                lane.moved()
            }
        }
    }

    /**
     * A row of cards. Card i sits at slot s = i - pos: s = 0 is the big card, s = 1, 2 the small ones; a card
     * between two slots is between their frames, so ◀ ▶ slide the row and the next card grows into the big slot.
     * [lift] is 1 on the top line and 0 when the row peeks below, where every card is small. [wide] rows
     * (series, videos) have 16:9 small cards; film posters keep the full height.
     */
    private inner class CardRow(private val wide: Boolean) : ViewGroup(context) {
        private var items: List<Card> = emptyList()
        private var pos = 0f
        private var slide: ValueAnimator? = null
        private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            color = Color.WHITE
            strokeWidth = dp(3).toFloat()
        }
        private val ringRect = RectF()
        private var bigW = 0
        private var bigH = 0
        /** The focused row: it has the white outline and keeps more big images (see [InView]). */
        var focused = true
            set(value) {
                if (value == field) return
                field = value
                if (onScreen) place() else invalidate()
            }

        /** False while the line is out of view: its cards then hold no images. */
        var onScreen = false
            set(value) {
                if (value == field) return
                field = value
                place()
            }
        var lift = 0f
            set(value) {
                if (value == field) return
                field = value
                place()
            }

        init {
            setWillNotDraw(false)
        }

        fun fill(row: List<RowItem>, focus: Int) {
            slide?.cancel()
            items.forEach { it.slot(InView.NONE) }
            removeAllViews()
            items = row.map { if (it is RowItem.Film) FilmCard(it.film, wide) else GlassCard(it) }
            items.forEach { addView(it) }
            pos = focus.toFloat()
            place()
        }

        fun jumpTo(focus: Int) {
            slide?.cancel()
            pos = focus.toFloat()
            place()
        }

        fun slideTo(focus: Int) {
            if (pos == focus.toFloat()) return
            slide?.cancel()
            slide = ValueAnimator.ofFloat(pos, focus.toFloat()).setDuration(SLIDE_MS).apply {
                interpolator = DecelerateInterpolator()
                addUpdateListener {
                    pos = it.animatedValue as Float
                    place()
                }
                start()
            }
        }

        fun moved() = items.forEach { if (it is GlassCard) it.moved() }

        override fun onMeasure(widthSpec: Int, heightSpec: Int) =
            setMeasuredDimension(MeasureSpec.getSize(widthSpec), MeasureSpec.getSize(heightSpec))

        override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) = place()

        private fun place() {
            if (width == 0) return
            val gap = dp(GAP)
            val small = (width - dp(LEFT) - dp(RIGHT) - 2 * gap) * 100 / 435
            val big = width - dp(LEFT) - dp(RIGHT) - 2 * gap - 2 * small
            val smallH = if (wide) small * 9 / 16 else dp(CARD_H)
            bigW = small + ((big - small) * lift).toInt()
            bigH = smallH + ((dp(CARD_H) - smallH) * lift).toInt()
            val end = width - dp(RIGHT)
            items.forEachIndexed { i, card ->
                val s = i - pos
                val x = dp(LEFT) + when {
                    s <= 1f -> s * (bigW + gap)
                    else -> bigW + gap + (s - 1) * (small + gap)
                }.toInt()
                if (!onScreen || s <= -1f || x >= end) {
                    card.visibility = View.INVISIBLE
                    card.slot(InView.NONE)
                    return@forEachIndexed
                }
                card.visibility = View.VISIBLE
                card.slot(InView.images(onScreen, s, focused))
                val w = if (s in 0f..1f) bigW + ((small - bigW) * s).toInt() else if (s < 0) bigW else small
                val h = if (s in 0f..1f) bigH + ((smallH - bigH) * s).toInt() else if (s < 0) bigH else smallH
                card.morph((1 - abs(s)).coerceIn(0f, 1f) * lift)
                card.measure(MeasureSpec.makeMeasureSpec(w, MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(h, MeasureSpec.EXACTLY))
                card.layout(x, dp(RING), x + w, dp(RING) + h)
                // Cards leave on the left and fade out over the last slot on the right.
                card.alpha = if (s < 0) 1 + s else ((end - x - w + small + gap) / (small + gap).toFloat()).coerceIn(0f, 1f)
            }
            invalidate()
        }

        override fun dispatchDraw(canvas: Canvas) {
            super.dispatchDraw(canvas)
            if (!focused || items.isEmpty()) return
            val out = dp(3) + ringPaint.strokeWidth / 2
            ringRect.set(dp(LEFT) - out, dp(RING) - out, dp(LEFT) + bigW + out, dp(RING) + bigH + out)
            canvas.drawRoundRect(ringRect, dp(CARD_R) + out, dp(CARD_R) + out, ringPaint)
        }
    }

    /** A card in a row; [morph] gets 1 in the big slot and 0 in a small one. */
    private abstract inner class Card : FrameLayout(context) {
        private var big: Boolean? = null

        init {
            rounded(dp(CARD_R))
        }

        /** The images the card holds now, from its slot (see [InView.images]). */
        open fun slot(want: InView.Images) = Unit

        open fun morph(bigness: Float) {
            val b = bigness >= 0.5f
            if (b != big) {
                big = b
                // A text change requests a layout, which must not happen inside the row's own layout pass.
                if (isInLayout) post { big?.let(::resize) } else resize(b)
            }
        }

        /** Switches the text between the big and the small card. */
        abstract fun resize(big: Boolean)
    }

    /** A film (poster, small) or a series or video ([wide]: landscape image, small 16:9). */
    private inner class FilmCard(private val film: Film, private val wide: Boolean) : Card() {
        private val poster = image()
        private val wideImage = image().apply {
            alpha = 0f
            visibility = View.INVISIBLE
        }
        private val glass = GlassSource(this)
        private var posterBlur: Bitmap? = null
        private var wideBlur: Bitmap? = null
        private var bigness = -1f
        private var want = InView.NONE
        private var smallLoaded = false
        private var smallRun = 0
        private var smallFails = 0
        private var wideLoaded = false
        private var wideRun = 0
        private var wideFails = 0
        private val title = text(17f, Color.WHITE, SEMIBOLD).apply { ellipsize = TextUtils.TruncateAt.END }
        private val meta = text(13f, 0xD9FFFFFF.toInt()).apply {
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
            text = Meta.card(film)
        }
        private val caption = GlassLayout(context, glass, dp(12).toFloat(), CAPTION).apply {
            orientation = LinearLayout.VERTICAL
            addView(title)
            addView(meta, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { topMargin = dp(3) })
        }
        private val posterUrls = if (wide) wide(film) else listOfNotNull(film.poster, film.backdrop).distinct()

        init {
            setBackgroundColor(0xFF1B1E25.toInt())
            addView(poster, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            addView(wideImage, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            addView(caption, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.BOTTOM))
            title.text = film.title
        }

        override fun slot(want: InView.Images) {
            if (want == this.want) return
            this.want = want
            if (want.small) loadSmall() else dropSmall()
            if (want.big) loadBig() else dropBig()
        }

        // A callback of an older run (the card dropped or loaded the image again since) is ignored.
        private fun loadSmall() {
            if (smallLoaded) return
            smallLoaded = true
            val run = ++smallRun
            load(posterUrls, dp(SMALL_W), 0) {
                if (run != smallRun) return@load
                if (it != null) {
                    poster.show(it)
                } else {
                    smallLoaded = false
                    retry(++smallFails) { if (run == smallRun && want.small) loadSmall() }
                }
            }
            load(posterUrls, POSTER_BLUR_W, POSTER_BLUR_PX) {
                if (run == smallRun) posterBlur = it
                glass.set(posterBlur, wideBlur, bigness.coerceAtLeast(0f))
            }
        }

        private fun dropSmall() {
            smallRun++
            val held = smallLoaded || posterBlur != null
            smallLoaded = false
            if (!held) return
            poster.show(null)
            posterBlur = null
            glass.set(null, wideBlur, bigness.coerceAtLeast(0f))
            dropped()
        }

        private fun loadBig() {
            if (wideLoaded) return
            wideLoaded = true
            val run = ++wideRun
            load(wide(film), dp(BIG_W), 0) {
                if (run != wideRun) return@load
                if (it != null) {
                    wideImage.show(it)
                } else {
                    wideLoaded = false
                    retry(++wideFails) { if (run == wideRun && want.big) loadBig() }
                }
            }
            load(wide(film), BACKDROP_W, BLUR_PX) {
                if (run == wideRun) wideBlur = it
                glass.set(posterBlur, wideBlur, bigness.coerceAtLeast(0f))
            }
        }

        private fun dropBig() {
            wideRun++
            val held = wideLoaded || wideBlur != null
            wideLoaded = false
            if (!held) return
            wideImage.show(null)
            wideBlur = null
            glass.set(posterBlur, null, bigness.coerceAtLeast(0f))
            dropped()
        }

        // A failed load (a network error, a cache file removed while it was read) tries again while the card wants it.
        private fun retry(failed: Int, again: () -> Unit) {
            val ms = InView.retryMs(failed) ?: return
            postDelayed(again, ms)
        }

        override fun morph(bigness: Float) {
            if (bigness == this.bigness) return
            this.bigness = bigness
            wideImage.alpha = bigness
            // At alpha 0 the image still sits in the display list and keeps its GPU texture; invisible, it does not.
            wideImage.visibility = if (bigness > 0f) View.VISIBLE else View.INVISIBLE
            glass.set(posterBlur, wideBlur, bigness)
            super.morph(bigness)
        }

        override fun resize(big: Boolean) {
            meta.visibility = if (big) View.VISIBLE else View.GONE
            title.maxLines = if (big || wide) 1 else 2
            title.setTextSize(TypedValue.COMPLEX_UNIT_SP, if (big) 19f else if (wide) 13f else 15f)
            // A small 16:9 card is short, so its caption is tighter.
            val tight = wide && !big
            caption.setPadding(dp(if (tight) 10 else 14), dp(if (tight) 6 else 10), dp(if (tight) 10 else 14), dp(if (tight) 6 else 10))
            (caption.layoutParams as FrameLayout.LayoutParams).setMargins(dp(if (tight) 6 else 10), 0, dp(if (tight) 6 else 10), dp(if (tight) 6 else 10))
            caption.requestLayout()
        }

        private fun image() = CardImage().apply { scaleType = ImageView.ScaleType.CENTER_CROP }
    }

    // A card image fills its card, so a new bitmap needs no layout pass; one per image would run on each slide.
    private inner class CardImage : ImageView(context) {
        private var quiet = false

        fun show(bitmap: Bitmap?) {
            quiet = true
            setImageBitmap(bitmap)
            quiet = false
        }

        override fun requestLayout() {
            if (!quiet) super.requestLayout()
        }
    }

    /** The message card ("Finding films…") and "All films ›": glass over the backdrop. */
    private inner class GlassCard(item: RowItem) : Card() {
        private val head = text(26f, Color.WHITE, Typeface.DEFAULT_BOLD)
        private val sub = text(15f, 0xFFC9CCD3.toInt()).apply { text = "Every pick for this time of day" }
        private val all = item == RowItem.AllFilms

        init {
            head.text = (item as? RowItem.Message)?.text ?: "All films ›"
            addView(GlassLayout(context, back, dp(CARD_R).toFloat(), PANEL).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.BOTTOM
                setPadding(dp(22), dp(20), dp(22), dp(22))
                addView(head)
                if (all) addView(sub, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { topMargin = dp(6) })
            }, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        }

        override fun morph(bigness: Float) {
            super.morph(bigness)
            moved()
        }

        // The glass shows the backdrop under it, which changes as the card slides.
        fun moved() = getChildAt(0).invalidate()

        override fun resize(big: Boolean) {
            sub.visibility = if (big) View.VISIBLE else View.GONE
            head.setTextSize(TypedValue.COMPLEX_UNIT_SP, if (big) 26f else 19f)
        }
    }

    private fun View.rounded(radius: Int) {
        clipToOutline = true
        outlineProvider = object : ViewOutlineProvider() {
            override fun getOutline(view: View, outline: Outline) = outline.setRoundRect(0, 0, view.width, view.height, radius.toFloat())
        }
    }

    private fun ring(radius: Int) = GradientDrawable().apply {
        cornerRadius = radius.toFloat()
        setStroke(dp(3), Color.WHITE)
    }

    private fun gap(start: Int) = LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { marginStart = dp(start) }

    private fun text(sp: Float, color: Int, face: Typeface = Typeface.DEFAULT) = TextView(context).apply {
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        setTextColor(color)
        includeFontPadding = false
        typeface = face
    }

    private fun dp(v: Int) = (v * context.resources.displayMetrics.density).toInt()

    companion object {
        const val INK = 0xFFF4F5F7.toInt()
        val MEDIUM: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        val SEMIBOLD: Typeface = Typeface.create(Typeface.DEFAULT, 600, false)
        val PANEL = GlassStyle(1.7f, 1f, fill = 0x1FFFFFFF, border = 0x38FFFFFF, top = 0x59FFFFFF)
        val CAPTION = GlassStyle(1.5f, 0.85f, fill = 0x73141618, border = 0x2EFFFFFF, top = 0x40FFFFFF)

        const val LEFT = 48
        const val RIGHT = 38
        const val TOP_Y = 96 // the focused line's label; its cards start LABEL_H + RING below
        const val PEEK_Y = 396
        const val LABEL_H = 22
        const val CARD_H = 248
        const val CARD_R = 16
        const val RING = 8 // room for the 3 dp outline, 3 dp off the card
        const val GAP = 18
        const val BIG_W = 453
        const val SMALL_W = 193
        // Five tiles fit in the margins, as on the Apple TV top row.
        const val TILE_W = 156
        const val TILE_H = 88
        const val TILE_R = 14
        const val TILE_GAP = 18
        const val RING_PAD = 5 // room for the 3 dp outline around a tile
        const val POSTER_BLUR_W = 48
        const val POSTER_BLUR_PX = 2
        const val BACKDROP_W = PickerScreen.BACKDROP_W
        const val BLUR_PX = PickerScreen.BLUR_PX
        const val FADE_MS = 200L
        const val SLIDE_MS = 220L
    }
}
