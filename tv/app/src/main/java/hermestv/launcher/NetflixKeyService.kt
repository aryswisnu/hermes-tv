package hermestv.launcher

import android.accessibilityservice.AccessibilityService
import android.content.ActivityNotFoundException
import android.content.Intent
import android.util.Log
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent

object NetflixKey {
    // The TCL Google TV policy forwards this key to the Google TV home, which is disabled, so nothing opens Netflix.
    const val TCL_NETFLIX = 4062
    // The WeTV button opens HBO Max instead.
    const val TCL_WETV = 4142
    // The YouTube button takes the same disabled Google-TV-home path, so Hermes TV opens YouTube itself.
    const val TCL_YOUTUBE = 4065
    // The channel button opens TCL Channel, which is not installed, so the firmware opens its Play Store page.
    const val TCL_CHANNEL = 4020

    /** The app a remote app button opens, or null for every other key. */
    fun app(keyCode: Int): String? = when (keyCode) {
        TCL_NETFLIX -> Actions.NETFLIX
        TCL_WETV -> Actions.HBO_MAX
        TCL_YOUTUBE -> Actions.YOUTUBE
        else -> null
    }

    /** A remote button that does nothing. */
    fun swallow(keyCode: Int): Boolean = keyCode == TCL_CHANNEL

    fun launch(action: Int, repeatCount: Int): Boolean = action == KeyEvent.ACTION_DOWN && repeatCount == 0
}

/** The remote's app and channel buttons, whatever app is in front. */
class NetflixKeyService : AccessibilityService() {
    override fun onKeyEvent(event: KeyEvent): Boolean {
        if (NetflixKey.swallow(event.keyCode)) return true
        val pkg = NetflixKey.app(event.keyCode) ?: return false
        if (NetflixKey.launch(event.action, event.repeatCount)) open(pkg)
        return true
    }

    private fun open(pkg: String) {
        val intent = packageManager.getLeanbackLaunchIntentForPackage(pkg)
            ?: packageManager.getLaunchIntentForPackage(pkg)
        if (intent == null) {
            Log.w(Actions.TAG, "no launch intent for $pkg")
            return
        }
        start(intent)
    }

    private fun start(intent: Intent) {
        try {
            startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            Log.w(Actions.TAG, "no activity for $intent")
        } catch (e: SecurityException) {
            Log.w(Actions.TAG, "not allowed to start $intent")
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {}

    override fun onInterrupt() {}
}
