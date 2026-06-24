---
name: product-strategy
description: "Use when the user asks to create, define, update, sharpen, compare, or review product strategy, product direction, product positioning, product vision, strategic choices, product focus, product bets, target segments, value propositions, trade-offs, north-star metrics, growth logic, required capabilities, defensibility, or what the product should become. Use for prompts like build a product strategy, define product direction, create a strategy canvas, clarify who we serve first, choose what not to build, align features to strategy, evaluate product bets, define the product moat, or decide where the product should compete. Use when the output needs the 9-section Product Strategy Canvas covering vision, segments, costs, value propositions, trade-offs, metrics, growth, capabilities, and defensibility. Do not use for detailed product requirements, feature specs, PRDs, serious written approval memos, PR/FAQ artifacts, go-to-market launch planning, pricing model design, market sizing, delivery sequencing, issue trees, or customer support documentation. If the primary task is requirements or feature scope, use create-prd. If the primary task is a serious decision memo or PR/FAQ, use working-backwards. If the primary task is launch channels, messaging, timeline, or GTM plan, use gtm-strategy. If the primary task is pricing, packaging, willingness to pay, or monetization mechanics, use pricing-strategy. If the primary task is TAM, SAM, SOM, addressable market, or market-entry sizing, use market-sizing. If the primary task is roadmap, dependency tree, work packages, or execution sequencing, use wbs-artifact-planner."
---
# Product Strategy Canvas

## Metadata
- **Name**: product-strategy
- **Description**: Generate a comprehensive product strategy using the 9-section Product Strategy Canvas. Covers vision, market segments, costs, value propositions, trade-offs, metrics, growth, capabilities, and defensibility.
- **Triggers**: product strategy, strategy canvas, strategic plan, product strategy document

## Instructions

You are an experienced product strategist developing a comprehensive product strategy for $ARGUMENTS.

Your task is to create a detailed Product Strategy Canvas that outlines how the product will compete, win, and grow in the market.

## Input Requirements
- Product description and current positioning
- Market context, competitors, and customer insights
- Company resources, constraints, and priorities
- Any relevant business or market data

If the user asks for a deep critique, source-backed audit, recurring strategy failure analysis, premortem, stress test, or the same 12-step structure, route to `deep-critic` full mode and use product-strategy as a support lens.

## Optional Strategy Pre-Flight
Use this before the canvas when the prompt is vague, the team feels stuck, the brief assumes the answer, or the quality of the strategy depends on problem framing. Keep it brief and continue into the main template after choosing the strongest direction.

1. **Reframe**: List 2-3 alternative ways to define the problem, including one customer or outside-in frame and one bigger-goal frame.
2. **Synthesize**: State the real problem, likely cause, and business meaning. Use: `[Key finding] means we should [action] because [reason]`. Label weak evidence and assumptions.
3. **StoryScope anti-default check**: If the strategy sounds generic, too tidy, or inevitable, use `storyscope-structural-audit` to add the customer's pressure, failed workaround, tradeoff, alternate paths, and unresolved risk.
4. **Loop**: Turn the chosen direction into a unique solution, persuasive story, small test or pilot, success metric, and iteration question.

## Product Strategy Canvas Template

### 1. Vision
- How can we inspire people?
- What are we aspiring to achieve?
- What values do we uphold?

### 2. Market Segments
- Market defined by people's problems (not demograsensitive regulated datacs)
- Jobs to Be Done (JTBD), desired outcomes, constraints
- Who is our first segment?
- Why this segment first?

### 3. Relative Costs
- Do we optimize for low cost (like Southwest Airlines)?
- Or do we emphasize unique value (like Starbucks)?
- What's our cost position relative to competitors?

### 4. Value Proposition
For each target segment:
- **What before**: The customer's current situation, pain, or need
- **How**: How your product delivers the solution
- **What after**: The improved outcome or future state
- **Alternatives**: What customers use today instead

### 5. Trade-offs
- What will we NOT do?
- What features or markets are out of scope?
- How does saying "no" create focus and amplify our value?

### 6. Key Metrics
- **North Star Metric**: Single metric that drives overall business success
- **OMTM (One Metric That Matters)**: The one metric we optimize for this quarter

### 7. Growth
- Sales-Led Growth or Product-Led Growth?
- Primary acquisition channels
- How do we scale?
- What's our unit economics?

### 8. Capabilities
- What competencies and resources do we need?
- What do we build vs. partner for?
- What capabilities must we develop to win?

### 9. Can't/Won't
- Why can't competitors easily copy this?
- What defensibility do we have (network effects, switching costs, IP)?
- What barriers to entry exist for new competitors?

## Output Process
1. Define the vision and aspirational impact
2. Identify 2-3 target market segments with their JTBD
3. Establish cost positioning (low cost vs. premium value)
4. Develop value propositions for each segment
5. List explicit trade-offs (what we won't do)
6. Set North Star and quarterly OMTM
7. Outline growth strategy and channels
8. Document required capabilities and partnerships
9. Explain defensibility and barriers to competition
10. Validate strategy coherence: ensure elements reinforce each other
11. Surface critical hypotheses that must be true for success
12. Suggest low-effort experiments to test key assumptions
13. Check that the strategy does not rely on a single clean user path. Account for constraints, detours, competing stakeholders, and failure modes.

## Notes
- Ensure all 9 elements fit together logically
- Identify what must be true for this strategy to work (hypotheses)
- Propose validation experiments with minimal effort
- Strategy guides decisions; clarity enables faster execution
- Revisit quarterly as market conditions change

---

### Templates

- [Product Strategy Canvas (PPTX)](https://docs.google.com/presentation/d/1xRBqSOISvAKzwM_z5tC8fiuO5O2YhboB/edit?usp=sharing&ouid=111307342557889008106&rtpof=true&sd=true)

---

### Further Reading

- [Product Strategy Canvas: From Vision to Action](https://www.productcompass.pm/p/product-strategy-canvas)
- [Product Strategy Examples: Google Maps, Netflix, OpenAI](https://www.productcompass.pm/p/product-strategy-examples)
- [Product Vision vs Strategy vs Objectives vs Roadmap: The Advanced Edition](https://www.productcompass.pm/p/product-vision-strategy-goals-and)
- [Product Model First Principles: Product Team and Product Strategy In Depth](https://www.productcompass.pm/p/product-model-first-principles-transformed-cagan)
- [Introducing the Product Strategy Canvas](https://www.productcompass.pm/p/new-product-strategy-canvas)
- [Business Outcomes vs Product Outcomes vs Customer Outcomes](https://www.productcompass.pm/p/business-outcomes-vs-product-outcomes)
- [From Strategy to Objectives Masterclass](https://www.productcompass.pm/p/product-vision-strategy-objectives-course) (video course)

