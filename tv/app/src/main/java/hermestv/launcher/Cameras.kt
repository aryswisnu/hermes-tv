package hermestv.launcher

import android.view.KeyEvent

/** One configured camera: the grid plays [sub] when set, full screen plays [main] unless [subOnly]. */
data class Camera(val name: String, val main: String, val sub: String, val subOnly: Boolean) {
    val grid: String get() = sub.ifEmpty { main }

    /** The full-screen stream: the grid's for a camera whose network cannot carry its main one. */
    val full: String get() = if (subOnly) grid else main
}

/** The Cameras view: up to four cameras in a 2x2 grid, read left to right, top to bottom; with three or fewer the last cell is the info cell. */
object Cameras {
    private const val MAX = 4

    val ALL = of(
        mapOf(
            "CAMERA_1_URL" to BuildConfig.CAMERA_1_URL,
            "CAMERA_1_SUB_URL" to BuildConfig.CAMERA_1_SUB_URL,
            "CAMERA_1_NAME" to BuildConfig.CAMERA_1_NAME,
            "CAMERA_1_FULL" to BuildConfig.CAMERA_1_FULL,
            "CAMERA_2_URL" to BuildConfig.CAMERA_2_URL,
            "CAMERA_2_SUB_URL" to BuildConfig.CAMERA_2_SUB_URL,
            "CAMERA_2_NAME" to BuildConfig.CAMERA_2_NAME,
            "CAMERA_2_FULL" to BuildConfig.CAMERA_2_FULL,
            "CAMERA_3_URL" to BuildConfig.CAMERA_3_URL,
            "CAMERA_3_SUB_URL" to BuildConfig.CAMERA_3_SUB_URL,
            "CAMERA_3_NAME" to BuildConfig.CAMERA_3_NAME,
            "CAMERA_3_FULL" to BuildConfig.CAMERA_3_FULL,
            "CAMERA_4_URL" to BuildConfig.CAMERA_4_URL,
            "CAMERA_4_SUB_URL" to BuildConfig.CAMERA_4_SUB_URL,
            "CAMERA_4_NAME" to BuildConfig.CAMERA_4_NAME,
            "CAMERA_4_FULL" to BuildConfig.CAMERA_4_FULL,
        ),
    )
    val INFO: Int? = info(ALL.size)

    /** What a grid cell shows. */
    sealed interface Cell {
        data class Live(val camera: Camera) : Cell
        object Info : Cell
        object Empty : Cell
    }

    /**
     * The cameras of camera.properties ([config] by key) in n order: one without CAMERA_n_URL is skipped,
     * an empty name is "Camera n", and CAMERA_n_FULL "sub" plays the sub stream full screen too.
     */
    fun of(config: Map<String, String>): List<Camera> = (1..MAX).mapNotNull { n ->
        fun value(key: String) = config["CAMERA_${n}_$key"].orEmpty()
        val main = value("URL")
        if (main.isEmpty()) null else Camera(value("NAME").ifEmpty { "Camera $n" }, main, value("SUB_URL"), value("FULL") == "sub")
    }

    /** The info cell's index for [count] cameras; none when four cameras fill the grid. */
    fun info(count: Int): Int? = if (count < MAX) MAX - 1 else null

    fun cell(i: Int, cameras: List<Camera> = ALL): Cell = when {
        i < cameras.size -> Cell.Live(cameras[i])
        i == info(cameras.size) -> Cell.Info
        else -> Cell.Empty
    }

    /** The cell the D-pad [keyCode] moves to from camera cell [cell]; the focus stays at the edges and on the [count] cameras. */
    fun move(cell: Int, keyCode: Int, count: Int = ALL.size): Int {
        var col = cell % 2
        var row = cell / 2
        when (keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT -> col--
            KeyEvent.KEYCODE_DPAD_RIGHT -> col++
            KeyEvent.KEYCODE_DPAD_UP -> row--
            KeyEvent.KEYCODE_DPAD_DOWN -> row++
            else -> return cell
        }
        val to = row * 2 + col
        return if (col in 0..1 && row in 0..1 && to < count) to else cell
    }
}
