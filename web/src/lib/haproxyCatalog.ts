// What HAProxy can do, in plain words: balancing methods, health checks and ready-made setups
// (with realistic example values) used by the backend form, the frontend wizard and the add-route dialog.
import type { HxBackend, HxFrontend, HxOp } from "./haproxy";

export interface BalanceInfo { value: string; label: string; hint: string; http?: boolean; tcp?: boolean; arg?: { label: string; placeholder: string; required: boolean } }
export const BALANCE_INFO: BalanceInfo[] = [
  { value: "roundrobin", label: "Round robin", hint: "Each server in turn, following the weights. The usual choice for web apps and APIs." },
  { value: "leastconn", label: "Least connections", hint: "The server with the fewest open connections. Best for long or uneven requests: WebSocket, databases, slow APIs." },
  { value: "random", label: "Random (power of two)", hint: "Picks two servers at random and uses the less busy one. Great for large or changing fleets." },
  { value: "first", label: "First available", hint: "Fills the first server up to its max connections before using the next. Good for a primary database with replicas as backup." },
  { value: "source", label: "Client IP (sticky)", hint: "The same client IP keeps reaching the same server. Stickiness without cookies; works for TCP too." },
  { value: "static-rr", label: "Static round robin", hint: "Like round robin but weights only change with a reload. Slightly lighter on very large backends." },
  { value: "uri", label: "URL path (hash)", http: true, hint: "The same path always goes to the same server. Good in front of caches (Varnish, image servers)." },
  { value: "url_param", label: "URL parameter (hash)", http: true, hint: "The same value of a query parameter always goes to the same server.",
    arg: { label: "Parameter name", placeholder: "user_id", required: true } },
  { value: "hdr", label: "HTTP header (hash)", http: true, hint: "The same header value always goes to the same server, e.g. one tenant per server.",
    arg: { label: "Header name", placeholder: "X-Tenant-Id", required: true } },
  { value: "hash", label: "Custom expression (hash)", hint: "Consistent hashing on any request data, e.g. a cookie or a header.",
    arg: { label: "Expression", placeholder: "req.cook(session_id)", required: true } },
  { value: "rdp-cookie", label: "RDP cookie", tcp: true, hint: "Keeps Windows Remote Desktop users on the same server (TCP).",
    arg: { label: "Cookie name (optional)", placeholder: "mstshash", required: false } },
];
export const balanceInfo = (v: string) => BALANCE_INFO.find((b) => b.value === v) || BALANCE_INFO[0];

export interface CheckInfo { value: string; label: string; hint: string; http?: boolean; tcp?: boolean; user?: string }
export const CHECK_INFO: CheckInfo[] = [
  { value: "http", label: "HTTP request", http: true, hint: "Sends a request to a path; 2xx or 3xx means healthy. Point it at a cheap endpoint like /health." },
  { value: "connect", label: "TCP connect", hint: "Only checks that the port accepts connections. Works for anything." },
  { value: "pgsql", label: "PostgreSQL login", tcp: true, user: "CREATE ROLE haproxy_check LOGIN; and allow it from HAProxy in pg_hba.conf (trust, no database needed).",
    hint: "Logs in as a user with no rights, so a database that is up but refusing logins counts as down." },
  { value: "mysql", label: "MySQL / MariaDB login", tcp: true, user: "CREATE USER 'haproxy_check'@'10.0.0.%'; (no password, no grants).",
    hint: "Logs in as a user with no rights and checks the server answers properly." },
  { value: "redis", label: "Redis PING", tcp: true, hint: "Sends PING and expects +PONG." },
  { value: "ssl", label: "TLS handshake", tcp: true, hint: "Checks a TLS service HAProxy passes through unchanged (e.g. HTTPS in TCP mode)." },
  { value: "smtp", label: "SMTP greeting", tcp: true, hint: "Sends HELO and expects a 2xx answer." },
  { value: "ldap", label: "LDAP bind", tcp: true, hint: "Sends an anonymous bind and expects success." },
  { value: "tcp", label: "TCP check rules", tcp: true, hint: "Uses tcp-check rules written in the raw config (connect only when there are none)." },
];
export const checkInfo = (v: string) => CHECK_INFO.find((c) => c.value === (v === "default" ? "connect" : v)) || CHECK_INFO[1];

export interface ExampleServer { address: string; backup?: boolean }
export interface ExampleBackend { name: string; path?: string; check: string; servers: ExampleServer[] }
export interface UseCase {
  id: string; label: string; icon: string; desc: string; group: "web" | "data" | "other";
  mode: "http" | "tcp"; shape: "paths" | "proxy"; frontend: string; port: number;
  balance: string; check: string; checkUser?: string; sticky?: boolean; forwardfor?: boolean;
  timeouts?: { client?: number; connect?: number; server?: number; tunnel?: number };
  backends: ExampleBackend[]; note?: string;
  // house style
  tls?: boolean; tlsToServers?: boolean; forwardedPort?: boolean; redirect?: boolean; everythingElse?: boolean;
  redispatch?: boolean; retries?: number; keepalive?: boolean; stickSrc?: number;
  timing?: { inter: number; rise: number; fall: number }; serverName?: string;     // e.g. "server{n}", "s-{nn}"
}
// The usual way here: TLS from a certificate file on the HAProxy, X-Forwarded-Port, redirect to HTTPS, 60 s client and
// server timeouts, 10 s connect, redispatch with 3 retries, fast health checks (1 s, up after 3, down after 1).
const HTTP_HOUSE = { tls: true, forwardedPort: true, redirect: true, redispatch: true, retries: 3,
  timing: { inter: 1, rise: 3, fall: 1 }, serverName: "server{n}" };
const TCP_HOUSE = { redispatch: true, retries: 3, keepalive: true, timing: { inter: 2, rise: 3, fall: 2 } };
export const CERT_FILE_EXAMPLE = "/etc/haproxy/new-tls.pem";
export const USE_CASES: UseCase[] = [
  { ...HTTP_HOUSE, id: "microservices", label: "Microservices by path", icon: "map", group: "web", mode: "http", shape: "paths", frontend: "microservices", port: 443,
    desc: "One HTTPS address; each service gets its own path: /order and /order/… → inbound-order, /payment and /payment/… → inbound-payment.",
    balance: "roundrobin", check: "connect", timeouts: { client: 60, connect: 10, server: 60 }, everythingElse: false,
    backends: [
      { name: "inbound-order", path: "/order", check: "", servers: [{ address: "172.2.2.1:32133" }, { address: "172.2.2.2:32133" }, { address: "172.2.2.3:32133" }] },
      { name: "inbound-payment", path: "/payment", check: "", servers: [{ address: "172.2.2.1:32134" }, { address: "172.2.2.2:32134" }, { address: "172.2.2.3:32134" }] },
      { name: "web", check: "", servers: [{ address: "172.2.2.1:32080" }] },
    ],
    note: "Servers are your Kubernetes nodes and each service's NodePort. A path matches itself and everything under it: /order and /order/123, but not /orders." },
  { ...HTTP_HOUSE, id: "webapp", label: "Web app over HTTPS", icon: "globe", group: "web", mode: "http", shape: "proxy", frontend: "mattermost", port: 8066,
    desc: "One app on its own HTTPS port, e.g. Mattermost: everything goes to the app, which itself speaks TLS.",
    balance: "roundrobin", check: "connect", timeouts: { client: 60, connect: 10, server: 60 }, redispatch: false, tlsToServers: true,
    timing: { inter: 2, rise: 3, fall: 2 }, serverName: "s-{nn}",
    backends: [{ name: "mattermost", check: "", servers: [{ address: "172.1.1.1:8065" }] }] },
  { ...TCP_HOUSE, id: "rabbitmq", label: "RabbitMQ / AMQP", icon: "events", group: "data", mode: "tcp", shape: "proxy", frontend: "outbound-rabbitmq-dev", port: 5672,
    desc: "Message queue over TCP: each client IP stays on the same broker for 5 minutes, with TCP keep-alive and long timeouts.",
    balance: "roundrobin", check: "connect", timeouts: { client: 300, connect: 10, server: 300 }, stickSrc: 5, retries: 0, serverName: "rabbitmq-server-{nn}",
    backends: [{ name: "outbound-rabbitmq-dev", check: "", servers: [{ address: "172.3.3.1:5672" }, { address: "172.3.3.2:5672" }] }] },
  { ...HTTP_HOUSE, id: "api", label: "REST / JSON API", icon: "deploy", group: "web", mode: "http", shape: "proxy", frontend: "api", port: 443,
    desc: "One API on several servers; slow calls are spread by open connections.",
    balance: "leastconn", check: "connect", timeouts: { client: 60, connect: 10, server: 60 },
    backends: [{ name: "inbound-api", check: "", servers: [{ address: "172.2.2.1:32140" }, { address: "172.2.2.2:32140" }] }] },
  { ...HTTP_HOUSE, id: "websocket", label: "WebSocket / real-time", icon: "events", group: "web", mode: "http", shape: "proxy", frontend: "realtime", port: 443,
    desc: "Long-lived connections (chat, live updates, Socket.IO): long tunnel timeout, least connections.",
    balance: "leastconn", check: "connect", timeouts: { client: 3600, connect: 10, server: 60, tunnel: 3600 },
    backends: [{ name: "inbound-realtime", check: "", servers: [{ address: "172.2.2.1:32150" }, { address: "172.2.2.2:32150" }] }],
    note: "Socket.IO with polling fallback needs sticky sessions: turn them on under Sessions, headers and timeouts." },
  { ...TCP_HOUSE, id: "postgres", label: "PostgreSQL", icon: "db", group: "data", mode: "tcp", shape: "proxy", frontend: "postgres", port: 5432,
    desc: "Clients connect to HAProxy; it sends them to the primary and fails over to the replica.",
    balance: "first", check: "pgsql", checkUser: "haproxy_check", timeouts: { client: 1800, server: 1800, connect: 10 }, serverName: "pg-{nn}",
    backends: [{ name: "postgres", check: "", servers: [{ address: "172.4.4.1:5432" }, { address: "172.4.4.2:5432", backup: true }] }],
    note: "The replica is marked backup: it only gets connections when the primary is down." },
  { ...TCP_HOUSE, id: "mysql", label: "MySQL / MariaDB", icon: "db", group: "data", mode: "tcp", shape: "proxy", frontend: "mysql", port: 3306,
    desc: "One address for the database; primary first, replica as backup.",
    balance: "first", check: "mysql", checkUser: "haproxy_check", timeouts: { client: 1800, server: 1800, connect: 10 }, serverName: "mysql-{nn}",
    backends: [{ name: "mysql", check: "", servers: [{ address: "172.4.4.11:3306" }, { address: "172.4.4.12:3306", backup: true }] }] },
  { ...TCP_HOUSE, id: "redis", label: "Redis", icon: "db", group: "data", mode: "tcp", shape: "proxy", frontend: "redis", port: 6379,
    desc: "Cache or queue behind one address, checked with PING.",
    balance: "first", check: "redis", timeouts: { client: 3600, server: 3600, connect: 10 }, serverName: "redis-{nn}",
    backends: [{ name: "redis", check: "", servers: [{ address: "172.4.4.21:6379" }, { address: "172.4.4.22:6379", backup: true }] }],
    note: "With a primary and replicas, only the primary takes writes: keep replicas as backup." },
  { ...TCP_HOUSE, id: "mongodb", label: "MongoDB", icon: "db", group: "data", mode: "tcp", shape: "proxy", frontend: "mongo", port: 27017,
    desc: "Plain TCP balancing for mongos routers or a single node.",
    balance: "leastconn", check: "connect", timeouts: { client: 1800, server: 1800, connect: 10 }, serverName: "mongos-{nn}",
    backends: [{ name: "mongos", check: "", servers: [{ address: "172.4.4.31:27017" }, { address: "172.4.4.32:27017" }] }],
    note: "For a replica set, put HAProxy in front of mongos; drivers talk to replica set members directly." },
  { ...TCP_HOUSE, id: "tcp", label: "Other TCP service", icon: "nodes", group: "other", mode: "tcp", shape: "proxy", frontend: "tcp-svc", port: 9000,
    desc: "Anything over TCP: MQTT (1883), SMTP (25), LDAP (389), gRPC without TLS termination…",
    balance: "leastconn", check: "connect", timeouts: { client: 300, server: 300, connect: 10 }, serverName: "server{n}",
    backends: [{ name: "tcp-svc", check: "", servers: [{ address: "172.5.5.1:9000" }, { address: "172.5.5.2:9000" }] }] },
];
export const useCase = (id: string) => USE_CASES.find((u) => u.id === id);
/** Server name from a pattern: {n} = 1, 2…; {nn} = 01, 02…; {backend} = the backend's name. */
export function serverName(pattern: string | undefined, backend: string, i: number): string {
  return (pattern || "server{n}").replace("{backend}", backend).replace("{nn}", String(i + 1).padStart(2, "0")).replace("{n}", String(i + 1));
}

/** "10.0.1.11:8080" or "[::1]:8080" -> address and port; null when there's no valid port. */
export function splitHostPort(s: string): { address: string; port: number } | null {
  const m = s.trim().match(/^\[?([^\]\s]+?)\]?:(\d{1,5})$/);
  if (!m) return null;
  const port = Number(m[2]);
  return port >= 1 && port <= 65535 ? { address: m[1], port } : null;
}
/** Backend settings a use case starts with (for the backend form and the wizard). */
export function backendData(u: UseCase, check: { path?: string; user?: string } = {}) {
  const typ = u.check === "connect" ? "connect" : u.mode === "http" ? "http" : u.check;
  return {
    mode: u.mode, balance: u.balance, balance_arg: "",
    check: typ === "http" ? { type: "http", method: "GET", uri: check.path || "/health" } : { type: typ, user: check.user ?? u.checkUser ?? "" },
    sticky: !!u.sticky, forwardfor: !!u.forwardfor,
    timeouts: { connect: u.timeouts?.connect || 0, server: u.timeouts?.server || 0, tunnel: u.timeouts?.tunnel || 0 },
    redispatch: !!u.redispatch, retries: u.retries || 0, keepalive: !!u.keepalive, stick_src: u.stickSrc || 0,
    timing: { inter: 0, rise: 0, fall: 0, ...(u.timing || {}) },
  };
}

// --- backend settings <-> backend, and only what changed ---------------------------------
export interface Settings {
  mode: string; balance: string; balance_arg: string;
  check: { type: string; method?: string; uri?: string; host?: string; user?: string };
  sticky: boolean; forwardfor: boolean; timeouts: { connect: number; server: number; tunnel: number };
  redispatch: boolean; retries: number; keepalive: boolean; stick_src: number;
  timing: { inter: number; rise: number; fall: number };
}
export function settingsOf(b: HxBackend): Settings {
  const c = b.check;
  return {
    mode: b.mode, balance: b.balance, balance_arg: b.balance_arg || "",
    check: c.type === "http" ? { type: "http", method: c.method || "GET", uri: c.uri || "/", host: c.host || "" } : { type: c.type === "default" ? "connect" : c.type, user: c.user || "" },
    sticky: !!b.sticky, forwardfor: !!b.forwardfor, timeouts: { connect: 0, server: 0, tunnel: 0, ...(b.timeouts || {}) },
    redispatch: !!b.redispatch, retries: b.retries || 0, keepalive: !!b.keepalive, stick_src: b.stick_src || 0,
    timing: { inter: 0, rise: 0, fall: 0, ...(b.check_timing || {}) },
  };
}
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
/** The "data" of a backend create/update op: everything for a new backend, only changes for an existing one. */
export function backendChanges(s: Settings, old: Settings | null): Record<string, any> {
  const t = { connect: Number(s.timeouts.connect) || 0, server: Number(s.timeouts.server) || 0, tunnel: Number(s.timeouts.tunnel) || 0 };
  const check = s.check.type === "http" ? { type: "http", method: s.check.method || "GET", uri: (s.check.uri || "/").trim(), host: (s.check.host || "").trim() }
    : { type: s.check.type, user: (s.check.user || "").trim() };
  const d: Record<string, any> = {};
  if (!old || s.mode !== old.mode) d.mode = s.mode;
  if (!old || s.balance !== old.balance || s.balance_arg.trim() !== old.balance_arg) Object.assign(d, { balance: s.balance, balance_arg: s.balance_arg.trim() });
  if (!old || !same(check, old.check.type === "http" ? { type: "http", method: old.check.method, uri: old.check.uri, host: old.check.host } : { type: old.check.type, user: old.check.user })) d.check = check;
  if (s.mode === "http" && (!old || s.sticky !== old.sticky)) d.sticky = s.sticky;
  if (s.mode !== "http" && old?.sticky) d.sticky = false;
  if (s.mode === "http" && (!old || s.forwardfor !== old.forwardfor)) d.forwardfor = s.forwardfor;
  if (s.mode !== "http" && old?.forwardfor) d.forwardfor = false;
  if (!old || !same(t, old.timeouts)) d.timeouts = t;
  if (!old || s.redispatch !== old.redispatch) d.redispatch = s.redispatch;
  if (!old || (Number(s.retries) || 0) !== old.retries) d.retries = Number(s.retries) || 0;
  if (!old || s.keepalive !== old.keepalive) d.keepalive = s.keepalive;
  if (!old || (Number(s.stick_src) || 0) !== old.stick_src) d.stick_src = Number(s.stick_src) || 0;
  const timing = { inter: Number(s.timing.inter) || 0, rise: Number(s.timing.rise) || 0, fall: Number(s.timing.fall) || 0 };
  if (old && !same(timing, old.timing)) d.check_timing = timing;
  return d;
}
/** Ops that recreate a backend (with its servers) on another HAProxy. */
export function backendCopyOps(b: HxBackend): HxOp[] {
  return [{ op: "create", kind: "backend", name: b.name, data: backendChanges(settingsOf(b), null) },
    ...b.servers.map((sv): HxOp => ({ op: "create", kind: "server", backend: b.name, name: sv.name,
      data: { address: sv.address, port: sv.port, weight: sv.weight, check: sv.check, backup: sv.backup, maxconn: sv.maxconn || 0, ssl: !!sv.ssl,
        inter: sv.inter || 0, rise: sv.rise || 0, fall: sv.fall || 0 } }))];
}
/** Op that recreates a frontend on another HAProxy (its backends must exist there). */
export function frontendCopyOp(f: HxFrontend): HxOp {
  const p = f.policies;
  return { op: "create", kind: "frontend", name: f.name, data: {
    mode: f.mode, default_backend: f.default_backend,
    binds: f.binds.map((b) => ({ address: b.address, port: b.port, ...(b.ssl ? { ssl: true, certificate: certValue(b) } : {}) })),
    ...(f.simple_routes ? { routes: f.routes.map((r) => ({ kind: r.kind, value: r.value, backend: r.backend, ...(r.header ? { header: r.header } : {}) })) } : {}),
    ...(f.mode === "http" ? { policies: { allow_ips: p.allow_ips, redirect_https: p.redirect_https, rate_limit: p.rate_limit, forwarded_port: !!p.forwarded_port,
      maintenance: p.maintenance || { enabled: false } } } : {}),
    timeouts: { client: f.timeouts?.client || 0 },
  } };
}

/** What to send for a TLS listen address: an uploaded certificate by name, or the certificate file path on the
 *  HAProxy server (e.g. /etc/haproxy/new-tls.pem) when it isn't one of the uploaded ones. */
export function certValue(b: { certificate?: string; certificate_file?: string }, uploaded?: string[]): string {
  if (uploaded?.includes(b.certificate || "")) return b.certificate!;
  return b.certificate_file || b.certificate || "";
}
/** Server create data with the backend's health check timing (inter in seconds). */
export const timingData = (s: Settings) => ({ inter: Number(s.timing.inter) || 0, rise: Number(s.timing.rise) || 0, fall: Number(s.timing.fall) || 0 });
