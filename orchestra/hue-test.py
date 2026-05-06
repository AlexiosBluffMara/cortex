from phue import Bridge
import time

b = Bridge("192.168.0.134")
b.connect()

groups = b.get_group()
print("--- groups ---")
for gid, g in groups.items():
    nm = g.get("name", "")
    tp = g.get("type", "")
    nl = len(g.get("lights", []))
    print("  id=%s name=%r type=%s lights=%s" % (gid, nm, tp, nl))

print("--- lights ---")
for lid, ll in b.get_light().items():
    nm = ll.get("name")
    on = ll["state"]["on"]
    rb = ll["state"]["reachable"]
    print("  id=%s name=%r on=%s reachable=%s" % (lid, nm, on, rb))

print("--- pulsing group 0: RED 2s then GREEN 2s then BLUE 2s ---")
b.set_group(0, {"on": True, "bri": 250, "xy": [0.675, 0.322], "transitiontime": 4})
time.sleep(2)
b.set_group(0, {"on": True, "bri": 250, "xy": [0.214, 0.709], "transitiontime": 4})
time.sleep(2)
b.set_group(0, {"on": True, "bri": 250, "xy": [0.155, 0.060], "transitiontime": 4})
time.sleep(2)
print("done")
