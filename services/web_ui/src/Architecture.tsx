import { useEffect, useState } from "react";

const STEPS = [
  ["可信入口", "网关完成认证，注入 tenant / user / ACL"],
  ["查询规划", "Python Planner 拆出本地文档、图片、视频与 Web 路由"],
  ["并行召回", "Embedding + Milvus 与 Bing + 安全网页抽取同时工作"],
  ["证据治理", "C++ 做 ACL 前置过滤、去重、冲突识别和 Token 预算"],
  ["约束生成", "通用 ChatModel 只读取编号证据，持续输出 token"],
  ["引用验收", "Python 校验引用编号，React 展示来源与冲突"],
] as const;

interface ArchitectureProps {
  liveStep: number;
}

export function Architecture({ liveStep }: ArchitectureProps) {
  const [demoStep, setDemoStep] = useState(-1);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      setDemoStep((current) => {
        if (current >= STEPS.length - 1) {
          setRunning(false);
          return current;
        }
        return current + 1;
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [running]);

  const activeStep = running || demoStep >= 0 ? demoStep : liveStep;
  const startDemo = () => {
    setDemoStep(0);
    setRunning(true);
  };

  return (
    <section className="architecture" aria-label="系统架构与流程">
      <div className="section-heading">
        <div>
          <span className="eyebrow">SYSTEM MAP</span>
          <h2>Python 表层，C++ 证据内核</h2>
          <p>模型和基础设施都藏在稳定接口后面，可替换、可审计、可独立扩缩容。</p>
        </div>
        <button className="secondary-button" type="button" onClick={startDemo}>
          {running ? "流程演示中…" : "播放流程演示"}
        </button>
      </div>

      <div className="system-map">
        <div className="map-lane experience-lane">
          <span className="lane-label">EXPERIENCE</span>
          <MapNode title="React + TypeScript" detail="问答 / SSE / 引用 / 冲突" tone="mint" />
          <div className="map-arrow">↓ POST + SSE</div>
          <MapNode title="可信 API 网关" detail="认证并覆盖身份与 ACL 头" tone="amber" />
        </div>

        <div className="map-lane surface-lane">
          <span className="lane-label">PYTHON SURFACE</span>
          <div className="node-grid">
            <MapNode title="FastAPI" detail="协议、会话、错误与心跳" tone="blue" />
            <MapNode title="Query Planner" detail="结构化多路检索计划" tone="blue" />
            <MapNode title="通用模型端口" detail="Chat / Embedding / Vision / ASR" tone="blue" />
            <MapNode title="Web & Kafka" detail="Bing 抽取 / 异步入库编排" tone="blue" />
          </div>
          <div className="map-arrow">↓ gRPC + Protobuf</div>
        </div>

        <div className="map-lane core-lane">
          <span className="lane-label">C++20 CORE</span>
          <div className="core-node">
            <strong>确定性证据内核</strong>
            <span>多路召回 · ACL 过滤 · RRF · 去重 · 冲突 · Token 预算 · Citation</span>
          </div>
          <div className="store-row">
            <Store label="Milvus" detail="C++ 检索" />
            <Store label="MySQL" detail="Python 元数据" />
            <Store label="S3" detail="Python 原始对象" />
            <Store label="Kafka" detail="Python 入库任务" />
          </div>
        </div>
      </div>

      <div className="flow-demo">
        <div className="flow-track" aria-hidden="true">
          <span style={{ width: `${Math.max(0, activeStep) * 20}%` }} />
        </div>
        {STEPS.map(([title, detail], index) => (
          <article
            className={`flow-step ${index <= activeStep ? "is-complete" : ""} ${index === activeStep ? "is-active" : ""}`}
            key={title}
          >
            <span className="step-index">{String(index + 1).padStart(2, "0")}</span>
            <h3>{title}</h3>
            <p>{detail}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function MapNode({
  title,
  detail,
  tone,
}: {
  title: string;
  detail: string;
  tone: "mint" | "amber" | "blue";
}) {
  return (
    <div className={`map-node tone-${tone}`}>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function Store({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="store-node">
      <span className="store-light" />
      <strong>{label}</strong>
      <small>{detail}</small>
    </div>
  );
}
