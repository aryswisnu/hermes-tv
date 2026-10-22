package hermestv.launcher

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.FrameLayout

/** One camera's live main stream full screen, the first unless [EXTRA_CAMERA] names another. Back returns to where it was opened from. */
class CameraActivity : Activity() {
    private lateinit var surface: SurfaceView
    private lateinit var message: View
    private var player: CameraPlayer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        CameraPlayer.quiet()
        val dp = resources.displayMetrics.density
        surface = SurfaceView(this).apply { keepScreenOn = true }
        message = CameraPlayer.message(this)
        setContentView(FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(surface, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            addView(message, FrameLayout.LayoutParams(WRAP_CONTENT, (CameraPlayer.MESSAGE_DP * dp).toInt(), Gravity.CENTER))
        })
    }

    override fun onStart() {
        super.onStart()
        play()
    }

    // singleTop: a new intent can name another camera while one shows here.
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (player != null) play()
    }

    override fun onStop() {
        player?.stop()
        player = null
        super.onStop()
    }

    private fun play() {
        player?.stop()
        val camera = Cameras.ALL.getOrElse(intent.getIntExtra(EXTRA_CAMERA, 0)) { Cameras.ALL[0] }
        player = CameraPlayer(surface, message, camera.full, "${camera.name} ${if (camera.full == camera.main) "main" else "sub"}").apply { play() }
    }

    companion object {
        /** The index in [Cameras.ALL] of the camera to show. */
        const val EXTRA_CAMERA = "camera"
    }
}
