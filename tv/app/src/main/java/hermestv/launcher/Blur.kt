package hermestv.launcher

/** CPU blur for the small backdrop bitmap; a GPU RenderEffect needs full-screen layers the TV cannot spare. */
object Blur {
    /** Three box passes per direction, close to a Gaussian with sigma about radius. In place; the result is opaque. */
    fun box(px: IntArray, w: Int, h: Int, radius: Int) {
        val tmp = IntArray(px.size)
        repeat(3) {
            pass(px, tmp, n = w, lines = h, lineStep = w, step = 1, radius = radius)
            pass(tmp, px, n = h, lines = w, lineStep = 1, step = w, radius = radius)
        }
    }

    private fun pass(src: IntArray, dst: IntArray, n: Int, lines: Int, lineStep: Int, step: Int, radius: Int) {
        val div = 2 * radius + 1
        for (line in 0 until lines) {
            val base = line * lineStep
            fun at(i: Int) = src[base + i.coerceIn(0, n - 1) * step]
            var r = 0
            var g = 0
            var b = 0
            for (k in -radius..radius) {
                val c = at(k)
                r += c shr 16 and 0xFF
                g += c shr 8 and 0xFF
                b += c and 0xFF
            }
            for (i in 0 until n) {
                dst[base + i * step] = (0xFF shl 24) or ((r + div / 2) / div shl 16) or
                    ((g + div / 2) / div shl 8) or ((b + div / 2) / div)
                val out = at(i - radius)
                val inn = at(i + radius + 1)
                r += (inn shr 16 and 0xFF) - (out shr 16 and 0xFF)
                g += (inn shr 8 and 0xFF) - (out shr 8 and 0xFF)
                b += (inn and 0xFF) - (out and 0xFF)
            }
        }
    }
}
