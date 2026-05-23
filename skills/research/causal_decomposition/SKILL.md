---
name: causal-decomposition
description: Use when the task asks "what caused X", "what improved X", "what policy affected X", or any search where the target's category is unclear. Replaces category-first search with causal decomposition — trace the chain from effect to root cause rather than guessing which category the answer belongs to.
---

# Causal Decomposition: Search by Effect Chain, Not by Category

## The Trap

When asked "what regulation/technology/policy achieved outcome X", the intuition is:
1. Map X to a domain category (e.g., "longer lifespan" → "healthcare regulation")
2. Search for matching items within that category
3. If not found, rotate keywords within the same category

This fails because the **largest contributing cause** often lives in a different domain than what intuition suggests. The most effective lever for X may not be in the "X category" at all.

## The Method: Four-Step Causal Decomposition

Instead of guessing the category, decompose the target and trace backward:

```
Step 1: DECOMPOSE — Break the target into measurable components.
         Target = Σ (component_i × weight_i)
         Ask: what are the constituent factors? how is it measured?

Step 2: IDENTIFY — Find the component with the largest change contribution.
         Ask: which factor's change explains most of the outcome?
         Use historical data, known trends, common knowledge.

Step 3: TRACE — Follow the causal chain back from that component.
         Ask: what drove THAT change? what is the proximal cause?
         Then: what drove that proximal cause?
         Continue until reaching an institutional/legal/technological lever.

Step 4: MATCH — Map the root lever to structural constraints.
         Ask: which entity (law, invention, policy) matches the structural
         clues AND sits at the end of the causal chain?
```

## Why This Works

| Conventional Search | Causal Decomposition |
|---|---|
| "X belongs to category Y, search Y" | "X = Σ factors, which factor dominates?" |
| Domain knowledge required up front | Domain discovered through decomposition |
| Wrong category = infinite dead ends | Wrong component = check another factor |
| Keyword rotation as primary strategy | Causal chain narrowing as primary strategy |

The causal chain gets progressively narrower — each link eliminates vast search spaces. "What improved lifespan?" spans all of human activity. "What reduced infant mortality?" narrows to public health and social conditions. "What delayed first childbirth?" narrows to marriage laws, education, and economic policy. Each step is a smaller search space than the last.

## When to Use

- Result-oriented questions: "what caused/drove/improved/achieved X"
- When the domain of the question differs from the domain of the answer (structural clues point to one category, the outcome suggests another)
- When search stalls after 2+ rounds — stop rotating keywords; re-examine your category assumption instead
- Any multi-clue puzzle where the outcome and the mechanism are in different domains

## Anti-Patterns

- ❌ "X is about health, so search health regulations" — category assumption
- ❌ "Let me try 'health regulation + structural clue'" — keyword soup within wrong category
- ❌ "Maybe it's a different type of health regulation" — rotating within same wrong frame
- ❌ Jumping to Step 4 (match structure) before Steps 1-3 (understand what to match)

## When NOT to Use

- The question directly states the domain (e.g., "which programming language...")
- The answer type is already narrow and well-defined (e.g., "what year...")
- Simple entity lookup (use wikipedia_lookup or web_search directly)
