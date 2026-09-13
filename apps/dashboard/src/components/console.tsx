"use client";
import { useCallback, useEffect, useRef, useState, FormEvent } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Cpu,
  FlaskConical,
  Layers,
  LogOut,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  Siren,
  Terminal,
  Unplug,
  X,
  Zap,
} from "lucide-react";
import { api, ApiError, Row, nice, stamp, duration, terminal } from "@/lib/api";
import {
  Badge,
  Chart,
  Journey,
  JsonDetails,
  Topology,
  LiveVerification,
} from "./visuals";

const nav = [
  { path: "/", label: "Demo Lab", icon: FlaskConical },
  { path: "/incidents", label: "Incidents", icon: Activity },
  { path: "/resources", label: "Resources", icon: Layers },
  { path: "/policies", label: "Policies & automation", icon: Settings2 },
  { path: "/system", label: "System health", icon: Radio },
];
const scenarios = [
  {
    id: "SCN-001",
    name: "Container crash",
    icon: Unplug,
    description:
      "Stop the demo API process. Watch the agent detect its absence and restart the bound container.",
    signal: "Availability drops",
    root: "CONTAINER_STOPPED",
  },
  {
    id: "SCN-002",
    name: "CPU saturation",
    icon: Cpu,
    description:
      "Apply CPU pressure to the demo API. Watch elevated CPU trigger diagnosis and a controlled restart.",
    signal: "CPU usage rises",
    root: "CPU_SATURATION",
  },
  {
    id: "SCN-003",
    name: "API hang",
    icon: Pause,
    description:
      "Make API requests unresponsive. Watch latency and readiness checks expose a running but unhealthy service.",
    signal: "Requests time out",
    root: "API_UNRESPONSIVE",
  },
];

type Confirmation = {
  title: string;
  description: string;
  label: string;
  run: (reason: string) => Promise<void>;
  reason?: boolean;
  danger?: boolean;
};

function Confirm({
  value,
  onClose,
  busy,
  onConfirm,
}: {
  value: Confirmation;
  onClose: () => void;
  busy: boolean;
  onConfirm: (reason: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  useEffect(() => {
    const el = dialog.current;
    el?.showModal();
    return () => el?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="confirm-dialog"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
      aria-labelledby="confirm-title"
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onConfirm(reason);
        }}
      >
        <span className="dialog-icon">
          <ShieldCheck />
        </span>
        <h2 id="confirm-title">{value.title}</h2>
        <p>{value.description}</p>
        {value.reason && (
          <label>
            Reason
            <textarea
              autoFocus
              required
              minLength={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Explain this operator decision"
            />
          </label>
        )}
        <div className="actions">
          <button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className={value.danger ? "danger" : "primary"}
            disabled={busy || (!!value.reason && reason.trim().length < 3)}
          >
            {busy ? "Submitting…" : value.label}
          </button>
        </div>
      </form>
    </dialog>
  );
}

function Login({
  onLogin,
  expired,
}: {
  onLogin: (user: Row) => void;
  expired: boolean;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      onLogin({ ...result.user, csrf_token: result.csrf_token });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-screen">
      <div className="login-story">
        <div className="brand">
          <span>
            <ShieldCheck />
          </span>
          sentinel<span className="brand-period">.</span>
        </div>
        <span className="eyebrow">AUTONOMOUS SELF-HEALING LAB</span>
        <h1>
          See the fault.
          <br />
          Follow the fix.
        </h1>
        <p>
          A live window into an agent that observes, reasons, acts, and
          verifies.
        </p>
        <div className="login-loop">
          {["Detect", "Diagnose", "Recover", "Verify"].map((s, i) => (
            <span key={s}>
              <i>{i + 1}</i>
              {s}
            </span>
          ))}
        </div>
        <small>MILESTONE 01 / LOCAL DOCKER ENVIRONMENT</small>
      </div>
      <form className="login-form" onSubmit={submit}>
        <span className="eyebrow">OPERATOR CONSOLE</span>
        <h2>Welcome to the lab</h2>
        <p>Sign in with your provisioned project account.</p>
        {expired && (
          <div className="notice warn" role="status">
            Your session expired. Sign in again to continue.
          </div>
        )}
        {error && (
          <div className="notice bad" role="alert">
            {error}
          </div>
        )}
        <label>
          Username
          <input
            autoComplete="username"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Open console"}
          <ArrowRight size={17} />
        </button>
        <p className="caption">
          <ShieldCheck size={15} /> Access and actions follow your
          server-assigned role.
        </p>
      </form>
    </main>
  );
}

export default function Console() {
  const pathname = usePathname();
  const [user, setUser] = useState<Row | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [expired, setExpired] = useState(false);
  const [snapshot, setSnapshot] = useState<Row | null>(null);
  const [telemetry, setTelemetry] = useState<Row>({});
  const [faults, setFaults] = useState<Row>({ available: false, active: [] });
  const [detail, setDetail] = useState<Row | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [lastUpdate, setLastUpdate] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [stream, setStream] = useState("Connecting");
  const [present, setPresent] = useState(false);
  const [minutes, setMinutes] = useState(10);
  const [confirm, setConfirm] = useState<Confirmation | null>(null);
  const [busy, setBusy] = useState(false);
  const refreshing = useRef(false);
  const telemetryAt = useRef(0);
  const role = String(user?.role || "").toUpperCase();
  const operator = ["OPERATOR", "APPROVER", "ADMIN"].includes(role);
  const approver = ["APPROVER", "ADMIN"].includes(role);
  const admin = role === "ADMIN";
  const expire = useCallback(() => {
    setUser(null);
    setSnapshot(null);
    setDetail(null);
    setTelemetry({});
    setFaults({ available: false, active: [] });
    setExpired(true);
  }, []);
  useEffect(() => {
    api("/auth/me")
      .then(setUser)
      .catch((e) => {
        if (e.status !== 401) setError(e.message);
      })
      .finally(() => setInitializing(false));
  }, []);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const readError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) expire();
      else setError((e as Error).message || "Unable to refresh");
    },
    [expire],
  );
  const refresh = useCallback(async () => {
    if (!user || refreshing.current) return;
    refreshing.current = true;
    try {
      const s = await api("/console/snapshot");
      setSnapshot(s);
      setLastUpdate(Date.now());
      setError("");
      const id = pathname.startsWith("/incidents/")
        ? pathname.split("/")[2]
        : selectedId ||
          s.incidents.find((i: Row) => !terminal.has(i.state))?.id ||
          s.incidents[0]?.id;
      if (id) {
        const d = await api(`/console/incidents/${id}`);
        setDetail(d);
      } else setDetail(null);
      if (Date.now() - telemetryAt.current > 7000) {
        telemetryAt.current = Date.now();
        const results = await Promise.allSettled([
          api(`/console/telemetry?minutes=${minutes}`),
          api("/console/faults"),
        ]);
        if (results[0].status === "fulfilled") setTelemetry(results[0].value);
        else setTelemetry({});
        if (results[1].status === "fulfilled") setFaults(results[1].value);
        else setFaults({ available: false, active: [] });
      }
    } catch (e) {
      readError(e);
    } finally {
      refreshing.current = false;
    }
  }, [user, pathname, selectedId, minutes, readError]);
  useEffect(() => {
    telemetryAt.current = 0;
    void refresh();
    const id = setInterval(() => void refresh(), 5000);
    return () => clearInterval(id);
  }, [refresh]);
  useEffect(() => {
    if (!user) return;
    const es = new EventSource("/api/v1/console/stream");
    es.addEventListener("snapshot", () => {
      setStream("Live");
      void refresh();
    });
    es.addEventListener("expired", () => {
      es.close();
      expire();
    });
    es.onerror = () => setStream("Polling fallback");
    return () => es.close();
  }, [user, refresh, expire]);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(""), 8000);
    return () => clearTimeout(id);
  }, [toast]);
  async function mutate(path: string, body: Row) {
    if (!user) throw new Error("Sign in first");
    return api(path, {
      method: "POST",
      headers: { "X-CSRF-Token": user.csrf_token },
      body: JSON.stringify(body),
    });
  }
  async function confirmed(reason: string) {
    if (!confirm) return;
    setBusy(true);
    try {
      await confirm.run(reason);
      setConfirm(null);
      telemetryAt.current = 0;
      await refresh();
    } catch (e) {
      readError(e);
      setConfirm(null);
      await refresh();
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function faultCommand(
    s: (typeof scenarios)[number],
    action: "inject" | "clear",
  ) {
    const key = crypto.randomUUID();
    setConfirm({
      title: `${action === "inject" ? "Inject" : "Clear"} ${s.name.toLowerCase()}?`,
      description:
        action === "inject"
          ? `${s.description} Target: the local demo-api container. Fault TTL: 10 minutes. Clearing or expiry can make recovery externally attributed.`
          : "Clear this fault in the local demo environment. This is an operator intervention; the verifier may attribute recovery externally.",
      label: action === "inject" ? "Inject fault" : "Clear fault",
      danger: true,
      run: async () => {
        const result = await mutate("/console/faults", {
          scenario_id: s.id,
          action,
          idempotency_key: key,
        });
        if (result.status !== "SUCCEEDED")
          throw new Error(
            result.result?.message ||
              `Command ${nice(result.status)}. Inspect system command history before retrying.`,
          );
        setToast(`${s.name}: ${nice(result.result?.status || result.status)}`);
      },
    });
  }
  function approve(decision: "APPROVE" | "REJECT") {
    if (!detail) return;
    const d = detail;
    const plan = d.plans[0];
    const key = crypto.randomUUID();
    setConfirm({
      title:
        decision === "APPROVE"
          ? "Approve this remediation plan?"
          : "Reject this remediation plan?",
      description: `Plan v${plan.version} targets ${d.resource?.name}. Incident v${d.incident.version}. ${decision === "APPROVE" ? "The agent may restart the exact bound container after policy validation." : "The agent will receive your rejection."}`,
      label: decision === "APPROVE" ? "Approve plan" : "Reject plan",
      reason: decision === "REJECT",
      danger: decision === "REJECT",
      run: async (reason) => {
        await mutate(`/incidents/${d.incident.id}/approval`, {
          decision,
          expected_incident_version: d.incident.version,
          plan_version: plan.version,
          content_hash: plan.content_hash,
          idempotency_key: key,
          rejection_reason: reason || undefined,
        });
        setToast(`Plan ${decision === "APPROVE" ? "approved" : "rejected"}`);
      },
    });
  }
  function dryRun() {
    if (!detail?.plans?.[0]) return;
    const d = detail;
    const p = d.plans[0];
    setConfirm({
      title: "Simulate this plan?",
      description:
        "Validate the stored plan and simulate its steps. No container restart, verification or incident state change occurs. Live execution must still pass current policy.",
      label: "Run simulation",
      run: async () => {
        const result = await mutate(
          `/console/incidents/${d.incident.id}/dry-run`,
          {
            expected_incident_version: d.incident.version,
            plan_version: p.version,
            content_hash: p.content_hash,
          },
        );
        setToast(
          result.validation_result.valid
            ? "Simulation complete: plan structure valid. Review the dry-run record."
            : "Simulation found validation errors. Review the dry-run record.",
        );
      },
    });
  }
  const fresh = !!lastUpdate && now - lastUpdate < 15000;
  const signals = telemetry.signals || {};
  const telemetryFresh =
    fresh &&
    !!telemetry.server_time &&
    now - Date.parse(telemetry.server_time) < 30000;
  const activeFaults: Row[] = Array.isArray(faults.active)
    ? faults.active
    : faults.active?.active_faults || [];
  const chartMarkers = [
    ...activeFaults.map((f) => ({
      time: f.injected_at,
      label: f.scenario_id,
      color: "#f3ac88",
    })),
    ...(detail?.incident.resolved_at
      ? [
          {
            time: Date.parse(detail.incident.resolved_at) / 1000,
            label: "Verified recovery",
            color: "#67d9b6",
          },
        ]
      : []),
  ];
  const pageTitle = pathname.startsWith("/incidents/")
    ? "Incident workspace"
    : nav.find((n) => n.path === pathname)?.label || "Demo Lab";
  if (initializing)
    return (
      <main className="loading-screen">
        <ShieldCheck size={32} />
        <p>Connecting to the control plane…</p>
      </main>
    );
  if (!user)
    return (
      <Login
        onLogin={(u) => {
          setUser(u);
          setExpired(false);
          setError("");
        }}
        expired={expired}
      />
    );
  return (
    <div className={`console ${present ? "presentation" : ""}`}>
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link href="/" className="brand">
          <span>
            <ShieldCheck size={23} />
          </span>
          sentinel<span className="brand-period">.</span>
        </Link>
        <div className="workspace-label">
          <i /> LOCAL WORKSPACE <small>M1</small>
        </div>
        <nav aria-label="Main navigation">
          {nav.map((n) => (
            <Link
              key={n.path}
              href={n.path}
              className={
                (
                  n.path === "/"
                    ? pathname === "/"
                    : pathname.startsWith(n.path)
                )
                  ? "active"
                  : ""
              }
            >
              <n.icon size={18} />
              {n.label}
              {n.path === "/incidents" &&
                !!snapshot?.counts?.PENDING_APPROVAL && (
                  <b>{snapshot.counts.PENDING_APPROVAL}</b>
                )}
            </Link>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="lab-note">
            <FlaskConical size={19} />
            <strong>Built to be observed.</strong>
            <p>Three faults. One autonomous recovery loop.</p>
          </div>
          <div className="user-card">
            <span className="avatar">
              {user.username?.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <strong>{user.username}</strong>
              <small>{nice(role)}</small>
            </div>
            <button
              aria-label="Sign out"
              onClick={async () => {
                try {
                  await mutate("/auth/logout", {});
                  expire();
                  setExpired(false);
                } catch (e) {
                  readError(e);
                }
              }}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} /> <strong>{pageTitle}</strong>
          </div>
          <div className="topbar-actions">
            <span className={`connection ${fresh ? "" : "stale"}`}>
              <i />
              {fresh ? stream : "Reconnecting"}
            </span>
            <button
              title="Refresh data"
              aria-label="Refresh data"
              onClick={() => {
                telemetryAt.current = 0;
                void refresh();
              }}
            >
              <RefreshCw size={16} />
            </button>
            <button onClick={() => setPresent(!present)}>
              {present ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              <span>{present ? "Exit presentation" : "Present"}</span>
            </button>
          </div>
        </header>
        <main id="main" className="content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                SELF-HEALING CONTROL PLANE / MILESTONE 01
              </span>
              <h1>
                {pageTitle === "Demo Lab" ? (
                  <>
                    Resilience, <span>in real time.</span>
                  </>
                ) : (
                  pageTitle
                )}
              </h1>
              <p>
                {pathname === "/"
                  ? "Introduce a fault. Watch the system find its way back."
                  : "Live operational records from the local control plane."}
              </p>
            </div>
            <div className="heading-status">
              <Badge value={snapshot?.automation?.mode || "UNKNOWN"} />
              <small>
                Updated{" "}
                {lastUpdate ? stamp(new Date(lastUpdate).toISOString()) : "—"}
              </small>
            </div>
          </div>
          {error && (
            <div className="notice bad" role="alert">
              <Siren size={18} />
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {!fresh && snapshot && (
            <div className="notice warn" role="status">
              Data is stale. Last known state is shown; wait for a fresh
              snapshot before acting.
            </div>
          )}
          {toast && (
            <div className="notice good" role="status">
              <Check size={18} />
              {toast}
            </div>
          )}
          {!snapshot ? (
            <div className="empty-state">
              <Radio />
              <h2>Waiting for the control plane</h2>
              <p>
                Check that the API and database are running. This page
                reconnects automatically.
              </p>
            </div>
          ) : (
            <>
              {pathname === "/" && (
                <>
                  <div className="metric-grid">
                    <Metric
                      label="Open incidents"
                      value={Object.entries(snapshot.counts)
                        .filter(([s]) => !terminal.has(s))
                        .reduce((sum, [, n]) => sum + Number(n), 0)}
                      note="Across all managed resources"
                      icon={Activity}
                    />
                    <Metric
                      label="Request throughput"
                      value={
                        signals.rps?.latest == null
                          ? "—"
                          : `${signals.rps.latest.toFixed(1)}`
                      }
                      unit="req/s"
                      note={
                        telemetryFresh
                          ? "Observed /jobs traffic · 1m rate"
                          : "Telemetry unavailable or stale"
                      }
                      icon={Radio}
                    />
                    <Metric
                      label="Verified recoveries"
                      value={snapshot.counts.RESOLVED || 0}
                      note="Final incident state: resolved"
                      icon={ShieldCheck}
                    />
                    <Metric
                      label="Awaiting approval"
                      value={snapshot.counts.PENDING_APPROVAL || 0}
                      note="Human decision required"
                      icon={Pause}
                    />
                  </div>
                  <section className="panel topology-panel">
                    <div className="panel-heading">
                      <div>
                        <span className="section-number">01</span>
                        <h2>Live environment</h2>
                        <span className="subtle-pill">Docker / local</span>
                      </div>
                      <div className="legend">
                        <span>
                          <i className="green" />
                          Healthy
                        </span>
                        <span>
                          <i className="red" />
                          Fault
                        </span>
                        <span>
                          <i />
                          Unknown
                        </span>
                      </div>
                    </div>
                    <Topology
                      signals={signals}
                      incident={detail?.incident}
                      fresh={telemetryFresh}
                      injected={activeFaults[0]?.scenario_id}
                    />
                  </section>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <span className="section-number">02</span>
                        <h2>Fault injection</h2>
                      </div>
                      <span className="caption">
                        Controlled scenarios · demo-api only · 10-minute TTL
                      </span>
                    </div>
                    {!snapshot.demo_controls_enabled && (
                      <div className="inline-note">
                        Local demo controls are disabled on the server.
                      </div>
                    )}
                    {!faults.available && (
                      <div className="inline-note">
                        Fault injector unavailable. Injection is disabled until
                        its state can be read.
                      </div>
                    )}
                    <div className="scenario-grid">
                      {scenarios.map((s, i) => {
                        const active = activeFaults.find(
                          (a) => a.scenario_id === s.id,
                        );
                        return (
                          <article
                            key={s.id}
                            className={`scenario ${active ? "injected" : ""}`}
                          >
                            <div className="scenario-top">
                              <span className="scenario-icon">
                                <s.icon size={22} />
                              </span>
                              <span className="mono">{s.id}</span>
                              {active && <Badge value="ACTIVE" />}
                            </div>
                            <h3>{s.name}</h3>
                            <p>{s.description}</p>
                            <div className="scenario-signal">
                              <Activity size={14} />
                              {s.signal}
                            </div>
                            <button
                              className={
                                active ? "danger-outline" : "scenario-button"
                              }
                              disabled={
                                !operator ||
                                !fresh ||
                                !snapshot.demo_controls_enabled ||
                                !faults.available ||
                                busy ||
                                (!active && activeFaults.length > 0)
                              }
                              onClick={() =>
                                faultCommand(s, active ? "clear" : "inject")
                              }
                            >
                              {active ? (
                                <>
                                  <X size={15} />
                                  Clear fault{" "}
                                  <small>
                                    {duration(
                                      Math.max(
                                        0,
                                        active.injected_at +
                                          active.ttl_seconds -
                                          now / 1000,
                                      ),
                                    )}{" "}
                                    TTL
                                  </small>
                                </>
                              ) : (
                                <>
                                  <Zap size={15} />
                                  Inject fault
                                  <ArrowUpRight size={16} />
                                </>
                              )}
                            </button>
                            {!active && (
                              <button
                                className="clear-reconcile"
                                disabled={
                                  !operator ||
                                  !fresh ||
                                  !snapshot.demo_controls_enabled ||
                                  !faults.available ||
                                  busy
                                }
                                onClick={() => faultCommand(s, "clear")}
                              >
                                Clear / reconcile scenario
                              </button>
                            )}
                          </article>
                        );
                      })}
                    </div>
                    {!operator && (
                      <p className="inline-note">
                        Your viewer role can observe scenarios. An operator or
                        administrator can inject faults.
                      </p>
                    )}
                  </section>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <span className="section-number">03</span>
                        <h2>Recovery journey</h2>
                        {detail && <Badge value={detail.incident.state} />}
                      </div>
                      <div className="actions">
                        <select
                          aria-label="Follow incident"
                          value={selectedId || detail?.incident.id || ""}
                          onChange={(e) => setSelectedId(e.target.value)}
                        >
                          <option value="">Follow latest incident</option>
                          {snapshot.incidents.map((i: Row) => (
                            <option key={i.id} value={i.id}>
                              {i.id.slice(0, 8)} · {nice(i.state)}
                            </option>
                          ))}
                        </select>
                        {detail && (
                          <Link
                            className="text-link"
                            href={`/incidents/${detail.incident.id}`}
                          >
                            Investigate
                            <ArrowUpRight size={15} />
                          </Link>
                        )}
                      </div>
                    </div>
                    <Journey detail={detail} />
                    {detail?.incident.state === "VERIFYING" && (
                      <LiveVerification detail={detail} />
                    )}{" "}
                    {detail ? (
                      <div className="journey-summary">
                        <span>
                          <strong>
                            {nice(
                              detail.diagnoses?.[0]?.root_cause ||
                                "Collecting evidence",
                            )}
                          </strong>{" "}
                          · {detail.resource?.name}
                        </span>
                        <span>
                          {detail.incident.state === "RESOLVED"
                            ? "Recovery independently verified"
                            : detail.incident.state === "VERIFYING"
                              ? "Restart completed. Independent observations in progress."
                              : detail.incident.state === "PENDING_APPROVAL"
                                ? "Review the plan in the incident workspace to approve or reject."
                                : "Following persisted workflow state."}
                        </span>
                      </div>
                    ) : (
                      <div className="inline-note">
                        No incidents yet. Start with a fault injection to see
                        the recovery loop unfold.
                      </div>
                    )}
                  </section>
                  <section>
                    <div className="section-heading">
                      <h2>Traffic & health signals</h2>
                      <label className="inline-label">
                        Window
                        <select
                          value={minutes}
                          onChange={(e) => setMinutes(Number(e.target.value))}
                        >
                          <option value={5}>5 minutes</option>
                          <option value={10}>10 minutes</option>
                          <option value={30}>30 minutes</option>
                          <option value={60}>1 hour</option>
                        </select>
                      </label>
                    </div>
                    {!telemetryFresh && (
                      <div className="inline-note">
                        Telemetry is unavailable or stale. Charts may show the
                        last known observations.
                      </div>
                    )}
                    <div className="charts-grid">
                      <Chart
                        label="Request throughput"
                        unit="req/s"
                        signal={signals.rps}
                        markers={chartMarkers}
                      />
                      <Chart
                        label="P95 request latency"
                        unit="ms"
                        signal={signals.latency}
                        markers={chartMarkers}
                        color="#54d6c6"
                      />
                      <Chart
                        label="Server error rate"
                        unit="%"
                        signal={signals.errors}
                        markers={chartMarkers}
                        color="#f3a97b"
                      />
                      <Chart
                        label="CPU budget utilization"
                        unit="%"
                        signal={signals.cpu}
                        markers={chartMarkers}
                        color="#a1b9ff"
                      />
                    </div>
                  </section>
                </>
              )}
              {pathname === "/incidents" && (
                <IncidentList
                  resources={snapshot.resources}
                  onError={readError}
                />
              )}
              {pathname.startsWith("/incidents/") &&
                (detail?.incident.id === pathname.split("/")[2] ? (
                  <IncidentDetail
                    detail={detail}
                    operator={operator && fresh}
                    approver={approver && fresh}
                    onApprove={approve}
                    onDryRun={dryRun}
                    onError={readError}
                    now={now}
                  />
                ) : (
                  <div className="empty-state">Loading incident records…</div>
                ))}
              {pathname === "/resources" && (
                <Resources
                  admin={admin && fresh}
                  automation={snapshot.automation}
                  confirm={setConfirm}
                  mutate={mutate}
                  onError={readError}
                  refreshKey={lastUpdate}
                />
              )}
              {pathname === "/policies" && (
                <Policies
                  admin={admin && fresh}
                  automation={snapshot.automation}
                  confirm={setConfirm}
                  mutate={mutate}
                  onError={readError}
                />
              )}
              {pathname === "/system" && (
                <System onError={readError} refreshKey={lastUpdate} />
              )}
            </>
          )}
          <footer className="footer">
            <span>
              <ShieldCheck size={14} /> Sentinel / M1 self-healing lab
            </span>
            <span>Observation → decision → action → verified recovery</span>
            <a href="http://localhost:3000" target="_blank" rel="noreferrer">
              Open Grafana
              <ArrowUpRight size={13} />
            </a>
          </footer>
        </main>
      </div>
      {confirm && (
        <Confirm
          value={confirm}
          busy={busy}
          onClose={() => setConfirm(null)}
          onConfirm={confirmed}
        />
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  unit,
  note,
  icon: Icon,
}: {
  label: string;
  value: string | number;
  unit?: string;
  note: string;
  icon: typeof Activity;
}) {
  return (
    <article className="metric">
      <div>
        <span>{label}</span>
        <Icon size={17} />
      </div>
      <strong>
        {value}
        <small>{unit}</small>
      </strong>
      <p>{note}</p>
    </article>
  );
}

function IncidentList({
  resources,
  onError,
}: {
  resources: Row[];
  onError: (e: unknown) => void;
}) {
  const [data, setData] = useState<Row>({ items: [], total: 0 });
  const [state, setState] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const d = await api(
          `/console/incidents?offset=${offset}&limit=25${state ? `&state=${state}` : ""}`,
        );
        if (alive) setData(d);
      } catch (e) {
        if (alive) onError(e);
      } finally {
        if (alive) setLoading(false);
      }
    }
    setLoading(true);
    void load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [offset, state, onError]);
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>
          Incident queue <span className="subtle-pill">{data.total}</span>
        </h2>
        <label className="inline-label">
          State
          <select
            value={state}
            onChange={(e) => {
              setState(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All states</option>
            {[
              "DETECTED",
              "TRIAGED",
              "DIAGNOSED",
              "PLANNED",
              "PENDING_APPROVAL",
              "EXECUTING",
              "VERIFYING",
              "RESOLVED",
              "ESCALATED",
              "ROLLEDBACK",
            ].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Incident</th>
              <th>Resource</th>
              <th>State</th>
              <th>Severity</th>
              <th>Retries used</th>
              <th>Updated</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.items.map((i: Row) => (
              <tr key={i.id}>
                <td>
                  <Link className="incident-link" href={`/incidents/${i.id}`}>
                    {i.id.slice(0, 8)}
                    <small>{i.correlation_key}</small>
                  </Link>
                </td>
                <td>
                  {resources.find((r) => r.id === i.resource_id)?.name ||
                    i.resource_id.slice(0, 8)}
                </td>
                <td>
                  <Badge value={i.state} />
                </td>
                <td>{nice(i.severity)}</td>
                <td>
                  {i.attempts} / {i.retry_limit}
                </td>
                <td>{stamp(i.updated_at)}</td>
                <td>
                  <Link
                    aria-label={`Open incident ${i.id.slice(0, 8)}`}
                    href={`/incidents/${i.id}`}
                  >
                    <ArrowUpRight size={17} />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!data.items.length && (
        <div className="empty-state">
          {loading ? "Loading incidents…" : "No incidents match this view."}
        </div>
      )}
      <div className="pagination">
        <span>
          {data.total
            ? `${offset + 1}–${Math.min(offset + 25, data.total)} of ${data.total}`
            : "0 incidents"}
        </span>
        <button
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 25))}
        >
          <ChevronLeft size={16} />
          Previous
        </button>
        <button
          disabled={offset + 25 >= data.total}
          onClick={() => setOffset(offset + 25)}
        >
          Next
          <ChevronRight size={16} />
        </button>
      </div>
    </section>
  );
}

function IncidentDetail({
  detail: d,
  operator,
  approver,
  onApprove,
  onDryRun,
  onError,
  now,
}: {
  detail: Row;
  operator: boolean;
  approver: boolean;
  onApprove: (v: "APPROVE" | "REJECT") => void;
  onDryRun: () => void;
  onError: (e: unknown) => void;
  now: number;
}) {
  const [tab, setTab] = useState("Overview");
  const [timeline, setTimeline] = useState<Row>({
    items: [],
    next_cursor: null,
  });
  const [older, setOlder] = useState<Row[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [olderLoaded, setOlderLoaded] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  useEffect(() => {
    setOlder([]);
    setOlderLoaded(false);
    setCursor(null);
  }, [d.incident.id]);
  useEffect(() => {
    let alive = true;
    api(`/console/incidents/${d.incident.id}/timeline`)
      .then((t) => {
        if (alive) setTimeline(t);
      })
      .catch(onError);
    return () => {
      alive = false;
    };
  }, [d.incident.id, d.incident.version, d.server_time, onError]);
  const allTimeline = [
    ...new Map(
      [...timeline.items, ...older].map((r: Row) => [r.sequence, r]),
    ).values(),
  ].sort((a, b) => b.sequence - a.sequence);
  const p = d.plans[0];
  const v = d.verifications[0];
  const observation = d.observations?.[0];
  const sample = observation?.payload;
  const diag = d.diagnoses[0];
  const tabs = [
    "Overview",
    "Evidence",
    "Plan & policy",
    "Execution",
    "Verification",
    "Audit timeline",
  ];
  return (
    <>
      <Link className="text-link back-link" href="/incidents">
        <ChevronLeft size={15} />
        All incidents
      </Link>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2 className="mono">{d.incident.id.slice(0, 8)}</h2>
            <Badge value={d.incident.state} />
            <span className="caption">
              {d.resource?.name} · v{d.incident.version}
            </span>
          </div>
          <span className="caption">
            Created {new Date(d.incident.created_at).toLocaleString()}
          </span>
        </div>
        <Journey detail={d} />
        {d.incident.state === "PENDING_APPROVAL" && p && (
          <div className="approval-banner">
            <div>
              <ShieldCheck />
              <span>
                <strong>Human approval required</strong>
                <small>
                  Review plan v{p.version}, target binding and policy before
                  deciding.
                </small>
              </span>
            </div>
            {approver ? (
              <div className="actions">
                <button onClick={() => onApprove("REJECT")}>Reject</button>
                <button
                  className="primary"
                  onClick={() => onApprove("APPROVE")}
                >
                  Approve plan
                  <Check size={15} />
                </button>
              </div>
            ) : (
              <span>An approver or administrator must decide.</span>
            )}
          </div>
        )}
        <div className="tabs" role="tablist" aria-label="Incident sections">
          {tabs.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              aria-controls="incident-tab-panel"
              id={`tab-${t.replaceAll(" ", "-")}`}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>
      </section>
      <section
        id="incident-tab-panel"
        role="tabpanel"
        aria-labelledby={`tab-${tab.replaceAll(" ", "-")}`}
      >
        {tab === "Overview" && (
          <div className="detail-grid">
            <section className="panel padded">
              <span className="eyebrow">WHAT THE SYSTEM KNOWS</span>
              <h2>{nice(diag?.root_cause || "Diagnosis pending")}</h2>
              <p>
                {(typeof diag?.reasoning === "string"
                  ? diag.reasoning
                  : diag?.reasoning?.summary ||
                    diag?.reasoning?.explanation ||
                    JSON.stringify(diag?.reasoning)) ||
                  "The agent is collecting evidence. No root cause has been recorded yet."}
              </p>
              <dl>
                <dt>Confidence</dt>
                <dd>
                  {diag?.confidence != null
                    ? `${(diag.confidence * 100).toFixed(0)}%`
                    : "—"}
                </dd>
                <dt>Rule</dt>
                <dd>
                  {diag?.rule_id || "—"}{" "}
                  {diag?.rule_version && `/ v${diag.rule_version}`}
                </dd>
                <dt>Retries used</dt>
                <dd>
                  {d.incident.attempts} / {d.incident.retry_limit}
                </dd>
                <dt>Resolved at</dt>
                <dd>
                  {d.incident.resolved_at
                    ? new Date(d.incident.resolved_at).toLocaleString()
                    : "Not resolved"}
                </dd>
              </dl>
              {diag?.contradictory_findings?.length > 0 && (
                <JsonDetails
                  title="Contradictory findings"
                  data={diag.contradictory_findings}
                  open
                />
              )}
            </section>
            <section className="panel padded">
              <span className="eyebrow">WHAT HAPPENS NEXT</span>
              <h2>{nice(d.incident.state)}</h2>
              <p>
                {d.incident.state === "VERIFYING"
                  ? "The execution has ended. The independent verifier must now observe sustained healthy behavior before resolving this incident."
                  : d.incident.state === "RESOLVED"
                    ? "The incident has a persisted final recovery state. Inspect verification for checks and attribution."
                    : d.incident.state === "ESCALATED"
                      ? "The autonomous path has stopped. Review the escalation record and evidence."
                      : "Follow the workflow wait reason and latest policy evaluation below."}
              </p>
              {d.schedules
                .filter((s: Row) => s.status === "PENDING")
                .map((s: Row) => (
                  <div className="record" key={s.id}>
                    <Badge value={s.wait_reason} />
                    <p>
                      Next wake-up: {new Date(s.next_run_at).toLocaleString()}
                    </p>
                    <small>
                      Deadline:{" "}
                      {s.deadline
                        ? new Date(s.deadline).toLocaleString()
                        : "None"}
                    </small>
                  </div>
                ))}
              {d.attention.map((a: Row) => (
                <div className="notice warn" key={a.id}>
                  {a.message}
                </div>
              ))}
              {d.escalations.map((e: Row) => (
                <JsonDetails
                  key={e.id}
                  title={`${e.ticket_reference}: ${e.title}`}
                  data={e}
                  open
                />
              ))}
            </section>
          </div>
        )}
        {tab === "Evidence" && (
          <div className="panel padded">
            <h2>Evidence collected</h2>
            <p>
              Observed signals with source, freshness, units and provenance.
            </p>
            <RecordCollection
              incidentId={d.incident.id}
              collection="evidence"
              initial={d.evidence}
              onError={onError}
              render={(e) => (
                <article className="record" key={e.id}>
                  <div className="record-heading">
                    <strong>{nice(e.kind)}</strong>
                    <span>
                      {e.source} · {stamp(e.observed_at)}
                    </span>
                  </div>
                  <div className="caption">
                    Unit: {e.unit || "not applicable"} · binding generation{" "}
                    {e.binding_generation ?? "—"} ·{" "}
                    {duration(
                      Math.max(0, (now - Date.parse(e.observed_at)) / 1000),
                    )}{" "}
                    old
                  </div>
                  <JsonDetails
                    title="Inspect evidence payload"
                    data={e.content}
                  />
                  <code className="hash">SHA-256 {e.sha256}</code>
                </article>
              )}
            />
            {d.events.map((e: Row) => (
              <JsonDetails
                key={e.id}
                title={`Source event · ${e.source} · ${stamp(e.occurred_at)}`}
                data={e}
              />
            ))}
          </div>
        )}
        {tab === "Plan & policy" && (
          <div className="detail-grid">
            <section className="panel padded">
              <div className="section-heading">
                <h2>Remediation plan</h2>
                <button disabled={!operator || !p} onClick={onDryRun}>
                  <Play size={14} />
                  Dry run
                </button>
              </div>
              {p ? (
                <>
                  <Badge value={p.risk} />
                  <dl>
                    <dt>Version</dt>
                    <dd>{p.version}</dd>
                    <dt>Container binding</dt>
                    <dd className="mono break">{p.container_id}</dd>
                    <dt>Generation</dt>
                    <dd>{p.binding_generation}</dd>
                    <dt>Verification profile</dt>
                    <dd>{p.verification_profile}</dd>
                  </dl>
                  <code className="hash">SHA-256 {p.content_hash}</code>
                  {d.steps
                    .filter((s: Row) => s.plan_id === p.id)
                    .sort((a: Row, b: Row) => a.position - b.position)
                    .map((s: Row) => (
                      <div className="record" key={s.id}>
                        <strong>
                          {s.position + 1}. {nice(s.action)}
                        </strong>
                        <JsonDetails
                          title="Parameters and verification contract"
                          data={{
                            parameters: s.parameters,
                            verification: s.verification,
                            schema: s.action_schema_version,
                          }}
                        />
                      </div>
                    ))}
                  <RecordCollection
                    incidentId={d.incident.id}
                    collection="plans"
                    initial={d.plans}
                    onError={onError}
                    render={(r) => (
                      <JsonDetails
                        key={r.id}
                        title={`Plan v${r.version} · ${stamp(r.created_at)}`}
                        data={r}
                      />
                    )}
                  />
                </>
              ) : (
                <p>No plan has been recorded.</p>
              )}
            </section>
            <section className="panel padded">
              <h2>Policy decisions</h2>
              <RecordCollection
                incidentId={d.incident.id}
                collection="policy_decisions"
                initial={d.policy_decisions}
                onError={onError}
                render={(r) => (
                  <div className="record" key={r.id}>
                    <Badge value={r.decision} />
                    <p>{r.reason_codes.join(" · ") || "No reason codes"}</p>
                    <JsonDetails
                      title={`Policy v${r.policy_version} · plan v${r.plan_version}`}
                      data={r}
                    />
                  </div>
                )}
              />
              <h3>Approval history</h3>
              {d.approvals.map((a: Row) => (
                <JsonDetails
                  key={a.id}
                  title={`${a.decision} · plan v${a.plan_version}`}
                  data={a}
                />
              ))}
              <h3>Dry-run history</h3>
              <p className="caption">
                Simulations do not authorize execution or verify recovery.
              </p>
              <RecordCollection
                incidentId={d.incident.id}
                collection="dry_runs"
                initial={d.dry_runs}
                onError={onError}
                render={(r) => (
                  <JsonDetails
                    key={r.id}
                    title={`Simulation · ${stamp(r.created_at)}`}
                    data={r}
                  />
                )}
              />
            </section>
          </div>
        )}
        {tab === "Execution" && (
          <section className="panel padded">
            <h2>Execution attempts</h2>
            <p>
              A successful execution means the action finished. Recovery
              requires independent verification.
            </p>
            <RecordCollection
              incidentId={d.incident.id}
              collection="executions"
              initial={d.executions}
              onError={onError}
              render={(e) => (
                <div className="record" key={e.id}>
                  <div className="record-heading">
                    <strong>Attempt {e.attempt_number}</strong>
                    <Badge value={e.status} />
                  </div>
                  <dl>
                    <dt>Started</dt>
                    <dd>{stamp(e.started_at)}</dd>
                    <dt>Finished</dt>
                    <dd>{stamp(e.finished_at)}</dd>
                    <dt>Container</dt>
                    <dd className="mono break">{e.container_id}</dd>
                    <dt>Binding generation</dt>
                    <dd>{e.binding_generation}</dd>
                  </dl>
                  {e.uncertainty_reason && (
                    <div className="notice warn">{e.uncertainty_reason}</div>
                  )}
                  <JsonDetails
                    title="Pre-state and result"
                    data={{ pre_state: e.pre_state, result: e.result }}
                  />
                </div>
              )}
            />
          </section>
        )}
        {tab === "Verification" && (
          <section className="panel padded">
            <div className="section-heading">
              <h2>Independent recovery verification</h2>
              <Badge
                value={
                  d.incident.state === "VERIFYING"
                    ? "VERIFYING"
                    : v
                      ? v.passed
                        ? "PASS"
                        : "FAILED"
                      : "PENDING"
                }
              />
            </div>
            <p>
              Readiness warm-up, sustained healthy samples, and recovery
              attribution determine the final verdict.
            </p>
            {d.incident.state === "VERIFYING" && (
              <div className="verification-live">
                <Radio size={19} />
                <div>
                  <strong>Observations in progress</strong>
                  <p>
                    {sample
                      ? `Sample ${sample.sample_index ?? "—"} · healthy duration reported: ${duration(sample.elapsed_healthy_seconds)} · observed ${stamp(observation.created_at)}`
                      : "Waiting for the first persisted observation."}
                  </p>
                  <small>Preliminary samples are not a recovery verdict.</small>
                </div>
              </div>
            )}
            {sample && d.incident.state === "VERIFYING" && (
              <Checks checks={sample.checks || {}} />
            )}
            <RecordCollection
              incidentId={d.incident.id}
              collection="verifications"
              initial={d.verifications}
              onError={onError}
              render={(r) => (
                <div className="record" key={r.id}>
                  <div className="record-heading">
                    <strong>
                      Attempt {r.attempt_number} · profile {r.profile_version}
                    </strong>
                    <Badge value={r.passed ? "PASS" : "FAILED"} />
                  </div>
                  <dl>
                    <dt>Attribution</dt>
                    <dd>{nice(r.attribution)}</dd>
                    <dt>Observation window</dt>
                    <dd>
                      {stamp(r.window_start)} → {stamp(r.window_end)}
                    </dd>
                    <dt>Warm-up</dt>
                    <dd>{duration(r.warm_up_duration_seconds)}</dd>
                    <dt>Stabilization resets</dt>
                    <dd>{r.stabilization_resets}</dd>
                    <dt>Health score</dt>
                    <dd>{r.health_score}</dd>
                  </dl>
                  <Checks checks={r.checks || {}} />
                  <JsonDetails
                    title={`Inspect ${r.samples?.length || 0} verification samples`}
                    data={r.samples}
                  />
                </div>
              )}
            />
            <h3>Live observation history</h3>
            <RecordCollection
              incidentId={d.incident.id}
              collection="observations"
              initial={d.observations || []}
              onError={onError}
              render={(r) => (
                <JsonDetails
                  key={r.id}
                  title={`Sample · ${stamp(r.created_at)} · ${r.payload.all_passed ? "passing" : "not passing"}`}
                  data={r.payload}
                />
              )}
            />
          </section>
        )}
        {tab === "Audit timeline" && (
          <section className="panel padded">
            <div className="section-heading">
              <h2>Durable audit timeline</h2>
              <span className="caption">
                Newest first · server sequence order
              </span>
            </div>
            <div className="audit-timeline">
              {allTimeline.map((e: Row) => (
                <article key={e.sequence}>
                  <span className="timeline-dot" />
                  <div className="record-heading">
                    <strong>
                      {nice(e.entity_type)} · {nice(e.operation)}
                    </strong>
                    <time>{stamp(e.created_at)}</time>
                  </div>
                  <p className="caption">
                    #{e.sequence} · {e.actor}
                  </p>
                  <JsonDetails title="Before / after" data={e.details} />
                </article>
              ))}
            </div>
            {!allTimeline.length && <p>No audit records yet.</p>}
            {(olderLoaded ? cursor : timeline.next_cursor) && (
              <button
                disabled={loadingOlder}
                onClick={async () => {
                  setLoadingOlder(true);
                  try {
                    const result = await api(
                      `/console/incidents/${d.incident.id}/timeline?before=${olderLoaded ? cursor : timeline.next_cursor}`,
                    );
                    setOlder((old) => [...old, ...result.items]);
                    setCursor(result.next_cursor);
                    setOlderLoaded(true);
                  } catch (e) {
                    onError(e);
                  } finally {
                    setLoadingOlder(false);
                  }
                }}
              >
                {loadingOlder ? "Loading…" : "Load older events"}
              </button>
            )}
          </section>
        )}
      </section>
    </>
  );
}

function Checks({ checks }: { checks: Row }) {
  return (
    <div className="checks-grid">
      {Object.entries(checks).map(([name, c]) => (
        <div key={name}>
          <div>
            <strong>{nice(name)}</strong>
            <Badge value={typeof c === "object" ? c.status : String(c)} />
          </div>
          <p>{c?.reason || c?.message || ""}</p>
          {c?.value != null && (
            <small>
              Observed: {String(c.value)} {c.unit || ""}
            </small>
          )}
        </div>
      ))}
    </div>
  );
}
function RecordCollection({
  incidentId,
  collection,
  initial,
  onError,
  render,
}: {
  incidentId: string;
  collection: string;
  initial: Row[];
  onError: (e: unknown) => void;
  render: (r: Row) => React.ReactNode;
}) {
  const [extra, setExtra] = useState<Row[]>([]);
  const [more, setMore] = useState(initial.length >= 50);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setExtra([]);
    setMore(initial.length >= 50);
  }, [incidentId, collection, initial.length]);
  const merged = [
    ...new Map([...initial, ...extra].map((r) => [r.id, r])).values(),
  ];
  return (
    <>
      {merged.map(render)}
      {!merged.length && <p className="empty-inline">No records yet.</p>}
      {more && (
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const result = await api(
                `/console/incidents/${incidentId}/records/${collection}?offset=${merged.length}`,
              );
              setExtra([...extra, ...result.items]);
              setMore(merged.length + result.items.length < result.total);
            } catch (e) {
              onError(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          Load more records
        </button>
      )}
    </>
  );
}

function Resources({
  admin,
  automation,
  confirm,
  mutate,
  onError,
  refreshKey,
}: {
  admin: boolean;
  automation: Row;
  confirm: (c: Confirmation) => void;
  mutate: (p: string, b: Row) => Promise<Row>;
  onError: (e: unknown) => void;
  refreshKey: number;
}) {
  const [data, setData] = useState<Row>({ items: [], locks: [] });
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    api(`/console/resources?offset=${offset}`).then(setData).catch(onError);
  }, [refreshKey, offset, onError]);
  return (
    <>
      <div className="resource-grid">
        {data.items.map((r: Row) => {
          const stopped = automation?.emergency_stopped_resources?.some(
            (id: string) => [r.id, r.external_id, r.name].includes(id),
          );
          const lock = data.locks.find((l: Row) => l.resource_id === r.id);
          return (
            <section className="panel padded" key={r.id}>
              <div className="section-heading">
                <span className="resource-icon">
                  <Server />
                </span>
                <Badge value={r.managed ? "MANAGED" : "UNMANAGED"} />
              </div>
              <h2>{r.name}</h2>
              <p>
                {r.provider} / {r.environment}
              </p>
              <dl>
                <dt>Binding generation</dt>
                <dd>{r.labels?.binding_generation ?? "—"}</dd>
                <dt>Container ID</dt>
                <dd className="mono break">
                  {r.labels?.docker_container_id ||
                    r.labels?.container_id ||
                    "Not bound"}
                </dd>
                <dt>Resource lock</dt>
                <dd>
                  {lock
                    ? `${lock.owner} · expires ${stamp(lock.expires_at)}`
                    : "No lock recorded"}
                </dd>
                <dt>Emergency stop</dt>
                <dd>{stopped ? "Active" : "Inactive"}</dd>
              </dl>
              <JsonDetails
                title="Resource labels and lock"
                data={{ labels: r.labels, lock }}
              />
              <button
                className={stopped ? "" : "danger-outline"}
                disabled={!admin}
                onClick={() =>
                  confirm({
                    title: stopped
                      ? "Release emergency stop?"
                      : "Stop automation for this resource?",
                    description: `Target: ${r.name}. ${stopped ? "Allow policy-governed actions again." : "Block new mutations for this resource. An already dispatched action may still finish."}`,
                    label: stopped ? "Release stop" : "Emergency stop",
                    reason: true,
                    danger: !stopped,
                    run: async (reason) => {
                      await mutate("/automation/emergency-stop", {
                        resource_identifier: r.id,
                        action: stopped ? "RELEASE" : "STOP",
                        reason,
                      });
                    },
                  })
                }
              >
                {stopped ? <Play size={15} /> : <Siren size={15} />}{" "}
                {stopped ? "Release stop" : "Emergency stop"}
              </button>
            </section>
          );
        })}
      </div>
      {!data.items.length && (
        <div className="empty-state">No resources discovered.</div>
      )}
      <div className="pagination">
        <button disabled={!offset} onClick={() => setOffset(offset - 50)}>
          Previous
        </button>
        <span>Page {offset / 50 + 1}</span>
        <button
          disabled={data.items.length < 50}
          onClick={() => setOffset(offset + 50)}
        >
          Next
        </button>
      </div>
    </>
  );
}
function Policies({
  admin,
  automation,
  confirm,
  mutate,
  onError,
}: {
  admin: boolean;
  automation: Row;
  confirm: (c: Confirmation) => void;
  mutate: (p: string, b: Row) => Promise<Row>;
  onError: (e: unknown) => void;
}) {
  const [data, setData] = useState<Row>({ items: [] });
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    api(`/console/policies?offset=${offset}`).then(setData).catch(onError);
  }, [offset, onError]);
  return (
    <>
      <section className="panel padded">
        <div className="section-heading">
          <h2>Automation mode</h2>
          <Badge value={automation?.mode} />
        </div>
        <p>
          Changes apply to future policy decisions. Every change requires an
          administrator and an audit reason.
        </p>
        <div className="mode-grid">
          {[
            {
              id: "DISABLED",
              title: "Paused",
              text: "Observe incidents; block workload mutations.",
              icon: Pause,
            },
            {
              id: "APPROVAL_REQUIRED",
              title: "Human approval",
              text: "Review and approve each eligible plan.",
              icon: ShieldCheck,
            },
            {
              id: "AUTOMATIC",
              title: "Automatic",
              text: "Execute eligible plans within policy boundaries.",
              icon: Zap,
            },
          ].map((m) => (
            <button
              key={m.id}
              className={`mode-option ${automation?.mode === m.id ? "selected" : ""}`}
              disabled={!admin || automation?.mode === m.id}
              onClick={() =>
                confirm({
                  title: `Switch to ${m.title.toLowerCase()}?`,
                  description:
                    m.text +
                    " Existing executions may still finish. This setting does not bypass resource stops, binding validation or verification.",
                  label: "Change mode",
                  reason: true,
                  run: async (reason) => {
                    await mutate("/automation/mode", { mode: m.id, reason });
                  },
                })
              }
            >
              <m.icon size={21} />
              <strong>{m.title}</strong>
              <small>{m.text}</small>
              {automation?.mode === m.id && <Badge value="CURRENT" />}
            </button>
          ))}
        </div>
        <p className="caption">
          Last changed by {automation?.updated_by || "—"} ·{" "}
          {stamp(automation?.updated_at)} · {automation?.reason}
        </p>
      </section>
      <section className="panel padded">
        <h2>Persisted policies</h2>
        <p>
          Read-only policy definitions. Decisions for specific plans appear in
          the incident workspace.
        </p>
        {data.items.map((p: Row) => (
          <div className="record" key={p.id}>
            <div className="record-heading">
              <strong>
                {p.name} · v{p.version}
              </strong>
              <Badge value={p.enabled ? "ENABLED" : "DISABLED"} />
            </div>
            <p>
              {p.environment} · {nice(p.action)} · {nice(p.risk)} risk
            </p>
            <JsonDetails title="Policy rules" data={p.rules} open />
          </div>
        ))}
        {!data.items.length && <p>No policy records.</p>}
        <div className="pagination">
          <button disabled={!offset} onClick={() => setOffset(offset - 50)}>
            Previous
          </button>
          <span>Page {offset / 50 + 1}</span>
          <button
            disabled={data.items.length < 50}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </button>
        </div>
      </section>
    </>
  );
}
function System({
  onError,
  refreshKey,
}: {
  onError: (e: unknown) => void;
  refreshKey: number;
}) {
  const [data, setData] = useState<Row | null>(null);
  const [commands, setCommands] = useState<Row[]>([]);
  useEffect(() => {
    api("/console/system").then(setData).catch(onError);
    api("/console/commands")
      .then((d) => setCommands(d.items))
      .catch(onError);
  }, [refreshKey, onError]);
  return (
    <>
      {data ? (
        <>
          <div className="metric-grid">
            <Metric
              label="Database"
              value={data.database_connected ? "Connected" : "Unavailable"}
              note="Control plane storage"
              icon={Layers}
            />
            <Metric
              label="Redis"
              value={data.redis_connected ? "Connected" : "Unavailable"}
              note="Workflow stream"
              icon={Radio}
            />
            <Metric
              label="Outbox backlog"
              value={data.outbox_backlog ?? "—"}
              note="Pending publication"
              icon={Activity}
            />
            <Metric
              label="Workers"
              value={data.worker_heartbeats?.length || 0}
              note="Recorded worker heartbeats"
              icon={Cpu}
            />
          </div>
          <section className="panel padded">
            <h2>Worker health</h2>
            {data.worker_heartbeats?.map((w: Row) => (
              <div className="record" key={w.worker_id}>
                <div className="record-heading">
                  <strong>{w.worker_id}</strong>
                  <Badge
                    value={
                      Date.now() - Date.parse(w.last_heartbeat) > 60000
                        ? "STALE"
                        : w.status
                    }
                  />
                </div>
                <p>Last heartbeat {stamp(w.last_heartbeat)}</p>
                <JsonDetails title="Worker metadata" data={w} />
              </div>
            ))}
            {!data.worker_heartbeats?.length && (
              <p>No workers have reported a heartbeat.</p>
            )}
            <JsonDetails
              title="Wake-ups, backlog and last processing error"
              data={data}
            />
          </section>
        </>
      ) : (
        <div className="empty-state">Waiting for system health…</div>
      )}
      <section className="panel padded">
        <h2>Fault command history</h2>
        <p>
          Durable command intent and results. An uncertain or pending command
          must be investigated before retrying.
        </p>
        {commands.map((c) => (
          <div key={c.id} className="record">
            <div className="record-heading">
              <strong>
                {c.scenario_id} · {c.action}
              </strong>
              <Badge value={c.status} />
            </div>
            <p className="caption">
              {c.actor} · {stamp(c.created_at)}
            </p>
            <JsonDetails title="Command result" data={c.result} />
          </div>
        ))}
        {!commands.length && <p>No fault commands have been submitted.</p>}
      </section>
    </>
  );
}
