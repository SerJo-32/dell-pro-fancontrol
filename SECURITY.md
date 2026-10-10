# Security

dellfan runs as root and controls cooling hardware. If you find a way to make it unsafe
(for example, to keep the fan low while the CPU overheats, or to change settings without
permission), please report it privately through GitHub:
**Security → Report a vulnerability** on this repository, instead of opening a public issue.

Notes on the design:

- the web page has no login; by default it listens only on 127.0.0.1;
- the app talks to the service over a Unix socket limited to the `dellfan` group;
- the service validates every setting (safety limit 60–85 °C, power 15–45 W) before applying it.
