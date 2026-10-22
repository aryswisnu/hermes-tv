package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BlurTest {
    private fun gray(v: Int) = (0xFF shl 24) or (v shl 16) or (v shl 8) or v

    @Test
    fun flatImageStaysFlat() {
        val px = IntArray(20 * 10) { gray(120) }
        Blur.box(px, 20, 10, radius = 3)
        assertTrue(px.all { it == gray(120) })
    }

    @Test
    fun brightPointSpreadsToItsNeighbours() {
        val w = 31
        val px = IntArray(w * w) { gray(0) }
        px[15 * w + 15] = gray(255)
        Blur.box(px, w, w, radius = 2)
        val center = px[15 * w + 15] and 0xFF
        val near = px[15 * w + 17] and 0xFF
        val far = px[15 * w + 30] and 0xFF
        assertTrue("center $center", center in 1..254)
        assertTrue("near $near", near in 1 until center)
        assertEquals(0, far)
        // Opaque stays opaque.
        assertTrue(px.all { it ushr 24 == 0xFF })
    }
}
