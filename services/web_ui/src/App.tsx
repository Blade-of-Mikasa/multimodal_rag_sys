import { FormEvent, ReactNode, useRef, useState } from "react";

import { streamAnswer } from "./api";
import { Architecture } from "./Architecture";
import type {
  Citation,
  Conflict,
  Modality,
  RetrievalScope,
  StreamEvent,
} from "./types";

const EVENT_STEP: Record<string, number> = {
  accepted: 0,
  planning: 1,
  retrieving: 2,
  sources: 3,
  delta: 4,
  done: 5,
};

const SCOPE_OPTIONS: { value: RetrievalScope; label: string }[] = [
  { value: "auto", label: "智能选择" },
  { value: "local", label: "仅知识库" },
  { value: "web", label: "仅联网" },
  { value: "hybrid", label: "混合检索" },
];

const MODALITY_OPTIONS: { value: Modality; label: string }[] = [
  { value: "document", label: "文档" },
  { value: "image", label: "图片" },
  { value: "video", label: "视频" },
];

export function App() {
  const [view, setView] = useState<"ask" | "architecture">("ask");
  const [query, setQuery] = useState(
    "请结合本地设计文档和公开资料，解释这套系统为什么把证据治理放在 C++ 内核。",
  );
  const [scope, setScope] = useState<RetrievalScope>("hybrid");
  const [modalities, setModalities] = useState<Modality[]>([
    "document",
    "image",
    "video",
  ]);
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [routes, setRoutes] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [liveStep, setLiveStep] = useState(-1);
  const [conversationId, setConversationId] = useState<string>();
  const [partialFailure, setPartialFailure] = useState(false);
  const [citationWarning, setCitationWarning] = useState("");
  const controller = useRef<AbortController | null>(null);

  const onEvent = (event: StreamEvent) => {
    setLiveStep((current) => Math.max(current, EVENT_STEP[event.event] ?? current));
    if (event.event === "accepted") {
      const id = event.data.conversation_id;
      if (typeof id === "string") setConversationId(id);
    } else if (event.event === "planning" && event.data.status === "completed") {
      if (Array.isArray(event.data.routes)) setRoutes(event.data.routes);
    } else if (event.event === "sources") {
      if (Array.isArray(event.data.citations)) {
        setCitations(event.data.citations as Citation[]);
      }
      if (Array.isArray(event.data.conflicts)) {
        setConflicts(event.data.conflicts as Conflict[]);
      }
      setPartialFailure(event.data.partial_failure === true);
    } else if (event.event === "delta" && typeof event.data.text === "string") {
      const text = event.data.text;
      setAnswer((current) => current + text);
    } else if (event.event === "done") {
      const invalid = event.data.invalid_citation_ids;
      if (Array.isArray(invalid) && invalid.length) {
        setCitationWarning(`模型生成了 ${invalid.length} 个无效引用，已标记复核。`);
      } else if (event.data.uncited_answer === true) {
        setCitationWarning("回答没有使用可用证据编号，请人工复核。");
      }
      setRunning(false);
    } else if (event.event === "error") {
      setError(
        typeof event.data.message === "string" ? event.data.message : "回答流程失败",
      );
      setRunning(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim() || running || modalities.length === 0) return;
    setAnswer("");
    setCitations([]);
    setConflicts([]);
    setRoutes([]);
    setError("");
    setPartialFailure(false);
    setCitationWarning("");
    setLiveStep(0);
    setRunning(true);
    controller.current = new AbortController();
    try {
      await streamAnswer(
        {
          query: query.trim(),
          conversation_id: conversationId,
          retrieval_scope: scope,
          modalities,
        },
        onEvent,
        controller.current.signal,
      );
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        setError(caught instanceof Error ? caught.message : "无法连接问答服务");
      }
      setRunning(false);
    }
  };

  const toggleModality = (modality: Modality) => {
    setModalities((current) =>
      current.includes(modality)
        ? current.filter((item) => item !== modality)
        : [...current, modality],
    );
  };

  return (
    <main>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">M</span>
          <div>
            <strong>Mikasa RAG</strong>
            <small>Evidence-first intelligence</small>
          </div>
        </div>
        <nav aria-label="主导航">
          <button className={view === "ask" ? "active" : ""} onClick={() => setView("ask")}>
            问答工作台
          </button>
          <button
            className={view === "architecture" ? "active" : ""}
            onClick={() => setView("architecture")}
          >
            架构与流程
          </button>
        </nav>
        <div className="system-status"><span /> Contract v1</div>
      </header>

      {view === "architecture" ? (
        <Architecture liveStep={liveStep} />
      ) : (
        <div className="workspace">
          <section className="query-panel">
            <div className="hero-copy">
              <span className="eyebrow">MULTIMODAL RESEARCH DESK</span>
              <h1>每个结论，<br />都能回到证据。</h1>
              <p>同时检索文档、图片、视频与公开网页，让 C++ 内核先治理证据，再交给模型回答。</p>
            </div>

            <form onSubmit={submit}>
              <label htmlFor="question">你的问题</label>
              <textarea
                id="question"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                maxLength={8000}
                rows={6}
              />
              <div className="control-group">
                <span>检索范围</span>
                <div className="segmented">
                  {SCOPE_OPTIONS.map((option) => (
                    <button
                      className={scope === option.value ? "selected" : ""}
                      key={option.value}
                      type="button"
                      onClick={() => setScope(option.value)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="control-group">
                <span>证据模态</span>
                <div className="check-row">
                  {MODALITY_OPTIONS.map((option) => (
                    <label key={option.value}>
                      <input
                        type="checkbox"
                        checked={modalities.includes(option.value)}
                        onChange={() => toggleModality(option.value)}
                      />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="form-actions">
                <button className="primary-button" disabled={running || !modalities.length}>
                  {running ? "正在构建证据链…" : "开始检索"}
                  <span aria-hidden="true">↗</span>
                </button>
                {running && (
                  <button
                    className="text-button"
                    type="button"
                    onClick={() => controller.current?.abort()}
                  >
                    停止
                  </button>
                )}
              </div>
            </form>
          </section>

          <section className="answer-panel" aria-live="polite">
            <div className="answer-header">
              <div>
                <span className="eyebrow">EVIDENCE ANSWER</span>
                <h2>回答与来源</h2>
              </div>
              <PipelineStatus step={liveStep} running={running} />
            </div>

            {!answer && !error && !running ? (
              <div className="empty-state">
                <div className="radar"><span /><span /><i /></div>
                <h3>等待一个值得追问的问题</h3>
                <p>检索计划、流式回答、来源和冲突会在这里逐步展开。</p>
              </div>
            ) : (
              <div className="answer-content">
                {routes.length > 0 && (
                  <div className="route-strip">
                    {routes.map((route, index) => (
                      <span key={`${String(route.route_id)}-${index}`}>
                        {String(route.source_scope)} · {String(route.modality)}
                      </span>
                    ))}
                  </div>
                )}
                {partialFailure && <div className="notice">部分检索路由失败，回答已使用剩余证据降级完成。</div>}
                {error && <div className="error-card">{error}</div>}
                {answer && <div className="answer-text">{renderAnswer(answer, citations)}</div>}
                {running && <span className="stream-cursor" />}
                {citationWarning && <div className="warning-card">{citationWarning}</div>}
                <SourceList citations={citations} conflicts={conflicts} />
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

function PipelineStatus({ step, running }: { step: number; running: boolean }) {
  const labels = ["已接收", "规划中", "检索中", "证据就绪", "生成中", "已完成"];
  return (
    <div className={`pipeline-status ${running ? "is-running" : ""}`}>
      <span /> {step >= 0 ? labels[Math.min(step, labels.length - 1)] : "待命"}
    </div>
  );
}

function renderAnswer(answer: string, citations: Citation[]): ReactNode[] {
  const known = new Map(citations.map((citation) => [citation.citation_id, citation]));
  return answer.split(/(\[证据\s+\d+\])/g).map((part, index) => {
    const match = part.match(/^\[证据\s+(\d+)\]$/);
    if (!match) return part;
    const id = Number(match[1]);
    const citation = known.get(id);
    return citation ? (
      <a className="inline-citation" href={`#citation-${id}`} key={`${part}-${index}`}>
        {part}
      </a>
    ) : (
      <span className="inline-citation invalid" key={`${part}-${index}`}>{part}</span>
    );
  });
}

function SourceList({ citations, conflicts }: { citations: Citation[]; conflicts: Conflict[] }) {
  if (!citations.length && !conflicts.length) return null;
  return (
    <div className="source-section">
      <h3>证据来源 <span>{citations.length}</span></h3>
      <div className="source-grid">
        {citations.map((citation) => (
          <article id={`citation-${citation.citation_id}`} key={citation.citation_id}>
            <span className="citation-number">{citation.citation_id}</span>
            <div>
              <small>{citation.modality} · {citation.source || "本地知识库"}</small>
              <strong>{citation.title || citation.evidence_id}</strong>
              {safeExternalUrl(citation.url) && (
                <a href={safeExternalUrl(citation.url)} target="_blank" rel="noreferrer">
                  打开原始来源 ↗
                </a>
              )}
            </div>
          </article>
        ))}
      </div>
      {conflicts.length > 0 && (
        <div className="conflict-list">
          <h3>证据冲突 <span>{conflicts.length}</span></h3>
          {conflicts.map((conflict, index) => (
            <p key={`${conflict.type}-${index}`}><strong>{conflict.type}</strong>{conflict.reason}</p>
          ))}
        </div>
      )}
    </div>
  );
}

function safeExternalUrl(value: string): string | undefined {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.toString()
      : undefined;
  } catch {
    return undefined;
  }
}
