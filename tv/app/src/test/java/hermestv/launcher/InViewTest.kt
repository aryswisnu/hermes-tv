package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InViewTest {
    // Lines 0..3 (films, series, YouTube, dock) with line [pos] at the top.
    private fun shown(pos: Float) = (0..3).filter { InView.line(it - pos) }

    @Test
    fun onlyTheTopAndPeekingLinesAreInView() {
        assertEquals(listOf(0, 1), shown(0f))
        assertEquals(listOf(1, 2), shown(1f))
        assertEquals(listOf(2, 3), shown(2f))
    }

    @Test
    fun aSlidingLineStaysInViewUntilItLeaves() {
        // ▼ from films: films leaves at the top, YouTube comes up from below.
        assertEquals(listOf(0, 1, 2), shown(0.5f))
        assertEquals(listOf(1, 2, 3), shown(1.5f))
    }

    @Test
    fun focusedRowKeepsBigImagesFromSlotMinus1To2() {
        listOf(-0.5f, 0f, 1f, 1.9f).forEach { assertTrue("slot $it", InView.big(it, focused = true)) }
        listOf(-1f, 2f, 3f).forEach { assertFalse("slot $it", InView.big(it, focused = true)) }
        assertFalse(InView.big(null, focused = true))
    }

    @Test
    fun otherRowKeepsOnlyTheFirstCardBig() {
        listOf(-0.5f, 0f, 0.5f).forEach { assertTrue("slot $it", InView.big(it, focused = false)) }
        listOf(-1f, 1f, 2f).forEach { assertFalse("slot $it", InView.big(it, focused = false)) }
        assertFalse(InView.big(null, focused = false))
    }

    @Test
    fun aCardOnScreenHoldsItsImagesOnlyWhileItsLineHoldsThem() {
        assertEquals(InView.Images(small = true, big = true), InView.images(held = true, s = 0f, focused = true))
        assertEquals(InView.Images(small = true, big = false), InView.images(held = true, s = 2f, focused = true))
        assertEquals(InView.NONE, InView.images(held = true, s = null, focused = true))
        assertEquals(InView.NONE, InView.images(held = false, s = 0f, focused = true))
    }

    @Test
    fun releaseDropsEveryImageUntilTheNextRender() {
        val hold = Hold()
        fun topCard() = InView.images(hold.line(0f), 0f, focused = true)
        fun peekCard() = InView.images(hold.line(1f), 0f, focused = false)
        assertEquals(InView.Images(small = true, big = true), topCard())
        hold.release()
        assertEquals(InView.NONE, topCard())
        assertEquals(InView.NONE, peekCard())
        // A slide still running after onStop places the lines again: they stay empty.
        assertFalse(hold.line(0.5f))
        hold.render()
        assertEquals(InView.Images(small = true, big = true), topCard())
        assertEquals(InView.Images(small = true, big = true), peekCard())
        assertFalse(hold.line(2f))
    }

    @Test
    fun aFailedImageTriesAgainAFewTimesWithLongerWaits() {
        assertEquals(listOf(3_000L, 6_000L, 9_000L), (1..3).map { InView.retryMs(it) })
        assertEquals(null, InView.retryMs(4))
        assertEquals(null, InView.retryMs(0))
    }
}
