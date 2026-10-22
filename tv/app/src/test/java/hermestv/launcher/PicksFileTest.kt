package hermestv.launcher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class PicksFileTest {
    @get:Rule val dir = TemporaryFolder()
    private val night = Picks.parse(javaClass.classLoader!!.getResource("picks_night.json")!!.readText())

    @Test
    fun savedListIsReadBackPerSlot() {
        // Two screens, one file per slot and row: what one saves, the other reads.
        val series = night.copy(row = "series", films = night.films.take(1).map { it.copy(kind = "series", seasons = 3) })
        PicksFile(dir.root).save(night)
        PicksFile(dir.root).save(series)
        val other = PicksFile(dir.root)
        assertEquals(night, other.local("night", "films"))
        assertEquals(series, other.local("night", "series"))
        assertNull(other.local("night", "youtube"))
        assertNull(other.local("lunch", "films"))
        assertEquals(listOf("picks-night-films.json", "picks-night-series.json"), dir.root.list()!!.sorted())
    }

    @Test
    fun failedSaveLeavesNoTmpFile() {
        // The target is a folder with a file in it, so the rename fails.
        File(dir.root, "picks-night-films.json").apply { mkdir(); File(this, "x").writeText("x") }
        PicksFile(dir.root).save(night)
        assertEquals(listOf("picks-night-films.json"), dir.root.list()!!.toList())
        assertNull(PicksFile(dir.root).local("night", "films"))
    }
}
