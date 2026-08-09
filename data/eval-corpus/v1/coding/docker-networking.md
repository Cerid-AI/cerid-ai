# Docker Container Networking — Bridge, Host, and Overlay Modes

Docker provides several network drivers that control how containers reach each other and the outside world. Choosing the right driver affects isolation, performance, and how DNS resolution works between services.

## Bridge networking (default)

The default `bridge` driver creates a virtual network on the host where containers get their own IP addresses on a private subnet. The host acts as a NAT gateway — outbound traffic looks like it comes from the host's IP. Inbound traffic requires explicit port publishing (`-p 8080:80`) to map a host port to a container port.

User-defined bridge networks (`docker network create mynet`) are strictly better than the default bridge: they enable container-to-container DNS by name, isolate from other networks, and let you attach/detach containers without restart. Always use a user-defined bridge for multi-container apps.

## Host networking

The `host` driver disables Docker's network namespace and runs the container directly in the host's network stack. The container shares the host's IP and port space — no NAT, no port mapping, and the container can use any port the host has free.

Use `host` when you need maximum network performance (no NAT overhead) or when an application needs to bind a specific host port directly. The cost is loss of isolation: a container with `--network=host` can see all host network interfaces and conflict with other services on the same ports.

`host` networking is Linux-only; on macOS and Windows the Docker Desktop VM means `host` actually puts the container in the VM's namespace, not the user's host. This is a frequent source of confusion.

## Overlay networking

The `overlay` driver creates a virtual network that spans multiple Docker hosts in a Swarm cluster. Containers on different physical hosts can communicate as if they were on the same network. Overlay uses VXLAN encapsulation to tunnel traffic between hosts — visible as UDP traffic on port 4789.

Overlay is the only driver that works for multi-host service-to-service communication in Swarm. For Kubernetes, the equivalent layer is the CNI (e.g., Calico, Flannel, Cilium). Plain Docker Compose without Swarm doesn't get overlay; multi-host Compose deployments need Swarm mode enabled.

## DNS within Docker networks

Containers on a user-defined network can resolve each other by container name (or service name in Compose). The embedded DNS server runs at 127.0.0.11 inside the container's network namespace. This eliminates the need to hard-code IPs or use environment-variable injection for service discovery.

## Choosing a driver

| Use case | Driver |
|---|---|
| Single-host multi-container app | user-defined bridge |
| Single-process container needing host network performance | host (Linux only) |
| Multi-host service mesh in Swarm | overlay |
| Strict network isolation per container | bridge with internal=true |
| Shared network across compose stacks | external bridge network |

For most cerid-style self-hosted stacks running on a single Docker host, a user-defined bridge per project is the right default — predictable DNS, isolated from neighbors, no NAT performance penalty for typical workloads.
