# No usage stats, and no host resolver. A closed port on loopback
# answers a stray lookup inside the container.
disable_monitoring = true;

dns {
  nameserver = ["127.0.0.1"];
  timeout = 1s;
  retransmits = 1;
}
