package hermestv.launcher

import android.app.Activity
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.KeyEvent
import android.widget.Toast
import java.time.ZonedDateTime
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

// The Hermes TV tile: the film picker. ◀ ▶ choose, OK opens the film, Back exits.
class MainActivity : Activity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var io: ExecutorService
    private lateinit var images: ImageLoader
    private lateinit var screen: PickerScreen
    private var state: PickerState? = null
    private lateinit var files: PicksFile
    private val client = PicksClient()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        io = Executors.newSingleThreadExecutor()
        images = ImageLoader(this)
        files = PicksFile(filesDir)
        screen = PickerScreen(this, images)
        setContentView(screen.root)
    }

    override fun onStart() {
        super.onStart()
        val timer = Timer { ms, task ->
            main.postDelayed(task, ms)
            Cancel { main.removeCallbacks(task) }
        }
        val slot = Slot.at(ZonedDateTime.now(Clock.zone()))
        state = PickerState(slot, files.local(slot, "films"), { client.picks() }, files::save, timer, io) { view ->
            main.post { screen.render(view) }
        }.also { it.start() }
    }

    override fun onStop() {
        state?.stop()
        state = null
        super.onStop()
    }

    override fun onDestroy() {
        io.shutdownNow()
        images.close()
        super.onDestroy()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        when (keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT -> screen.move(-1)
            KeyEvent.KEYCODE_DPAD_RIGHT -> screen.move(1)
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_BUTTON_A ->
                if (event.repeatCount == 0) open()
            else -> return super.onKeyDown(keyCode, event)
        }
        return true
    }

    private fun open() {
        val film = screen.focused() ?: return
        if (!Actions.open(film, this)) {
            Toast.makeText(this, "Cannot open ${Meta.service(film.service)}", Toast.LENGTH_LONG).show()
            return
        }
        io.execute {
            try {
                client.opened(film)
            } catch (e: Exception) {
                Log.w(Actions.TAG, "opened not sent: $e")
            }
        }
    }
}
