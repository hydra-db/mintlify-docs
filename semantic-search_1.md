---
title: "Semantic Search & Retrieval"
description: "From the fundamentals of embeddings and similarity to HydraDB's hybrid recall, how semantic search works, where it breaks, and how to use it in production."
---

Most developers adopt semantic search before they understand what it actually does. That's fine until it stops working at which point the fixes are hard because the failure modes aren't obvious from the API surface.

This page walks through semantic search from first principles, then shows how HydraDB composes it with lexical search and graph signals into the hybrid recall that actually powers production agents. By the end you should know *why* you're choosing a particular `alpha`, not just *which* value to type.

## 1. What "semantic" means here

Traditional search matches **tokens** -> the word "refund" retrieves documents containing the literal string "refund". Semantic search matches **meaning** -> a query about "getting my money back" also retrieves the refund documents, because both expressions point to the same underlying concept.

The way this works is deceptively simple:

1. Every piece of text (a chunk of a document, a user memory, a query) is passed through an **embedding model** -> a neural network trained to output a fixed-length vector of numbers, typically 768 or 1,536 dimensions.
2. Texts that mean similar things end up with vectors that are close together in that high-dimensional space.
3. "Search" becomes: embed the query, then find the nearest document vectors.

That's it. The entire magic of semantic search is: *similarity in meaning → proximity in vector space*.

### How "proximity" is measured

The most common metric is **cosine similarity** the cosine of the angle between two vectors. It ranges from -1 (opposite) to 1 (identical direction), and critically, it ignores vector magnitude. Two chunks about the same topic will have vectors pointing the same way even if one is a long document and one is a short note.

You'll sometimes see **dot product** or **Euclidean distance** used instead. For HydraDB's purposes the choice is internal what you see as a developer is the `relevancy_score` in the response, normalized to a 0–1 range where higher is more relevant.

### Why this works at all

Embedding models are trained on enormous amounts of text in a way that forces them to encode concepts into vector directions. A model has "learned" that "refund" and "money back" are related because they appeared in similar contexts across billions of examples. The specific numbers in any given vector are meaningless on their own, what matters is the *geometry* of the space, and that geometry aligns with meaning because training forced it to.

This also explains the failure modes we'll get to in the next section: if the model's training never saw your domain, the geometry won't align with your meaning.

## 2. Where pure semantic search breaks

Semantic search feels like magic the first time it works:)) Then you ship it to real users and hit these, in roughly this order:

### The "strawberry problem"

> The project "strawberry" and the fruit "strawberry" are the same word, but completely different contexts;)

Embedding models collapse both senses into similar vectors because the surface form is identical. A query about fruit supply chains surfaces the secret internal project of the same name, and vice versa. Pure semantic search has no notion of which meaning you care about.

### Exact-match queries get worse, not better

Ask a pure semantic system for "error code E_AUTH_429" and you get a pile of chunks that are *about* authentication errors but don't contain that specific code. The model has generalized the query into a concept, which is exactly what you didn't want. "Lexical search" literally matching the string beats SEMANTIC here every time.

### No temporal awareness

The model doesn't know that your 2024 pricing doc has been superseded by the 2026 one. Both chunks are equally relevant semantically. Without recency signals, stale information surfaces as confidently as current information.

### No notion of "who's asking"

Two users asking "what's our deployment process?" get the same results, even though one is on the mobile team (Fastlane + TestFlight) and the other is on the infra team (Kubernetes + ArgoCD). Embeddings have no idea who's querying.

### No structural reasoning

"Which service does the payments API depend on?" is a relational question. Semantic search finds *documents that discuss payments and dependencies* but cannot *compute* the dependency chain. You need a graph for that.

These five failure modes are why HydraDB doesn't rely on semantic search alone. They're also why dumping everything into a vector database and calling it a memory system produces agents that feel plausible in demos and fall apart in production.

## 3. Lexical search: the unglamorous half

Before we get to HydraDB's hybrid model, a word about lexical search, the thing semantic search was meant to replace but actually needs to complement.

Lexical search scores documents by **term overlap**, typically with BM25, an algorithm that weights rare terms more heavily than common ones and accounts for document length. It's what every search engine used for decades before embeddings existed.

Lexical is the right tool when:

- The query contains proper nouns, product names, or identifiers ("HydraDB MCP", "project-phoenix-v2")
- The query contains codes, SKUs, or error strings
- Users are searching for something they read verbatim
- You need *guaranteed* inclusion of a keyword in results

HydraDB exposes pure lexical recall via `/recall/keyword`. You probably won't use it as your primary query path, but it's the correct fallback when semantic + graph returns nothing, because the one thing semantic *can't* fake is a literal string match.

## 4. Hybrid recall: HydraDB's default

HydraDB doesn't make you choose between semantic and lexical. The `full_recall` endpoint runs both, fuses the scores, and then layers graph and personalization signals on top. You control the semantic-vs-lexical blend with a single parameter:

```json
{
  "query": "How do I rotate API keys?",
  "tenant_id": "acme",
  "alpha": 0.7,
  "recency_bias": 0.2,
  "max_results": 10
}
```

### The `alpha` parameter

`alpha` is the weight given to the **semantic** score in the final fusion. `1 - alpha` is the weight given to the **lexical** score.

| `alpha` | Behavior | Use when |
|---|---|---|
| `1.0` | Pure semantic | Conceptual queries, paraphrases, natural language questions |
| `0.7–0.8` | Semantic-leaning hybrid | **Default for most agent use cases** |
| `0.5` | Balanced | Mixed query patterns, unsure which to prefer |
| `0.2–0.3` | Lexical-leaning hybrid | Technical queries with identifiers, codes, names |
| `0.0` | Pure lexical | Exact-match queries, debugging "why isn't X showing up" |

The intuition: start at `0.7`, measure, and adjust. If users frequently search for product names or codes and get irrelevant results, lower alpha. If they ask conceptual questions and get nothing, raise it.

### The `recency_bias` parameter

`recency_bias` adds a decay factor to the final score based on the memory's timestamp. Higher values push newer memories up the rankings.

- `0.0` — no recency weighting. A 2-year-old document scores the same as yesterday's if equally relevant.
- `0.1–0.3` — mild preference for fresh content. Good for most business contexts where information evolves.
- `0.5+` — strong recency preference. Good for news, support tickets, conversation history.

Use recency carefully: setting it too high means relevant-but-older documents get buried. If you have canonical reference docs (a pricing page, an API spec), consider marking them with metadata and handling them explicitly rather than relying on recency alone.

### What's happening under the hood

When you call `full_recall`, HydraDB runs a multi-stage pipeline. The rough order:

1. **Parse the query** — extract intent, detect entity mentions
2. **Apply deterministic metadata filters** — hard-exclude anything outside the requested scope before any scoring runs
3. **Candidate retrieval** — run semantic search and lexical search in parallel, each producing a top-N candidate list
4. **Score fusion** — combine semantic and lexical scores using `alpha`
5. **Graph traversal** — for queries that reference entities, traverse the context graph to pull in related memories and boost chunks connected to those entities
6. **Personalization re-ranking** — adjust scores based on user and agent behavior history
7. **Recency decay** — apply `recency_bias`
8. **Final ranking** — return top `max_results`

The key insight: pure vector databases only do step 3 (semantic half) and step 8. Everything in between: filters, fusion, graph, personalization is what turns raw similarity search into something an agent can actually trust.

## 5. Applying it: practical recipes

Here's how the theory maps onto real queries. Each recipe assumes you've already created a tenant and ingested some memories.

### Recipe 1: General Q&A over a knowledge base

Most common case. The user asks a natural-language question, you want the most useful chunks surfaced.

```typescript
const result = await client.recall.fullRecall({
  query: userQuestion,
  tenantId: "acme",
  subTenantId: userId,
  alpha: 0.7,
  recencyBias: 0.2,
  maxResults: 8
});
```

Why these values: `0.7` leans semantic because users phrase things differently than docs are written; `0.2` recency gives a mild freshness boost without burying canonical references; `8` results is enough context for most LLMs without padding the prompt.

### Recipe 2: Technical lookup with identifiers

User is searching for something specific, an error code, a function name, a product SKU. Lean lexical.

```typescript
const result = await client.recall.fullRecall({
  query: "TimeoutError in payments-worker v4.2.1",
  tenantId: "acme",
  alpha: 0.3,              // lexical-leaning
  recencyBias: 0.4,        // recent logs matter more
  maxResults: 5
});
```

Why: the version number and error class name need to match literally. `alpha: 0.3` means 70% of the score comes from lexical — but keeping 30% semantic still catches docs that describe the error without using that exact phrase.

### Recipe 3: Scoped recall with metadata filters

User is in a specific project workspace. You don't want memories from other projects bleeding in, regardless of similarity.

```typescript
const result = await client.recall.fullRecall({
  query: "What's the current sprint status?",
  tenantId: "acme",
  subTenantId: "team-mobile",
  alpha: 0.8,
  documentMetadata: { project: "phoenix" },   // hard filter
  maxResults: 6
});
```

The metadata filter runs *before* scoring, so nothing outside `project: phoenix` enters the candidate pool. This is the deterministic control that pure vector search can't give you.

### Recipe 4: Personalized recall for a returning user

User memories are stored and should shape what surfaces. Use `recall_preferences` instead of `full_recall`.

```typescript
const prefs = await client.recall.recallPreferences({
  query: "How should I explain this feature?",
  tenantId: "acme",
  subTenantId: userId,
  alpha: 0.8
});
```

This queries the user's own memory store — preferences, past conversations, inferred traits — rather than the shared knowledge base. Typically you'd run both in parallel and feed both into the agent prompt.

### Recipe 5: Graceful degradation

Combine the above with the fallback chain from the [Error Handling Playbook](/error-handling). The short version:

1. Try `full_recall` with your chosen `alpha`
2. If empty, retry with `alpha: 0.9` and `recency_bias: 0`
3. If still empty, fall back to `keyword` (pure lexical)
4. If still empty, tell the user you don't have context — don't let the agent hallucinate

## 6. Choosing good chunks in the response

Recall returns ranked chunks with a `relevancy_score`. A common mistake is feeding all of them into the agent prompt regardless of score. Better pattern:

```typescript
const MIN_SCORE = 0.55;
const usable = result.chunks.filter(c => c.relevancy_score >= MIN_SCORE);

if (usable.length === 0) {
  // Don't pass garbage context to the LLM — it'll anchor on it
  return agentWithoutContext(userQuestion);
}

return agentWithContext(userQuestion, usable);
```

The threshold is workload-specific. Watch your own recall distributions for a week and you'll see a natural break — usually somewhere between `0.4` and `0.6`. Below that, chunks are typically more harmful than helpful because they give the agent confident-looking-but-wrong context.

## 7. When semantic alone is the right answer

Despite everything above, there are workloads where plain semantic search with no hybrid, no graph, no personalization is genuinely the right tool:

- **Small, homogeneous corpora** — a few hundred product descriptions, a single technical manual. The graph adds overhead without benefit.
- **One-shot duplicate detection** — "is this text semantically similar to anything already stored?" Use the embeddings API directly (`/embeddings/search`) and skip the recall pipeline.
- **Clustering and analysis** — you're not answering queries, you're finding structure. Raw embeddings are the right primitive.

For these, HydraDB's Custom Embeddings endpoints let you bypass the opinionated recall pipeline and work with vectors directly. The rest of the time — which is most of the time — `full_recall` with a sensible `alpha` is what you want.

## 8. Mental model to walk away with

If you only remember one thing from this page:

> Semantic search finds things that **mean the same**. Lexical search finds things that **say the same**. Graphs find things that are **connected**. Real retrieval needs all three, and HydraDB runs all three for you when you call `full_recall`.

The `alpha` parameter is you telling HydraDB how much of the first versus the second to weigh. The graph and personalization layers are always on. And the metadata filters are the one piece of *absolute* control you have — use them whenever a query has a scope that should never be violated.

Everything else is tuning.
