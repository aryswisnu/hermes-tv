package hermestv.launcher

/**
 * What the home screen keeps in memory. Line i sits at d = i − pos (0 is the top, 1 peeks below); it is in view
 * while −1 < d < 2 and holds no images out of view. A card on screen holds its small image, and its big image at
 * slot −1 < s < 2 in the focused row (a slide never reloads it) or −1 < s < 1 in another row (its first card
 * grows big when ▲ ▼ focus the row).
 */
object InView {
    /** The images a card holds: its small one and its big one. */
    data class Images(val small: Boolean, val big: Boolean)

    val NONE = Images(small = false, big = false)

    fun line(d: Float) = d > -1f && d < 2f

    /** [s] is the card's slot, null when it is off screen. */
    fun big(s: Float?, focused: Boolean) = s != null && s > -1f && s < if (focused) 2f else 1f

    /** [held] is false while the card's line holds no images (see [Hold]). */
    fun images(held: Boolean, s: Float?, focused: Boolean) = if (!held || s == null) NONE else Images(small = true, big = big(s, focused))

    /** The wait before the next try after the card's [failed]th failed load of an image; null: no more tries. */
    fun retryMs(failed: Int): Long? = if (failed in 1..RETRIES) RETRY_MS * failed else null

    const val RETRIES = 3
    const val RETRY_MS = 3_000L
}

/** Whether the lines in view hold images: not from [release] (the home screen went to the background) to the next [render]. */
class Hold {
    var released = false
        private set

    fun release() {
        released = true
    }

    fun render() {
        released = false
    }

    fun line(d: Float) = !released && InView.line(d)
}
