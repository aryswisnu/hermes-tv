package hermestv.launcher

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress

class TailnetRouteTest {
    @Test fun acceptsCgNatRange() {
        assertTrue(TailnetRoute.isTailnet(InetAddress.getByName("100.64.0.1")))
        assertTrue(TailnetRoute.isTailnet(InetAddress.getByName("100.127.255.254")))
    }

    @Test fun rejectsAddressesOutsideCgNatRange() {
        assertFalse(TailnetRoute.isTailnet(InetAddress.getByName("100.63.255.255")))
        assertFalse(TailnetRoute.isTailnet(InetAddress.getByName("100.128.0.1")))
        assertFalse(TailnetRoute.isTailnet(InetAddress.getByName("203.0.113.10")))
    }

    @Test fun acceptsOnlyTailscaleIpv6Prefix() {
        assertTrue(TailnetRoute.isTailnet(InetAddress.getByName("fd7a:115c:a1e0::1")))
        assertFalse(TailnetRoute.isTailnet(InetAddress.getByName("fd00::1")))
    }
}
