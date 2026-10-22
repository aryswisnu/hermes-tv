package hermestv.launcher

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.SurfaceView
import android.view.View
import android.widget.TextView
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.rtsp.RtspMediaSource

/**
 * One camera stream [url] on [surface], silent. [message] shows while the camera is not reachable, and a failed
 * stream retries every 10 s. [label] names it in the log.
 */
@OptIn(UnstableApi::class)
class CameraPlayer(private val surface: SurfaceView, private val message: View, private val url: String, private val label: String) {
    private val main = Handler(Looper.getMainLooper())
    private var player: ExoPlayer? = null
    private val retry = Runnable { play() }

    fun play() {
        stop()
        // TCP: RTP over UDP loses packets on the TV's Wi-Fi and the HEVC picture breaks up.
        val source = RtspMediaSource.Factory().setForceUseRtpTcp(true).createMediaSource(MediaItem.fromUri(url))
        player = ExoPlayer.Builder(surface.context).build().apply {
            setVideoSurfaceView(surface)
            // The live view is silent; the audio track is not even decoded.
            trackSelectionParameters = trackSelectionParameters.buildUpon().setTrackTypeDisabled(C.TRACK_TYPE_AUDIO, true).build()
            addListener(object : Player.Listener {
                override fun onPlaybackStateChanged(state: Int) {
                    Log.i(Actions.TAG, "camera state $state ($label)")
                    if (state == Player.STATE_READY) message.visibility = View.GONE
                    // A live camera has no end: the server closed the session, so start it again.
                    if (state == Player.STATE_ENDED) restart()
                }

                override fun onRenderedFirstFrame() = Log.i(Actions.TAG, "camera first frame ($label)").let {}

                override fun onPlayerError(error: PlaybackException) = failed(error)
            })
            setMediaSource(source)
            playWhenReady = true
            prepare()
        }
    }

    fun stop() {
        main.removeCallbacks(retry)
        player?.release()
        player = null
    }

    private fun failed(error: PlaybackException) {
        // The code name only: the message and the cause may hold the URL.
        Log.w(Actions.TAG, "camera failed: ${error.errorCodeName} ($label)")
        restart()
    }

    private fun restart() {
        stop()
        message.visibility = View.VISIBLE
        main.postDelayed(retry, RETRY_MS)
    }

    companion object {
        private const val RETRY_MS = 10_000L

        /** media3 logs its errors with their causes, which may hold the camera URL and its password. */
        fun quiet() = androidx.media3.common.util.Log.setLogLevel(androidx.media3.common.util.Log.LOG_LEVEL_OFF)

        /** Height of [message] in dp. */
        const val MESSAGE_DP = 48

        /** The hidden "Camera not reachable" glass message. */
        fun message(context: Context): View {
            val dp = context.resources.displayMetrics.density
            return GlassLayout(context, null, 20 * dp, HomeScreen.PANEL).apply {
                gravity = Gravity.CENTER
                setPadding((24 * dp).toInt(), 0, (24 * dp).toInt(), 0)
                visibility = View.GONE
                addView(TextView(context).apply {
                    text = "Camera not reachable"
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 17f)
                    setTextColor(HomeScreen.INK)
                    typeface = HomeScreen.MEDIUM
                })
            }
        }
    }
}
