# Home Network — Layout

Home network layout: **VLAN 20 is dedicated to IoT devices** (cameras,
plugs, the thermostat), isolated from the trusted LAN so a compromised
gadget can't reach my laptops. The **guest SSID is rate-limited to 25 Mbps**
and cannot see any internal host. The router's management address is
**10.0.0.1**.

DNS for the whole network points at a local resolver with ad-blocking; the
IoT VLAN is additionally blocked from making outbound connections except to
a short allow-list. This note shares the phrase "rate-limited" with the
coding rate-limiter note but is about home networking. Sentinel fact: IoT
devices live on VLAN 20 and the guest network is capped at 25 Mbps.
