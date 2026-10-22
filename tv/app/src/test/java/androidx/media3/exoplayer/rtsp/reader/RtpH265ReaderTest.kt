package androidx.media3.exoplayer.rtsp.reader

import androidx.media3.common.C
import androidx.media3.common.DataReader
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.ParsableByteArray
import androidx.media3.exoplayer.rtsp.RtpPayloadFormat
import androidx.media3.extractor.ExtractorOutput
import androidx.media3.extractor.SeekMap
import androidx.media3.extractor.TrackOutput
import org.junit.Assert.assertEquals
import org.junit.Test

class RtpH265ReaderTest {
    private class Out : TrackOutput {
        val flags = mutableListOf<Int>()

        override fun format(format: Format) {}

        override fun sampleData(input: DataReader, length: Int, allowEndOfInput: Boolean, sampleDataPart: Int): Int =
            throw UnsupportedOperationException()

        override fun sampleData(data: ParsableByteArray, length: Int, sampleDataPart: Int) = data.skipBytes(length)

        override fun sampleMetadata(timeUs: Long, flags: Int, size: Int, offset: Int, cryptoData: TrackOutput.CryptoData?) {
            this.flags += flags
        }
    }

    private fun frame(vararg fuHeaders: Int): List<Int> {
        val out = Out()
        val reader = RtpH265Reader(RtpPayloadFormat(Format.Builder().setSampleMimeType(MimeTypes.VIDEO_H265).build(), 96, 90_000, emptyMap(), "H265"))
        reader.createTracks(object : ExtractorOutput {
            override fun track(id: Int, type: Int) = out
            override fun endTracks() {}
            override fun seekMap(seekMap: SeekMap) {}
        }, 0)
        reader.onReceivingFirstPacket(1_000, 1)
        fuHeaders.forEachIndexed { i, header ->
            // FU payload header (type 49), FU header, then a few payload bytes.
            val packet = ParsableByteArray(byteArrayOf(0x62, 0x01, header.toByte(), 9, 9, 9))
            reader.consume(packet, 1_000, 1 + i, i == fuHeaders.lastIndex)
        }
        return out.flags
    }

    @Test
    fun ezvizIdrIsAKeyFrame() {
        // An EZVIZ camera's real FU headers: start + IDR (0x93), then the NAL header byte 0x26 with S/E bits.
        assertEquals(listOf(C.BUFFER_FLAG_KEY_FRAME), frame(0x93, 0x26, 0x66))
    }

    @Test
    fun rfcFramesKeepTheirFlags() {
        assertEquals(listOf(C.BUFFER_FLAG_KEY_FRAME), frame(0x93, 0x13, 0x53))
        assertEquals(listOf(0), frame(0x81, 0x01, 0x41))
    }
}
