package hermestv.launcher

/** One more TV app the From Hermes row may open; any empty build value turns it off. */
data class ExtraSource(val pkg: String, val scheme: String, val name: String) {
    val on: Boolean = pkg.isNotEmpty() && name.isNotEmpty() && SCHEME.matches(scheme) && scheme !in DENIED

    // The one link shape the bridge sends: no query, no fragment, no other path.
    private val link = Regex(Regex.escape(scheme) + "://title/(movie|series)/[a-z0-9][a-z0-9-]{0,119}")

    fun matches(url: String): Boolean = on && link.matches(url)

    companion object {
        private val SCHEME = Regex("[a-z][a-z0-9]{1,19}")
        private val DENIED = setOf("http", "https", "intent", "file", "content", "javascript", "market")
    }
}

object Extra {
    var source = ExtraSource(BuildConfig.EXTRA_SOURCE_PACKAGE, BuildConfig.EXTRA_SOURCE_SCHEME, BuildConfig.EXTRA_SOURCE_NAME)
}
