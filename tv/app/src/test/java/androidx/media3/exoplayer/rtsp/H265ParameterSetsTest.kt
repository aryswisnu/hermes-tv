package androidx.media3.exoplayer.rtsp

import java.util.Base64
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class H265ParameterSetsTest {
    // An EZVIZ camera's own sprop-parameter-sets (/ch1/main): an SPS, then a PPS, no VPS.
    private val sps = nal("QgEBAWAAAAMAAAMAAAMAAAMAlqABQCAFoWNrkk5TNwFEMyIRAAAAAHAAAAA=")
    private val pps = nal("RAHA8vAiQA==")
    private val vps = byteArrayOf(0, 0, 0, 1, 0x40, 0x01, 0x0C)
    private val sei = byteArrayOf(0, 0, 0, 1, 0x4E, 0x01)

    @Test
    fun cameraSetsGiveSpsSecondFromEnd() {
        val sets = RtspMediaTrack.orderH265ParameterSets(listOf(pps, sei, sps))
        assertEquals(2, sets.size)
        assertArrayEquals(sps, sets[sets.size - 2])
        assertArrayEquals(pps, sets.last())
    }

    @Test
    fun vpsGoesFirst() {
        val sets = RtspMediaTrack.orderH265ParameterSets(listOf(sps, pps, vps))
        assertEquals(listOf(vps, sps, pps).map { it.toList() }, sets.map { it.toList() })
    }

    @Test
    fun spsAndPpsAreRequired() {
        assertThrows(IllegalArgumentException::class.java) { RtspMediaTrack.orderH265ParameterSets(listOf(pps)) }
        assertThrows(IllegalArgumentException::class.java) { RtspMediaTrack.orderH265ParameterSets(listOf(sps)) }
    }

    private fun nal(base64: String) = byteArrayOf(0, 0, 0, 1) + Base64.getDecoder().decode(base64)
}
