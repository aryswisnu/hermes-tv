package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Test

class GenerationTest {
    @Test
    fun clearStopsLateDecodes() {
        val g = Generation()
        val cache = ArrayList<String>()
        val started = g.now()
        g.ifStill(started) { cache += "first" }
        // onStop clears the cache while a decode still runs: its bitmap must not come back.
        g.next { cache.clear() }
        g.ifStill(started) { cache += "late" }
        assertEquals(emptyList<String>(), cache)
        g.ifStill(g.now()) { cache += "new" }
        assertEquals(listOf("new"), cache)
    }
}
