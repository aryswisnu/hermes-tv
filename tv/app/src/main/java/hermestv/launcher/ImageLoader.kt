package hermestv.launcher

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.util.Log
import android.util.LruCache
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors

/** Loads posters, backdrops and the bridge's scenery photos: memory LRU, then cacheDir, then HTTP. Callbacks run on the main thread. */
class ImageLoader(context: Context) {
    private val dir = File(context.cacheDir, "img")
    private val main = Handler(Looper.getMainLooper())
    // One thread at background priority: on the TV's 4 small cores, 3 parallel decodes at normal priority slowed the slide frames.
    private val pool = Executors.newFixedThreadPool(1) { r -> Thread { Process.setThreadPriority(Process.THREAD_PRIORITY_BACKGROUND); r.run() } }
    private val memory = object : LruCache<String, Bitmap>(MEMORY_BYTES) {
        override fun sizeOf(key: String, value: Bitmap) = value.allocationByteCount
    }
    private val generation = Generation()

    init {
        pool.execute(::trimDisk)
    }

    /**
     * Calls done with the image scaled down to about [width] px wide, or with null when it cannot be loaded.
     * [blur] is the box-blur radius in px of the scaled image; 0 is no blur.
     */
    fun load(url: String?, width: Int, blur: Int = 0, done: (Bitmap?) -> Unit) {
        // After close() the screen may still render once from a queued main-thread post.
        if (pool.isShutdown) return
        if (url == null) {
            done(null)
            return
        }
        val key = "$width $blur $url"
        memory.get(key)?.let {
            done(it)
            return
        }
        val gen = generation.now()
        pool.execute {
            val bitmap = try {
                decode(file(url), width)?.let { if (blur > 0) blurred(it, blur) else it }
            } catch (e: Exception) {
                Log.w(Actions.TAG, "image failed: $url: $e")
                null
            }
            bitmap?.let { generation.ifStill(gen) { memory.put(key, it) } }
            // Starts the GPU upload now, so the first frame that draws it does not upload it.
            bitmap?.prepareToDraw()
            main.post { done(bitmap) }
        }
    }

    /** Empties the memory cache; the files in cacheDir stay. A decode still running does not fill it again. */
    fun clear() = generation.next { memory.evictAll() }

    fun close() {
        pool.shutdownNow()
        clear()
    }

    private fun file(url: String): File {
        val name = MessageDigest.getInstance("SHA-1").digest(url.toByteArray()).joinToString("") { "%02x".format(it) }
        val file = File(dir, name)
        if (file.exists()) return file.also { it.setLastModified(System.currentTimeMillis()) }
        dir.mkdirs()
        val auth = PicksClient.authFor(url)
        if (auth != null) TailnetRoute.require(URL(url).host)
        val tmp = File.createTempFile(name, ".tmp", dir)
        val c = URL(url).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = TIMEOUT_MS
            c.readTimeout = TIMEOUT_MS
            // A redirect could carry the key to another host, so a bridge request never follows one.
            auth?.let { c.instanceFollowRedirects = false; c.setRequestProperty("Authorization", it) }
            if (c.responseCode != 200) throw IOException("HTTP ${c.responseCode}")
            c.inputStream.use { input -> tmp.outputStream().use { input.copyTo(it) } }
        } catch (e: IOException) {
            tmp.delete()
            throw e
        } finally {
            c.disconnect()
        }
        if (!tmp.renameTo(file)) throw IOException("cannot store $file")
        return file
    }

    private fun decode(file: File, width: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        if (bounds.outWidth <= 0) {
            file.delete()
            return null
        }
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= width) sample *= 2
        val opts = BitmapFactory.Options().apply {
            inSampleSize = sample
            // Posters and backdrops have no alpha: half the memory of ARGB_8888.
            inPreferredConfig = Bitmap.Config.RGB_565
        }
        val bitmap = BitmapFactory.decodeFile(file.path, opts) ?: return null
        if (bitmap.width <= width * 5 / 4) return bitmap
        val scaled = Bitmap.createScaledBitmap(bitmap, width, bitmap.height * width / bitmap.width, true)
        bitmap.recycle()
        return scaled
    }

    private fun blurred(bitmap: Bitmap, radius: Int): Bitmap {
        val w = bitmap.width
        val h = bitmap.height
        val px = IntArray(w * h)
        bitmap.getPixels(px, 0, w, 0, 0, w, h)
        bitmap.recycle()
        Blur.box(px, w, h, radius)
        return Bitmap.createBitmap(px, w, h, Bitmap.Config.ARGB_8888)
    }

    private fun trimDisk() {
        val files = dir.listFiles()?.sortedBy { it.lastModified() } ?: return
        var total = files.sumOf { it.length() }
        for (f in files) {
            if (total <= DISK_BYTES) break
            total -= f.length()
            f.delete()
        }
    }

    companion object {
        // The home screen's cards on screen hold about 7 MB; the rest keeps recent images for a fast way back.
        const val MEMORY_BYTES = 16 * 1024 * 1024
        const val DISK_BYTES = 40L * 1024 * 1024
        const val TIMEOUT_MS = 15_000
    }
}

/** A counter that [next] moves on; work started in an older generation skips [ifStill]'s action. */
class Generation {
    private var n = 0

    @Synchronized
    fun now(): Int = n

    @Synchronized
    fun next(action: () -> Unit) {
        n++
        action()
    }

    @Synchronized
    fun ifStill(gen: Int, action: () -> Unit) {
        if (gen == n) action()
    }
}
