export const HydraGraph = () => {
  const USER_ID = "user";
  const NODE_W = 132;
  const NODE_H = 38;
  const MEMORY_DELAY = 700;
  const KNOWLEDGE_DELAY = 700;
  const STOP_WORDS = {
    is: 1, the: 1, a: 1, an: 1, to: 1, for: 1, and: 1, or: 1, in: 1, on: 1, at: 1,
    uses: 1, use: 1, prefers: 1, prefer: 1, both: 1, with: 1, from: 1, that: 1, this: 1,
    what: 1, how: 1, do: 1, we: 1, have: 1, are: 1, can: 1, my: 1, your: 1,
  };
  const DEMO_NODES = [
    { id: "mem-1", type: "memory", label: "Alice is vegetarian" },
    { id: "mem-2", type: "memory", label: "Prefers dark mode" },
    { id: "mem-3", type: "memory", label: "Uses VS Code" },
    { id: "know-1", type: "knowledge", label: "Auth Guide", relatedTo: [] },
    { id: "know-2", type: "knowledge", label: "Vegetarian menu options", relatedTo: [] },
    { id: "know-3", type: "knowledge", label: "Runbook", relatedTo: ["know-1"] },
  ];

  const [infer, setInfer] = useState(false);
  const [nodes, setNodes] = useState([]);
  const [question, setQuestion] = useState("");
  const [submittedQuestion, setSubmittedQuestion] = useState(null);
  const [traversePhase, setTraversePhase] = useState("idle");
  const [highlightedMemories, setHighlightedMemories] = useState({});
  const [highlightedKnowledge, setHighlightedKnowledge] = useState({});
  const [retrievedItems, setRetrievedItems] = useState([]);
  const [cannedAnswer, setCannedAnswer] = useState("");
  const [queryResponsePreview, setQueryResponsePreview] = useState(null);
  const [timerTick, setTimerTick] = useState(0);
  const [positionOverrides, setPositionOverrides] = useState({});

  const clearTimers = () => {
    setTimerTick((t) => t + 1);
  };

  const resetAskState = () => {
    clearTimers();
    setQuestion("");
    setSubmittedQuestion(null);
    setTraversePhase("idle");
    setHighlightedMemories({});
    setHighlightedKnowledge({});
    setRetrievedItems([]);
    setCannedAnswer("");
    setQueryResponsePreview(null);
  };

  const handleReset = () => {
    resetAskState();
    setNodes([]);
    setInfer(false);
    setPositionOverrides({});
  };

  const handleLoadDemo = () => {
    resetAskState();
    setPositionOverrides({});
    setNodes(DEMO_NODES.map((n) => ({ ...n, relatedTo: n.relatedTo ? n.relatedTo.slice() : undefined })));
  };

  const addMemory = () => {
    let count = 0;
    for (let i = 0; i < nodes.length; i++) if (nodes[i].type === "memory") count++;
    setNodes((prev) => prev.concat([{ id: "mem-" + Date.now(), type: "memory", label: "Memory " + (count + 1) }]));
  };

  const addKnowledge = () => {
    const knowledge = nodes.filter((n) => n.type === "knowledge");
    const last = knowledge[knowledge.length - 1];
    setNodes((prev) => prev.concat([{
      id: "know-" + Date.now(),
      type: "knowledge",
      label: "Doc " + (knowledge.length + 1),
      relatedTo: last ? [last.id] : [],
    }]));
  };

  const extractKeywords = (text) => {
    const words = text.toLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/);
    const seen = {};
    const result = [];
    for (let i = 0; i < words.length; i++) {
      const word = words[i];
      if (word.length < 3 || STOP_WORDS[word] || seen[word]) continue;
      seen[word] = 1;
      result.push(word);
    }
    return result;
  };

  const findSharedKeywords = (left, right) => {
    const leftSet = {};
    const leftWords = extractKeywords(left);
    for (let i = 0; i < leftWords.length; i++) leftSet[leftWords[i]] = 1;
    const shared = [];
    const rightWords = extractKeywords(right);
    for (let i = 0; i < rightWords.length; i++) {
      if (leftSet[rightWords[i]]) shared.push(rightWords[i]);
    }
    return shared;
  };

  const computeInferredLinks = (nodeList) => {
    const memories = nodeList.filter((n) => n.type === "memory");
    const knowledge = nodeList.filter((n) => n.type === "knowledge");
    const links = [];
    for (let i = 0; i < memories.length; i++) {
      for (let j = 0; j < knowledge.length; j++) {
        const shared = findSharedKeywords(memories[i].label, knowledge[j].label);
        if (!shared.length) continue;
        links.push({
          memoryId: memories[i].id,
          knowledgeId: knowledge[j].id,
          memoryLabel: memories[i].label,
          knowledgeLabel: knowledge[j].label,
          keywords: shared,
        });
      }
    }
    return links;
  };

  const computeLayout = (nodeList) => {
    const userCenter = { x: 360, y: 210 };
    const positions = { [USER_ID]: { x: userCenter.x - NODE_W / 2, y: userCenter.y - NODE_H / 2 } };
    const memories = nodeList.filter((n) => n.type === "memory");
    const memRadius = Math.max(120, 80 + memories.length * 14);
    for (let i = 0; i < memories.length; i++) {
      const angle = memories.length === 1 ? Math.PI : Math.PI * 0.62 + ((Math.PI * 0.76) * i) / (memories.length - 1);
      positions[memories[i].id] = {
        x: userCenter.x + memRadius * Math.cos(angle) - NODE_W / 2,
        y: userCenter.y + memRadius * Math.sin(angle) - NODE_H / 2,
      };
    }
    const knowledge = nodeList.filter((n) => n.type === "knowledge");
    for (let i = 0; i < knowledge.length; i++) {
      positions[knowledge[i].id] = { x: 500 + (i % 2) * 156, y: 56 + Math.floor(i / 2) * 56 };
    }
    return positions;
  };

  const buildCannedAnswer = (queryText, memoryMatches, knowledgeMatches) => {
    if (!memoryMatches.length && !knowledgeMatches.length) {
      return "Based on your memory and knowledge, I couldn't find a strong match for that question. Try mentioning a preference (dark mode, VS Code) or a topic (auth, vegetarian, runbook).";
    }
    const memPart = memoryMatches.length
      ? "your memory that " + memoryMatches.map((n) => n.label.toLowerCase()).join(" and ")
      : "";
    const knowPart = knowledgeMatches.length
      ? knowledgeMatches.map((n) => n.label).join(" and ")
      : "the matched knowledge docs";
    return memPart
      ? "Based on your memory and knowledge, here's what I'd suggest: given " + memPart + ", start with " + knowPart + "."
      : "Based on your memory and knowledge, here's what I'd suggest: review " + knowPart + " for guidance related to \"" + queryText + "\".";
  };

  const buildQueryResponsePreview = (queryText, memoryMatches, knowledgeMatches, reasonMap) => {
    const results = [];
    for (let i = 0; i < memoryMatches.length; i++) {
      const node = memoryMatches[i];
      results.push({ type: "memory", id: node.id, text: node.label, score: 0.92 - i * 0.04, reason: reasonMap[node.id] });
    }
    for (let i = 0; i < knowledgeMatches.length; i++) {
      const node = knowledgeMatches[i];
      results.push({ type: "knowledge", id: node.id, text: node.label, score: 0.88 - i * 0.03, reason: reasonMap[node.id] });
    }
    return {
      results,
      graph_context: {
        nodes: results.map((r) => ({ id: r.id, type: r.type, label: r.text })),
        traversal: ["user"].concat(memoryMatches.map((m) => m.id)).concat(knowledgeMatches.map((k) => k.id)),
      },
      query: queryText,
    };
  };

  useEffect(() => {
    if (!submittedQuestion || !nodes.length) return undefined;

    const memMatches = [];
    const knowMatches = [];
    const knowledgeIds = {};
    const reasons = {};
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      const shared = findSharedKeywords(submittedQuestion, node.label);
      if (!shared.length) continue;
      reasons[node.id] = "Keyword overlap: " + shared.join(", ");
      if (node.type === "memory") memMatches.push(node);
      if (node.type === "knowledge") {
        knowMatches.push(node);
        knowledgeIds[node.id] = 1;
      }
    }
    if (infer) {
      const links = computeInferredLinks(nodes);
      for (let i = 0; i < links.length; i++) {
        const link = links[i];
        const memoryMatched = memMatches.some((m) => m.id === link.memoryId);
        if (!memoryMatched || knowledgeIds[link.knowledgeId]) continue;
        const knowNode = nodes.find((n) => n.id === link.knowledgeId);
        if (!knowNode) continue;
        knowMatches.push(knowNode);
        knowledgeIds[knowNode.id] = 1;
        reasons[knowNode.id] = 'Inferred from memory "' + link.memoryLabel + '" via "' + link.keywords.join(", ") + '"';
      }
    }
    for (let i = 0; i < knowMatches.length; i++) {
      const node = knowMatches[i];
      if (!node.relatedTo) continue;
      for (let j = 0; j < node.relatedTo.length; j++) {
        const targetId = node.relatedTo[j];
        if (knowledgeIds[targetId]) continue;
        const target = nodes.find((n) => n.id === targetId);
        if (!target) continue;
        knowMatches.push(target);
        knowledgeIds[target.id] = 1;
        reasons[target.id] = 'Linked knowledge edge from "' + node.label + '"';
      }
    }

    const memMap = {};
    for (let i = 0; i < memMatches.length; i++) memMap[memMatches[i].id] = 1;
    const knowMap = {};
    for (let i = 0; i < knowMatches.length; i++) knowMap[knowMatches[i].id] = 1;

    setTraversePhase("memory");
    setHighlightedMemories(memMap);
    setHighlightedKnowledge({});
    setRetrievedItems([]);
    setCannedAnswer("");
    setQueryResponsePreview(null);

    const memoryTimer = setTimeout(() => {
      setTraversePhase("knowledge");
      setHighlightedKnowledge(knowMap);
    }, MEMORY_DELAY);

    const doneTimer = setTimeout(() => {
      setTraversePhase("done");
      const items = [];
      for (let i = 0; i < memMatches.length; i++) {
        items.push({ id: memMatches[i].id, type: "memory", label: memMatches[i].label, reason: reasons[memMatches[i].id] });
      }
      for (let i = 0; i < knowMatches.length; i++) {
        items.push({ id: knowMatches[i].id, type: "knowledge", label: knowMatches[i].label, reason: reasons[knowMatches[i].id] });
      }
      setRetrievedItems(items);
      setCannedAnswer(buildCannedAnswer(submittedQuestion, memMatches, knowMatches));
      setQueryResponsePreview(buildQueryResponsePreview(submittedQuestion, memMatches, knowMatches, reasons));
    }, MEMORY_DELAY + KNOWLEDGE_DELAY);

    return () => {
      clearTimeout(memoryTimer);
      clearTimeout(doneTimer);
    };
  }, [submittedQuestion, infer, timerTick]);

  const handleAsk = (event) => {
    if (event) event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || !nodes.length) return;
    clearTimers();
    setSubmittedQuestion(trimmed);
  };

  // Mintlify strips onPointer* from SVG JSX — attach native listeners instead.
  useEffect(() => {
    const svg = document.getElementById("hydra-graph-svg");
    if (!svg) return undefined;

    let session = null;

    const clientToSvgPoint = (clientX, clientY) => {
      if (!svg.createSVGPoint) return null;
      const pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = clientY;
      const ctm = svg.getScreenCTM();
      if (!ctm) return null;
      const local = pt.matrixTransform(ctm.inverse());
      return { x: local.x, y: local.y };
    };

    const clearDraggingClass = () => {
      svg.classList.remove("is-dragging");
      const nodes = svg.querySelectorAll(".hg-node.is-dragging");
      for (let i = 0; i < nodes.length; i++) nodes[i].classList.remove("is-dragging");
    };

    const endSession = () => {
      if (!session) return;
      clearDraggingClass();
      session = null;
    };

    const onPointerDown = (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      const target = event.target;
      if (!target || !target.closest) return;
      const group = target.closest(".hg-node");
      if (!group || !svg.contains(group)) return;
      const nodeId = group.getAttribute("data-node-id");
      const rect = group.querySelector(".hg-node-rect");
      if (!nodeId || !rect) return;
      const local = clientToSvgPoint(event.clientX, event.clientY);
      if (!local) return;
      const x = parseFloat(rect.getAttribute("x"));
      const y = parseFloat(rect.getAttribute("y"));
      if (isNaN(x) || isNaN(y)) return;
      event.preventDefault();
      event.stopPropagation();
      session = {
        id: nodeId,
        pointerId: event.pointerId,
        offsetX: local.x - x,
        offsetY: local.y - y,
        moved: false,
      };
      if (svg.setPointerCapture) svg.setPointerCapture(event.pointerId);
      svg.classList.add("is-dragging");
      group.classList.add("is-dragging");
    };

    const onPointerMove = (event) => {
      if (!session || event.pointerId !== session.pointerId) return;
      const bounds = svg.getBoundingClientRect();
      if (
        event.clientX < bounds.left ||
        event.clientX > bounds.right ||
        event.clientY < bounds.top ||
        event.clientY > bounds.bottom
      ) {
        endSession();
        return;
      }
      const local = clientToSvgPoint(event.clientX, event.clientY);
      if (!local) return;
      session.moved = true;
      const nextX = local.x - session.offsetX;
      const nextY = local.y - session.offsetY;
      const nodeId = session.id;
      setPositionOverrides((prev) => {
        const next = {};
        for (const key in prev) next[key] = prev[key];
        next[nodeId] = { x: nextX, y: nextY };
        return next;
      });
    };

    const onPointerUp = (event) => {
      if (!session || event.pointerId !== session.pointerId) return;
      if (svg.releasePointerCapture) {
        try { svg.releasePointerCapture(event.pointerId); } catch (err) { /* already released */ }
      }
      endSession();
    };

    svg.addEventListener("pointerdown", onPointerDown);
    svg.addEventListener("pointermove", onPointerMove);
    svg.addEventListener("pointerup", onPointerUp);
    svg.addEventListener("pointercancel", onPointerUp);
    svg.addEventListener("pointerleave", onPointerUp);

    return () => {
      svg.removeEventListener("pointerdown", onPointerDown);
      svg.removeEventListener("pointermove", onPointerMove);
      svg.removeEventListener("pointerup", onPointerUp);
      svg.removeEventListener("pointercancel", onPointerUp);
      svg.removeEventListener("pointerleave", onPointerUp);
      endSession();
    };
  }, []);

  const isEmpty = nodes.length === 0;
  const allNodes = [{ id: USER_ID, type: "user", label: "User" }].concat(nodes);
  const layoutPositions = computeLayout(nodes);
  const positions = {};
  for (const id in layoutPositions) positions[id] = layoutPositions[id];
  for (const id in positionOverrides) {
    if (positionOverrides[id]) positions[id] = positionOverrides[id];
  }
  const inferredLinks = computeInferredLinks(nodes);

  const edges = [];

  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    if (node.type === "memory" && positions[node.id] && positions[USER_ID]) {
      edges.push({
        key: node.id + "-user",
        x1: positions[node.id].x + NODE_W / 2,
        y1: positions[node.id].y + NODE_H / 2,
        x2: positions[USER_ID].x + NODE_W / 2,
        y2: positions[USER_ID].y + NODE_H / 2,
        stroke: "var(--hg-edge-mem)",
        dashed: false,
      });
    }
    if (node.type === "knowledge" && node.relatedTo) {
      for (let j = 0; j < node.relatedTo.length; j++) {
        const targetId = node.relatedTo[j];
        if (!positions[node.id] || !positions[targetId]) continue;
        edges.push({
          key: node.id + "-" + targetId,
          x1: positions[node.id].x + NODE_W / 2,
          y1: positions[node.id].y + NODE_H / 2,
          x2: positions[targetId].x + NODE_W / 2,
          y2: positions[targetId].y + NODE_H / 2,
          stroke: "var(--hg-edge-know)",
          dashed: false,
        });
      }
    }
  }

  if (infer) {
    for (let i = 0; i < inferredLinks.length; i++) {
      const link = inferredLinks[i];
      if (!positions[link.memoryId] || !positions[link.knowledgeId]) continue;
      edges.push({
        key: "inf-" + link.memoryId + "-" + link.knowledgeId,
        x1: positions[link.memoryId].x + NODE_W / 2,
        y1: positions[link.memoryId].y + NODE_H / 2,
        x2: positions[link.knowledgeId].x + NODE_W / 2,
        y2: positions[link.knowledgeId].y + NODE_H / 2,
        stroke: "var(--hg-edge-infer)",
        dashed: true,
      });
    }
  }

  let memoryCount = 0;
  let knowledgeCount = 0;
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].type === "memory") memoryCount++;
    if (nodes[i].type === "knowledge") knowledgeCount++;
  }

  const queryRequestPreview = submittedQuestion
    ? JSON.stringify({
        database: "acme_corp",
        collection: "default",
        query: submittedQuestion,
        type: "all",
        mode: "thinking",
        graph_context: true,
        max_results: 10,
      }, null, 2)
    : null;

  const queryResponseJson = queryResponsePreview
    ? JSON.stringify(queryResponsePreview, null, 2)
    : null;

  const explanationItems = infer
    ? inferredLinks.map((link) => ({
        key: link.memoryId + "-" + link.knowledgeId,
        text: '"' + link.memoryLabel + '" connects to "' + link.knowledgeLabel + '" because both mention "' + link.keywords.join('", "') + '".',
      }))
    : [];

  const isNodeHighlighted = (node) => {
    if (node.type === "user") return false;
    if (node.type === "memory") return !!highlightedMemories[node.id];
    if (node.type === "knowledge") return !!highlightedKnowledge[node.id];
    return false;
  };

  const nodeColors = (type) => {
    if (type === "user") return { fill: "var(--hg-user-fill)", stroke: "var(--hg-user)", text: "var(--hg-user-text)" };
    if (type === "memory") return { fill: "var(--hg-mem-fill)", stroke: "var(--hg-mem)", text: "var(--hg-mem-text)" };
    return { fill: "var(--hg-know-fill)", stroke: "var(--hg-know)", text: "var(--hg-know-text)" };
  };

  return (
    <div className="not-prose hydra-graph">
      <style>{`
        .hydra-graph {
          /* Map onto Mintlify docs tokens from docs.json / :root */
          --hg-primary: rgb(var(--primary, 255 87 26));
          --hg-primary-light: rgb(var(--primary-light, 255 138 84));
          --hg-primary-dark: rgb(var(--primary-dark, 204 69 21));
          --hg-bg-page: rgb(var(--background-light, 255 255 255));
          --hg-gray-50: rgb(var(--gray-50, 250 245 243));
          --hg-gray-100: rgb(var(--gray-100, 245 240 238));
          --hg-gray-200: rgb(var(--gray-200, 230 225 223));
          --hg-gray-400: rgb(var(--gray-400, 166 161 159));
          --hg-gray-500: rgb(var(--gray-500, 119 114 112));
          --hg-gray-600: rgb(var(--gray-600, 87 82 80));
          --hg-gray-700: rgb(var(--gray-700, 70 65 63));
          --hg-gray-900: rgb(var(--gray-900, 30 25 23));

          --hg-bg: var(--hg-gray-50);
          --hg-panel: var(--hg-bg-page);
          --hg-border: var(--hg-gray-200);
          --hg-text: var(--hg-gray-900);
          --hg-muted: var(--hg-gray-500);
          --hg-title: var(--hg-primary);
          --hg-code: var(--hg-gray-700);
          --hg-input-bg: var(--hg-bg-page);
          --hg-hover: rgba(87, 82, 80, 0.05);
          --hg-radius: 0.75rem;
          --hg-radius-lg: 1rem;
          --hg-font: Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif;
          --hg-mono: paperMono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
          --hg-text-xs: 0.75rem;
          --hg-text-sm: 0.875rem;

          /* Memory = primary accent; Knowledge = warm-gray secondary; User = neutral */
          --hg-user: var(--hg-gray-600);
          --hg-user-fill: var(--hg-gray-100);
          --hg-user-text: var(--hg-gray-900);
          --hg-mem: var(--hg-primary);
          --hg-mem-fill: color-mix(in srgb, var(--hg-primary) 10%, white);
          --hg-mem-text: var(--hg-primary-dark);
          --hg-know: var(--hg-gray-600);
          --hg-know-fill: var(--hg-gray-100);
          --hg-know-text: var(--hg-gray-700);
          --hg-infer: var(--hg-primary-light);
          --hg-highlight: var(--hg-primary);
          --hg-answer-border: color-mix(in srgb, var(--hg-primary) 35%, var(--hg-border));
          --hg-answer-label: var(--hg-primary);
          --hg-edge-mem: color-mix(in srgb, var(--hg-primary) 55%, transparent);
          --hg-edge-know: color-mix(in srgb, var(--hg-gray-500) 55%, transparent);
          --hg-edge-infer: var(--hg-primary-light);

          max-width: 100%;
          box-sizing: border-box;
          margin-top: 1.5rem;
          margin-bottom: 2rem;
          font-family: var(--hg-font);
          background: var(--hg-bg);
          color: var(--hg-text);
          padding: 1.25rem;
          border-radius: var(--hg-radius-lg);
          border: 1px solid var(--hg-border);
          overflow-x: hidden;
        }
        .dark .hydra-graph,
        html.dark .hydra-graph,
        [data-theme="dark"] .hydra-graph {
          --hg-bg-page: rgb(var(--background-dark, 0 0 0));
          --hg-gray-50: rgb(17 12 10);
          --hg-gray-100: rgb(30 25 23);
          --hg-gray-200: rgba(255, 255, 255, 0.07);
          --hg-gray-400: rgb(166 161 159);
          --hg-gray-500: rgb(166 161 159);
          --hg-gray-600: rgb(213 208 206);
          --hg-gray-700: rgb(230 225 223);
          --hg-gray-900: rgb(245 240 238);
          --hg-bg: rgba(255, 255, 255, 0.04);
          --hg-panel: rgba(255, 255, 255, 0.04);
          --hg-border: rgba(255, 255, 255, 0.07);
          --hg-text: rgb(229 231 235);
          --hg-muted: rgb(156 163 175);
          --hg-title: var(--hg-primary-light);
          --hg-code: rgb(212 212 212);
          --hg-input-bg: rgba(0, 0, 0, 0.35);
          --hg-hover: rgba(255, 255, 255, 0.05);
          --hg-user: var(--hg-gray-400);
          --hg-user-fill: rgba(255, 255, 255, 0.06);
          --hg-user-text: rgb(229 231 235);
          --hg-mem: var(--hg-primary-light);
          --hg-mem-fill: color-mix(in srgb, var(--hg-primary-light) 14%, transparent);
          --hg-mem-text: var(--hg-primary-light);
          --hg-know: var(--hg-gray-400);
          --hg-know-fill: rgba(255, 255, 255, 0.06);
          --hg-know-text: rgb(229 231 235);
          --hg-infer: var(--hg-primary-light);
          --hg-highlight: var(--hg-primary-light);
          --hg-answer-border: color-mix(in srgb, var(--hg-primary-light) 40%, transparent);
          --hg-answer-label: var(--hg-primary-light);
          --hg-edge-mem: color-mix(in srgb, var(--hg-primary-light) 50%, transparent);
          --hg-edge-know: color-mix(in srgb, var(--hg-gray-400) 45%, transparent);
          --hg-edge-infer: var(--hg-primary);
        }
        .hydra-graph * { box-sizing: border-box; }
        .hydra-graph .hg-panel {
          margin-top: 0.75rem;
          padding: 0.75rem 1rem;
          border-radius: var(--hg-radius);
          background: var(--hg-panel);
          border: 1px solid var(--hg-border);
        }
        .hydra-graph .hg-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 0.5rem;
          margin-top: 0.75rem;
        }
        .hydra-graph .hg-btn {
          padding: 0.5rem 0.75rem;
          border-radius: var(--hg-radius);
          font-size: var(--hg-text-xs);
          font-weight: 500;
          font-family: inherit;
          cursor: pointer;
          background: transparent;
          white-space: nowrap;
          border: 1px solid var(--hg-border);
          color: var(--hg-text);
          transition: background-color 150ms ease, border-color 150ms ease, color 150ms ease;
        }
        .hydra-graph .hg-btn:hover:not(:disabled) {
          background: var(--hg-hover);
        }
        .hydra-graph .hg-btn:focus-visible {
          outline: 2px solid var(--hg-primary);
          outline-offset: 2px;
        }
        .hydra-graph .hg-btn-primary {
          border-color: color-mix(in srgb, var(--hg-primary) 40%, var(--hg-border));
          color: var(--hg-primary);
        }
        .hydra-graph .hg-btn-mem {
          border-color: color-mix(in srgb, var(--hg-mem) 45%, var(--hg-border));
          color: var(--hg-mem);
        }
        .hydra-graph .hg-btn-know {
          border-color: color-mix(in srgb, var(--hg-know) 45%, var(--hg-border));
          color: var(--hg-know);
        }
        .hydra-graph .hg-btn-muted { color: var(--hg-muted); }
        .hydra-graph .hg-ask {
          display: flex;
          flex-wrap: wrap;
          gap: 0.5rem;
          margin-top: 0.875rem;
        }
        .hydra-graph .hg-ask input {
          flex: 1 1 160px;
          min-width: 0;
          width: 100%;
          max-width: 100%;
          padding: 0.625rem 0.75rem;
          border-radius: var(--hg-radius);
          border: 1px solid var(--hg-border);
          background: var(--hg-input-bg);
          color: var(--hg-text);
          font-size: var(--hg-text-sm);
          font-family: inherit;
          transition: border-color 150ms ease, background-color 150ms ease;
        }
        .hydra-graph .hg-ask input:hover:not(:disabled) {
          background: var(--hg-hover);
        }
        .hydra-graph .hg-ask input:focus {
          outline: 2px solid var(--hg-primary);
          outline-offset: 1px;
        }
        .hydra-graph .hg-ask input:disabled {
          opacity: 0.55;
          cursor: not-allowed;
        }
        .hydra-graph svg {
          width: 100%;
          height: auto;
          max-width: 100%;
          display: block;
          margin-top: 0.75rem;
          touch-action: none;
        }
        .hydra-graph .hg-node-rect {
          transition: stroke 150ms ease, stroke-width 150ms ease, fill 150ms ease, opacity 150ms ease;
          pointer-events: all;
        }
        .hydra-graph .hg-node {
          cursor: grab;
          touch-action: none;
          -webkit-user-select: none;
          user-select: none;
        }
        .hydra-graph .hg-node.is-dragging,
        .hydra-graph svg.is-dragging .hg-node {
          cursor: grabbing;
        }
        .hydra-graph svg.is-dragging {
          cursor: grabbing;
        }
        .hydra-graph .hg-node text,
        .hydra-graph .hg-node path,
        .hydra-graph .hg-node circle {
          pointer-events: none;
        }
        .hydra-graph .hg-node:hover .hg-node-rect {
          stroke-width: 2;
          filter: brightness(1.03);
        }
        .hydra-graph .hg-node:focus {
          outline: none;
        }
        .hydra-graph .hg-node:focus .hg-node-rect {
          stroke: var(--hg-primary);
          stroke-width: 2;
        }
        .hydra-graph .hg-node.is-highlighted .hg-node-rect {
          stroke: var(--hg-highlight);
          stroke-width: 2.5;
        }
        .hydra-graph pre {
          margin: 0;
          font-size: var(--hg-text-xs);
          line-height: 1.5;
          font-family: var(--hg-mono);
          color: var(--hg-code);
          overflow-x: auto;
          white-space: pre-wrap;
          word-break: break-word;
          max-width: 100%;
        }
        .hydra-graph .hg-toggle {
          width: 44px;
          height: 24px;
          border-radius: 999px;
          border: 1px solid var(--hg-border);
          background: var(--hg-panel);
          position: relative;
          cursor: pointer;
          flex-shrink: 0;
          transition: background-color 150ms ease, border-color 150ms ease;
        }
        .hydra-graph .hg-toggle[aria-pressed="true"] {
          background: var(--hg-primary);
          border-color: var(--hg-primary);
        }
        .hydra-graph .hg-toggle-knob {
          position: absolute;
          top: 2px;
          left: 2px;
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: #fff;
          transition: left 0.2s ease;
          box-shadow: 0 0 0 1px rgba(0,0,0,0.06);
        }
        .hydra-graph .hg-toggle[aria-pressed="true"] .hg-toggle-knob { left: 22px; }
        @media (max-width: 420px) {
          .hydra-graph { padding: 0.875rem; }
          .hydra-graph .hg-ask .hg-btn { width: 100%; }
        }
      `}</style>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", flexWrap: "wrap" }}>
        <div style={{ minWidth: 0, flex: "1 1 180px" }}>
          <div style={{ fontSize: "var(--hg-text-sm)", fontWeight: 600, color: "var(--hg-title)" }}>Hydra Context Graph</div>
          <div style={{ fontSize: "var(--hg-text-xs)", color: "var(--hg-muted)", marginTop: "4px", lineHeight: 1.5 }}>
            {isEmpty
              ? "Empty graph — only the user anchor. Load demo data or add nodes to explore."
              : "Memories attach to the user; knowledge nodes link to related docs."}
          </div>
        </div>
        <button type="button" onClick={handleReset} className="hg-btn hg-btn-muted" aria-label="Reset graph and clear ask state">
          Reset
        </button>
      </div>

      <div className="hg-actions">
        <button type="button" onClick={handleLoadDemo} className="hg-btn hg-btn-primary">Load demo data</button>
        <button type="button" onClick={addMemory} className="hg-btn hg-btn-mem">+ Memory</button>
        <button type="button" onClick={addKnowledge} className="hg-btn hg-btn-know">+ Knowledge</button>
      </div>

      <div className="hg-panel" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
        <div style={{ minWidth: 0, flex: "1 1 160px" }}>
          <div style={{ fontSize: "var(--hg-text-xs)", fontWeight: 600 }}>infer</div>
          <div style={{ fontSize: "var(--hg-text-xs)", color: "var(--hg-muted)", marginTop: "2px", lineHeight: 1.45 }}>
            {infer ? "Extract insights and draw keyword-inferred links." : "Store verbatim; show only direct graph edges."}
          </div>
        </div>
        <button type="button" onClick={() => setInfer((prev) => !prev)} aria-pressed={infer} aria-label="Toggle infer" className="hg-toggle">
          <span className="hg-toggle-knob" />
        </button>
      </div>

      <div style={{ display: "flex", gap: "12px", marginTop: "10px", fontSize: "var(--hg-text-xs)", color: "var(--hg-muted)", flexWrap: "wrap" }}>
        <span>User</span>
        <span style={{ color: "var(--hg-mem)" }}>Memory ({memoryCount})</span>
        <span style={{ color: "var(--hg-know)" }}>Knowledge ({knowledgeCount})</span>
        {infer && !isEmpty ? <span style={{ color: "var(--hg-infer)" }}>Inferred ({inferredLinks.length})</span> : null}
        {traversePhase === "memory" ? <span style={{ color: "var(--hg-highlight)" }}>Traversing memories…</span> : null}
        {traversePhase === "knowledge" ? <span style={{ color: "var(--hg-highlight)" }}>Traversing knowledge…</span> : null}
      </div>

      <svg
        id="hydra-graph-svg"
        viewBox="0 0 820 400"
        preserveAspectRatio="xMidYMid meet"
        aria-label="Context graph"
      >
        {edges.map((edge) => (
          <line
            key={edge.key}
            x1={edge.x1}
            y1={edge.y1}
            x2={edge.x2}
            y2={edge.y2}
            stroke={edge.stroke}
            strokeWidth={edge.dashed ? 1.75 : 1.5}
            opacity={edge.dashed ? 0.9 : 0.5}
            strokeDasharray={edge.dashed ? "5 4" : undefined}
            strokeLinecap="round"
          />
        ))}
        {allNodes.map((node) => {
          const pos = positions[node.id];
          if (!pos) return null;
          const colors = nodeColors(node.type);
          const label = node.label.length > 16 ? node.label.slice(0, 15) + "…" : node.label;
          const highlighted = isNodeHighlighted(node);
          const iconX = pos.x + 14;
          const iconY = pos.y + NODE_H / 2;
          const textX = pos.x + NODE_W / 2 + 8;
          return (
            <g
              key={node.id}
              data-node-id={node.id}
              className={"hg-node hg-node-" + node.type + (highlighted ? " is-highlighted" : "")}
              tabIndex={0}
              opacity={traversePhase !== "idle" && !highlighted && node.type !== "user" ? 0.4 : 1}
            >
              <rect
                className="hg-node-rect"
                x={pos.x}
                y={pos.y}
                width={NODE_W}
                height={NODE_H}
                rx={12}
                fill={colors.fill}
                stroke={colors.stroke}
                strokeWidth={1}
              />
              {node.type === "user" ? (
                <circle cx={iconX} cy={iconY} r={5} fill="none" stroke={colors.stroke} strokeWidth={1.25} />
              ) : null}
              {node.type === "memory" ? (
                <path
                  d={"M " + (iconX - 4) + " " + (iconY + 4) + " v-5 a4 4 0 1 1 8 0 v5 M " + (iconX - 4) + " " + (iconY + 4) + " h8"}
                  fill="none"
                  stroke={colors.stroke}
                  strokeWidth={1.25}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ) : null}
              {node.type === "knowledge" ? (
                <path
                  d={"M " + (iconX - 4) + " " + (iconY - 5) + " h6 l2 2 v8 h-8 z M " + (iconX + 2) + " " + (iconY - 5) + " v2 h2"}
                  fill="none"
                  stroke={colors.stroke}
                  strokeWidth={1.25}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ) : null}
              <text
                x={textX}
                y={pos.y + NODE_H / 2 + 4}
                textAnchor="middle"
                fill={colors.text}
                fontSize={12}
                fontFamily="var(--hg-font)"
                fontWeight={node.type === "user" ? 600 : 500}
              >
                {label}
              </text>
            </g>
          );
        })}
        {isEmpty ? (
          <text x="410" y="280" textAnchor="middle" fill="var(--hg-muted)" fontSize={12} fontFamily="var(--hg-font)">
            No memories or knowledge yet — click Load demo data
          </text>
        ) : null}
      </svg>

      {isEmpty ? (
        <div className="hg-panel" style={{ fontSize: "var(--hg-text-sm)", color: "var(--hg-muted)", lineHeight: 1.5 }}>
          Empty state: the graph starts with only a User anchor. Add memories and knowledge, or load the demo set (vegetarian preference, dark mode, VS Code, Auth Guide, menu options, Runbook) to try infer and recall.
        </div>
      ) : null}

      <form onSubmit={handleAsk} className="hg-ask">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={isEmpty ? "Load demo data first, then ask…" : "Ask a question… e.g. What vegetarian options do we have?"}
          disabled={isEmpty}
          aria-label="Ask a question"
        />
        <button
          type="submit"
          disabled={isEmpty}
          className="hg-btn hg-btn-primary"
          style={{ opacity: isEmpty ? 0.5 : 1, cursor: isEmpty ? "not-allowed" : "pointer" }}
        >
          Ask
        </button>
      </form>

      {traversePhase === "idle" && !isEmpty ? (
        <div className="hg-panel" style={{ fontSize: "var(--hg-text-sm)", color: "var(--hg-muted)", lineHeight: 1.5 }}>
          Ready to recall — ask a question to highlight matching memories first, then connected knowledge. Nothing is selected until you submit.
        </div>
      ) : null}

      {traversePhase === "done" && retrievedItems.length > 0 ? (
        <div className="hg-panel">
          <div style={{ fontSize: "var(--hg-text-xs)", fontWeight: 600, color: "var(--hg-muted)", marginBottom: "8px" }}>Retrieved context</div>
          <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "var(--hg-text-sm)", lineHeight: 1.6 }}>
            {retrievedItems.map((item) => (
              <li key={item.id} style={{ marginBottom: "6px" }}>
                <span style={{ color: item.type === "memory" ? "var(--hg-mem)" : "var(--hg-know)", fontWeight: 600 }}>
                  {item.type === "memory" ? "Memory" : "Knowledge"}:
                </span>{" "}
                {item.label}
                <span style={{ display: "block", fontSize: "var(--hg-text-xs)", color: "var(--hg-muted)", marginTop: "2px" }}>{item.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {traversePhase === "done" && retrievedItems.length === 0 && submittedQuestion ? (
        <div className="hg-panel" style={{ fontSize: "var(--hg-text-sm)" }}>
          No nodes matched that question. Try keywords like vegetarian, dark, vscode, auth, or runbook.
        </div>
      ) : null}

      {traversePhase === "done" && cannedAnswer ? (
        <div className="hg-panel" style={{ borderColor: "var(--hg-answer-border)" }}>
          <div style={{ fontSize: "var(--hg-text-xs)", fontWeight: 600, color: "var(--hg-answer-label)", marginBottom: "6px" }}>Answer</div>
          <div style={{ fontSize: "var(--hg-text-sm)", lineHeight: 1.55 }}>{cannedAnswer}</div>
        </div>
      ) : null}

      <div className="hg-panel">
        <div style={{ fontSize: "var(--hg-text-xs)", fontWeight: 600, color: "var(--hg-muted)", marginBottom: "6px" }}>
          {infer ? "Inferred connections" : "Graph mode"}
        </div>
        {!infer ? (
          <div style={{ fontSize: "var(--hg-text-sm)", lineHeight: 1.5 }}>
            Inference is off. Retrieval uses keyword overlap on node labels plus declared knowledge links.
          </div>
        ) : null}
        {infer && isEmpty ? (
          <div style={{ fontSize: "var(--hg-text-sm)", color: "var(--hg-muted)", lineHeight: 1.5 }}>
            Infer is on, but there are no nodes yet — load demo data to see keyword-inferred edges.
          </div>
        ) : null}
        {infer && !isEmpty && explanationItems.length === 0 ? (
          <div style={{ fontSize: "var(--hg-text-sm)", color: "var(--hg-muted)", lineHeight: 1.5 }}>
            No shared keywords between memory and knowledge nodes yet.
          </div>
        ) : null}
        {infer && explanationItems.length > 0 ? (
          <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "var(--hg-text-sm)", lineHeight: 1.6 }}>
            {explanationItems.map((item) => (
              <li key={item.key} style={{ marginBottom: "4px" }}>{item.text}</li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="hg-panel">
        <div style={{ fontSize: "var(--hg-text-xs)", fontWeight: 600, color: "var(--hg-muted)", marginBottom: "6px" }}>
          API preview · POST /query{submittedQuestion ? "" : " (ask a question to preview recall)"}
        </div>
        {queryRequestPreview ? (
          <>
            <div style={{ fontSize: "var(--hg-text-xs)", color: "var(--hg-muted)", marginBottom: "4px" }}>Request</div>
            <pre style={{ marginBottom: "10px" }}>{queryRequestPreview}</pre>
            {queryResponseJson ? (
              <>
                <div style={{ fontSize: "var(--hg-text-xs)", color: "var(--hg-muted)", marginBottom: "4px" }}>Response shape</div>
                <pre>{queryResponseJson}</pre>
              </>
            ) : null}
          </>
        ) : (
          <pre style={{ color: "var(--hg-muted)" }}>{`{
  "database": "acme_corp",
  "collection": "default",
  "query": "<your question>",
  "type": "all",
  "mode": "thinking",
  "graph_context": true
}`}</pre>
        )}
      </div>
    </div>
  );
};
