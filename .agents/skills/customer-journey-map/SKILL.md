---
name: customer-journey-map
description: "Use when the user asks to create, map, analyze, improve, or visualize a customer journey, user journey, service journey, onboarding journey, buying journey, support journey, adoption journey, or end-to-end customer experience. Use for journey maps, stages, touchpoints, user actions, emotions, questions, pain points, drop-off points, friction, time to value, aha moment, moments of truth, churn triggers, retention opportunities, experience gaps, onboarding improvements, and prioritized journey improvements. Use for prompts like map the customer experience, where do users drop off, improve onboarding, identify friction points, map touchpoints, visualize the user journey, find pain points, what is the aha moment, why are users confused, or prioritize experience fixes. Do not use for GTM launch channels, customer-facing help articles, API docs, product requirements, broad product strategy, stakeholder influence mapping, or pure support-ticket response writing. If the primary task is launch channels, acquisition, messaging, or market-entry execution, use gtm-strategy. If the primary task is customer-facing support documentation, troubleshooting, setup, or API docs, use support-docs. If the primary task is PRD, feature requirements, or MVP scope, use create-prd. If the primary task is product direction, segments, trade-offs, or defensibility, use product-strategy. If the primary task is stakeholder alignment, approvals, or communication planning, use stakeholder-map."
---

## Customer Journey Map

Map the end-to-end customer experience from awareness through advocacy, identifying emotions, pain points, and improvement opportunities at each stage.

### Context

You are creating a customer journey map for **$ARGUMENTS**.

If the user provides files (interview transcripts, survey data, analytics, support tickets, or existing journey maps), read them first. Use web search to understand the product if a URL is provided.

### Instructions

1. **Define the persona**: Who is traveling this journey? Use a specific persona with JTBD, not a generic user.

2. **Map the journey stages** (adapt to the product):

   | Stage | Description |
   |---|---|
   | **Awareness** | How do they first learn about the product? |
   | **Consideration** | What do they evaluate? What alternatives do they compare? |
   | **Acquisition** | How do they sign up or purchase? |
   | **Onboarding** | First experience with the product — time to value |
   | **Engagement** | Regular usage — building habits |
   | **Retention** | What keeps them coming back? What might cause churn? |
   | **Advocacy** | When and why do they recommend the product to others? |

   If the journey looks too linear or generic, apply `storyscope-structural-audit` as a companion lens. Add loops, failed workarounds, social influence, delayed trust, competing priorities, and moments where the user changes course.

3. **For each stage, document**:

   - **Touchpoints**: Where the user interacts with the product, brand, or team (website, email, in-app, support, social media)
   - **User actions**: What they do at this stage
   - **Thoughts & questions**: What's on their mind ("Is this worth my time?" "How do I...?")
   - **Emotions**: How they feel (excited, confused, frustrated, delighted) — rate on a scale or use emoji indicators
   - **Pain points**: Friction, confusion, drop-off risks
   - **Opportunities**: How to improve the experience at this point

4. **Identify critical moments**:
   - **Aha moment**: When the user first experiences core value
   - **Moments of truth**: Decision points where they commit or abandon
   - **Churn triggers**: Where users most commonly drop off
   - **Revelation moments**: When new information changes how the user interprets the product, risk, cost, or value

5. **Create the journey map table**:

   | Stage | Touchpoint | User Action | Emotion | Pain Point | Opportunity |
   |---|---|---|---|---|---|

6. **Recommend prioritized improvements**:
   - Which pain points have the highest impact on conversion or retention?
   - What quick wins can improve the experience immediately?
   - What requires deeper investment but has the biggest payoff?

Think step by step. Save as a markdown document. For visual journey maps, suggest the user create one in Miro or FigJam using this analysis as the foundation.

---

### Further Reading

- [User Journey Mapping 101](https://www.productcompass.pm/p/user-journey-mapping-101)
- [Funnel Analysis 101: How to Track and Optimize Your User Journey](https://www.productcompass.pm/p/funnel-analysis)
- [Market Research: Advanced Techniques](https://www.productcompass.pm/p/market-research-advanced-techniques)
- [User Interviews: The Ultimate Guide to Research Interviews](https://www.productcompass.pm/p/interviewing-customers-the-ultimate)
