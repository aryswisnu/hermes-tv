package hermestv.launcher

import android.app.ActivityManager
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

sealed interface NetState {
    data class Wifi(val level: Int) : NetState
    data object Ethernet : NetState
    data object Offline : NetState
}

/** The TV's time zone; the bridge's TIME_ZONE setting should be the same zone. */
object Clock {
    fun zone(): ZoneId = ZoneId.systemDefault()
}

/** The status pill's text and icons. Times show in the zone of the time given. */
object Status {
    private val CLOCK = DateTimeFormatter.ofPattern("HH:mm", Locale.US)
    private val DATE = DateTimeFormatter.ofPattern("EEE d MMM", Locale.US)

    /** `RAM 41% free`: [avail] of [total] bytes, rounded to a whole percent. */
    fun ramText(avail: Long, total: Long): String = "RAM ${Math.round(avail * 100.0 / total)}% free"

    fun clock(now: ZonedDateTime): String = now.format(CLOCK)

    fun date(now: ZonedDateTime): String = now.format(DATE)

    /** level runs 0..maxLevel as WifiManager reports it; the icon has 0..4. */
    fun network(wifi: Boolean, ethernet: Boolean, level: Int, maxLevel: Int): NetState = when {
        ethernet -> NetState.Ethernet
        wifi -> NetState.Wifi(if (maxLevel <= 0) 0 else (level * 4 / maxLevel).coerceIn(0, 4))
        else -> NetState.Offline
    }
}

/** Reads RAM and network state for the status pill. */
class StatusReader(context: Context) {
    private val activity = context.getSystemService(ActivityManager::class.java)
    private val connectivity = context.getSystemService(ConnectivityManager::class.java)
    private val wifi = context.getSystemService(WifiManager::class.java)

    fun ram(): String = ActivityManager.MemoryInfo().also(activity::getMemoryInfo).let { Status.ramText(it.availMem, it.totalMem) }

    @Suppress("DEPRECATION")
    fun network(): NetState {
        val caps = connectivity.getNetworkCapabilities(connectivity.activeNetwork)
        val onWifi = caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        val level = if (onWifi) wifi.calculateSignalLevel(wifi.connectionInfo.rssi) else 0
        return Status.network(onWifi, caps?.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) == true, level, wifi.maxSignalLevel)
    }
}
