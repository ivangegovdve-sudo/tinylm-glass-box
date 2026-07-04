import { FormEvent, useEffect, useMemo, useState } from "react";

type RunStatus = "idle" | "submitting" | "queued" | "running" | "completed" | "failed";
type SeatState = "waiting" | "running" | "done";

type TraceEvent = {
  event_type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

type RunResponse = {
  run_id: string;
  status: string;
  run_url: string;
  events_url: string;
};

type RunSnapshot = {
  run_id: string;
  status: string;
  current_state: string;
  final_output: string | null;
};

const seatLabels: Record<string, string> = {
  proposer: "Fast Proposer · 135M",
  critic: "Specialist Critic · 0.5B",
  synthesizer: "Chairman / Synthesizer · 1B",
};

const seatModelDefaults: Record<string, string> = {
  proposer: "smollm2:135m",
  critic: "qwen2.5:0.5b",
  synthesizer: "llama3.2:1b",
};

const samplePrompts = [
  "What does it mean for intelligence to be a property of a network instead of a node?",
  "Argue whether tiny model councils are an honest research artifact or mostly theater.",
  "Design a local-first policy for public runs versus private runs in this system.",
];

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8000/api";

function formatEventTitle(eventType: string) {
  return eventType.replaceAll("_", " ");
}

function stringifyValue(value: unknown) {
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function App() {
  const [prompt, setPrompt] = useState("");
  const [runStatus, setRunStatus] = useState<RunStatus>("idle");
  const [runId, setRunId] = useState("");
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [errorMessage, setErrorMessage] = useState("");
  const [runSnapshot, setRunSnapshot] = useState<RunSnapshot | null>(null);

  useEffect(() => {
    if (!runId) {
      return;
    }

    let cancelled = false;

    async function syncRun() {
      try {
        const [runResponse, traceResponse] = await Promise.all([
          fetch(`${apiBaseUrl}/runs/${runId}`),
          fetch(`${apiBaseUrl}/runs/${runId}/trace`),
        ]);

        if (!runResponse.ok || !traceResponse.ok) {
          throw new Error("Unable to refresh run state");
        }

        const nextRun: RunSnapshot = await runResponse.json();
        const nextTrace: { events: TraceEvent[] } = await traceResponse.json();
        if (cancelled) {
          return;
        }

        setRunSnapshot(nextRun);
        setEvents(nextTrace.events);
        if (nextRun.status === "completed") {
          setRunStatus("completed");
        } else if (nextRun.status === "failed") {
          setRunStatus("failed");
        } else if (nextRun.status === "running") {
          setRunStatus("running");
        } else {
          setRunStatus("queued");
        }
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error instanceof Error ? error.message : "Run refresh failed");
        }
      }
    }

    void syncRun();
    const intervalId = window.setInterval(() => {
      void syncRun();
    }, 1200);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [runId]);

  useEffect(() => {
    if (runStatus !== "completed" && runStatus !== "failed") {
      return;
    }

    const failureEvent = [...events].reverse().find((event) => event.event_type === "run_failed");
    if (runStatus === "failed" && failureEvent && typeof failureEvent.payload.error === "string") {
      setErrorMessage(failureEvent.payload.error);
    }
  }, [events, runStatus]);

  const activeSeat = useMemo(() => {
    if (runStatus === "completed") {
      return undefined;
    }
    const latestSeatStarted = [...events].reverse().find((event) => event.event_type === "seat_started");
    return latestSeatStarted?.payload.seat_name as string | undefined;
  }, [events, runStatus]);

  const seatStates = useMemo<Record<string, SeatState>>(() => {
    const states: Record<string, SeatState> = {
      proposer: "waiting",
      critic: "waiting",
      synthesizer: "waiting",
    };

    for (const event of events) {
      const seatName = event.payload.seat_name;
      if (typeof seatName !== "string" || !(seatName in states)) {
        continue;
      }

      if (event.event_type === "seat_started") {
        states[seatName] = "running";
      }

      if (event.event_type === "seat_completed" || event.event_type === "critic_payload") {
        states[seatName] = "done";
      }
    }

    return states;
  }, [events]);

  const roundCount = useMemo(() => {
    return events.filter((event) => event.event_type === "seat_started").length;
  }, [events]);

  const finalOutput = useMemo(() => {
    return (
      [...events]
      .reverse()
      .find((event) => event.event_type === "run_completed")
      ?.payload.final_output ?? runSnapshot?.final_output
    );
  }, [events, runSnapshot]);

  const traceEntries = useMemo(() => {
    return events.map((event, index) => {
      const seatName =
        typeof event.payload.seat_name === "string" ? (event.payload.seat_name as string) : undefined;
      const title = seatName ? `${seatLabels[seatName] ?? seatName} · ${formatEventTitle(event.event_type)}` : formatEventTitle(event.event_type);
      return {
        id: `${event.event_type}-${index}`,
        title,
        body:
          typeof event.payload.text === "string"
            ? event.payload.text
            : typeof event.payload.final_output === "string"
              ? event.payload.final_output
              : stringifyValue(event.payload),
      };
    });
  }, [events]);

  const traceDownloadUrl = useMemo(() => {
    if (!events.length) {
      return "";
    }

    const trace = {
      runId,
      exportedAt: new Date().toISOString(),
      events,
    };
    return URL.createObjectURL(new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" }));
  }, [events, runId]);

  useEffect(() => {
    return () => {
      if (traceDownloadUrl) {
        URL.revokeObjectURL(traceDownloadUrl);
      }
    };
  }, [traceDownloadUrl]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRunStatus("submitting");
    setErrorMessage("");
    setEvents([]);
    setRunSnapshot(null);
    setRunId("");

    try {
      const response = await fetch(`${apiBaseUrl}/runs`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ prompt }),
      });

      if (!response.ok) {
        const responseBody = await response.json();
        throw new Error(responseBody.detail ?? "Run creation failed");
      }

      const run: RunResponse = await response.json();
      setRunId(run.run_id);
      setRunStatus("queued");
    } catch (error) {
      setRunStatus("failed");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <main className="page-shell">
      <section className="hero-panel">
        <div className="hero-copy">
          <p className="eyebrow">Public experiment · local tiny models · visible limits</p>
          <h1>TinyLM Glass Box</h1>
          <p className="lede">
            Submit a question and watch a proposer, critic, and synthesizer deliberate in public. The
            interface shows the machinery, the handoffs, and the moments where the tiny council hits its ceiling.
          </p>
        </div>
        <div className="hero-aside">
          <div className="mode-card">
            <span className="mode-pill">Current mode</span>
            <strong>Public glass-box run</strong>
            <p>No privacy promise. Prompts should be treated as inspectable and locally logged.</p>
          </div>
          <div className="mode-card">
            <span className="mode-pill">Local demo target</span>
            <strong>CPU-first model stack</strong>
            <p>1B-class seats stay in the local-friendly path. Heavier fallback was downgraded from 3B to Gemma 1B.</p>
          </div>
        </div>
      </section>

      <section className="seat-grid">
        {Object.entries(seatLabels).map(([seatName, label]) => (
          <article className={`seat-card seat-${seatStates[seatName]}`} key={seatName}>
            <header>
              <span className="seat-state">{seatStates[seatName]}</span>
              <h2>{label}</h2>
            </header>
            <p>{seatModelDefaults[seatName]}</p>
            {activeSeat === seatName ? <div className="speaking-indicator">Speaking now</div> : <div className="seat-placeholder">Awaiting turn</div>}
          </article>
        ))}
      </section>

      <section className="overview-strip">
        <div className="metric-card">
          <span>BNE-style rounds</span>
          <strong>{roundCount}</strong>
        </div>
        <div className="metric-card">
          <span>Run status</span>
          <strong>{runStatus}</strong>
        </div>
        <div className="metric-card">
          <span>Run id</span>
          <strong>{runId || "not started"}</strong>
        </div>
        <div className="metric-card">
          <span>Fallback trace</span>
          <strong>{events.some((event) => event.event_type === "fallback_activated") ? "visible" : "none yet"}</strong>
        </div>
      </section>

      <section className="prompt-shell">
        <div className="sample-prompts">
          <h2>Try a prompt</h2>
          <div className="prompt-chip-row">
            {samplePrompts.map((samplePrompt) => (
              <button className="prompt-chip" key={samplePrompt} onClick={() => setPrompt(samplePrompt)} type="button">
                {samplePrompt}
              </button>
            ))}
          </div>
          <p className="support-copy">
            Private run mode is planned for a later build. This local demo is intentionally explicit about being public.
          </p>
        </div>
      </section>

      <section className="workbench">
        <form className="prompt-form" onSubmit={handleSubmit}>
          <label htmlFor="prompt">Prompt</label>
          <textarea
            id="prompt"
            name="prompt"
            rows={7}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Ask the tiny council something worth watching think about."
          />
          <button disabled={!prompt.trim() || runStatus === "submitting"} type="submit">
            {runStatus === "submitting" ? "Starting council..." : "Start public run"}
          </button>
        </form>

        <div className="status-panel">
          <div className="status-chip">Status: {runStatus}</div>
          <div className="status-chip">Active seat: {activeSeat ? seatLabels[activeSeat] : "waiting"}</div>
          <div className="status-chip">Final stage: {finalOutput ? "synthesis ready" : "in progress"}</div>
          <div className="status-chip">Engine state: {runSnapshot?.current_state ?? "draft"}</div>
          {traceDownloadUrl ? (
            <a className="download-link" download={`tinylm-trace-${runId || "draft"}.json`} href={traceDownloadUrl}>
              Download trace log
            </a>
          ) : (
            <div className="status-chip muted">Trace export appears after the first event.</div>
          )}
          {errorMessage ? <p className="error-copy">{errorMessage}</p> : null}
        </div>
      </section>

      <section className="trace-grid">
        <article className="trace-column">
          <h2>Thought process</h2>
          <ul className="event-list">
            {traceEntries.map((entry) => (
              <li key={entry.id}>
                <strong>{entry.title}</strong>
                <pre>{entry.body}</pre>
              </li>
            ))}
            {!traceEntries.length ? <li className="empty-state">The trace will populate as each seat starts speaking.</li> : null}
          </ul>
        </article>

        <article className="trace-column">
          <h2>Final synthesis</h2>
          <div className="final-panel">
            {typeof finalOutput === "string" ? <p>{finalOutput}</p> : null}
            {!finalOutput ? (
              <p>The final synthesis will appear here when the council finishes.</p>
            ) : null}
          </div>
        </article>
      </section>
    </main>
  );
}

export default App;
