package hermestv.launcher

import android.view.KeyEvent.KEYCODE_DPAD_DOWN
import android.view.KeyEvent.KEYCODE_DPAD_LEFT
import android.view.KeyEvent.KEYCODE_DPAD_RIGHT
import android.view.KeyEvent.KEYCODE_DPAD_UP
import android.view.KeyEvent.KEYCODE_VOLUME_UP
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CamerasTest {
    // A path some cameras rewrite between main and sub: the app must play the URLs as configured.
    private val main = "rtsp://user:code@192.0.2.10:554/ch1/main"
    private val sub = "rtsp://user:code@192.0.2.10:554/ch1/sub"
    private val arrows = listOf(KEYCODE_DPAD_LEFT, KEYCODE_DPAD_RIGHT, KEYCODE_DPAD_UP, KEYCODE_DPAD_DOWN)

    private fun cameras(vararg config: Pair<String, String>) = Cameras.of(config.toMap())

    private fun cells(cameras: List<Camera>) = (0..3).map { Cameras.cell(it, cameras) }

    @Test
    fun oneCameraFillsFirstCellAndInfoIsLast() {
        val one = cameras("CAMERA_1_URL" to main, "CAMERA_1_NAME" to "Porch")
        assertEquals(3, Cameras.info(one.size))
        assertEquals(listOf(Cameras.Cell.Live(one[0]), Cameras.Cell.Empty, Cameras.Cell.Empty, Cameras.Cell.Info), cells(one))
    }

    @Test
    fun gapInNumbersIsSkipped() {
        // Camera 2 has a name but no URL: it is not configured.
        val got = cameras(
            "CAMERA_1_URL" to main, "CAMERA_1_NAME" to "Porch",
            "CAMERA_2_NAME" to "Stairs",
            "CAMERA_3_URL" to sub, "CAMERA_3_NAME" to "Garage",
        )
        assertEquals(2, got.size)
        assertEquals(listOf("Porch", "Garage"), got.map { it.name })
        assertEquals(listOf(main, sub), got.map { it.main })
        assertEquals(listOf(Cameras.Cell.Live(got[0]), Cameras.Cell.Live(got[1]), Cameras.Cell.Empty, Cameras.Cell.Info), cells(got))
    }

    @Test
    fun fourCamerasHaveNoInfoCell() {
        // A fifth camera is not read: the grid has four cells.
        val four = cameras(*(1..5).map { "CAMERA_${it}_URL" to main }.toTypedArray())
        assertEquals(4, four.size)
        assertNull(Cameras.info(four.size))
        assertEquals(four.map { Cameras.Cell.Live(it) }, cells(four))
        val three = four.take(3)
        assertEquals(3, Cameras.info(three.size))
        assertEquals(Cameras.Cell.Info, Cameras.cell(3, three))
    }

    @Test
    fun moveNeverLandsOnEmptyCell() {
        // One camera: every arrow holds.
        for (key in arrows) assertEquals(0, Cameras.move(0, key, 1))
        // Two cameras: the bottom row is empty and info.
        assertEquals(1, Cameras.move(0, KEYCODE_DPAD_RIGHT, 2))
        assertEquals(0, Cameras.move(1, KEYCODE_DPAD_LEFT, 2))
        assertEquals(0, Cameras.move(0, KEYCODE_DPAD_DOWN, 2))
        assertEquals(1, Cameras.move(1, KEYCODE_DPAD_DOWN, 2))
        // Three cameras: the info cell never takes the focus.
        assertEquals(2, Cameras.move(0, KEYCODE_DPAD_DOWN, 3))
        assertEquals(0, Cameras.move(2, KEYCODE_DPAD_UP, 3))
        assertEquals(1, Cameras.move(1, KEYCODE_DPAD_DOWN, 3))
        assertEquals(2, Cameras.move(2, KEYCODE_DPAD_RIGHT, 3))
        // Four cameras: the bottom right cell is a camera.
        assertEquals(3, Cameras.move(1, KEYCODE_DPAD_DOWN, 4))
        assertEquals(3, Cameras.move(2, KEYCODE_DPAD_RIGHT, 4))
        assertEquals(2, Cameras.move(3, KEYCODE_DPAD_LEFT, 4))
        // The edges hold; other keys do not move.
        assertEquals(0, Cameras.move(0, KEYCODE_DPAD_LEFT, 4))
        assertEquals(0, Cameras.move(0, KEYCODE_DPAD_UP, 4))
        assertEquals(3, Cameras.move(3, KEYCODE_DPAD_RIGHT, 4))
        assertEquals(3, Cameras.move(3, KEYCODE_DPAD_DOWN, 4))
        assertEquals(1, Cameras.move(1, KEYCODE_VOLUME_UP, 4))
    }

    @Test
    fun gridUsesSubUrlWhenSet() {
        val (withSub, mainOnly) = cameras("CAMERA_1_URL" to main, "CAMERA_1_SUB_URL" to sub, "CAMERA_2_URL" to main)
        assertEquals(sub, withSub.grid)
        assertEquals(main, mainOnly.grid)
        // Full screen keeps the main stream.
        assertEquals(main, withSub.full)
        assertEquals(main, mainOnly.full)
    }

    @Test
    fun fullUsesSubWhenSubOnly() {
        val (subOnly, both, noSub) = cameras(
            "CAMERA_1_URL" to main, "CAMERA_1_SUB_URL" to sub, "CAMERA_1_FULL" to "sub",
            "CAMERA_2_URL" to main, "CAMERA_2_SUB_URL" to sub, "CAMERA_2_FULL" to "main",
            "CAMERA_3_URL" to main, "CAMERA_3_FULL" to "sub",
        )
        assertEquals(sub, subOnly.full)
        assertEquals(main, both.full)
        // FULL=sub without a sub URL plays the main stream: there is no other.
        assertEquals(main, noSub.full)
    }

    @Test
    fun missingNameDefaultsToCameraN() {
        // n is the number in camera.properties, not the position in the grid.
        val got = cameras("CAMERA_2_URL" to main, "CAMERA_4_URL" to main, "CAMERA_4_NAME" to "Garage")
        assertEquals(listOf("Camera 2", "Garage"), got.map { it.name })
    }
}
