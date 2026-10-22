package hermestv.launcher

import android.view.KeyEvent.ACTION_DOWN
import android.view.KeyEvent.ACTION_UP
import android.view.KeyEvent.KEYCODE_BUTTON_A
import android.view.KeyEvent.KEYCODE_DPAD_CENTER
import android.view.KeyEvent.KEYCODE_ENTER
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NetflixKeyTest {
    @Test
    fun netflixButtonOpensNetflix() {
        assertEquals(Actions.NETFLIX, NetflixKey.app(4062))
    }

    @Test
    fun weTvButtonOpensHboMax() {
        // The WeTV button opens HBO Max instead.
        assertEquals(Actions.HBO_MAX, NetflixKey.app(4142))
    }

    @Test
    fun youTubeButtonOpensYouTube() {
        assertEquals(Actions.YOUTUBE, NetflixKey.app(4065))
    }

    @Test
    fun channelButtonDoesNothing() {
        // The firmware opens the Play Store page of the missing TCL Channel app.
        assertTrue(NetflixKey.swallow(4020))
        assertNull(NetflixKey.app(4020))
    }

    @Test
    fun appOpensOnlyOnTheFirstDown() {
        assertTrue(NetflixKey.launch(ACTION_DOWN, repeatCount = 0))
        assertFalse(NetflixKey.launch(ACTION_DOWN, repeatCount = 1))
        assertFalse(NetflixKey.launch(ACTION_UP, repeatCount = 0))
    }

    @Test
    fun everyOtherKeyPassesThroughUntouched() {
        // D-pad, OK, back, home, media, volume and mute, TV input.
        for (code in listOf(19, 20, 21, 22, 23, 4, 3, 85, 126, 127, 24, 25, 164, 176)) {
            assertNull("key $code", NetflixKey.app(code))
            assertFalse("key $code", NetflixKey.swallow(code))
        }
    }

    @Test
    fun okKeyPassesThrough() {
        // OK belongs to the app in front: the service takes only the app and channel buttons.
        for (code in listOf(KEYCODE_DPAD_CENTER, KEYCODE_ENTER, KEYCODE_BUTTON_A)) {
            assertNull("key $code", NetflixKey.app(code))
            assertFalse("key $code", NetflixKey.swallow(code))
        }
    }
}
