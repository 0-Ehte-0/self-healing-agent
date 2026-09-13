"use client";
import { useState } from "react";
import {
  Activity,
  Database,
  Server,
  Cpu,
  Radio,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { Row, nice } from "@/lib/api";

export function Badge({ value }: { value?: string }) {
  const tone = /RESOLVED|PASS|SUCCEEDED|HEALTHY|AUTOMATIC/.test(value || "")
    ? "good"
    : /FAIL|ESCALAT|STOP|DENY|DISABLED|UNCERTAIN/.test(value || "")
      ? "bad"
      : /APPROVAL|VERIFY|PENDING|DEFER/.test(value || "")
        ? "warn"
        : "neutral";
  return (
    <span className={`badge ${tone}`}>
      <i />
      {nice(value)}
    </span>
  );
}

export function Chart({
  signal,
  label,
  unit,
  color = "#a99aff",
  markers = [],
}: {
  signal?: Row;
  label: string;
  unit: string;
  color?: string;
  markers?: { time: number; label: string; color: string }[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const points: [number, number | null][] = signal?.points || [];
  const valid = points.filter((p) => p[1] != null);
  const max =
    Math.max(...valid.map((p) => p[1]!), unit === "%" ? 100 : 1) * 1.1;
  const point =
    hover === null ? null : points[Math.min(hover, points.length - 1)];
  const path = points
    .map((p, i) =>
      p[1] == null
        ? ""
        : `${i === 0 || points[i - 1][1] == null ? "M" : "L"}${(i / Math.max(points.length - 1, 1)) * 600},${105 - (p[1] / max) * 95}`,
    )
    .join(" ");
  return (
    <div className="chart-card">
      <div className="chart-heading">
        <span>{label}</span>
        <strong>
          {signal?.latest == null
            ? "—"
            : Number(signal.latest).toFixed(unit === "%" ? 1 : 2)}{" "}
          <small>{unit}</small>
        </strong>
      </div>
      {valid.length ? (
        <div className="chart-wrap">
          <svg
            viewBox="0 0 600 120"
            role="img"
            aria-label={`${label}, ${points.length} samples; latest ${signal?.latest ?? "unavailable"} ${unit}`}
            onMouseLeave={() => setHover(null)}
            onMouseMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              setHover(
                Math.round(
                  ((e.clientX - r.left) / r.width) * (points.length - 1),
                ),
              );
            }}
          >
            {[25, 65, 105].map((y) => (
              <line
                key={y}
                x1="0"
                y1={y}
                x2="600"
                y2={y}
                stroke="#252c3a"
                strokeDasharray="3 6"
              />
            ))}
            <path d={path} fill="none" stroke={color} strokeWidth="2.5" />
            {markers
              .filter(
                (m) =>
                  m.time >= points[0][0] &&
                  m.time <= points[points.length - 1][0],
              )
              .map((m) => {
                const x =
                  ((m.time - points[0][0]) /
                    (points[points.length - 1][0] - points[0][0])) *
                  600;
                return (
                  <g key={`${m.label}-${m.time}`}>
                    <line
                      x1={x}
                      x2={x}
                      y1="10"
                      y2="110"
                      stroke={m.color}
                      strokeDasharray="3 4"
                    />
                    <text
                      x={Math.min(x + 5, 515)}
                      y="9"
                      fill={m.color}
                      fontSize="10"
                    >
                      {m.label}
                    </text>
                  </g>
                );
              })}
            {point && point[1] != null && (
              <>
                <line
                  x1={(hover! / Math.max(points.length - 1, 1)) * 600}
                  x2={(hover! / Math.max(points.length - 1, 1)) * 600}
                  y1="0"
                  y2="110"
                  stroke="#7e879b"
                />
                <circle
                  cx={(hover! / Math.max(points.length - 1, 1)) * 600}
                  cy={105 - (point[1] / max) * 95}
                  r="4"
                  fill={color}
                />
              </>
            )}
          </svg>
          {point && (
            <output className="chart-tooltip">
              {new Date(point[0] * 1000).toLocaleTimeString()} ·{" "}
              {point[1]?.toFixed(2) ?? "missing"} {unit}
            </output>
          )}
          <div className="chart-axis">
            <span>
              {new Date(points[0][0] * 1000).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <span>now</span>
          </div>
        </div>
      ) : (
        <div className="chart-empty">Waiting for telemetry</div>
      )}
    </div>
  );
}

const nodeInfo: Record<
  string,
  {
    title: string;
    subtitle: string;
    description: string;
    icon: typeof Server;
    x: number;
    y: number;
  }
> = {
  traffic: {
    title: "Traffic generator",
    subtitle: "Continuous /jobs requests",
    description:
      "The traffic generator submits jobs to the demo API. Packet motion is scaled to the observed successful and failed request rate; it is not a trace of individual requests.",
    icon: Radio,
    x: 30,
    y: 100,
  },
  api: {
    title: "Demo API",
    subtitle: "Managed healing target",
    description:
      "The only M1 restart target. Crash, CPU saturation and API hang faults affect this container. Its health is verified independently after execution.",
    icon: Server,
    x: 325,
    y: 100,
  },
  database: {
    title: "PostgreSQL",
    subtitle: "Application data",
    description:
      "The demo API persists application data here. This node shows a configured dependency; traffic on this edge is illustrative, not database query telemetry.",
    icon: Database,
    x: 635,
    y: 25,
  },
  redis: {
    title: "Redis → worker",
    subtitle: "Background job queue",
    description:
      "The demo API submits jobs to Redis, consumed by the demo worker. Connectivity is reported by the API; queue throughput is not measured in this view.",
    icon: Cpu,
    x: 635,
    y: 185,
  },
  agent: {
    title: "Healing agent",
    subtitle: "Detect · decide · act · verify",
    description:
      "The agent correlates alerts, collects evidence, diagnoses the fault, applies policy, executes the approved plan and observes recovery.",
    icon: ShieldCheck,
    x: 325,
    y: 300,
  },
};
export function Topology({
  signals,
  incident,
  fresh,
  injected,
}: {
  signals: Row;
  incident?: Row;
  fresh: boolean;
  injected?: string;
}) {
  const [selected, setSelected] = useState("api");
  const flowing = fresh && (signals?.rps?.latest ?? 0) > 0;
  const unhealthy =
    fresh && (signals?.ready?.latest === 0 || signals?.cpu?.latest > 75);
  const InfoIcon = nodeInfo[selected].icon;
  return (
    <div className="topology-layout">
      <div
        className={`topology ${flowing ? "flowing" : ""} ${unhealthy ? "faulted" : ""}`}
      >
        <svg className="connections" viewBox="0 0 880 405" aria-hidden="true">
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="5"
              markerHeight="5"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#59667d" />
            </marker>
          </defs>
          <path className="wire" d="M245 143 H325" />
          <path className="wire" d="M540 143 H585 V68 H635" />
          <path className="wire" d="M540 143 H585 V228 H635" />
          <path
            className="wire control-wire"
            d="M430 300 V186"
            markerEnd="url(#arrow)"
          />
          {injected && (
            <g className="fault-beam">
              <path
                d="M430 54 V94"
                fill="none"
                stroke="#eead84"
                strokeWidth="2"
                strokeDasharray="3 5"
              />
              <text
                x="430"
                y="40"
                textAnchor="middle"
                fill="#f2b38c"
                fontSize="12"
              >
                Injection registered · {injected}
              </text>
            </g>
          )}
          {flowing && (
            <path
              className="packets"
              d="M245 143 H325"
              style={{
                animationDuration: `${Math.max(0.45, 3 / Math.sqrt(signals.rps.latest))}s`,
              }}
            />
          )}
          <text x="439" y="259" fill="#8f9ab0" fontSize="12">
            {incident?.state === "EXECUTING"
              ? "executing plan"
              : incident?.state === "VERIFYING"
                ? "observing recovery"
                : "control loop"}
          </text>
        </svg>
        {Object.entries(nodeInfo).map(([id, n]) => {
          const Icon = n.icon;
          return (
            <button
              key={id}
              className={`topology-node ${selected === id ? "selected" : ""} ${id === "api" && unhealthy ? "unhealthy" : ""}`}
              style={{
                left: `${(n.x / 880) * 100}%`,
                top: `${(n.y / 405) * 100}%`,
              }}
              onClick={() => setSelected(id)}
              aria-pressed={selected === id}
            >
              <span className="node-icon">
                <Icon size={21} />
              </span>
              <strong>{n.title}</strong>
              <small>{n.subtitle}</small>
              <span
                className={`node-dot ${id === "api" ? (fresh && signals?.ready?.latest != null ? (unhealthy ? "red" : "green") : "") : id === "redis" ? (fresh && signals?.redis?.latest === 1 ? "green" : "") : ""}`}
              />
            </button>
          );
        })}
      </div>
      <div className="node-inspector">
        <span className="eyebrow">SELECTED COMPONENT</span>
        <h3>
          <InfoIcon size={18} />
          {nodeInfo[selected].title}
        </h3>
        <p>{nodeInfo[selected].description}</p>
        <div className="inspector-status">
          {selected === "api" ? (
            <>
              <span>Readiness</span>
              <Badge
                value={
                  !fresh || signals?.ready?.latest == null
                    ? "UNKNOWN"
                    : signals.ready.latest === 1
                      ? "HEALTHY"
                      : "FAILED"
                }
              />
            </>
          ) : (
            <>
              <span>Connection</span>
              <span>
                {selected === "redis" && fresh && signals.redis?.latest === 1
                  ? "Observed healthy"
                  : "Configured topology"}
              </span>
            </>
          )}
        </div>
        <p className="caption">
          Click a component to inspect it. Only the traffic → API edge animates
          from measured requests.
        </p>
      </div>
    </div>
  );
}

export function Journey({ detail }: { detail?: Row | null }) {
  const inc = detail?.incident;
  const state = inc?.state;
  const plan = detail?.plans?.[0];
  const execution = detail?.executions?.[0];
  const verdict = detail?.verifications?.[0];
  const items = [
    { label: "Detect", done: !!inc, active: false, note: "Alert correlated" },
    {
      label: "Diagnose",
      done: !!detail?.diagnoses?.length,
      active: ["TRIAGED", "DETECTED"].includes(state),
      note: "Evidence collected",
    },
    {
      label: "Plan",
      done: !!plan,
      active: state === "DIAGNOSED",
      note: "Policy evaluated",
    },
    {
      label: "Approve",
      done:
        !!detail?.approvals?.some((a: Row) => a.decision === "APPROVE") ||
        ["EXECUTING", "VERIFYING", "RESOLVED"].includes(state),
      active: state === "PENDING_APPROVAL",
      note:
        state === "PENDING_APPROVAL"
          ? "Your decision needed"
          : "Approval or automation",
    },
    {
      label: "Execute",
      done: execution?.status === "SUCCEEDED",
      active: state === "EXECUTING",
      note: "Bound container restart",
    },
    {
      label: "Verify",
      done: !!verdict?.passed && state === "RESOLVED",
      active: state === "VERIFYING",
      note: "Independent health window",
    },
  ];
  return (
    <div className="journey">
      {items.map((s, i) => (
        <div
          className={`journey-step ${s.done ? "done" : ""} ${s.active ? "current" : ""}`}
          key={s.label}
        >
          <span className="step-num">
            {s.done ? "✓" : String(i + 1).padStart(2, "0")}
          </span>
          <strong>{s.label}</strong>
          <small>{s.note}</small>
        </div>
      ))}
    </div>
  );
}

export function JsonDetails({
  title,
  data,
  open = false,
}: {
  title: string;
  data: unknown;
  open?: boolean;
}) {
  return (
    <details className="json-details" open={open}>
      <summary>{title}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}

export function LiveVerification({ detail }: { detail: Row }) {
  const observation = detail.observations?.[0];
  const sample = observation?.payload;
  const checks = Object.entries(sample?.checks || {}) as [string, Row][];
  return (
    <div className="live-verification-strip">
      <div>
        <Radio size={18} />
        <strong>Verifying recovery</strong>
        <span>
          {observation
            ? `Latest sample ${new Date(observation.created_at).toLocaleTimeString()}`
            : "Waiting for the first observation"}
        </span>
      </div>
      <div className="sample-checks">
        {checks.map(([name, c]) => (
          <span key={name} title={c.reason}>
            <Badge value={c.status} />
            {nice(name)}
          </span>
        ))}
      </div>
      <div className="verification-progress">
        <progress
          value={Math.min(sample?.elapsed_healthy_seconds || 0, 90)}
          max={90}
          aria-label="Reported healthy observation duration"
        />
        <small>
          {Math.min(sample?.elapsed_healthy_seconds || 0, 90)} / 90s healthy
          duration reported · Final verdict pending
        </small>
      </div>
    </div>
  );
}
