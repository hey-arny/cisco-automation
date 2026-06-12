#!/usr/bin/env python3

COMMANDS = (
    ("Version", "show version | include uptime|reason|bin|IOS Software", False),
    ("Interface status", "show interface description | exclude down", False),
    ("Duplex settings", "sh int | i dupl|Dupl", False),
    ("BGPv4 status", "show ip bgp summary | begin Neighbor", True),
    ("BGP VPNv4 summary", "show ip bgp vpnv4 all summary | begin Neighbor", True),
    ("BGP IPv6 summary", "show ip bgp ipv6 unicast summary | begin Neighbor", True),
    ("BGP VPNv6 summary", "show ip bgp vpnv6 unicast all summary | begin Neighbor", True),
    ("Crypto session", "show crypto session brief | begin Peer", True),
    ("HSRP status", "show standby brief all", True),
    ("VRRP status", "show vrrp brief", True),
    ("IPv4 route summary", "show ip route summary | i Total", False),
    ("IPv6 route summary", "show ipv6 route summary | i Total", True),
)
